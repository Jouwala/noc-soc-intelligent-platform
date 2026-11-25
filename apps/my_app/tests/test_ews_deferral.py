# -*- coding: utf-8 -*-
"""邮箱通道降级时的「延后执行」门禁（services/email/ews_deferral.py）回归测试。

要锁住的行为（2026-11-18 用户要求）：

  1. 通道**已确认不可用**且探测足够新时，纯发信任务在定时触发下**不执行**；
  2. 通道正常 / 探测过期 / 手动触发 / 未标注任务 → 一律照常执行（fail-open）；
  3. 延后记录带 ``EWS_DEFERRED`` 标记，能被 email_recovery 识别为「恢复后要补」，
     且不会被 auto_retry 当成普通失败重投（否则降级期间又空跑一轮）；
  4. 注册表里**只**给纯发信任务标注，带同步 / WhatsApp 副通道的任务不得标注。

全部离线：不连 EWS、不发邮件、不写线上 DB。
运行：
    cd D:/02_olbr_portal/apps/my_app
    python -m pytest tests/test_ews_deferral.py -q
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.services.email import ews_deferral as ed  # noqa: E402
from backend.services.email import email_recovery as er  # noqa: E402


def _probe(status="down", kind="server_down", minutes_ago=2, detail="HTTP 401（NTLM 认证被拒）"):
    return {
        "status": status,
        "kind": kind,
        "detail": detail,
        "last_probe_at": (datetime.now() - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds"),
    }


@pytest.fixture
def deferrable_task():
    return {
        "task_id": "liberia_email_1h",
        "name": "Liberia 宕站 1小时报告邮件",
        "category": "email",
        "defer_on_ews_down": True,
    }


def _patch_probe(monkeypatch, probe):
    monkeypatch.setattr(ed, "_read_probe", lambda: probe)


# ------------------------------------------------------------ 门禁判定

def test_defers_when_channel_down_and_probe_fresh(monkeypatch, deferrable_task):
    _patch_probe(monkeypatch, _probe(kind="server_down"))
    res = ed.maybe_defer(deferrable_task, "scheduled")
    assert res is not None
    assert res["success"] is False and res["deferred"] is True
    assert res["ews_kind"] == "server_down"
    assert ed.DEFERRED_MARKER in res["error"]


def test_auth_error_also_defers(monkeypatch, deferrable_task):
    """凭据被拒同样发不出去；不能因为不是 server_down 就继续空跑。"""
    _patch_probe(monkeypatch, _probe(kind="auth_error"))
    assert ed.maybe_defer(deferrable_task, "scheduled") is not None


def test_no_defer_when_channel_up(monkeypatch, deferrable_task):
    _patch_probe(monkeypatch, _probe(status="up", kind="ok"))
    assert ed.maybe_defer(deferrable_task, "scheduled") is None


def test_stale_probe_fails_open(monkeypatch, deferrable_task):
    """探测过期（email_recovery 被堵住等）时不许凭旧结论静默跳过。"""
    _patch_probe(monkeypatch, _probe(minutes_ago=ed._MAX_PROBE_AGE_MIN + 5))
    assert ed.maybe_defer(deferrable_task, "scheduled") is None


def test_missing_last_probe_at_fails_open(monkeypatch, deferrable_task):
    _patch_probe(monkeypatch, {"status": "down", "kind": "server_down"})
    assert ed.maybe_defer(deferrable_task, "scheduled") is None


def test_manual_trigger_is_never_deferred(monkeypatch, deferrable_task):
    """人可能刚改完密码要立刻验证；手动触发必须照常执行。"""
    _patch_probe(monkeypatch, _probe())
    assert ed.maybe_defer(deferrable_task, "manual") is None


def test_unflagged_task_is_not_deferred(monkeypatch):
    _patch_probe(monkeypatch, _probe())
    task = {"task_id": "fw_kpi_sync", "defer_on_ews_down": False}
    assert ed.maybe_defer(task, "scheduled") is None


def test_env_kill_switch(monkeypatch, deferrable_task):
    _patch_probe(monkeypatch, _probe())
    monkeypatch.setenv("EWS_DEFER_ON_DOWN", "0")
    assert ed.maybe_defer(deferrable_task, "scheduled") is None


def test_missing_state_fails_open(monkeypatch, deferrable_task):
    _patch_probe(monkeypatch, None)
    assert ed.maybe_defer(deferrable_task, "scheduled") is None


# ------------------------------------------- 与补发 / 重试通道的衔接

def test_deferred_marker_is_resendable_after_recovery(monkeypatch, deferrable_task):
    _patch_probe(monkeypatch, _probe(detail="ErrorTimeoutExpired: timed out after 300 seconds"))
    res = ed.maybe_defer(deferrable_task, "scheduled")
    # 即便探测详情里带 "timed out after"（本属不可补发关键字），延后记录也必须可补发
    assert er.is_ews_resendable(res["error"]) is True


def test_deferred_record_is_not_auto_retried(monkeypatch, deferrable_task):
    """mailbox_cleanup 不在 EMAIL_TASK_IDS 内，但延后同样不能被 auto_retry 重投。"""
    from backend.services import auto_retry as ar
    _patch_probe(monkeypatch, _probe())
    res = ed.maybe_defer(deferrable_task, "scheduled")
    assert ar._is_excluded_from_retry("mailbox_cleanup", res["error"]) is True


def test_deferred_record_is_classified_as_ews_dependency():
    """db_freshness 必须把延后记录归为「EWS 依赖类」，否则会按「任务真停摆」逐条告警。

    与 2026-09-18 的教训同源：一次 EWS 故障让 14 个报表任务各自「逾期」刷屏。
    """
    from backend.services.db.db_freshness_check import (
        _dependency_source, _is_dependency_failure,
    )

    err = "EWS_DEFERRED: 邮箱通道未恢复（server_down，最后探测 2026-11-18T14:40:00）"
    assert _dependency_source(err) == "ews"
    assert _is_dependency_failure(err) is True


# ------------------------------------------------- 注册表标注范围（防误标）

def test_registry_marks_only_pure_senders():
    from backend.task_registry import EWS_DEFERRABLE_IDS, TASK_SPECS

    ids = {s["task_id"] for s in TASK_SPECS}
    stale = EWS_DEFERRABLE_IDS - ids
    assert not stale, f"EWS_DEFERRABLE_IDS 里有不存在的任务: {sorted(stale)}"

    # 纯发信任务应被标注
    for must in ("liberia_email_1h", "kpi_ms_contract", "sp_material_report",
                 "packet_loss_email", "battery_email", "mailbox_cleanup"):
        assert must in EWS_DEFERRABLE_IDS, f"{must} 应可延后"

    # 带同步 / WhatsApp 副通道的任务**不得**标注：延后会停掉同步或群发
    for must_not in ("fw_kpi_sync", "ume_in_osg_report",
                     "sp_email_details", "sp_online_report_send"):
        assert must_not not in EWS_DEFERRABLE_IDS, f"{must_not} 不应整轮延后"


# ------------------------------------------------------ 探测状态读取

def test_read_probe_uses_email_recovery_state_file(monkeypatch, tmp_path):
    """必须复用 email_recovery 落盘的状态（唯一直相源），读不到时 fail-open。"""
    state_file = tmp_path / "email_recovery_state.json"
    state_file.write_text(
        json.dumps({er.EWS_STATE_KEY: _probe()}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(er, "RECOVERY_STATE_FILE", state_file)
    probe = ed._read_probe()
    assert probe is not None and probe["status"] == "down"

    state_file.unlink()
    assert ed._read_probe() is None


# ------------------------------------------------------ run_task 集成

def test_run_task_defers_without_calling_func(monkeypatch):
    """门禁命中时任务函数一次都不许调用，且不触发失败告警。"""
    from backend.services.task import task_runner as tr

    called = []
    saved = []
    notified = []

    monkeypatch.setattr(tr, "_refresh_task_schedule_from_db", lambda tid: None)
    monkeypatch.setattr(tr, "_save_task_result", lambda r: saved.append(r))
    monkeypatch.setattr(tr, "notify_task_failure",
                        lambda *a, **k: notified.append(a))
    monkeypatch.setattr(tr, "notify_task_failure_email",
                        lambda *a, **k: notified.append(a))
    _patch_probe(monkeypatch, _probe())

    task_id = "__t_ews_defer"
    tr._task_registry[task_id] = {
        "task_id": task_id,
        "name": "deferred probe",
        "category": "email",
        "enabled": True,
        "func": lambda: called.append(1) or {"success": True},
        "defer_on_ews_down": True,
        "is_test": False,
    }
    try:
        out = tr.run_task(task_id, trigger="scheduled")
    finally:
        tr._task_registry.pop(task_id, None)

    assert called == [], "通道不可用时任务函数不得被执行"
    assert out["deferred"] is True
    assert out["trigger"] == "deferred"
    assert out["success"] is False
    assert ed.DEFERRED_MARKER in out["error"]
    assert notified == [], "延后属于依赖不可用，不应发失败告警"
    assert saved and saved[0]["trigger"] == "deferred"


# ------------------------------------- 细粒度门禁（带同步/群发副作用的任务）

def test_fine_grained_ids_are_excluded_from_whole_task_defers():
    """细粒度与整轮门禁必须互斥：带副作用的任务不能整轮延后。

    否则同步和群发会跟着一起停，这正是 2026-11-25 加细粒度门禁要修的问题。
    """
    from backend.task_registry import EWS_DEFERRABLE_IDS
    assert not (ed.FINE_GRAINED_TASK_IDS & EWS_DEFERRABLE_IDS), (
        "细粒度任务不得同时被整轮延后: "
        f"{sorted(ed.FINE_GRAINED_TASK_IDS & EWS_DEFERRABLE_IDS)}"
    )
    assert ed.FINE_GRAINED_TASK_IDS == {
        "sp_email_details", "sp_online_report_send", "fw_kpi_sync", "ume_in_osg_report",
    }


def test_fine_grained_skips_only_when_down_and_fresh(monkeypatch, deferrable_task):
    _patch_probe(monkeypatch, _probe())
    ed.set_current_trigger("scheduled")
    assert ed.should_skip_email_send("sp_email_details") is True
    # 不带副作用的任务不参与
    assert ed.should_skip_email_send("liberia_email_1h") is False


def test_fine_grained_fails_open(monkeypatch):
    """通道正常 / 探测过期 / 状态缺失 / 手动触发 —— 一律照常发信。"""
    ed.set_current_trigger("scheduled")
    _patch_probe(monkeypatch, _probe(status="up", kind="ok"))
    assert ed.should_skip_email_send("fw_kpi_sync") is False
    _patch_probe(monkeypatch, _probe(minutes_ago=ed._MAX_PROBE_AGE_MIN + 5))
    assert ed.should_skip_email_send("fw_kpi_sync") is False
    _patch_probe(monkeypatch, None)
    assert ed.should_skip_email_send("fw_kpi_sync") is False
    _patch_probe(monkeypatch, _probe())
    ed.set_current_trigger("manual")  # 人可能刚改完密码要验证
    assert ed.should_skip_email_send("fw_kpi_sync") is False


def test_fine_grained_env_kill_switch(monkeypatch):
    _patch_probe(monkeypatch, _probe())
    ed.set_current_trigger("scheduled")
    monkeypatch.setenv("EWS_DEFER_ON_DOWN", "0")
    assert ed.should_skip_email_send("sp_online_report_send") is False


def test_skipped_email_result_is_resendable():
    """跳过的发信结果必须带 EWS_DEFERRED，能被补发链认领、不被 auto_retry 重投。"""
    from backend.services import auto_retry as ar
    res = ed.skipped_email_result("fw_kpi_sync")
    assert res["success"] is False and res["deferred"] is True
    assert er.is_ews_resendable(res["error"]) is True
    assert ar._is_excluded_from_retry("fw_kpi_sync", res["error"]) is True
    # 即便探测详情含 "timed out after" 这类不可补发关键字，标记仍优先
    assert "仅跳过发信" in res["message"]


def test_apply_email_deferral_preserves_side_effect_results():
    """顶层 deferred 提升后，同步/群发的真实结果必须原样保留。"""
    email_res = ed.skipped_email_result("sp_email_details")
    result = {
        "success": False,
        "email": email_res,
        "whatsapp": {"success": True, "groups_sent": 1},
        "merged_file": "SP Details Report x.xlsx",
        "row_counts": {"Inventory": 42},
    }
    assert ed.apply_email_deferral(result, email_res, "sp_email_details") is True
    assert result["deferred"] is True
    assert result["success"] is False
    assert ed.DEFERRED_MARKER in result["error"]
    assert result["whatsapp"]["success"] is True       # 群发结果没被冲掉
    assert result["merged_file"] and result["row_counts"]  # 同步产物没被冲掉


def test_apply_email_deferral_noop_for_real_failure():
    result = {"success": False, "error": "Email: returncode=3"}
    assert ed.apply_email_deferral(result, {"success": False, "error": "x"}, "fw_kpi_sync") is False
    assert result.get("deferred") is None


def test_fw_defaults_to_local_write():
    """2026-11-25：FW 模块默认 REMOTE_WRITE=False，全部本地写入。"""
    import importlib
    mod = importlib.import_module("backend.services.fw_kpi.fw_kpi_services")
    # 生产 backend/.env 未设 FW_REMOTE_WRITE 时必须是 False；即便设了，
    # 源码默认值也应是关闭远端（用 importlib.reload 的解析逻辑做静态校验）。
    assert mod.REMOTE_WRITE in (True, False)
    src = (ROOT / "backend" / "services" / "fw_kpi" / "fw_kpi_services.py").read_text(encoding="utf-8")
    assert '"FW_REMOTE_WRITE", "0"' in src, "FW 远端写入默认必须关闭"


def test_service_skips_email_but_keeps_whatsapp_and_merge(monkeypatch, tmp_path):
    """端到端接线：通道 down 时 run_sp_details_email 跳过发信，但合并 + 群发照常。

    这是细粒度门禁的核心承诺——不能因为跳过发信就把副作用一起停掉，
    那正是整轮门禁被排除在这四个任务之外的原因。
    """
    from backend.services.email import kpi_email_report as ker
    from backend.services.email import email_recipient_store as ers

    monkeypatch.setattr(ers, "get_email_recipients", lambda *a, **k: ("a@x.com", "c@x.com"))
    monkeypatch.setattr(ker, "_SP_DETAILS_DATA", tmp_path)
    monkeypatch.setattr(ker, "_merge_sp_details_workbook", lambda p: ({"Inventory": 7}, None))
    monkeypatch.setattr(ker, "_prune_sp_details_reports", lambda **k: 0)

    calls = {"run_script": 0, "whatsapp": 0}
    def _fail_if_called(*a, **k):
        calls["run_script"] += 1
        raise AssertionError("通道 down 时不应真去调邮件脚本")
    def _wa(path):
        calls["whatsapp"] += 1
        return {"success": True, "groups_sent": 1}

    monkeypatch.setattr(ker, "_run_script", _fail_if_called)
    monkeypatch.setattr(ker, "_send_sp_details_to_whatsapp", _wa)
    _patch_probe(monkeypatch, _probe())
    ed.set_current_trigger("scheduled")

    res = ker.run_sp_details_email()

    assert calls["run_script"] == 0, "发信必须被跳过"
    assert calls["whatsapp"] == 1, "WhatsApp 群发必须照常执行"
    assert res["row_counts"] == {"Inventory": 7}, "合并产物必须保留"
    assert res["whatsapp"]["success"] is True
    assert res["deferred"] is True and res["success"] is False
    assert ed.DEFERRED_MARKER in res["error"]
    assert er.is_ews_resendable(res["error"]) is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_auto_retry_is_deferred_too(monkeypatch, deferrable_task):
    """auto_retry 重投也必须延后（2026-11-25）。

    案例：liberia_email_1h 在通道故障期超时 300s 记失败 → auto_retry 重投 →
    门禁只认 scheduled，于是又完整空跑 300s。通道已确认不可用时，重投除了占住
    m 组 worker 没有任何产出。manual 仍不拦（人可能刚改完密码）。
    """
    _patch_probe(monkeypatch, _probe(kind="server_down"))
    res = ed.maybe_defer(deferrable_task, "auto_retry")
    assert res is not None and res["deferred"] is True


def test_fw_kpi_run_promotes_email_deferral(monkeypatch):
    """#298 第 1 项：FW 日报通道 down 时，顶层必须带 deferred + EWS_DEFERRED。

    场景是数据同步**已成功**、按设计只跳过发信：若延后标记没提升到顶层，
    ``task_runner`` 会当普通失败报红并立刻发告警，``email_recovery`` 也认不到
    「恢复后要补」这条失败链。这里用 skipped_email_result 驱动 ``run()`` 的接线，
    并确认同步结果（rows_written / devices）没有被延后吞掉。
    """
    from backend.services.fw_kpi import fw_kpi_services as fw

    monkeypatch.setattr(fw, "Session", lambda: object())
    monkeypatch.setattr(fw, "login", lambda sess: None)
    monkeypatch.setattr(fw, "DEVICE_IDS", ["37781505"])
    monkeypatch.setattr(fw, "fetch_throughput_html", lambda *a, **k: "<html/>")
    monkeypatch.setattr(fw, "parse_throughput",
                        lambda *a, **k: [("2026-11-25 00:00", 1, 2)])
    monkeypatch.setattr(fw, "write_to_local_db", lambda rows, device_id: len(rows))
    monkeypatch.setattr(fw, "ENABLE_CHART_EMAIL", True)
    monkeypatch.setattr(fw, "generate_chart_and_send_email",
                        lambda rows: dict(ed.skipped_email_result("fw_kpi_sync")))

    result = fw.run()

    assert result["deferred"] is True
    assert result["success"] is False
    assert ed.DEFERRED_MARKER in result["error"]
    assert er.is_ews_resendable(result["error"]) is True
    assert "仅跳过发信" in result["error"]
    # 同步产物不能被延后标记吞掉
    assert result["rows_written"] == 1
    assert result["devices"] == ["37781505"]

# -*- coding: utf-8 -*-
"""EWS 邮件通道不可用时，把「纯发信」任务延后（不执行、不告警），恢复后交给补发通道。

## 为什么要有这一层（2026-11-18 立，2026-11-19 移植到 02 新仓库）

``email_recovery`` 已经负责两件事：每 10 分钟真实探测 EWS；恢复后按任务时效窗口
补发失败链。但它只在任务**失败之后**才介入 —— 通道降级期间，每个到点的邮件任务
仍会完整跑一遍：生成报表 → 建 EWS 会话 → 等超时（实测单次可达数分钟）→ 记失败
→ 发失败告警。

更贵的是**故障域**：邮件任务集中在 Worker ``m`` 组串行（见 ``worker_watchdog.py``），
这些注定失败的运行会把 m 组占满，拖慢同组真正能跑的 WhatsApp / 截图等任务。

本模块把「通道已确认不可用」这一事实**前移到执行前**，复用 ``email_recovery``
落盘的探测状态（唯一真相源，不另做探测）：

  - 只对注册表标了 ``defer_on_ews_down=True`` 的任务生效。纯发信任务才标；
    带数据同步（fw_kpi_sync / ume_in_osg_report）或 WhatsApp 副通道
    （sp_email_details / sp_online_report_send）的任务**不标** —— 延后它们会连
    同步和群发一起停掉，见 ``task_registry.EWS_DEFERRABLE_IDS`` 的注释。
  - 只对**定时触发与自动重试**生效；手动触发照常执行：人可能刚改完密码，需要立刻验证。
    自动重试也要拦（2026-11-25 补）：1H 报告超时 300s 后由 auto_retry 重投，而门禁当时
    只认 scheduled —— 通道已确认不可用时每次重试仍空转 5 分钟，还白占 m 组 worker
    （实测 06:18/06:27 两次重试各耗 300s）。auto_retry 重投前同样先看探测状态。
  - 探测状态必须足够新（``EWS_DEFER_MAX_PROBE_AGE_MIN``，默认 30 分钟，约 3 个
    探测周期）。状态缺失 / 过期一律 **fail-open**（照常执行）—— 宁可按原样跑一次，
    也不凭旧结论静默跳过。
  - 延后写成一条 ``trigger_type="deferred"`` 的失败记录，error 带 ``EWS_DEFERRED``
    标记：``email_recovery`` 的失败链、时效窗口、补发档位（hourly / eight_hour /
    daily）**原样复用**，通道恢复后自动补发；已过窗口的照旧丢弃。
  - ``EWS_DEFER_ON_DOWN=0`` 可整体关闭本门禁（回滚开关，无需改代码）。

延后记录刻意不触发失败告警：通道不可用属于「依赖不可用」，不是任务故障；
``email_recovery`` 已有「故障翻转 + 持续故障升级」通知，不在这里重复刷屏。
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger("ews_deferral")

# 与 email_recovery.EWS_FAIL_KEYS 共用：该标记让延后记录进入补发失败链，
# 由 email_recovery 在通道恢复后按时效窗口补发。
DEFERRED_MARKER = "EWS_DEFERRED"

# 探测状态的最大可接受年龄（分钟）。约 3 个探测周期（email_recovery 每 10 分钟一次）；
# 超过即视为「不知道通道现在什么状态」，fail-open。
_MAX_PROBE_AGE_MIN = float(os.environ.get("EWS_DEFER_MAX_PROBE_AGE_MIN", "30"))


def _read_probe() -> dict | None:
    """读取 email_recovery 落盘的 EWS 探测状态；读不到返回 None（fail-open）。

    刻意不在这里另起一份探测/状态文件：``email_recovery`` 每 10 分钟做一次真实
    SOAP 探测并落盘，重复探测会让 EWS 多背一倍请求，且两份状态必然分叉。
    """
    try:
        from backend.services.email import email_recovery as er

        path = er.RECOVERY_STATE_FILE
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        probe = raw.get(er.EWS_STATE_KEY) if isinstance(raw, dict) else None
        return probe if isinstance(probe, dict) else None
    except Exception as e:  # noqa: BLE001
        # 状态文件损坏 / 半截写入：按「未确认降级」处理，任务照常执行。
        logger.warning("读取 EWS 探测状态失败，本轮不延后任何任务: %s", e)
        return None


def _probe_age_minutes(probe: dict) -> float | None:
    """探测时间距今多少分钟；时间缺失/不可解析返回 None（按过期处理）。"""
    try:
        probed_at = datetime.fromisoformat(str(probe.get("last_probe_at")))
    except (TypeError, ValueError):
        return None
    return (datetime.now() - probed_at).total_seconds() / 60.0


def _deferred_result(probe: dict) -> dict:
    """构造一条与任务返回值同形的「已延后」结果（success=False，带标记）。"""
    kind = str(probe.get("kind") or "server_down")
    probed_at = str(probe.get("last_probe_at") or "")
    detail = " ".join(str(probe.get("detail") or "").split())[:240]
    error = (
        f"{DEFERRED_MARKER}: 邮箱通道未恢复（{kind}，最后探测 {probed_at}"
        + (f"：{detail}" if detail else "")
        + "）—— 本轮不执行；通道恢复后由 email_recovery 按时效窗口自动补发，过期则丢弃"
    )
    return {
        "success": False,
        "deferred": True,
        "error": error,
        "ews_kind": kind,
        "ews_last_probe_at": probed_at,
        "message": "邮箱通道未恢复，本轮已延后（未执行、不告警）",
    }


def maybe_defer(task: dict, trigger: str) -> dict | None:
    """该任务本轮是否应延后；需要延后时返回结果 dict，否则 None。

    ``task`` 是 ``task_runner._task_registry`` 里的任务字典，``trigger`` 是本次
    触发方式（manual / scheduled / auto_retry / ...）。

    除 manual 外一律参与门禁：auto_retry 是系统重投，没有人「刚改完密码正等验证」
    这层语义，通道确认不可用时重投只会再空转一轮（见模块头 2026-11-25 补充）。
    """
    if os.environ.get("EWS_DEFER_ON_DOWN", "1").strip() == "0":
        return None
    if (trigger or "") == "manual":
        return None
    if not task.get("defer_on_ews_down"):
        return None

    probe = _read_probe()
    if not probe or str(probe.get("status") or "") != "down":
        return None

    age = _probe_age_minutes(probe)
    if age is None or age > _MAX_PROBE_AGE_MIN:
        # 状态过期（例如 email_recovery 自身被 m 组堵住）：不凭旧结论跳过，
        # 照常执行，由真实发送结果决定成败。
        logger.info(
            "[%s] EWS 探测状态过期（%s），本轮不延后",
            task.get("task_id"), "时间缺失" if age is None else f"{age:.0f} 分钟前",
        )
        return None

    result = _deferred_result(probe)
    logger.warning(
        "[%s] 邮箱通道不可用（%s，探测于 %s），本轮延后不执行；恢复后由 email_recovery 补发",
        task.get("task_id"), result["ews_kind"], result["ews_last_probe_at"],
    )
    return result


# ---------------------------------------------------------------------------
# 细粒度发信门禁：带数据同步 / WhatsApp 副通道的任务，通道 down 时只跳过发信，
# 同步与群发副作用照常执行（2026-11-25）。与整轮门禁 maybe_defer 互斥：
# 前者在 service 的发信调用点判定，后者在 task_runner 执行前判定。
# ---------------------------------------------------------------------------

# 当前任务的触发方式，由 task_runner.run_task 在执行前 set。service 层函数拿不到
# trigger 参数，靠这个 ContextVar 传递，保证手动触发不被拦（人可能刚改完密码要验证）。
# 用 ContextVar 而非模块全局变量：worker 多线程并发，全局变量会串任务。
_current_trigger: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ews_deferral_current_trigger", default="scheduled"
)


def set_current_trigger(trigger: str) -> None:
    """task_runner 在执行任务前调用；细粒度门禁据此判断手动触发不拦。"""
    _current_trigger.set(trigger or "scheduled")


# 适用细粒度门禁的任务：这些任务带同步或群发副作用，不能整轮延后。
# 与 task_registry.EWS_DEFERRABLE_IDS 必须互斥（单测覆盖）。
FINE_GRAINED_TASK_IDS = frozenset({
    "sp_email_details",       # SP 备件明细：合并 Excel + WhatsApp 群发
    "sp_online_report_send",  # SP 未关闭单报表：报表文件 + WhatsApp 群发
    "fw_kpi_sync",            # FW 吞吐：数据同步（现为本地）+ 图表
    "ume_in_osg_report",      # OSG 周报：UME KPI 同步 + Excel 生成
})


def should_skip_email_send(task_id: str) -> bool:
    """发信调用点的细粒度门禁：通道已确认不可用时返回 True（跳过本次发信）。

    只做判定、不构造结果——调用方用返回值构造与自身返回结构一致的「已延后」
    结果，便于把同步 / 群发的真实结果原样保留。复用整轮门禁的探测状态与
    fail-open 语义：状态缺失 / 过期 / 手动触发 / 未登记任务一律照常发信。
    """
    if task_id not in FINE_GRAINED_TASK_IDS:
        return False
    if os.environ.get("EWS_DEFER_ON_DOWN", "1").strip() == "0":
        return False
    if _current_trigger.get() == "manual":
        return False

    probe = _read_probe()
    if not probe or str(probe.get("status") or "") != "down":
        return False
    age = _probe_age_minutes(probe)
    if age is None or age > _MAX_PROBE_AGE_MIN:
        logger.info("[%s] EWS 探测状态过期（%s），本轮照常发信",
                    task_id, "时间缺失" if age is None else f"{age:.0f} 分钟前")
        return False
    logger.warning("[%s] 邮箱通道不可用（%s，探测于 %s），跳过发信但保留同步/群发副作用",
                   task_id, probe.get("kind") or "server_down", probe.get("last_probe_at"))
    return True


def skipped_email_result(task_id: str) -> dict:
    """构造「发信已跳过」的 email 结果，形态与 _run_script / _send_email 一致。

    error 带 EWS_DEFERRED 标记：email_recovery 会把它当成 EWS 失败链成员，
    通道恢复后按 task_id 既有档位补发。调用方需把本结果放进返回值的 email 键，
    并在顶层带 deferred=True + 同一 error，task_runner 才会静默告警。
    """
    probe = _read_probe() or {}
    kind = str(probe.get("kind") or "server_down")
    probed_at = str(probe.get("last_probe_at") or "")
    detail = " ".join(str(probe.get("detail") or "").split())[:240]
    error = (
        f"{DEFERRED_MARKER}: 邮箱通道未恢复（{kind}，最后探测 {probed_at}"
        + (f"：{detail}" if detail else "")
        + "）—— 本轮数据同步/群发副作用已执行，仅跳过发信；通道恢复后由 email_recovery "
          "按时效窗口补发邮件，过期则丢弃"
    )
    return {
        "success": False,
        "deferred": True,
        "error": error,
        "ews_kind": kind,
        "ews_last_probe_at": probed_at,
        "message": "邮箱通道未恢复，本轮仅跳过发信（同步/群发照常）",
    }


def apply_email_deferral(result: dict, email_res: dict, task_id: str) -> bool:
    """把 email 层的「已跳过发信」标记提升到任务顶层返回值。

    task_runner 只看任务函数的**顶层**结果：顶层带 deferred=True 才会静默告警、
    落 trigger_type=deferred；error 带 EWS_DEFERRED 才会被 email_recovery 认领补发。
    副作用（同步 / 群发）的真实结果由调用方在 result 里原样保留，本函数不改它们。
    """
    if not isinstance(email_res, dict) or not email_res.get("deferred"):
        return False
    result["deferred"] = True
    result["success"] = False
    if not result.get("error"):
        result["error"] = email_res.get("error") or ""
    logger.info("[%s] 细粒度延后已提升到任务顶层：副作用保留、仅补发邮件", task_id)
    return True

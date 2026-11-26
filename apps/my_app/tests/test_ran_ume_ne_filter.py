# -*- coding: utf-8 -*-
"""UME 服务端「按网元过滤」的回归测试。

## 背景（2026-11-25）

`POST /api/pm-analysis/v2/history` 默认回全网数据。要让它只回单个站点，必须
**同时**满足两个条件：

    "filterlayer": "me"
    "uilocations": [{"haslogicmoc": false, "id": "<网元 GUID>",
                     "moc": "ITBBU", "model": "ranme"}]

只写其中一个会被 UME **静默忽略**并照旧返回全网数据（HTTP 200，不报错）。2026-11-24
曾因此误判「UME 不支持按站点过滤」，实际是组合没凑对。

本测试锁死三件事，防止这类"看起来成功、其实没过滤"的回归：

1. `apply_ne_filter` 必须把 filterlayer 和 uilocations **一起**改；
2. 没有网元映射时必须保持全网形态（不能半改，否则既不过滤又丢字段）；
3. 一次查询整体挂到 `_build_payload` 上仍然是自洽的（防止上层忘了传 ne）。

运行：
    cd D:/02_olbr_portal/apps/my_app
    python -m pytest tests/test_ran_ume_ne_filter.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.ran.ran_ume_ne_filter import apply_ne_filter, resolve_ne  # noqa: E402

NE = {"ne_id": "2d7b38de-2236-4d6b-9a66-d90ae9a29530", "nbi_id": "5300",
      "name": "10th Street", "moc": "ITBBU"}


def test_apply_sets_both_fields():
    payload = {"filterlayer": "all", "uilocations": [], "gr": "900"}
    out = apply_ne_filter(payload, NE)

    assert out is payload, "应就地修改，避免调用方误用旧 dict"
    assert out["filterlayer"] == "me"
    assert out["uilocations"] == [{
        "haslogicmoc": False,
        "id": NE["ne_id"],
        "moc": "ITBBU",
        "model": "ranme",
    }]
    assert out["gr"] == "900", "不应触碰其它字段"


def test_apply_without_ne_keeps_global_form():
    payload = {"filterlayer": "all", "uilocations": []}
    out = apply_ne_filter(payload, None)

    assert out["filterlayer"] == "all"
    assert out["uilocations"] == []


def test_apply_uses_real_moc_from_record():
    """moc 必须取自网元表，不能硬编码 ITBBU（表里还有 SDR / MRNC / MEC）。"""
    out = apply_ne_filter({"filterlayer": "all", "uilocations": []},
                          {**NE, "moc": "SDR"})
    assert out["uilocations"][0]["moc"] == "SDR"


@pytest.mark.parametrize("tech", ["GSM", "2G", ""])
def test_resolve_ne_skips_unsupported_tech(tech):
    assert resolve_ne(tech, "6004", "Vai Town") is None


def test_resolve_ne_5g_requires_name():
    """5G 的 nbi_id 与 gnb_id 不同体系，只能按网元名匹配，没名字就不该猜。"""
    assert resolve_ne("5G", "6004", None) is None
    assert resolve_ne("5G", "6004", "   ") is None


def test_resolve_ne_skips_empty_site():
    assert resolve_ne("4G", "") is None
    assert resolve_ne("4G", "   ") is None


def test_build_payload_is_consistent():
    """整体链路：_build_payload 拿到 ne 后必须产出可用的过滤形态。"""
    from datetime import datetime, timedelta, timezone

    from backend.services.ran import ran_site_kpi_rt_service as svc

    end = datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)
    start = end - timedelta(days=1)

    global_p = svc._build_payload(start, end, "900", "4G")
    assert global_p["filterlayer"] == "all" and global_p["uilocations"] == []

    ne_p = svc._build_payload(start, end, "900", "4G", NE)
    assert ne_p["filterlayer"] == "me"
    assert ne_p["uilocations"] and ne_p["uilocations"][0]["id"] == NE["ne_id"]
    # 过滤只应改这两处，查询语义（时间窗/粒度/分组层）必须保持一致
    for key in ("gr", "grouplayer", "motid", "nfctid", "items", "timeinfo"):
        assert ne_p[key] == global_p[key], f"{key} 不应被过滤改变"


# ============================================================
# 4G 站名兜底（2026-11-25）：nbi_id 与 enodeb_id 不等时的站点
# ============================================================
NE_5245 = {"ne_id": "14ffe070-b497-4388-83f3-0d1a5b78616b", "nbi_id": "5245",
           "name": "16th Street", "moc": "ITBBU"}


class _Cur:
    def __init__(self, rows):
        self.description = [("ne_id",), ("nbi_id",), ("name",), ("moc",)]
        # 真实驱动返回元组行；测试里用 dict 写得更清楚，这里转成元组
        self._rows = [(r["ne_id"], r["nbi_id"], r["name"], r["moc"]) for r in rows]

    def fetchall(self):
        return self._rows


class _Conn:
    """假连接：按 SQL 里是否含 LOWER(name) 区分「按编号」与「按站名」两次查询。"""

    def __init__(self, by_nbi, by_name, sqls):
        self.by_nbi, self.by_name, self.sqls = by_nbi, by_name, sqls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.sqls.append(sql)
        return _Cur(self.by_name if "LOWER(name)" in sql else self.by_nbi)


def _patch_conn(monkeypatch, by_nbi, by_name):
    import backend.services.ran.ran_ume_ne_filter as mod

    sqls = []
    monkeypatch.setattr(mod, "get_raw_connection", lambda: _Conn(by_nbi, by_name, sqls))
    return sqls


def test_resolve_ne_4g_prefers_nbi_id(monkeypatch):
    sqls = _patch_conn(monkeypatch, [NE], [NE_5245])
    assert resolve_ne("4G", "5300", "10th Street") == NE
    assert not any("LOWER(name)" in s for s in sqls), "直接命中时不应再查站名"


def test_resolve_ne_4g_falls_back_to_unique_itbbu_name(monkeypatch):
    """16th Street：工参 4245、网元只有 nbi_id=5245 的 ITBBU → 必须按站名兜底。

    不回退也不行（功能可用但 8 天要 270s）；关键是兜底 SQL 必须限定 ITBBU，
    否则同名 SDR/MRNC 会把 4G 查询过滤到 2G/3G 网元上，拿回空数据。
    """
    sqls = _patch_conn(monkeypatch, [], [NE_5245])
    assert resolve_ne("4G", "4245", "16th Street") == NE_5245
    name_sql = next(s for s in sqls if "LOWER(name)" in s)
    assert "ITBBU" in name_sql, "站名兜底必须限定 moc=ITBBU"


def test_resolve_ne_4g_name_fallback_rejects_duplicates(monkeypatch):
    """同名 ITBBU 多条时不做猜测，回退全网（猜错比慢更糟）。"""
    other = {**NE_5245, "ne_id": "other-guid", "nbi_id": "5246"}
    _patch_conn(monkeypatch, [], [NE_5245, other])
    assert resolve_ne("4G", "4245", "16th Street") is None


def test_resolve_ne_4g_without_name_does_not_guess(monkeypatch):
    """只有编号、没有站名时不能兜底。"""
    _patch_conn(monkeypatch, [], [NE_5245])
    assert resolve_ne("4G", "4245", None) is None
    assert resolve_ne("4G", "4245", "  ") is None

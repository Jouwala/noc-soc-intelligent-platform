# -*- coding: utf-8 -*-
"""告警看板预计算快照的离线回归守卫（2026-11-24，接管 PR #221 时补）。

原 PR 的方案文档第 7 节写的是「需要带样例数据的测试库」的对比用例（现算 vs 快照、
pandas 下钻 vs 物化表下钻），这类用例在 CI 里跑不了。这里补的是**零数据依赖**的部分：

1. `snapshot_store` 的发布 / 读取 / 版本递增 / ETag / 反序列化缓存（用临时 SQLite）；
2. 路由读取层 `alarm_dashboard._serve` 的三种模式：off 原样返回、on 读快照（含 304）、
   shadow 现算并与快照比对；快照缺失或过期时回退现算、现算失败时继续给旧快照；
3. Worker 侧 `alarm_snapshot.safe()`：`ALARM_SNAPSHOT=off` 不执行，异常只记日志。

真正「快照内容与现算逐字相等」「明细行集合相同」的验收仍要在一份有样例数据的库上做，
见 docs/architecture/告警看板预计算改造方案.md 第 7 节。
"""
import json
import sqlite3

import pytest
from starlette.requests import Request

from backend.routers import alarm_dashboard as ad
from backend.services.alarm import alarm_snapshot
from backend.services.dashboard import snapshot_store


@pytest.fixture()
def tmp_store(tmp_path, monkeypatch):
    """把 snapshot_store 的连接换成临时 SQLite，别碰真实库。"""
    db = tmp_path / "snapshot.db"

    def _conn(*args, **kwargs):
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(snapshot_store, "get_raw_connection", _conn)
    monkeypatch.setattr(snapshot_store, "_ddl_done", False)
    snapshot_store._mem.clear()
    snapshot_store._last_check.clear()
    snapshot_store._parsed.clear()
    return db


def _req(if_none_match=None):
    headers = []
    if if_none_match:
        headers.append((b"if-none-match", if_none_match.encode()))
    return Request({"type": "http", "method": "GET", "path": "/api/alarm-dashboard/overview",
                    "headers": headers, "query_string": b""})


class FakeSnap:
    def __init__(self, body: str, version: int = 1, age: float = 1.0):
        self.body = body.encode("utf-8")
        self.version = version
        self.etag = f'"k-v{version}"'
        self._age = age

    def age_sec(self):
        return self._age


# ---------------------------------------------------------------------------
# snapshot_store
# ---------------------------------------------------------------------------
def test_publish_then_get_roundtrip(tmp_store):
    v1 = snapshot_store.publish("alarm:test", lambda: {"a": 1, "s": "中文"})
    assert v1 == 1
    snap = snapshot_store.get("alarm:test", force=True)
    assert snap is not None
    assert snap.version == 1
    assert json.loads(snap.body) == {"a": 1, "s": "中文"}   # ensure_ascii=False，中文不转义
    assert snap.etag == '"alarm:test-v1"'
    assert snap.age_sec() < 60


def test_republish_bumps_version_and_etag(tmp_store):
    snapshot_store.publish("alarm:test", lambda: {"a": 1})
    v2 = snapshot_store.publish("alarm:test", lambda: {"a": 2})
    assert v2 == 2
    snap = snapshot_store.get("alarm:test", force=True)
    assert snap.version == 2
    assert json.loads(snap.body) == {"a": 2}
    assert snap.etag == '"alarm:test-v2"'


def test_get_missing_key_returns_none(tmp_store):
    assert snapshot_store.get("alarm:missing", force=True) is None


def test_peek_payload_returns_parsed_object(tmp_store):
    snapshot_store.publish("alarm:peek", lambda: {"rows": [1, 2, 3]})
    assert snapshot_store.peek_payload("alarm:peek") == {"rows": [1, 2, 3]}
    assert snapshot_store.peek_payload("alarm:none") is None


def test_get_returns_memory_copy_when_read_fails(tmp_store, monkeypatch):
    snapshot_store.publish("alarm:test", lambda: {"a": 1})
    assert snapshot_store.get("alarm:test", force=True) is not None

    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(snapshot_store, "get_raw_connection", boom)
    snap = snapshot_store.get("alarm:test", force=True)   # 读库失败 → 继续给内存副本
    assert snap is not None and json.loads(snap.body) == {"a": 1}


# ---------------------------------------------------------------------------
# 路由读取层 _serve
# ---------------------------------------------------------------------------
@pytest.fixture()
def clean_fallback():
    ad._fallback.clear()
    yield
    ad._fallback.clear()


def test_serve_off_returns_live_value(monkeypatch):
    monkeypatch.setattr(ad, "_MODE", "off")
    assert ad._serve(_req(), "alarm:overview", 300, lambda: {"a": 1}) == {"a": 1}


def test_serve_shadow_returns_live_value_and_ignores_generated_at(monkeypatch):
    monkeypatch.setattr(ad, "_MODE", "shadow")
    monkeypatch.setattr(ad.snapshot_store, "peek_payload",
                        lambda key: {"a": 1, "generated_at": "snapshot-label"})
    live = ad._serve(_req(), "alarm:overview", 300,
                     lambda: {"a": 1, "generated_at": "live-label"})
    assert live == {"a": 1, "generated_at": "live-label"}


def test_serve_on_returns_snapshot_hit(monkeypatch):
    monkeypatch.setattr(ad, "_MODE", "on")
    monkeypatch.setattr(ad.snapshot_store, "get", lambda key: FakeSnap('{"a":1}'))
    res = ad._serve(_req(), "alarm:overview", 300, lambda: {"a": 2})
    assert res.status_code == 200
    assert res.body == b'{"a":1}'
    assert res.headers["etag"] == '"k-v1"'
    assert res.headers["x-snapshot"] == "hit"


def test_serve_on_returns_304_when_etag_matches(monkeypatch):
    monkeypatch.setattr(ad, "_MODE", "on")
    monkeypatch.setattr(ad.snapshot_store, "get", lambda key: FakeSnap('{"a":1}'))
    res = ad._serve(_req(if_none_match='"k-v1"'), "alarm:overview", 300, lambda: {"a": 2})
    assert res.status_code == 304
    assert res.body == b""


def test_serve_on_miss_falls_back_to_compute(monkeypatch, clean_fallback):
    monkeypatch.setattr(ad, "_MODE", "on")
    monkeypatch.setattr(ad.snapshot_store, "get", lambda key: None)
    res = ad._serve(_req(), "alarm:overview", 300, lambda: {"a": 2})
    assert res.status_code == 200
    assert json.loads(res.body) == {"a": 2}
    assert res.headers["x-snapshot"] == "miss"


def test_serve_on_expired_snapshot_uses_fallback(monkeypatch, clean_fallback):
    monkeypatch.setattr(ad, "_MODE", "on")
    monkeypatch.setattr(ad.snapshot_store, "get", lambda key: FakeSnap('{"old":1}', age=999))
    res = ad._serve(_req(), "alarm:overview", 300, lambda: {"new": 2})
    assert res.headers["x-snapshot"] == "stale"
    assert json.loads(res.body) == {"new": 2}


def test_serve_on_compute_failure_serves_old_snapshot(monkeypatch, clean_fallback):
    monkeypatch.setattr(ad, "_MODE", "on")
    monkeypatch.setattr(ad.snapshot_store, "get", lambda key: FakeSnap('{"old":1}', age=999))

    def boom():
        raise RuntimeError("compute failed")

    res = ad._serve(_req(), "alarm:overview", 300, boom)   # 页面不能白屏
    assert res.status_code == 200
    assert res.headers["x-snapshot"] == "stale-error"
    assert json.loads(res.body) == {"old": 1}


def test_serve_on_missing_snapshot_and_failing_compute_raises(monkeypatch, clean_fallback):
    monkeypatch.setattr(ad, "_MODE", "on")
    monkeypatch.setattr(ad.snapshot_store, "get", lambda key: None)

    def boom():
        raise RuntimeError("both gone")

    with pytest.raises(RuntimeError):
        ad._serve(_req(), "alarm:overview", 300, boom)


# ---------------------------------------------------------------------------
# Worker 侧安全包装
# ---------------------------------------------------------------------------
def test_safe_skips_when_off(monkeypatch):
    monkeypatch.setattr(alarm_snapshot, "DB_MODE", "pg")
    monkeypatch.setenv("ALARM_SNAPSHOT", "off")
    called = []
    alarm_snapshot.safe(lambda: called.append(1))
    assert called == []


def test_safe_skips_on_sqlite(monkeypatch):
    """SQLite 副本上不发布快照：复用的两条 SQL 是 PG 方言，跑了只会报语法错误。"""
    monkeypatch.setattr(alarm_snapshot, "DB_MODE", "sqlite")
    called = []
    alarm_snapshot.safe(lambda: called.append(1))
    assert called == []


def test_safe_swallows_errors(monkeypatch):
    """快照失败绝不能把采集任务标成失败。"""
    monkeypatch.setattr(alarm_snapshot, "DB_MODE", "pg")
    monkeypatch.setenv("ALARM_SNAPSHOT", "on")

    def boom():
        raise RuntimeError("publish failed")

    alarm_snapshot.safe(boom)   # 不应抛出


@pytest.mark.parametrize("header", ['W/"k-v1"', '"k-v0", "k-v1"', "*"])
def test_etag_matches_weak_and_multi_value(header):
    """nginx gzip 会把强 ETag 改成 W/"..."；多值与 * 也要认。"""
    assert ad._etag_matches(_req(if_none_match=header), '"k-v1"')


def test_etag_does_not_match_other_version():
    assert not ad._etag_matches(_req(if_none_match='"k-v0"'), '"k-v1"')


def test_env_int_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("ALARM_SNAPSHOT_RT_MAX_AGE", "5m")
    assert ad._env_int("ALARM_SNAPSHOT_RT_MAX_AGE", 300) == 300
    monkeypatch.setenv("ALARM_SNAPSHOT_RT_MAX_AGE", "120")
    assert ad._env_int("ALARM_SNAPSHOT_RT_MAX_AGE", 300) == 120


# ---------------------------------------------------------------------------
# 2026-11-24 第二轮审核补的回归项
# ---------------------------------------------------------------------------
import types  # noqa: E402


def _fake_ad(rows):
    return types.SimpleNamespace(
        _cache={},
        _NETWORK_KPI_XLSX="不存在的文件.xlsx",
        _network_availability=lambda: {"rows": rows, "threshold_pct": 95, "latest_month": None},
    )


def test_network_avail_empty_read_is_not_published(monkeypatch):
    """读数为空（xlsx 被占用等）时不能固化进快照、也不能推进 mtime，否则永不重试。"""
    published = []
    monkeypatch.setattr(alarm_snapshot, "_ad", lambda: _fake_ad([]))
    monkeypatch.setattr(alarm_snapshot, "_last_xlsx_mtime", None)
    monkeypatch.setattr(alarm_snapshot.snapshot_store, "publish",
                        lambda key, fn, **kw: published.append(key) or 1)
    monkeypatch.setattr(alarm_snapshot.snapshot_store, "peek_payload", lambda key: None)

    alarm_snapshot._refresh_network_avail_if_changed()

    assert published == [], "空读数被当成正常数据发布了"
    assert alarm_snapshot._last_xlsx_mtime is None, "空读数推进了 mtime，之后不会再重试"


def test_network_avail_success_is_published_and_advances_mtime(monkeypatch):
    published = []
    monkeypatch.setattr(alarm_snapshot, "_ad", lambda: _fake_ad([{"cell_group": "A"}]))
    monkeypatch.setattr(alarm_snapshot, "_last_xlsx_mtime", None)
    monkeypatch.setattr(alarm_snapshot.snapshot_store, "publish",
                        lambda key, fn, **kw: published.append(key) or 1)
    monkeypatch.setattr(alarm_snapshot.snapshot_store, "peek_payload", lambda key: None)

    alarm_snapshot._refresh_network_avail_if_changed()

    assert published == ["alarm:network_avail"]
    assert alarm_snapshot._last_xlsx_mtime == -1.0   # 文件不存在 → getmtime 失败取 -1


class _GateSnap:
    version = 1
    computed_at = "2026-11-24T00:00:00Z"
    etag = '"x"'

    def age_sec(self):
        return 1.0


def test_history_drilldown_gate_closes_when_rebuild_failed(monkeypatch):
    """重建失败会把门禁发布成 {"ok": false}，下钻必须退回 pandas、且**不能去连库**。

    （上一版这条测试是假阳性：它打桩了 peek_payload 但被测函数根本没调用，返回 None
    只是因为临时库里没有物化表 —— 2026-11-24 审核用真实表复现的。）
    """
    monkeypatch.setattr(ad.snapshot_store, "get", lambda key: _GateSnap())
    monkeypatch.setattr(ad.snapshot_store, "peek_payload",
                        lambda key: {"ok": False, "rows": 0})

    def _must_not_connect(*args, **kwargs):
        raise AssertionError("门禁未生效：ok=false 时不该去连库查物化表")

    monkeypatch.setattr(ad, "get_raw_connection", _must_not_connect)
    assert ad._history_drilldown_sql("all", 10) is None


def test_history_drilldown_gate_open_reaches_db(monkeypatch):
    """ok=true 时门禁放行、确实去查物化表（防止把门禁写成永远关闭）。"""
    opened = []

    class _Ctx:
        def __enter__(self):
            return object()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(ad.snapshot_store, "get", lambda key: _GateSnap())
    monkeypatch.setattr(ad.snapshot_store, "peek_payload",
                        lambda key: {"ok": True, "rows": 10})
    monkeypatch.setattr(ad, "get_raw_connection",
                        lambda *a, **k: opened.append(1) or _Ctx())
    monkeypatch.setattr(ad, "_table_exists", lambda conn, name: False)  # 表不存在 → None
    assert ad._history_drilldown_sql("all", 10) is None
    assert opened == [1], "ok=true 时没有去查物化表"


def test_alarm_overview_snapshot_has_same_keys_as_live(monkeypatch):
    """快照与现算必须同形：payload 里多出字段会让 shadow 永久 mismatch。"""
    class _Ctx:
        def __enter__(self):
            return object()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(ad, "_alarm_category_counts", lambda conn: {"ALL": 1})
    monkeypatch.setattr(ad, "_recovery_mttr", lambda conn: [])
    monkeypatch.setattr(ad, "_network_availability",
                        lambda: {"rows": [], "threshold_pct": 95, "latest_month": None})
    monkeypatch.setattr(ad, "_last_alarm_sync_iso", lambda: None)
    monkeypatch.setattr(ad, "get_raw_connection", lambda *a, **k: _Ctx())
    monkeypatch.setattr(alarm_snapshot, "get_raw_connection", lambda *a, **k: _Ctx())
    monkeypatch.setattr(alarm_snapshot.snapshot_store, "peek_payload", lambda key: None)

    published = {}

    def _publish(key, fn, **kwargs):
        if key == "alarm:alarm_overview":
            published["payload"] = fn()
        return 1

    monkeypatch.setattr(alarm_snapshot.snapshot_store, "publish", _publish)
    alarm_snapshot._publish_alarm_overview("2026-11-24 00:00 UTC", "2026-11-24T00:00:00Z")

    live = ad._compute_alarm_overview_payload()
    assert set(published["payload"]) == set(live), (
        f"快照与现算字段不一致: only_snapshot={set(published['payload']) - set(live)}, "
        f"only_live={set(live) - set(published['payload'])}")


def test_network_missing_file_warns_only_once(monkeypatch, tmp_path, caplog):
    """xlsx 不存在时只警告一次（开发副本/首次部署本来就没这个文件）。"""
    import logging

    monkeypatch.setattr(ad, "_net_missing_warned", False)
    monkeypatch.setattr(ad, "_NETWORK_KPI_XLSX", tmp_path / "absent.xlsx")

    with caplog.at_level(logging.DEBUG, logger=ad.__name__):
        ad._cache.pop("network_availability", None)
        ad._network_availability()
        ad._cache.pop("network_availability", None)
        ad._network_availability()

    levels = [r.levelname for r in caplog.records if "network KPI xlsx" in r.getMessage()]
    assert levels[:2] == ["WARNING", "DEBUG"], f"警告没有限频: {levels}"

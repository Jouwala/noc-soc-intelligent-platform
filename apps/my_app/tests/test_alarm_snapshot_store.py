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
    monkeypatch.setenv("ALARM_SNAPSHOT", "off")
    called = []
    alarm_snapshot.safe(lambda: called.append(1))
    assert called == []


def test_safe_swallows_errors(monkeypatch, caplog):
    """快照失败绝不能把采集任务标成失败。"""
    monkeypatch.setenv("ALARM_SNAPSHOT", "on")

    def boom():
        raise RuntimeError("publish failed")

    alarm_snapshot.safe(boom)   # 不应抛出

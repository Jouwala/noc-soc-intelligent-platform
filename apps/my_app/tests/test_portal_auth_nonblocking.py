# -*- coding: utf-8 -*-
"""鉴权热路径不碰数据库（2026-11-23 后端故障的回归守卫）。

故障机理：鉴权中间件是 async 的，却同步调用 current_user → read_session → _touch →
_db_upsert（每个会话每 60 秒一次）。数据库连不上时，这次写库要等满连接超时，期间
**事件循环被卡死**，全站请求排队直到 nginx 504。

本测试锁定：
1. 内存命中时 current_user_async / read_session 都只登记后台落盘，不在调用线程写库；
2. 内存未命中时的回查在线程池里执行，不在事件循环线程；
3. 后台写入器：失败的条目保留重试；同一 sid 只写最新状态（删除覆盖先前的 upsert）。
"""
import threading
import time

import pytest
from starlette.requests import Request

from backend.services.portal import portal_auth as pa
from backend.services.portal.session_writer import BackgroundWriter


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _req(sid: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/portal-qc/summary",
        "query_string": b"",
        "headers": [(b"cookie", f"{pa.SESSION_COOKIE}={sid}".encode())],
        "client": ("10.20.30.40", 52000),   # 非本机：走真实会话校验分支
    }
    return Request(scope)


@pytest.fixture
def mem_session(monkeypatch):
    monkeypatch.setattr(pa.auth_switch, "is_auth_bypass", lambda: False)
    upserts = []

    def slow_upsert(sid, payload):
        upserts.append(sid)
        time.sleep(3)          # 模拟数据库连不上时的连接超时
        return True

    writer = BackgroundWriter(slow_upsert, lambda sid: True)
    monkeypatch.setattr(writer, "_ensure_thread", lambda: None)  # 测试里不起后台线程
    monkeypatch.setattr(pa, "_writer", writer)
    monkeypatch.setattr(pa, "_warm_done", True)

    def _no_db_get(sid):
        raise AssertionError("内存命中时不应回查数据库")

    monkeypatch.setattr(pa, "_db_get", _no_db_get)
    sid = "test-" + str(time.time_ns())
    now = time.time()
    pa._SESSIONS[sid] = {
        "username": "alice", "role": "viewer", "exp": now + 3600, "created": now,
        "last_seen": now, "allowedGroups": None, "allowedCards": None, "persistent": False,
    }
    pa._last_persist[sid] = 0.0   # 已到落盘时间点：旧实现会在这里同步写库
    yield sid, upserts, writer
    pa._SESSIONS.pop(sid, None)
    pa._last_persist.pop(sid, None)


@pytest.mark.anyio
async def test_async_memory_hit_never_writes_db_inline(mem_session):
    sid, upserts, writer = mem_session
    t0 = time.monotonic()
    user = await pa.current_user_async(_req(sid))
    elapsed = time.monotonic() - t0
    assert user is not None and user["username"] == "alice"
    assert elapsed < 0.5, f"鉴权热路径耗时 {elapsed:.2f}s，疑似在请求路径同步写库"
    assert upserts == []                 # 没有在调用方写库
    assert writer.pending_count() == 1   # 只登记了后台落盘
    writer.flush()
    assert upserts == [sid]


def test_sync_read_session_memory_hit_never_writes_db_inline(mem_session):
    sid, upserts, writer = mem_session
    t0 = time.monotonic()
    assert pa.read_session(_req(sid))["username"] == "alice"
    assert time.monotonic() - t0 < 0.5
    assert upserts == []
    assert writer.pending_count() == 1


@pytest.mark.anyio
async def test_memory_miss_lookup_runs_in_threadpool(monkeypatch, mem_session):
    loop_thread = threading.get_ident()
    seen = {}

    def fake_db_get(sid):
        seen["thread"] = threading.get_ident()
        return None

    monkeypatch.setattr(pa, "_db_get", fake_db_get)
    assert await pa.current_user_async(_req("not-in-memory")) is None
    assert seen["thread"] != loop_thread


def test_writer_retries_failures_and_newer_state_wins():
    ok = [False]
    written = []

    def upsert(key, payload):
        written.append((key, payload["v"]))
        return ok[0]

    w = BackgroundWriter(upsert, lambda key: True)
    w._ensure_thread = lambda: None
    w.enqueue("a", {"v": 1})
    assert w.flush() == 1 and w.pending_count() == 1   # 失败的留在队列
    w.enqueue("a", {"v": 2})                           # 期间有更新：以新的为准
    ok[0] = True
    assert w.flush() == 0 and w.pending_count() == 0
    assert written[-1] == ("a", 2)


def test_writer_delete_supersedes_pending_upsert():
    upserts, deletes = [], []
    w = BackgroundWriter(lambda k, p: upserts.append(k) or True, lambda k: deletes.append(k) or True)
    w._ensure_thread = lambda: None
    w.enqueue("s1", {"v": 1})
    w.enqueue("s1", None)
    w.flush()
    assert upserts == [] and deletes == ["s1"]

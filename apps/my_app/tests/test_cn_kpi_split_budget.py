# -*- coding: utf-8 -*-
"""CS/PS CN KPI 分组看板「整页预算 + 单飞」的回归守卫（2026-11-22）。

背景：远端只读 API 与采集/同步任务共用一台机器，偶发排队会让单个组件查询
从 20ms 涨到 15s。原实现只有单条 60s 超时、没有整页预算，20 个组件 / 6 并发
最坏能把整页拖到 4 分钟，浏览器 120s 就 499（`?page=cs_cn_kpi` 实测）。

这里锁四件事：
1. 整页预算到达即返回，未完成组件带 `timeout=True`，不再干等；
2. 并发请求共享同一次取数（单飞），不会 N 倍打远端；
3. 残缺结果不写缓存，不覆盖上一份完整结果；
4. 全部组件失败时回退上一份完整结果并标记 `stale`。
"""
import threading
import time

import pytest

from backend.services.cn import cn_kpi_split_service as svc


def _widget(wid: str) -> dict:
    return {
        "id": wid, "title": wid, "chart": "line", "pivot": False, "pct": False,
        "unit": "", "table": f"t_{wid}", "fixed_range": False,
        "sql": "SELECT 1", "limit": 10, "missing_code": "",
    }


BOARD = {
    "title": "TEST BOARD",
    "sections": [
        {"key": "ems", "widgets": [_widget("a"), _widget("b")]},
        {"key": "ume", "widgets": [_widget("c"), _widget("d")]},
    ],
}
ALL_IDS = ["a", "b", "c", "d"]


def _ok(w: dict) -> dict:
    out = svc._empty_widget(w)
    out["rows"] = [[1]]
    out["columns"] = ["v"]
    return out


def _full_payload(rows_by_id=None) -> dict:
    rows_by_id = rows_by_id or {}
    results = {}
    for sec in BOARD["sections"]:
        for w in sec["widgets"]:
            out = _ok(w)
            out["rows"] = [[rows_by_id.get(w["id"], 1)]]
            results[w["id"]] = out
    return svc._assemble("cs", BOARD, "2026-01-01", "2026-01-31", results,
                         time.time(), 0)


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    monkeypatch.setattr(svc, "get_board", lambda _board: BOARD)
    svc._cache.clear()
    svc._key_locks.clear()
    yield
    svc._cache.clear()
    svc._key_locks.clear()


def test_budget_returns_partial_with_timeout(monkeypatch):
    """慢组件不能拖住整页：预算一到就返回，快组件照常出数。"""
    slow_started = threading.Event()
    slow_release = threading.Event()
    slow_finished = threading.Event()

    def fake_fetch(w, start, end, timeout=60.0):
        if w["id"] == "d":
            slow_started.set()
            # 慢组件一直堵到用例放行；10s 兜底防产品回归时把用例挂死
            slow_release.wait(timeout=10)
            slow_finished.set()
        return _ok(w)

    monkeypatch.setattr(svc, "_fetch_widget", fake_fetch)

    box = {}

    def run():
        box["data"] = svc._compute("cs", None, None, budget=0.2)

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=5)
    try:
        # 不变量用「完成标志」而非墙钟：整页必须在慢组件仍堵着时返回。
        # 旧写法 assert elapsed < 0.9（慢组件睡 1.5s）在负载高的机器上测到的是
        # 线程调度延迟——2026-11-24 冒烟机整套 83s（常态约 64s）时误报（#280），
        # 预算机制（wait 到截止即返回）本身没有问题。
        assert not worker.is_alive(), "整页被慢组件拖住：预算到后 5s 仍未返回"
        assert slow_started.is_set()
        assert not slow_finished.is_set(), "整页等慢组件跑完才返回"
        data = box["data"]
        widgets = {w["id"]: w for sec in data["sections"] for w in sec["widgets"]}
        assert widgets["a"]["rows"] == [[1]]
        assert widgets["d"]["timeout"] is True
        assert widgets["d"]["error"]
        assert data["meta"]["degraded"] == 1
        assert data["meta"]["failed"] == 1
    finally:
        slow_release.set()
        worker.join(timeout=5)


def test_concurrent_requests_share_one_build(monkeypatch):
    """4 个并发冷请求只触发一次取数（单飞）。"""
    calls = []
    calls_lock = threading.Lock()

    def fake_fetch(w, start, end, timeout=60.0):
        with calls_lock:
            calls.append(w["id"])
        time.sleep(0.2)
        return _ok(w)

    monkeypatch.setattr(svc, "_fetch_widget", fake_fetch)
    monkeypatch.setattr(svc, "BUILD_BUDGET", 5.0)

    barrier = threading.Barrier(4)
    errors = []

    def worker():
        try:
            barrier.wait()
            svc.get_overview("cs", "2026-01-01", "2026-01-31", force=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors
    assert sorted(calls) == sorted(ALL_IDS), f"应只取一轮，实际 {calls}"


def test_partial_result_not_cached(monkeypatch):
    """残缺页不写缓存，上一份完整结果保留。"""
    key = ("cs", "2026-01-01", "2026-01-31")
    good = _full_payload({"a": 42})
    old_ts = time.time() - svc.CACHE_TTL - 1
    svc._cache[key] = (old_ts, good)

    def fake_fetch(w, start, end, timeout=60.0):
        if w["id"] == "d":
            return svc._failed_widget(w, "boom")
        return _ok(w)

    monkeypatch.setattr(svc, "_fetch_widget", fake_fetch)
    data = svc.get_overview("cs", "2026-01-01", "2026-01-31")

    assert data["meta"]["degraded"] == 1
    assert svc._cache[key][0] == old_ts, "残缺结果覆盖了上一份完整结果"
    assert svc._cache[key][1]["sections"][0]["widgets"][0]["rows"] == [[42]]


def test_all_failed_falls_back_to_stale(monkeypatch):
    """全部组件失败：回退上一份完整结果，并标记 stale。"""
    key = ("cs", "2026-01-01", "2026-01-31")
    good = _full_payload({"a": 7})
    svc._cache[key] = (time.time() - svc.CACHE_TTL - 1, good)

    monkeypatch.setattr(
        svc, "_fetch_widget",
        lambda w, start, end, timeout=60.0: svc._failed_widget(w, "down"),
    )
    data = svc.get_overview("cs", "2026-01-01", "2026-01-31")

    assert data["meta"]["stale"] is True
    assert data["meta"]["cached"] is True
    widgets = {w["id"]: w for sec in data["sections"] for w in sec["widgets"]}
    assert widgets["a"]["rows"] == [[7]]


def test_fresh_cache_skips_fetch(monkeypatch):
    """TTL 内的缓存直接返回，不再打远端。"""
    key = ("cs", "2026-01-01", "2026-01-31")
    svc._cache[key] = (time.time(), _full_payload({"a": 99}))

    def boom(*_args, **_kwargs):
        raise AssertionError("命中缓存时不应取数")

    monkeypatch.setattr(svc, "_fetch_widget", boom)
    data = svc.get_overview("cs", "2026-01-01", "2026-01-31")

    assert data["meta"]["cached"] is True
    assert data["meta"]["stale"] is False
    widgets = {w["id"]: w for sec in data["sections"] for w in sec["widgets"]}
    assert widgets["a"]["rows"] == [[99]]

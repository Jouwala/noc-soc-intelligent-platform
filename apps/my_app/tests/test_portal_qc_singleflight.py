# -*- coding: utf-8 -*-
"""门户 QC 缓存「过期先返回旧值 + 单飞刷新」的回归守卫（2026-11-24）。

背景（nginx access.log 11-19 切生产 ~ 11-23 实测）：`/api/portal-qc/summary`
每天约 5,700 次，p50 只有 17ms（缓存生效），但 p95 1.3s、p99 7.4s，全天每小时
都有 >1s 的请求。慢的不是单次计算，而是 60s TTL 一过期，同时到达的请求**各自**
把 6 条全表 COUNT 重跑一遍 —— 旧实现的锁只包住「读缓存 / 写缓存」，计算在锁外，
等于没有互斥。

这里锁五件事：
1. 过期且有旧值 → **立即**返回旧值（`stale=True`），不等重算；
2. 重算期间到达的请求继续拿旧值，不触发第二次重算（单飞）；
3. 冷启动（没有旧值）仍同步算一次，不返回空壳；
4. `refresh=1` 同步重算，不受旧值影响；
5. 后台刷新失败只保留旧值，且 `refreshing` 必须复位 —— 否则缓存永久卡死。
"""
import threading
import time

from backend.routers import portal_qc


def _payload(tag: str) -> dict:
    return {"generated_at": tag, "metrics": {"alarm": {"value": 1, "sites": 1, "error": None}}}


def _new_cache() -> dict:
    return {"at": 0.0, "payload": None, "refreshing": False, "gen": 0}


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_cold_start_computes_synchronously():
    """没有旧值时必须同步算 —— QC 面板没有「全空值」的可用形态。"""
    cache = _new_cache()
    calls = []

    def collect():
        calls.append(1)
        return _payload("cold")

    res = portal_qc._serve_cached(cache, collect, refresh=False, label="test")
    assert res["generated_at"] == "cold"
    assert res["cached"] is False and res["stale"] is False
    assert len(calls) == 1
    assert cache["payload"]["generated_at"] == "cold"


def test_fresh_hit_never_computes():
    cache = _new_cache()
    cache["payload"] = _payload("hit")
    cache["at"] = time.time()
    calls = []

    res = portal_qc._serve_cached(
        cache, lambda: calls.append(1) or _payload("new"), refresh=False, label="test")
    assert res["generated_at"] == "hit"
    assert res["cached"] is True and res["stale"] is False
    assert calls == []


def test_expired_returns_old_value_immediately_and_refreshes_once():
    """核心用例：过期后第一个请求立刻拿到旧值，后台只算一次。"""
    cache = _new_cache()
    cache["payload"] = _payload("old")
    cache["at"] = time.time() - 3600

    started = threading.Event()
    release = threading.Event()
    calls = []

    def collect():
        calls.append(1)
        started.set()
        release.wait(5)
        return _payload("new")

    t0 = time.time()
    res = portal_qc._serve_cached(cache, collect, refresh=False, label="test")
    elapsed = time.time() - t0
    assert res["generated_at"] == "old"
    assert res["stale"] is True
    assert elapsed < 0.5, f"过期时没有立即返回旧值，等了 {elapsed:.2f}s"
    assert started.wait(2), "后台刷新没有启动"

    # 后台还在算：并发打 5 次，全部拿旧值，且不再触发新的重算
    for _ in range(5):
        r = portal_qc._serve_cached(cache, collect, refresh=False, label="test")
        assert r["generated_at"] == "old" and r["stale"] is True
    assert calls == [1], f"单飞失效，重算了 {len(calls)} 次"

    release.set()
    assert _wait_until(lambda: not cache["refreshing"]), "后台刷新没有收尾"
    assert cache["payload"]["generated_at"] == "new"
    # TTL 必须重新起算：紧接着的一次请求应命中新值（而不是又判过期）
    again = portal_qc._serve_cached(cache, collect, refresh=False, label="test")
    assert again["generated_at"] == "new" and again["stale"] is False
    assert calls == [1], "刷新完成后不应再触发重算"


def test_force_refresh_is_synchronous_even_with_old_value():
    cache = _new_cache()
    cache["payload"] = _payload("old")
    cache["at"] = time.time() - 3600

    res = portal_qc._serve_cached(
        cache, lambda: _payload("forced"), refresh=True, label="test")
    assert res["generated_at"] == "forced"
    assert res["cached"] is False and res["stale"] is False


def test_background_failure_keeps_old_value_and_resets_flag():
    cache = _new_cache()
    cache["payload"] = _payload("old")
    cache["at"] = time.time() - 3600

    def boom():
        raise RuntimeError("db down")

    res = portal_qc._serve_cached(cache, boom, refresh=False, label="test")
    assert res["generated_at"] == "old" and res["stale"] is True
    assert _wait_until(lambda: not cache["refreshing"]), "失败后 refreshing 没有复位"
    assert cache["payload"]["generated_at"] == "old", "失败不应把旧值清掉"


def test_sync_failure_resets_refreshing():
    """同步路径抛错也必须复位，否则缓存永久卡在「正在刷新」。"""
    cache = _new_cache()

    def boom():
        raise RuntimeError("db down")

    try:
        portal_qc._serve_cached(cache, boom, refresh=True, label="test")
    except RuntimeError:
        pass
    else:  # pragma: no cover - 失败路径没抛错说明实现被改了
        raise AssertionError("同步取数失败应当抛出")
    assert cache["refreshing"] is False


def test_summary_and_alarm_caches_are_independent():
    """两条路径必须各用各的缓存：告警 5 分钟一刷，不能顶掉全量那份。"""
    assert portal_qc._summary_cache is not portal_qc._alarm_cache
    stale_cache = _new_cache()
    stale_cache["payload"] = _payload("alarm")
    stale_cache["at"] = time.time()
    res = portal_qc._serve_cached(
        stale_cache, lambda: _payload("alarm-new"), refresh=False, label="alarm")
    assert res["generated_at"] == "alarm"
    assert portal_qc._summary_cache["payload"] is None


def test_background_result_not_written_over_newer_sync_refresh():
    """后台算完时若用户已点过刷新（gen 变了），旧结果不能覆盖新数据。"""
    cache = _new_cache()
    cache["payload"] = _payload("old")
    cache["at"] = time.time() - 3600

    release = threading.Event()

    def slow_collect():
        release.wait(5)
        return _payload("background")

    res = portal_qc._serve_cached(cache, slow_collect, refresh=False, label="test")
    assert res["stale"] is True

    # 模拟用户点「刷新」：同步重算并推进 gen
    portal_qc._serve_cached(cache, lambda: _payload("user-refresh"), refresh=True, label="test")
    assert cache["payload"]["generated_at"] == "user-refresh"

    release.set()
    assert _wait_until(lambda: not cache["refreshing"])
    assert cache["payload"]["generated_at"] == "user-refresh", "后台旧结果覆盖了更新的数据"

# -*- coding: utf-8 -*-
"""丢包看板总览预热的回归守卫（2026-11-24）。

背景（nginx access.log 11-19 切生产 ~ 11-23 实测）：`/api/packet-loss-dashboard/overview`
29 次里 28 次带 `site_type=NORMAL`，而旧 `warmup()` 只预热 `site_type=""`（ALL）——
预热的键谁也没命中，用户打开页面仍是 5.9~8.4 秒冷算（31% 的请求以 499 收场）。

这里锁四件事：
1. 预热档位 = 页面下拉里实际会出现的每个档位（含「未匹配」档），不是只有 ALL；
2. 单个档位预热失败不影响其余档位；
3. 预热间隔必须短于看板缓存的 600 秒，否则「刚过期」会撞上「还没预热」；
4. `warmup()` 被调两次只起一个后台线程。
"""
import threading
import time

import pytest

from backend.routers import packet_loss_dashboard as pl


def test_warmup_site_types_covers_every_dropdown_entry(monkeypatch):
    """下拉里有什么档位就预热什么档位（ALL + ERA/NORMAL + 未匹配）。"""
    monkeypatch.setattr(pl, "_load_summary_rows", lambda: [
        {"site_type": "ERA"}, {"site_type": "NORMAL"}, {"site_type": "(unmapped)"},
    ])
    monkeypatch.setattr(pl, "_site_type_index", lambda: {
        "code": {}, "name": {}, "total": {"ERA": 200, "NORMAL": 600},
    })
    assert pl._warmup_site_types() == ["", "ERA", "NORMAL", "(unmapped)"]


def test_warmup_site_types_falls_back_to_all_only(monkeypatch):
    """档位解析失败时不能抛错，退回「只热全部」——预热永远不能影响启动。"""
    def boom():
        raise RuntimeError("eflow_site 读不到")

    monkeypatch.setattr(pl, "_load_summary_rows", boom)
    assert pl._warmup_site_types() == [""]


def test_warm_once_warms_every_type(monkeypatch):
    seen = []
    monkeypatch.setattr(pl, "_warmup_site_types", lambda: ["", "NORMAL", "ERA"])
    monkeypatch.setattr(pl, "_compute_overview", lambda st: seen.append(st) or {})
    pl._warm_once()
    assert seen == ["", "NORMAL", "ERA"]


def test_warm_once_continues_after_one_type_fails(monkeypatch):
    seen = []

    def fake_compute(st):
        seen.append(st)
        if st == "ERA":
            raise RuntimeError("单档失败")
        return {}

    monkeypatch.setattr(pl, "_warmup_site_types", lambda: ["", "ERA", "NORMAL"])
    monkeypatch.setattr(pl, "_compute_overview", fake_compute)
    pl._warm_once()
    assert seen == ["", "ERA", "NORMAL"]


def test_refresh_interval_is_shorter_than_overview_ttl():
    """间隔必须短于 TTL，否则缓存到期与下一轮预热之间的窗口仍会漏给用户冷算。"""
    assert 0 < pl._WARMUP_REFRESH_SEC_DEFAULT < pl._OVERVIEW_TTL


def test_refresh_sec_env(monkeypatch):
    monkeypatch.delenv("PL_WARMUP_REFRESH_SEC", raising=False)
    assert pl._warmup_refresh_sec() == pl._WARMUP_REFRESH_SEC_DEFAULT
    monkeypatch.setenv("PL_WARMUP_REFRESH_SEC", "120")
    assert pl._warmup_refresh_sec() == 120
    monkeypatch.setenv("PL_WARMUP_REFRESH_SEC", "0")  # 0 = 关闭周期刷新
    assert pl._warmup_refresh_sec() == 0
    monkeypatch.setenv("PL_WARMUP_REFRESH_SEC", "-5")  # 负数同样视为关闭
    assert pl._warmup_refresh_sec() == 0
    monkeypatch.setenv("PL_WARMUP_REFRESH_SEC", "abc")
    assert pl._warmup_refresh_sec() == pl._WARMUP_REFRESH_SEC_DEFAULT


def test_refresh_sec_clamped_to_safe_range(monkeypatch):
    """越界值必须夹到安全区间：太小=后台连续扫表，太大=露出过期窗口/睡死线程。"""
    upper = pl._OVERVIEW_TTL - 30
    monkeypatch.setenv("PL_WARMUP_REFRESH_SEC", "5")
    assert pl._warmup_refresh_sec() == pl._WARMUP_REFRESH_SEC_MIN
    monkeypatch.setenv("PL_WARMUP_REFRESH_SEC", "100000")
    assert pl._warmup_refresh_sec() == upper
    assert pl._WARMUP_REFRESH_SEC_MIN < upper < pl._OVERVIEW_TTL


def test_warm_once_bypasses_cache_but_keeps_old_value_until_ready(monkeypatch):
    """预热必须绕开缓存（否则命中 600s TTL 等于空转），但**不能**先把缓存打空 ——
    重算那几秒里正好进来的用户会一起冷算（每 8 分钟一次惊群）。"""
    during = {}

    def slow_compute(st):
        during["payload"] = pl._cache["overview:NORMAL"][1]
        return {"fresh": True}

    monkeypatch.setattr(pl, "_warmup_site_types", lambda: ["NORMAL"])
    monkeypatch.setattr(pl, "_compute_overview", slow_compute)
    pl._cache["overview:NORMAL"] = (time.time(), {"old": True})

    pl._warm_once()

    assert during["payload"] == {"old": True}, "预热期间缓存被清空了"
    assert pl._cache["overview:NORMAL"][1] == {"fresh": True}


def test_warm_once_actually_recomputes_and_refreshes_cache(monkeypatch, tmp_path):
    """用真实 pl_overview（数据源打桩）验证：预热后缓存时间戳被推进。"""
    monkeypatch.setattr(pl, "PL_DB_PATH", tmp_path / "absent.sqlite3")  # 跳过读数扫描
    monkeypatch.setattr(pl, "_load_summary_rows", lambda: [])
    monkeypatch.setattr(pl, "_load_meta", lambda: {})
    monkeypatch.setattr(pl, "_site_type_index", lambda: {"code": {}, "name": {}, "total": {}})
    monkeypatch.setattr(pl, "_warmup_site_types", lambda: ["NORMAL"])
    pl._cache.clear()

    pl.pl_overview(site_type="NORMAL")          # 第一次现算并落缓存
    key = "overview:NORMAL"
    assert key in pl._cache
    old_ts = time.time() - 100
    pl._cache[key] = (old_ts, pl._cache[key][1])   # 模拟「100 秒前的缓存」

    pl._warm_once()
    assert pl._cache[key][0] > old_ts, "预热没有真正重算，缓存时间戳没推进"


def test_warmup_starts_single_thread(monkeypatch):
    monkeypatch.setattr(pl, "_warmup_started", False)
    monkeypatch.setattr(pl, "_warmup_refresh_sec", lambda: 999999)  # 线程起完就长睡
    monkeypatch.setattr(pl, "_warm_once", lambda: None)
    pl.warmup()
    pl.warmup()
    alive = [t for t in threading.enumerate() if t.name == "pl-warmup"]
    assert len(alive) == 1, f"warmup 重复调用起了多个线程: {alive}"


@pytest.mark.parametrize("env_value", ["0", "600"])
def test_warmup_thread_warms_once_before_sleep(monkeypatch, env_value):
    """无论是否开周期刷新，启动后都要先热一轮 —— 这是修「第一次打开就冷算」的那一步。"""
    calls = []
    monkeypatch.setattr(pl, "_warmup_started", False)
    monkeypatch.setattr(pl, "_warm_once", lambda: calls.append(1))
    monkeypatch.setenv("PL_WARMUP_REFRESH_SEC", env_value)
    pl.warmup()
    time.sleep(0.05)
    assert len(calls) == 1


def test_concurrent_warmup_starts_exactly_one_thread(monkeypatch):
    """8 个线程同时调 warmup() 只能起 1 个预热线程（占位必须先于起线程）。"""
    monkeypatch.setattr(pl, "_warmup_started", False)
    monkeypatch.setattr(pl, "_warm_once", lambda: None)
    monkeypatch.setattr(pl, "_warmup_refresh_sec", lambda: 999999)
    barrier = threading.Barrier(8)

    def call():
        barrier.wait(5)
        pl.warmup()

    before = {t for t in threading.enumerate() if t.name == "pl-warmup"}
    callers = [threading.Thread(target=call, name=f"warm-caller-{i}") for i in range(8)]
    for t in callers:
        t.start()
    for t in callers:
        t.join(5)

    started = {t for t in threading.enumerate() if t.name == "pl-warmup"} - before
    assert len(started) == 1, f"并发调 warmup 新起了 {len(started)} 个线程: {started}"


def test_warmup_retries_after_thread_start_failure(monkeypatch):
    """线程起不来时占位要还回去，下次调用还能再试（并发占位不能变成永久锁死）。"""
    monkeypatch.setattr(pl, "_warmup_started", False)
    monkeypatch.setattr(pl, "_warm_once", lambda: None)

    class _BoomThread:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(pl, "threading",
                        __import__("types").SimpleNamespace(Thread=_BoomThread, Lock=threading.Lock))
    pl.warmup()
    assert pl._warmup_started is False, "线程失败后占位没有复位，后续永远不再预热"

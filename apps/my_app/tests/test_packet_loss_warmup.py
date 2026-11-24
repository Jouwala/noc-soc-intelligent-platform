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
    monkeypatch.setattr(pl, "pl_overview", lambda site_type="": seen.append(site_type) or {})
    pl._warm_once()
    assert seen == ["", "NORMAL", "ERA"]


def test_warm_once_continues_after_one_type_fails(monkeypatch):
    seen = []

    def fake_overview(site_type=""):
        seen.append(site_type)
        if site_type == "ERA":
            raise RuntimeError("单档失败")
        return {}

    monkeypatch.setattr(pl, "_warmup_site_types", lambda: ["", "ERA", "NORMAL"])
    monkeypatch.setattr(pl, "pl_overview", fake_overview)
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
    monkeypatch.setenv("PL_WARMUP_REFRESH_SEC", "abc")
    assert pl._warmup_refresh_sec() == pl._WARMUP_REFRESH_SEC_DEFAULT


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

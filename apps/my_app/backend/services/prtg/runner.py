# -*- coding: utf-8 -*-
"""PRTG 采集常驻循环与启动入口（Issue #227）。

与 collector.py 分开是为了让「业务编排」和「进程生命周期」各自独立：
  - collector.py 只负责一轮一轮干什么（poll/catalog/backfill/aggregate/prune），可单测；
  - 本模块负责什么时候干、失败了怎么记账、线程怎么起停。

生产由 main.py 的 lifespan 调 start_collector_thread()；默认关闭
（``PRTG_COLLECTOR_ENABLED=1`` 才启动），避免 CI / 冒烟 / 测试环境去连 PRTG 内网。
"""
import logging
import threading
import time

from backend.services.prtg import store
from backend.services.prtg.collector import (
    AGGREGATE_INTERVAL_SECONDS,
    CATALOG_INTERVAL_SECONDS,
    DETAIL_INTERVAL_SECONDS,
    LOOP_TICK_SECONDS,
    PRUNE_INTERVAL_SECONDS,
    PrtgCollector,
)
from backend.services.prtg.settings import collector_enabled, load_settings

logger = logging.getLogger(__name__)


def run_forever(collector: PrtgCollector, stop_event: threading.Event | None = None) -> None:
    """常驻循环：每 5 秒看一眼该做什么，单轮异常不退出（下轮继续）。"""
    stop = stop_event or threading.Event()
    tick = time.monotonic()
    next_live = tick
    next_catalog = tick
    next_detail = tick + DETAIL_INTERVAL_SECONDS
    next_aggregate = tick + AGGREGATE_INTERVAL_SECONDS
    next_prune = tick + 30
    logger.info("PRTG 采集循环启动：每 %s 秒轮询", collector.settings.poll_seconds)
    while not stop.is_set():
        tick = time.monotonic()
        try:
            if tick >= next_live:
                _safe(collector, "live", collector.poll_once)
                next_live = tick + collector.settings.poll_seconds
            if tick >= next_catalog:
                _safe(collector, "catalog", collector.sync_catalog)
                # 目录同步完立即补一次详情（Uptime/Last Down），不必等 5 分钟
                next_detail = tick
                next_catalog = tick + CATALOG_INTERVAL_SECONDS
            if tick >= next_detail:
                _safe(collector, "detail", collector.refresh_details)
                next_detail = tick + DETAIL_INTERVAL_SECONDS
            if collector.token_bucket.available() >= 1:
                _safe(collector, "backfill", collector.backfill_step)
            if tick >= next_aggregate:
                _safe(collector, "aggregate", collector.aggregate)
                next_aggregate = tick + AGGREGATE_INTERVAL_SECONDS
            if tick >= next_prune:
                _safe(collector, "prune", collector.prune)
                next_prune = tick + PRUNE_INTERVAL_SECONDS
        except Exception:  # noqa: BLE001 —— 循环不能因单轮异常退出
            logger.exception("PRTG 采集循环异常，5 秒后继续")
        stop.wait(LOOP_TICK_SECONDS)


def _safe(collector: PrtgCollector, state_key: str, func) -> None:
    try:
        result = func()
        if result is not None:
            logger.debug("PRTG %s 完成：%s", state_key, result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("PRTG %s 失败：%s: %s", state_key, type(exc).__name__, exc)
        try:
            store.set_state(
                state_key,
                status="error",
                last_run_at=collector.now(),
                error=f"{type(exc).__name__}: {exc}",
            )
        except Exception:  # noqa: BLE001 —— 连状态都写不进去时只留日志
            logger.exception("PRTG %s 状态写入失败", state_key)


def start_collector_thread(stop_event: threading.Event | None = None) -> threading.Thread | None:
    """生产入口：按环境开关启动 daemon 线程；未启用或配置不全时返回 None 并记日志。"""
    if not collector_enabled():
        logger.info("PRTG 采集未启用（PRTG_COLLECTOR_ENABLED 未设为 1），跳过常驻线程")
        return None
    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001
        logger.warning("PRTG 采集配置不可用，未启动：%s: %s", type(exc).__name__, exc)
        return None
    collector = PrtgCollector(settings)
    thread = threading.Thread(
        target=run_forever, args=(collector, stop_event), name="prtg-collector", daemon=True
    )
    thread.start()
    return thread

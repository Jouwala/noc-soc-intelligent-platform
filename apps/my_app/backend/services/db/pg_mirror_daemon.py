# -*- coding: utf-8 -*-
"""本机 PG 镜像的后台守护线程（从 `pg_mirror.py` 拆出，控制单文件体积）。

为什么不做成 `TASK_SPECS` 任务：镜像要每 5 分钟跑，且不能被 worker 分组里的
长任务（`pg_to_sqlite_sync` 9~20 分钟、`eflow_sync` 数分钟）堵住；单独为它
开一个 worker 组要同步改五处（watchdog / start_hidden.ps1 / restart_workers.py /
start.bat / 前端帮助），代价与收益不成比例。看板镜像已有 daemon 先例
（`native_board_mirror_sync.start_mirror_daemon`）。
"""
from __future__ import annotations

import logging
import os
import threading
import time

from backend.services.db.pg_mirror import run_mirror

logger = logging.getLogger("pg_mirror")


# ----------------------------------------------------------------------
# 后台常驻守护（与 native_board_mirror_sync.start_mirror_daemon 同款）
# ----------------------------------------------------------------------
# 为什么不登记成 TASK_SPECS 任务：镜像要每 5 分钟跑、且不能被 worker 分组的
# 长任务（pg_to_sqlite_sync 9~20 分钟、eflow_sync 数分钟）堵住；单独为它开一个
# worker 组要同步改五处（watchdog / start_hidden.ps1 / restart_workers.py /
# start.bat / 前端帮助），代价与收益不成比例。看板镜像已有 daemon 先例。
_daemon_lock = threading.Lock()
_daemon_started = False


def start_mirror_daemon(initial_delay: float = 30.0,
                        interval: float = 300.0) -> bool:
    """启动后台镜像线程；返回是否真的启动了（重复调用 / 隔离副本 / 关闭时 False）。"""
    global _daemon_started
    if os.environ.get("PG_MIRROR_DAEMON", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    if os.environ.get("PG_MIRROR_ENABLED", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    try:
        from backend.database import DB_MODE

        if DB_MODE != "pg":
            return False
    except Exception:  # noqa: BLE001 — 数据层不可用时不启动
        return False
    try:
        from backend.services.env_role import is_isolated_copy

        if is_isolated_copy():
            logger.info("[PG-MIRROR] 隔离副本不启动镜像守护（避免打生产远端）")
            return False
    except Exception:  # noqa: BLE001
        pass

    with _daemon_lock:
        if _daemon_started:
            return False
        _daemon_started = True

    def _loop() -> None:
        time.sleep(max(0.0, initial_delay))
        while True:
            t0 = time.time()
            try:
                out = run_mirror()
                if out.get("failures"):
                    logger.warning("[PG-MIRROR] %d 张表失败：%s",
                                   len(out["failures"]), out["failures"][:3])
                else:
                    logger.info("[PG-MIRROR] 完成：%d 表 / %d 行 / %.1fs",
                                out.get("tables", 0), out.get("rows", 0),
                                time.time() - t0)
            except Exception as e:  # noqa: BLE001 — 守护线程绝不退出
                logger.error("[PG-MIRROR] 本轮异常（下一轮重试）：%s", e)
            time.sleep(max(30.0, interval))

    threading.Thread(target=_loop, name="pg-mirror", daemon=True).start()
    logger.info("[PG-MIRROR] 后台镜像已启动：每 %.0fs 一轮", interval)
    return True

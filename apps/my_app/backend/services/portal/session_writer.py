# -*- coding: utf-8 -*-
"""会话后台落盘线程（2026-11-23 从 portal_auth 的请求路径里拆出）。

为什么需要：portal_auth 的会话落盘此前在**请求线程里同步写库**，鉴权中间件又是
async 的 —— 数据库连不上时事件循环被卡住整整一个连接超时，全站请求排队
（2026-11-23 09:39–11:08，详见 docs/incidents/2026-11-23-backend-outage.md）。

本模块只负责"把要写的东西攒起来、由一个后台线程写掉、失败了过会儿再写"：

- `enqueue(key, payload)`：登记一次写入；payload=None 表示删除。同一 key 只保留
  **最新**状态（后来的 upsert/delete 覆盖先前未写的），所以积压量 ≤ 会话数。
- 后台线程被唤醒或每 `periodic_seconds` 秒醒一次：先写积压，再跑周期任务（清理过期行）。
- 写失败的条目放回队列（不覆盖更新的状态），退避 `retry_seconds` 后重试。

写入函数由调用方注入（upsert_fn / delete_fn 返回 bool 表示是否成功），本模块不认识
数据库，便于单测。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class BackgroundWriter:
    def __init__(
        self,
        upsert_fn: Callable[[str, dict], bool],
        delete_fn: Callable[[str], bool],
        periodic_fn: Optional[Callable[[float], Any]] = None,
        periodic_seconds: float = 300.0,
        retry_seconds: float = 15.0,
        name: str = "portal-session-writer",
    ):
        self._upsert = upsert_fn
        self._delete = delete_fn
        self._periodic = periodic_fn
        self.periodic_seconds = periodic_seconds
        self.retry_seconds = retry_seconds
        self._name = name
        self._pending: dict[str, Optional[dict]] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_periodic = 0.0

    # ---- 生产者（请求路径，只碰内存） ----
    def enqueue(self, key: str, payload: Optional[dict]) -> None:
        with self._lock:
            self._pending[key] = dict(payload) if payload is not None else None
            self._ensure_thread()
        self._wake.set()

    def wake(self) -> None:
        with self._lock:
            self._ensure_thread()
        self._wake.set()

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    # ---- 消费者 ----
    def flush(self) -> int:
        """同步写完当前积压；返回失败条数。测试与关停时可直接调用。"""
        with self._lock:
            batch = dict(self._pending)
            self._pending.clear()
        failed: dict[str, Optional[dict]] = {}
        for key, payload in batch.items():
            try:
                ok = self._delete(key) if payload is None else self._upsert(key, payload)
            except Exception:  # noqa: BLE001  写入函数本应自己吞异常，这里兜底
                logger.warning("[%s] 写入异常 key=%s", self._name, key[:8], exc_info=True)
                ok = False
            if not ok:
                failed[key] = payload
        if failed:
            with self._lock:
                for key, payload in failed.items():
                    self._pending.setdefault(key, payload)  # 期间有更新的状态则以新的为准
        return len(failed)

    def run_periodic(self, now: Optional[float] = None) -> None:
        if self._periodic is None:
            return
        now = time.time() if now is None else now
        if now - self._last_periodic < self.periodic_seconds:
            return
        self._last_periodic = now
        try:
            self._periodic(now)
        except Exception:  # noqa: BLE001
            logger.warning("[%s] 周期任务异常", self._name, exc_info=True)

    def _ensure_thread(self) -> None:  # 须持有 self._lock
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while True:
            self._wake.wait(timeout=self.periodic_seconds)
            self._wake.clear()
            failed = 0
            try:
                failed = self.flush()
                self.run_periodic()
            except Exception:  # noqa: BLE001  后台线程绝不能退出
                logger.warning("[%s] 后台落盘异常（稍后重试）", self._name, exc_info=True)
                failed = 1
            if failed:
                # 数据库不可用：退避后重试。配合 db_guard 熔断，每次尝试都是立即失败，不会卡线程。
                time.sleep(self.retry_seconds)
                self._wake.set()

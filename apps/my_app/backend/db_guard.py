# -*- coding: utf-8 -*-
"""PostgreSQL 连接熔断（2026-11-23 故障后新增）。

背景（详见 docs/incidents/2026-11-23-backend-outage.md）
---------------------------------------------------------
2026-11-23 09:32–11:08，后端连 192.168.100.20:5432 反复 connect timeout。
此前每次 `get_raw_connection()` 都会**新开一条 TCP 连接**并等满 `connect_timeout`
（调用方几乎都传 timeout=30，那是 sqlite 的 busy 等待语义，被原样当成了 PG 的
connect_timeout）。数据库不可达时：

* 每个查库请求占住一个线程 30 秒，线程池被占满，连不查库的请求也排队；
* 鉴权中间件（async）里同步写会话表，事件循环被直接卡住 30 秒（见 portal_auth）；
* nginx 等满 proxy_read_timeout 300s 才回 504，用户看到的是"全站打不开"。

本模块提供两样东西：

1. **短连接超时** `PG_CONNECT_TIMEOUT`（默认 5 秒）。本机 PG 正常建连 <50ms，
   5 秒足够区分"慢"和"连不上"。调用方传入的 timeout 只会被**收紧**，不会放宽。
2. **熔断器**：连续 `PG_BREAKER_THRESHOLD`（默认 3）次连接级失败后进入 open 状态，
   `PG_BREAKER_OPEN_SECONDS`（默认 10 秒）内所有建连请求**立刻**抛
   `DatabaseUnavailable`，不再等超时；到期后放行**一个**探测请求（half-open），
   成功则恢复，失败则再 open 一个周期。

只统计"连接级"失败（连不上、超时、连接被断开）。SQL 语法错、约束冲突、
表不存在等业务错误与数据库可达性无关，不计入，也不触发熔断。

`DatabaseUnavailable` 是 `psycopg2.OperationalError` 的子类（psycopg2 可用时），
所以既有 `except psycopg2.OperationalError` / `except Exception` 的调用点行为不变；
main.py 的异常处理器把它统一转成 HTTP 503 + Retry-After。

环境变量
--------
- PG_CONNECT_TIMEOUT       建连超时秒，默认 5（下限 1）
- PG_BREAKER_THRESHOLD     连续失败几次后熔断，默认 3；设 0 关闭熔断（只保留短超时）
- PG_BREAKER_OPEN_SECONDS  熔断持续秒，默认 10
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


PG_CONNECT_TIMEOUT = _env_int("PG_CONNECT_TIMEOUT", 5, 1)
PG_BREAKER_THRESHOLD = _env_int("PG_BREAKER_THRESHOLD", 3, 0)
PG_BREAKER_OPEN_SECONDS = _env_int("PG_BREAKER_OPEN_SECONDS", 10, 1)

# libpq TCP keepalive：连接建好后对端失联时，几十秒内能发现，而不是挂到 OS 默认的两小时。
PG_KEEPALIVE_PARAMS = {
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}

try:  # sqlite 模式下不要求安装 psycopg2
    import psycopg2 as _psycopg2  # type: ignore

    _BaseUnavailable: Any = _psycopg2.OperationalError
except Exception:  # noqa: BLE001
    _psycopg2 = None
    _BaseUnavailable = RuntimeError


class DatabaseUnavailable(_BaseUnavailable):  # type: ignore[misc, valid-type]
    """数据库当前不可达（熔断中或建连失败）。调用方应快速失败，不要重试等待。"""

    def __init__(self, message: str, retry_after: int = PG_BREAKER_OPEN_SECONDS):
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))


# 这些片段出现在 libpq 的建连/断连报错里；只用于没有 SQLSTATE 的 OperationalError。
_CONN_ERROR_MARKERS = (
    "timeout expired",
    "could not connect",
    "connection refused",
    "actively refused",
    "could not translate host name",
    "server closed the connection unexpectedly",
    "connection already closed",
    "terminating connection",
    "no route to host",
    "network is unreachable",
    "connection timed out",
    "the database system is starting up",
    "the database system is shutting down",
    "sorry, too many clients already",
    "remaining connection slots are reserved",
)


def is_connection_error(exc: BaseException) -> bool:
    """判断异常是否属于"数据库连不上 / 连接断了"（而不是 SQL 本身的错误）。"""
    if isinstance(exc, DatabaseUnavailable):
        return True
    # SQLAlchemy 会把 DBAPI 异常包一层，原始异常在 .orig
    orig = getattr(exc, "orig", None)
    if orig is not None and orig is not exc:
        return is_connection_error(orig)
    if _psycopg2 is not None:
        if isinstance(exc, _psycopg2.InterfaceError):
            return True  # 连接已关闭后再使用
        if not isinstance(exc, _psycopg2.OperationalError):
            return False
        pgcode = getattr(exc, "pgcode", None) or ""
        # 08xxx = connection exception；57P01-57P03 = 管理员关库/崩溃恢复/不能连接
        if pgcode.startswith("08") or pgcode in ("57P01", "57P02", "57P03", "53300"):
            return True
        if pgcode:
            return False  # 有 SQLSTATE 的其他 OperationalError（如 57014 查询被取消）不算
    msg = str(exc).lower()
    return any(m in msg for m in _CONN_ERROR_MARKERS)


class _Breaker:
    """线程安全的三态熔断器：closed → open → half-open → closed/open。"""

    def __init__(self, threshold: int, open_seconds: int, clock=time.monotonic):
        self.threshold = threshold
        self.open_seconds = open_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: Optional[float] = None
        self._probe_in_flight = False
        self._last_error = ""
        self._last_error_at: Optional[float] = None  # wall clock，仅用于展示
        self._last_ok_at: Optional[float] = None
        self._rejected = 0

    # ---- 状态 ----
    def _state_locked(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at < self.open_seconds:
            return "open"
        return "half_open"

    @property
    def state(self) -> str:
        with self._lock:
            return self._state_locked()

    # ---- 建连前调用 ----
    def before_connect(self) -> None:
        """熔断中直接抛 DatabaseUnavailable；half-open 时只放行一个探测请求。"""
        if self.threshold <= 0:
            return
        with self._lock:
            st = self._state_locked()
            if st == "closed":
                return
            if st == "half_open" and not self._probe_in_flight:
                self._probe_in_flight = True
                return
            self._rejected += 1
            remaining = self.open_seconds
            if self._opened_at is not None:
                remaining = max(1, int(self.open_seconds - (self._clock() - self._opened_at)) + 1)
            raise DatabaseUnavailable(
                f"PostgreSQL 暂不可用（熔断中，{remaining}s 后重试）：{self._last_error}",
                retry_after=remaining,
            )

    def record_success(self) -> None:
        with self._lock:
            was_open = self._opened_at is not None
            self._failures = 0
            self._opened_at = None
            self._probe_in_flight = False
            self._last_ok_at = time.time()
        if was_open:
            logger.warning("[db_guard] PostgreSQL 已恢复，熔断关闭")

    def record_failure(self, exc: BaseException) -> None:
        if self.threshold <= 0:
            return
        opened = False
        with self._lock:
            self._failures += 1
            self._last_error = f"{type(exc).__name__}: {str(exc).strip()[:200]}"
            self._last_error_at = time.time()
            was_probe = self._probe_in_flight
            self._probe_in_flight = False
            if was_probe or self._failures >= self.threshold:
                opened = self._opened_at is None or was_probe
                self._opened_at = self._clock()
            failures = self._failures
        if opened:
            logger.error(
                "[db_guard] PostgreSQL 连续 %d 次连接失败，熔断 %ds：%s",
                failures, self.open_seconds, self._last_error,
            )

    def release_probe(self) -> None:
        """探测请求因非连接原因提前结束（如被调用方取消）时归还探测名额。"""
        with self._lock:
            self._probe_in_flight = False

    def snapshot(self) -> dict:
        """给 /api/health 用的只读快照（不触发任何数据库访问）。"""
        with self._lock:
            return {
                "state": self._state_locked(),
                "consecutive_failures": self._failures,
                "threshold": self.threshold,
                "open_seconds": self.open_seconds,
                "connect_timeout": PG_CONNECT_TIMEOUT,
                "rejected_total": self._rejected,
                "last_error": self._last_error or None,
                "last_error_at": self._last_error_at,
                "last_ok_at": self._last_ok_at,
            }

    def reset(self) -> None:  # 测试用
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probe_in_flight = False
            self._last_error = ""
            self._rejected = 0


breaker = _Breaker(PG_BREAKER_THRESHOLD, PG_BREAKER_OPEN_SECONDS)


def effective_connect_timeout(requested: Optional[float]) -> int:
    """调用方传入的 timeout 只收紧不放宽（历史调用方普遍传 30，是 sqlite 语义）。"""
    try:
        req = int(requested) if requested else PG_CONNECT_TIMEOUT
    except (TypeError, ValueError):
        req = PG_CONNECT_TIMEOUT
    return max(1, min(req, PG_CONNECT_TIMEOUT))


def guarded_connect(connect_fn, *args, **kwargs):
    """在熔断保护下执行一次建连：open 时立刻失败；连接级失败计数，成功清零。

    非连接级异常（参数错误等）原样抛出，但会归还 half-open 的探测名额。
    """
    breaker.before_connect()
    try:
        conn = connect_fn(*args, **kwargs)
    except BaseException as exc:  # noqa: BLE001
        if is_connection_error(exc):
            breaker.record_failure(exc)
            if isinstance(exc, DatabaseUnavailable):
                raise
            raise DatabaseUnavailable(
                f"PostgreSQL 连接失败：{type(exc).__name__}: {str(exc).strip()[:200]}"
            ) from exc
        breaker.release_probe()
        raise
    breaker.record_success()
    return conn

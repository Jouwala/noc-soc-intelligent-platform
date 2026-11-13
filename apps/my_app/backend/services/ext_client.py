# -*- coding: utf-8 -*-
"""外部系统客户端的共享基础：超时、重试、错误类型、健康检查。

## 为什么要有这个模块

本仓库要打交道的外部系统很多（eFlow、UME、VMAX、Open WebUI、远程 DB
写入服务、GP-RAN……），但每个客户端各写一套容错，实测下来四件套
（超时 / 重试 / 错误类型 / 健康检查）的齐全程度完全不一致
（2026-11-13 第二轮重构盘点，`backend/services/` 下的主要客户端）：

| 客户端 | 超时 | 重试 | 错误类型 | 健康检查 |
| --- | --- | --- | --- | --- |
| eflow_api_client | 有（env 可调） | 有（有界退避） | 无（裸 Exception/RuntimeError） | 无 |
| auth_manager（eFlow/UME 登录链路） | 有（env 可调） | 登录层有 | 无 | 无（有内部 probe，不对外） |
| ume_alarm_client | 靠 auth_manager | 仅 401 重登 | 无（RuntimeError） | 无 |
| openwebui_client | 有 | 无（靠调用链换下一档） | 有（自有三类） | 无 |
| db_writer_client | 有 | 有（区分连接阶段） | 无（最终抛 builtin ConnectionError） | 有（health） |
| gp_ran_client | 有 | 无 | 有 | 无 |
| ipt_energy_client | 有 | 部分接口有 | 有 | 无 |
| vmax_cca / vmax_xdr | 有 | 仅会话失效重试 | 有 | 无 |
| vmax_gbase_client | 有 | 有 | 有 | 有（healthy） |
| vmax_report_client | 有 | 仅会话失效重试 | 有 | 有（healthy） |

问题不在某一家写得差，而在**没有共同契约**：上层拿到失败时分不清
「对端没起来」「超时」「被拒（401）」「上游 5xx」「响应解析不了」，
只能把一切都当成「取不到」——而 AGENTS.md 原则 6 要求取不到显示 `—`、
不能显示成 0，前提就是错误必须能被区分。

## 本模块提供什么

- **错误类型**：`ExtClientError` 为统一基类（继承 `RuntimeError`，
  既有 `except RuntimeError` 的调用方不受影响）；`ExtTimeoutError`
  同时是 `TimeoutError`、`ExtUnavailableError` 同时是
  `ConnectionError`，同理兼容既有捕获。`ExtHttpError` 带
  `status_code`，401/403 用子类 `ExtAuthError`；响应结构/解析问题用
  `ExtProtocolError`。每个错误都可带 `system` / `operation` /
  `attempts`，日志里能直接回答「哪个系统、哪个操作、试了几次」。
- **超时**：`TimeoutSpec(connect, read)`，给 requests 风格的
  `(connect, read)` 元组与总时长；各客户端原有的 env 配置不变，
  本类只是把散落的常量收成同一个形状。
- **重试**：`RetryPolicy`（有界次数 + 指数退避 + 抖动 + 可重试状态码
  集合）与 `call_with_retry()`。只对**暂时性**失败重试：超时、连接
  失败、HTTP 408/429/502/503/504。4xx（除 408/429）与协议错误不重试。
  **非幂等调用必须显式传 `retry=False`**（只做错误分类，不重试）——
  写接口盲重试会重复提交，本仓库在 db_writer_client 上吃过这个亏
  （见该文件 2026-11-07 的注释），不要在这里把它请回来。
- **健康检查**：`run_health_check(name, probe)` 把一次探活收成统一的
  `HealthResult`（`ok / latency_ms / detail / error / checked_at`），
  **永不抛错**——健康检查本身失败不能再变成一个新的异常源。

## 收敛策略（与 silent_except.py 同款）

**不做全量替换。** 存量客户端的传输层各不相同（原始 socket、urllib、
requests、paramiko、pymysql），强行统一传输层不在本轮范围内。
策略是「改到哪个接哪个」：

- 新写客户端：直接用本模块的四件套，不要再自造错误类与重试循环。
- 改到存量客户端：优先补它缺的那一两项（多半是错误类型与健康检查），
  已有的超时/重试实现**保持原样**，只在最终抛错处换成统一错误类型。
- 本轮已接入：`openwebui_client`、`db_writer_client`、
  `ume_alarm_client`、`eflow_api_client`（各自的接入点见其模块内注释）。
  其余客户端（gp_ran / ipt_energy / vmax_* 等）留待改到时逐个接入，
  它们自有错误类与本模块基类的合并也要逐个评估，不在本轮一刀切。

本模块只依赖标准库：守卫与离线测试不需要装 requests/urllib3，
对外部异常的分类靠鸭子类型（读 `code` / `status_code` / `response`），
不 import 任何传输库。
"""
from __future__ import annotations

import logging
import random
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FuturesTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Tuple, TypeVar

logger = logging.getLogger("ext_client")

__all__ = [
    "ExtClientError", "ExtTimeoutError", "ExtUnavailableError",
    "ExtHttpError", "ExtAuthError", "ExtProtocolError",
    "TimeoutSpec", "DEFAULT_TIMEOUT",
    "RetryPolicy", "DEFAULT_RETRY_POLICY",
    "classify_error", "call_with_retry",
    "HealthResult", "run_health_check",
]

T = TypeVar("T")


# ============================================================
# 错误类型
# ============================================================
class ExtClientError(RuntimeError):
    """外部系统客户端统一错误基类。

    继承 RuntimeError 是刻意的：存量调用方大量 `except RuntimeError`，
    换成统一错误类型后这些捕获点不需要跟着改。

    属性：
        system:      外部系统名（如 "eflow" / "openwebui"），排障时先看它
        operation:   失败的操作（如 "GET /api/models"）
        status_code: HTTP 状态码（仅 ExtHttpError 有值，其余为 None）
        attempts:    实际尝试次数（由 call_with_retry 回填）
    """

    def __init__(self, message: str = "", *, system: str = "",
                 operation: str = "", status_code: Optional[int] = None,
                 attempts: Optional[int] = None) -> None:
        super().__init__(message)
        self.system = system
        self.operation = operation
        self.status_code = status_code
        self.attempts = attempts


class ExtTimeoutError(ExtClientError, TimeoutError):
    """连接或读取超时。同时是 builtin TimeoutError，兼容既有捕获。"""


class ExtUnavailableError(ExtClientError, ConnectionError):
    """对端不可达：连接被拒 / 无路由 / DNS 失败。同时是 ConnectionError。"""


class ExtHttpError(ExtClientError):
    """对端返回了 HTTP 错误状态（请求已送达，区别于连不上）。"""

    def __init__(self, message: str = "", *, status_code: int,
                 system: str = "", operation: str = "",
                 attempts: Optional[int] = None) -> None:
        super().__init__(message, system=system, operation=operation,
                         status_code=status_code, attempts=attempts)


class ExtAuthError(ExtHttpError):
    """鉴权失败（401/403）：凭据失效或权限不足，重试无意义，要重登/换凭据。"""


class ExtProtocolError(ExtClientError):
    """响应拿到但解析不了 / 结构不符合契约（对端升级改版时最先命中）。"""


# ============================================================
# 超时
# ============================================================
@dataclass(frozen=True)
class TimeoutSpec:
    """连接/读取两段超时。各客户端的 env 配置仍是各自的真相源，
    需要统一形状时用本类承载，不要反过来用本类覆盖既有 env 约定。"""

    connect: float = 10.0
    read: float = 60.0

    def __post_init__(self) -> None:
        if self.connect <= 0 or self.read <= 0:
            raise ValueError(
                f"超时必须为正数: connect={self.connect} read={self.read}")

    def as_tuple(self) -> Tuple[float, float]:
        """requests 风格的 (connect, read) 元组。"""
        return (self.connect, self.read)

    @property
    def total(self) -> float:
        """一次请求理论最长耗时（连接 + 读取），健康检查定超时时用它。"""
        return self.connect + self.read


DEFAULT_TIMEOUT = TimeoutSpec()


# ============================================================
# 重试
# ============================================================
@dataclass(frozen=True)
class RetryPolicy:
    """有界重试策略：次数有上限、退避有上限，禁止无边界重试
    （AGENTS.md 原则 5）。`max_retries` 是首次失败后**再**试的次数，
    总尝试次数 = max_retries + 1。"""

    max_retries: int = 2
    base_delay: float = 0.5
    max_delay: float = 20.0
    jitter: float = 0.25
    retry_statuses: frozenset = frozenset({408, 429, 502, 503, 504})

    def delay_for(self, attempt: int) -> float:
        """第 attempt 次重试（从 0 数）前等待的秒数：指数退避 + 抖动。"""
        backoff = min(self.max_delay, self.base_delay * (2 ** attempt))
        return backoff + random.uniform(0, self.jitter)

    def retryable_status(self, status_code: Optional[int]) -> bool:
        return status_code is not None and status_code in self.retry_statuses

    def retryable_error(self, exc: BaseException) -> bool:
        """只有暂时性失败值得重试：超时 / 连不上 / 可重试状态码。
        鉴权失败重试只会加速把账号打锁死，协议错误重试也不会变好。"""
        err = classify_error(exc)
        if isinstance(err, (ExtTimeoutError, ExtUnavailableError)):
            return True
        if isinstance(err, ExtAuthError):
            return False
        if isinstance(err, ExtHttpError):
            return self.retryable_status(err.status_code)
        return False


DEFAULT_RETRY_POLICY = RetryPolicy()


def _extract_status(exc: BaseException) -> Optional[int]:
    """鸭子类型取 HTTP 状态码：urllib 的 HTTPError 有 code，
    requests 的异常挂在 response.status_code 上。不 import 传输库。"""
    for attr in ("status_code", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        for attr in ("status_code", "status"):
            value = getattr(response, attr, None)
            if isinstance(value, int) and 100 <= value <= 599:
                return value
    return None


def classify_error(exc: BaseException, *, system: str = "",
                   operation: str = "") -> ExtClientError:
    """把传输层各式异常归一成统一错误类型。

    已经是 ExtClientError 的原样返回（只补空缺的 system/operation），
    所以本函数可以套在任何一层，不会把已分类的错误再包一层。
    """
    if isinstance(exc, ExtClientError):
        if system and not exc.system:
            exc.system = system
        if operation and not exc.operation:
            exc.operation = operation
        return exc
    status = _extract_status(exc)
    if status is not None:
        cls = ExtAuthError if status in (401, 403) else ExtHttpError
        return cls(str(exc) or f"HTTP {status}", status_code=status,
                   system=system, operation=operation)
    # urllib 的 URLError 把真实原因放在 reason 里（如 timed out / refused）
    reason = getattr(exc, "reason", None)
    if isinstance(reason, BaseException) and reason is not exc:
        inner = classify_error(reason, system=system, operation=operation)
        if type(inner) is not ExtClientError:
            # 内层分出了更具体的类型（超时/连不上…）：用它，但保留外层文案
            inner.args = (str(exc),)
            return inner
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return ExtTimeoutError(str(exc), system=system, operation=operation)
    if isinstance(exc, ConnectionError):
        return ExtUnavailableError(str(exc), system=system, operation=operation)
    if isinstance(exc, OSError):
        return ExtUnavailableError(str(exc), system=system, operation=operation)
    return ExtClientError(str(exc), system=system, operation=operation)


def call_with_retry(fn: Callable[[], T], *, policy: Optional[RetryPolicy] = None,
                    system: str = "", operation: str = "",
                    retry: bool = True,
                    sleep: Callable[[float], None] = time.sleep) -> T:
    """执行 fn()，对暂时性失败按 policy 有界重试。

    最终失败时抛 classify_error 归一后的 ExtClientError（带 attempts），
    不把传输层的原始异常漏给上层。`retry=False` 时只执行一次——
    非幂等调用（写接口、派单、发消息）必须用它，防重复提交。
    `sleep` 可注入，测试里传空函数即可不真等待。
    """
    policy = policy or DEFAULT_RETRY_POLICY
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 —— 归一后统一上抛，不是吞
            err = classify_error(exc, system=system, operation=operation)
            if not retry or attempt >= policy.max_retries \
                    or not policy.retryable_error(err):
                err.attempts = attempt + 1
                raise err from exc
            delay = policy.delay_for(attempt)
            logger.warning(
                "[ext_client] %s %s 第 %d 次尝试失败（%s: %s），%.2fs 后重试",
                system or "-", operation or "-", attempt + 1,
                type(err).__name__, err, delay)
            sleep(delay)
            attempt += 1


# ============================================================
# 健康检查
# ============================================================
@dataclass
class HealthResult:
    """一次健康检查的结构化结果。各客户端的 health_check() 都返回
    它的 to_dict()，上层（巡检/看板）不需要再认识每家的私有格式。"""

    name: str
    ok: bool
    latency_ms: float
    detail: str = ""
    error: str = ""          # 失败时的错误类型名（ExtTimeoutError 等）
    checked_at: str = ""     # ISO8601（UTC）

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "detail": self.detail,
            "error": self.error,
            "checked_at": self.checked_at,
        }


def _coerce_probe_result(raw: Any) -> Tuple[bool, str]:
    """probe 的返回值约定：bool / (ok, detail) / dict(ok|success, detail)。"""
    if isinstance(raw, bool):
        return raw, ""
    if isinstance(raw, (tuple, list)) and len(raw) == 2:
        return bool(raw[0]), str(raw[1])
    if isinstance(raw, dict):
        ok = raw.get("ok", raw.get("success", False))
        detail = raw.get("detail") or raw.get("message") or ""
        return bool(ok), str(detail)
    if raw is None:
        return False, "探针无返回"
    return True, str(raw)[:200]


def run_health_check(name: str, probe: Callable[[], Any], *,
                     timeout: Optional[float] = None) -> HealthResult:
    """跑一次探活并收成 HealthResult，**永不抛错**。

    probe 应是轻量、无副作用的探测（打健康端点 / 探已有会话 /
    只建 TCP 连接），不要在 probe 里做登录、写库这类有副作用的动作。
    timeout 给定时用线程等待上限封顶：超时记为 ExtTimeoutError 失败；
    注意线程无法强杀，超时的 probe 仍可能在后台跑完——所以 probe
    自身仍必须带超时，timeout 只是最后一道闸。
    """
    started = time.monotonic()
    ok, detail, error = False, "", ""
    try:
        if timeout is not None:
            executor = ThreadPoolExecutor(max_workers=1)
            try:
                raw = executor.submit(probe).result(timeout=timeout)
            except _FuturesTimeoutError:
                raw = None
                ok, detail, error = False, f"健康检查超过 {timeout}s 未返回", \
                    "ExtTimeoutError"
            finally:
                executor.shutdown(wait=False)
            if not error:
                ok, detail = _coerce_probe_result(raw)
        else:
            ok, detail = _coerce_probe_result(probe())
    except Exception as exc:  # noqa: BLE001 —— 健康检查永不抛，失败收进结果
        err = classify_error(exc, system=name, operation="health_check")
        ok, detail, error = False, str(err), type(err).__name__
    latency_ms = round((time.monotonic() - started) * 1000, 1)
    return HealthResult(
        name=name, ok=ok, latency_ms=latency_ms, detail=detail,
        error=error, checked_at=datetime.now(timezone.utc).isoformat())

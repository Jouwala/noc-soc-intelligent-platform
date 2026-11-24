# -*- coding: utf-8 -*-
"""PRTG API 客户端骨架（Issue #225）。

职责只到「把请求可靠地发出去、把响应原文拿回来」：带超时与退避重试，
PRTG 不可达时不卡死、每次重试都留日志记录（Issue #225 验收第 4 条）。
响应解析一律走 parsing.py；采集编排（轮询、入库）是 #227 的范围，不在这里。

凭证只从环境变量读，键名：PRTG_BASE_URL / PRTG_USERNAME / PRTG_PASSHASH
（本文件只出现键名；值只在内存里持有，不得写入代码、文档、Issue 或日志——
本模块日志只记路径、状态码与尝试次数，不记请求参数）。

真实联调只能在能访问 PRTG 内网的主机（MSNOC1）上执行；VM 上只跑
MockTransport 离线测试（tests/test_prtg_client.py）。
"""
import logging
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping

import httpx

logger = logging.getLogger(__name__)

# 凭证在 GET 查询串里（PRTG API 既有方式），而 httpx 在 INFO 级会把完整 URL
# 打进应用日志（main.py 的根 logger 就是 INFO）。2026-11-24 独立审核实测：
# `HTTP Request: GET ...&passhash=...` 会原样落盘。这里做两道防护：
#   1) 把 httpx 日志降到 WARNING，不再记录每个请求行；
#   2) 再加一个遮罩 Filter，WARNING/ERROR 万一带 URL 也把凭证值替换成 ***。
_REDACT_PATTERN = re.compile(r"(passhash|password|username)=([^&\s\"']+)", re.IGNORECASE)


def redact_prtg_secrets(text: str) -> str:
    """把查询串里的凭证值替换成 ***（给日志 Filter 用，可单测）。"""
    return _REDACT_PATTERN.sub(lambda m: f"{m.group(1)}=***", text)


class _SecretRedactFilter(logging.Filter):
    """logging Filter：遮住 msg / args 里的凭证。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_prtg_secrets(record.msg)
        args = record.args
        if isinstance(args, Mapping):
            # %(name)s 风格：必须保持 dict，改成 tuple 会让 logging 格式化直接抛
            # TypeError("not all arguments converted")（2026-11-24 CI 实测）
            record.args = {
                key: redact_prtg_secrets(value) if isinstance(value, str) else value
                for key, value in args.items()
            }
        elif isinstance(args, tuple):
            record.args = tuple(
                redact_prtg_secrets(a) if isinstance(a, str) else a for a in args
            )
        return True


def _install_http_log_guard() -> None:
    """降低 httpx 请求行日志级别，并给已存在的根 handler 挂遮罩 Filter。

    注意：Filter 挂在 logger 上只对「直接打到该 logger」的记录生效，子 logger
    （httpx._client）不会继承父 logger 的 Filter —— 所以遮罩必须挂到根
    handler 上，才是「所有输出」都必经的那一层。之后再新增的 handler
    （如测试的 caplog）由 WARNING 级别兜底：httpx 默认不再发 INFO 请求行。
    """
    for name in ("httpx", "httpcore"):
        log = logging.getLogger(name)
        if not any(isinstance(f, _SecretRedactFilter) for f in log.filters):
            log.addFilter(_SecretRedactFilter())
        log.setLevel(logging.WARNING)
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, _SecretRedactFilter) for f in handler.filters):
            handler.addFilter(_SecretRedactFilter())


_install_http_log_guard()

ENV_BASE_URL = "PRTG_BASE_URL"
ENV_USERNAME = "PRTG_USERNAME"
ENV_PASSHASH = "PRTG_PASSHASH"


class PrtgError(RuntimeError):
    """PRTG 客户端错误基类。"""


class PrtgConfigError(PrtgError):
    """环境变量缺失或不合法（消息里只有键名，没有任何值）。"""


class PrtgUnavailable(PrtgError):
    """重试耗尽仍不可达（网络错误或 5xx）。"""


@dataclass(frozen=True)
class PrtgConfig:
    """连接参数。passhash 只在内存持有，禁止落盘、禁止打印。"""

    base_url: str
    username: str
    passhash: str = field(repr=False)  # 防止 repr 把 Passhash 打进日志

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "PrtgConfig":
        env = os.environ if environ is None else environ
        missing = [k for k in (ENV_BASE_URL, ENV_USERNAME, ENV_PASSHASH) if not env.get(k)]
        if missing:
            raise PrtgConfigError(f"缺少环境变量：{', '.join(missing)}（值请在运行主机上配置，不要写入代码）")
        return cls(
            base_url=env[ENV_BASE_URL].rstrip("/"),
            username=env[ENV_USERNAME],
            passhash=env[ENV_PASSHASH],
        )


class PrtgClient:
    """同步 PRTG API 客户端（httpx），带超时与退避重试。"""

    def __init__(
        self,
        config: PrtgConfig,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep
        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=10.0,
            pool=5.0,
        )
        self._client = httpx.Client(
            base_url=config.base_url,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PrtgClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _auth_params(self) -> dict[str, str]:
        return {"username": self._config.username, "passhash": self._config.passhash}

    def get_text(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        """GET 并返回响应原文。网络错误与 5xx 按退避重试，耗尽抛 PrtgUnavailable；
        4xx 不重试、直接抛 PrtgError。每次重试都记一条 warning 日志（不含参数）。"""
        merged: dict[str, Any] = dict(params or {})
        merged.update(self._auth_params())
        attempts = self._max_retries + 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = self._client.get(path, params=merged)
            except httpx.TransportError as exc:
                last_error = exc
                logger.warning(
                    "PRTG 请求失败（第 %d/%d 次）：path=%s error=%s",
                    attempt, attempts, path, type(exc).__name__,
                )
            else:
                if response.status_code < 500:
                    if response.status_code >= 400:
                        raise PrtgError(f"PRTG 返回 {response.status_code}：path={path}（4xx 不重试）")
                    return response.text
                last_error = PrtgError(f"HTTP {response.status_code}")
                logger.warning(
                    "PRTG 返回 %d（第 %d/%d 次）：path=%s",
                    response.status_code, attempt, attempts, path,
                )
            if attempt < attempts:
                self._sleep(self._backoff_seconds * (2 ** (attempt - 1)))
        raise PrtgUnavailable(f"PRTG 不可达：path={path}，共尝试 {attempts} 次") from last_error

    # ---- 端点薄封装（只拼参数、返回原文，解析走 parsing.py） ----

    def table_json(
        self,
        content: str,
        *,
        columns: list[str] | tuple[str, ...] | None = None,
        count: int | None = None,
        start: int | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> str:
        """GET /api/table.json。注意默认只回 5 列且无 objid，按 id 定位
        必须显式传 columns（见 channels.TABLE_DEFAULT_COLUMNS 与字典文档）。"""
        params: dict[str, Any] = {"content": content, "output": "json"}
        if columns:
            params["columns"] = ",".join(columns)
        if count is not None:
            params["count"] = count
        if start is not None:
            params["start"] = start
        if extra:
            params.update(extra)
        return self.get_text("/api/table.json", params)

    def sensortree_html(self) -> str:
        """GET /api/table.xml?content=sensortree。注意：实测返回的是
        HTML 片段不是 XML（见 parsing.parse_sensortree_html）。"""
        return self.get_text("/api/table.xml", {"content": "sensortree"})

    def historicdata_json(
        self,
        sensor_id: int,
        sdate: str | datetime,
        edate: str | datetime,
        *,
        avg: int = 0,
    ) -> str:
        """GET /api/historicdata.json。sdate/edate 接受 "YYYY-MM-DD-HH-MM-SS"
        字符串或 datetime（按 PRTG 服务器本地时间解释，时区待 MSNOC1 复核）。"""
        params = {
            "id": sensor_id,
            "avg": avg,
            "sdate": _format_prtg_datetime(sdate),
            "edate": _format_prtg_datetime(edate),
            "output": "json",
        }
        return self.get_text("/api/historicdata.json", params)


def _format_prtg_datetime(value: str | datetime) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d-%H-%M-%S")
    return value

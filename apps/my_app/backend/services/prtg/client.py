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
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

import httpx

logger = logging.getLogger(__name__)

ENV_BASE_URL = "PRTG_BASE_URL"
ENV_USERNAME = "PRTG_USERNAME"
ENV_PASSHASH = "PRTG_PASSHASH"

# content=channels 必须显式指定列，否则 objid/lastvalue 都不在返回里。
CHANNEL_COLUMNS = "objid,name,lastvalue,lastvalue_raw"


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
    passhash: str

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

    def channels_xml(self, sensor_id: int) -> str:
        """GET /api/table.xml?content=channels&id=<sensor>。

        采集端用 XML 而非 JSON：lastvalue / lastvalue_raw 在这里各重复三次
        （速率、体积、体积），JSON 的普通 dict 解析只留一个（#227 实测）。
        """
        return self.get_text(
            "/api/table.xml",
            {
                "content": "channels",
                "id": str(sensor_id),
                "columns": CHANNEL_COLUMNS,
            },
        )

    def sensor_details_xml(self, sensor_id: int) -> str:
        """GET /api/getsensordetails.xml（无 .json 版，实测 404）。"""
        return self.get_text("/api/getsensordetails.xml", {"id": str(sensor_id)})

    def historicdata_csv(
        self,
        sensor_id: int,
        sdate: str | datetime,
        edate: str | datetime,
        *,
        avg: int = 0,
    ) -> str:
        """GET /api/historicdata.csv（回填用；列结构见 parsing.parse_historic_csv）。"""
        return self.get_text(
            "/api/historicdata.csv",
            {
                "id": str(sensor_id),
                "avg": str(avg),
                "sdate": _format_prtg_datetime(sdate),
                "edate": _format_prtg_datetime(edate),
            },
        )

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

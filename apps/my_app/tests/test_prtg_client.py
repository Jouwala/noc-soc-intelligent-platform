# -*- coding: utf-8 -*-
"""PRTG 客户端离线测试（Issue #225）。

纯离线：全部走 httpx.MockTransport，不发任何真实网络请求。
覆盖验收第 4 条：PRTG 不可达时不卡死（超时配置在位）、失败有退避重试、
重试耗尽抛 PrtgUnavailable；以及配置只认环境变量键名、缺失时报错不带值。
"""
import httpx
import pytest

from backend.services.prtg.client import (
    PrtgClient,
    PrtgConfig,
    PrtgConfigError,
    PrtgError,
    PrtgUnavailable,
)

DUMMY_ENV = {
    "PRTG_BASE_URL": "http://prtg.invalid",
    "PRTG_USERNAME": "dummy-user",
    "PRTG_PASSHASH": "dummy-passhash",
}


def _config() -> PrtgConfig:
    return PrtgConfig.from_env(DUMMY_ENV)


def _client(handler, **kwargs) -> PrtgClient:
    kwargs.setdefault("sleep", lambda _s: None)
    kwargs.setdefault("backoff_seconds", 0.0)
    return PrtgClient(_config(), transport=httpx.MockTransport(handler), **kwargs)


def test_config_from_env_ok():
    cfg = _config()
    assert cfg.base_url == "http://prtg.invalid"
    assert cfg.username == "dummy-user"


def test_config_missing_env_reports_key_names_only():
    with pytest.raises(PrtgConfigError) as excinfo:
        PrtgConfig.from_env({"PRTG_BASE_URL": "http://prtg.invalid"})
    message = str(excinfo.value)
    assert "PRTG_USERNAME" in message and "PRTG_PASSHASH" in message
    assert "dummy" not in message


def test_get_text_success_and_auth_params_attached():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, text="hello")

    with _client(handler) as client:
        assert client.get_text("/api/table.json", {"content": "sensors"}) == "hello"
    assert "username=dummy-user" in seen["url"]
    assert "passhash=dummy-passhash" in seen["url"]


def test_retry_on_transport_error_then_success():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, text="ok")

    with _client(handler, max_retries=3) as client:
        assert client.get_text("/api/table.xml", {"content": "sensortree"}) == "ok"
    assert attempts["n"] == 3


def test_retry_exhausted_raises_unavailable():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503, text="down")

    with _client(handler, max_retries=2) as client:
        with pytest.raises(PrtgUnavailable):
            client.get_text("/api/table.json", {"content": "sensors"})
    assert attempts["n"] == 3  # 首次 + 2 次重试


def test_4xx_not_retried():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(404, text="not found")

    with _client(handler, max_retries=3) as client:
        with pytest.raises(PrtgError):
            client.get_text("/api/chart.png")
    assert attempts["n"] == 1


def test_timeout_config_in_place():
    with _client(lambda req: httpx.Response(200, text="")) as client:
        timeout = client._client.timeout
    assert timeout.connect == 5.0
    assert timeout.read == 30.0


def test_table_json_passes_columns_and_count():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, text="{}")

    with _client(handler) as client:
        client.table_json("sensors", columns=["objid", "sensor"], count=5)
    assert "columns=objid%2Csensor" in seen["url"]
    assert "count=5" in seen["url"]

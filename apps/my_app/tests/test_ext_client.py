"""外部系统客户端共享基础（backend/services/ext_client.py）的离线守卫测试。

不连任何外部系统：只验四件套的纯逻辑（错误分类 / 有界重试 / 健康检查
收口）与四个已接入客户端的兼容性契约——错误类仍是 RuntimeError /
ConnectionError、db_writer 的最终失败带状态码、health_check 永不抛错。
这些契约一旦被改坏，上层会重新分不清「连不上 / 超时 / 被拒 / 5xx」，
取不到的数据就可能被当成 0 显示（AGENTS.md 原则 6）。

    pytest tests/test_ext_client.py -q
"""
import io
import socket
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services import ext_client as ec  # noqa: E402
from backend.services.ext_client import (  # noqa: E402
    ExtAuthError, ExtClientError, ExtHttpError, ExtProtocolError,
    ExtTimeoutError, ExtUnavailableError, RetryPolicy, TimeoutSpec,
    call_with_retry, classify_error, run_health_check,
)


def _http_error(code):
    return urllib.error.HTTPError(
        "http://upstream.invalid/x", code, "err", {}, io.BytesIO(b"err"))


# ---------------------------------------------------------------- 超时
def test_timeout_spec_tuple_and_total():
    spec = TimeoutSpec(connect=3, read=7)
    assert spec.as_tuple() == (3, 7)
    assert spec.total == 10


def test_timeout_spec_rejects_non_positive():
    with pytest.raises(ValueError):
        TimeoutSpec(connect=0, read=5)


# ---------------------------------------------------------------- 错误分类
def test_classify_timeout():
    err = classify_error(socket.timeout("timed out"), system="eflow")
    assert isinstance(err, ExtTimeoutError)
    assert isinstance(err, TimeoutError)  # 兼容既有 except TimeoutError
    assert err.system == "eflow"


def test_classify_connection_refused():
    err = classify_error(ConnectionRefusedError("refused"))
    assert isinstance(err, ExtUnavailableError)
    assert isinstance(err, ConnectionError)  # 兼容既有 except ConnectionError


def test_classify_http_status():
    err = classify_error(_http_error(503))
    assert isinstance(err, ExtHttpError)
    assert err.status_code == 503


def test_classify_auth_status():
    assert isinstance(classify_error(_http_error(401)), ExtAuthError)
    assert isinstance(classify_error(_http_error(403)), ExtAuthError)


def test_classify_urlerror_with_timeout_reason():
    err = classify_error(urllib.error.URLError(socket.timeout("timed out")))
    assert isinstance(err, ExtTimeoutError)


def test_classify_passthrough_keeps_subtype_and_fills_context():
    original = ExtProtocolError("bad payload", system="vmax")
    err = classify_error(original, operation="parse")
    assert err is original
    assert err.system == "vmax"
    assert err.operation == "parse"


def test_classify_unknown_error_is_base_type():
    err = classify_error(ValueError("???"))
    assert type(err) is ExtClientError
    assert isinstance(err, RuntimeError)


# ---------------------------------------------------------------- 重试策略
def test_retry_delay_is_bounded():
    policy = RetryPolicy(base_delay=10, max_delay=20, jitter=0.25)
    for attempt in range(6):
        assert policy.delay_for(attempt) <= 20.25


def test_retryable_matrix():
    policy = RetryPolicy()
    assert policy.retryable_error(ConnectionError("down"))
    assert policy.retryable_error(socket.timeout("slow"))
    assert policy.retryable_error(_http_error(503))
    assert policy.retryable_error(_http_error(429))
    assert not policy.retryable_error(_http_error(400))
    assert not policy.retryable_error(_http_error(401))  # 鉴权失败不重试
    assert not policy.retryable_error(ExtProtocolError("bad payload"))


def test_call_with_retry_recovers():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("down")
        return "ok"

    assert call_with_retry(flaky, sleep=lambda s: None) == "ok"
    assert calls["n"] == 3


def test_call_with_retry_exhaustion_raises_typed_with_attempts():
    def always_down():
        raise ConnectionRefusedError("refused")

    with pytest.raises(ExtUnavailableError) as caught:
        call_with_retry(always_down, sleep=lambda s: None)
    assert caught.value.attempts == 3  # 默认 max_retries=2 → 共 3 次


def test_call_with_retry_does_not_retry_4xx():
    calls = {"n": 0}

    def bad_request():
        calls["n"] += 1
        raise _http_error(400)

    with pytest.raises(ExtHttpError):
        call_with_retry(bad_request, sleep=lambda s: None)
    assert calls["n"] == 1


def test_call_with_retry_retry_false_means_single_attempt():
    calls = {"n": 0}

    def down():
        calls["n"] += 1
        raise ConnectionError("down")

    with pytest.raises(ExtUnavailableError):
        call_with_retry(down, retry=False, sleep=lambda s: None)
    assert calls["n"] == 1


# ---------------------------------------------------------------- 健康检查
def test_health_check_ok_forms():
    assert run_health_check("a", lambda: True).ok
    assert run_health_check("a", lambda: (True, "fine")).detail == "fine"
    result = run_health_check("a", lambda: {"success": True}).to_dict()
    assert result["ok"] and result["name"] == "a" and result["checked_at"]


def test_health_check_failure_never_raises():
    result = run_health_check("a", lambda: (_ for _ in ()).throw(
        ConnectionRefusedError("refused")))
    assert not result.ok
    assert result.error == "ExtUnavailableError"


def test_health_check_timeout():
    import time
    result = run_health_check("a", lambda: time.sleep(2), timeout=0.2)
    assert not result.ok
    assert result.error == "ExtTimeoutError"


# ---------------------------------------------------------------- 接入：openwebui_client
def test_openwebui_error_types_compatible():
    from backend.services import openwebui_client as owu
    for cls in (owu.ModelError, owu.UnreachableError, owu.CustomApiKeyError):
        assert issubclass(cls, ExtClientError)
        assert issubclass(cls, RuntimeError)
    assert issubclass(owu.UnreachableError, ConnectionError)


def test_openwebui_get_retries_but_post_does_not(monkeypatch):
    from backend.services import openwebui_client as owu
    calls = {"n": 0}

    def flaky_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise owu.UnreachableError("down")
        return 200, {}

    monkeypatch.setattr(owu, "_http_once", flaky_once)
    assert owu._http("GET", "/api/models") == (200, {})
    assert calls["n"] == 3

    calls["n"] = 0

    def always_down(*args, **kwargs):
        calls["n"] += 1
        raise owu.UnreachableError("down")

    monkeypatch.setattr(owu, "_http_once", always_down)
    with pytest.raises(owu.UnreachableError):
        owu._http("POST", "/api/chat/completions", {})
    assert calls["n"] == 1


def test_openwebui_health_check(monkeypatch):
    from backend.services import openwebui_client as owu
    monkeypatch.setattr(owu, "_http_once", lambda *a, **k: (200, {}))
    monkeypatch.setattr(owu, "_OWU_DOWN_UNTIL", 0.0)
    result = owu.health_check()
    assert result["ok"] and result["name"] == "openwebui"


# ---------------------------------------------------------------- 接入：db_writer_client
class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b'{"success": true}'


def test_db_writer_typed_errors(monkeypatch):
    from backend.services import db_writer_client as dw
    client = dw.DbWriterClient(host="127.0.0.1", port=9, timeout=1)

    def refused(req, timeout=None):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(dw.urllib.request, "urlopen", refused)
    with pytest.raises(ExtUnavailableError) as caught:
        client._request("GET", "/health", max_retries=1)
    assert isinstance(caught.value, ConnectionError)  # 接入前的捕获方式不变

    def http_503(req, timeout=None):
        raise _http_error(503)

    monkeypatch.setattr(dw.urllib.request, "urlopen", http_503)
    with pytest.raises(ExtHttpError) as caught:
        client._request("GET", "/health", max_retries=1)
    assert caught.value.status_code == 503


def test_db_writer_health_check(monkeypatch):
    from backend.services import db_writer_client as dw
    client = dw.DbWriterClient(host="127.0.0.1", port=9, timeout=1)
    monkeypatch.setattr(dw.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResponse())
    result = client.health_check()
    assert result["ok"] and result["name"] == "db_writer"


# ---------------------------------------------------------------- 接入：ume_alarm_client
def test_ume_error_type_and_health_check(monkeypatch):
    from backend.services import ume_alarm_client as ume
    assert issubclass(ume.UmeAlarmError, ExtClientError)
    assert ume.UmeAlarmError("x").system == "ume_alarm"

    ume._clear_session_cache()

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(socket, "create_connection",
                        lambda addr, timeout=None: _Conn())
    result = ume.health_check()
    assert result["ok"] and result["name"] == "ume_alarm"

    def unreachable(addr, timeout=None):
        raise OSError("no route")

    monkeypatch.setattr(socket, "create_connection", unreachable)
    result = ume.health_check()
    assert not result["ok"]
    assert result["error"] == "ExtUnavailableError"


# ---------------------------------------------------------------- 接入：eflow_api_client
def test_eflow_error_type_and_health_check(monkeypatch):
    from backend.services import eflow_api_client as eflow
    assert issubclass(eflow.EflowApiError, ExtClientError)
    assert eflow.EflowApiError("x").system == "eflow"

    class _Sock:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(eflow, "_create_ssl_socket", lambda: _Sock())
    result = eflow.health_check()
    assert result["ok"] and result["name"] == "eflow"

    def boom():
        raise OSError("network unreachable")

    monkeypatch.setattr(eflow, "_create_ssl_socket", boom)
    result = eflow.health_check()
    assert not result["ok"]

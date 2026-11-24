# -*- coding: utf-8 -*-
"""号码查询名单守卫测试（Issue #241：直连 VMAX 接口补 web_users 名单校验）。

纯离线：不打上游、不读真实会话。覆盖：
  1. services/phone_query_acl.check_web_user 的判定口径（与对话面一致）：
     名单命中（不区分大小写）放行；不在名单、用户名为空、名单为空一律拒绝
     （fail-closed）；enabled=False 按配置既定语义放行（总开关关掉 = 不限制）。
  2. routers/phone_query_acl.require_phone_query_allowed 守卫：
     远程未授权用户 → 403 + 配置的拒绝文案；本机回环放行（机器间取数）。
  3. 三个直连接口（vmax-cca-query、vmax-diagnosis、vmax-diagnosis/stream）
     都先过守卫：守卫拒绝时上游一次都不碰；守卫放行后走原有流程。
  4. N1：GET /api/phone-query-acl 已显式要求登录（require_user）。
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.routers import phone_query_acl as acl_router          # noqa: E402
from backend.routers import vmax_cca_query as cca_router           # noqa: E402
from backend.routers import vmax_diagnosis as diag_router          # noqa: E402
from backend.services import phone_query_acl as acl_service        # noqa: E402

DENY_ZH = "你无权查询该信息，请找admin/zeeshan进行查询"
CFG_LISTED = {
    "enabled": True,
    "web_users": ["admin", "zeeshan"],
    "whatsapp_numbers": [],
    "deny_text_zh": DENY_ZH,
    "deny_text_en": "denied",
}


def _request(client_host: str) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/vmax-cca-query",
        "headers": [],
        "client": (client_host, 5555),
        "server": ("test", 80),
        "scheme": "http",
    })


REMOTE = "10.0.0.9"
LOOPBACK = "127.0.0.1"


# ---------- 1. check_web_user 判定口径 ----------

def test_check_web_user_listed_case_insensitive():
    assert acl_service.check_web_user("Admin", CFG_LISTED) == (True, "web-user")
    assert acl_service.check_web_user("ZEESHAN", CFG_LISTED) == (True, "web-user")


def test_check_web_user_not_listed_denied():
    assert acl_service.check_web_user("mallory", CFG_LISTED) == (False, "web-user-not-listed")


def test_check_web_user_missing_denied():
    assert acl_service.check_web_user("", CFG_LISTED) == (False, "web-user-missing")
    assert acl_service.check_web_user(None, CFG_LISTED) == (False, "web-user-missing")


def test_check_web_user_empty_list_denies_everyone():
    cfg = {**CFG_LISTED, "web_users": []}
    assert acl_service.check_web_user("admin", cfg)[0] is False


def test_check_web_user_disabled_allows():
    cfg = {**CFG_LISTED, "enabled": False}
    assert acl_service.check_web_user("mallory", cfg) == (True, "acl-disabled")


# ---------- 2. require_phone_query_allowed 守卫 ----------

@pytest.fixture()
def guard_env(monkeypatch):
    """把守卫依赖的会话用户与名单配置换成可控假值。"""
    state = {"user": {"username": "mallory", "role": "user"}}
    monkeypatch.setattr(acl_router.portal_auth, "current_user",
                        lambda request: state["user"])
    monkeypatch.setattr(acl_router.auth_switch, "is_auth_bypass", lambda: False)
    monkeypatch.setattr(acl_service, "get_config",
                        lambda force_refresh=False: dict(CFG_LISTED))
    return state


def test_guard_denies_unlisted_remote_user(guard_env):
    with pytest.raises(HTTPException) as excinfo:
        acl_router.require_phone_query_allowed(_request(REMOTE))
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == DENY_ZH


def test_guard_allows_listed_remote_user(guard_env):
    guard_env["user"] = {"username": "admin", "role": "user"}
    acl_router.require_phone_query_allowed(_request(REMOTE))  # 不抛即放行


def test_guard_denies_when_no_session_user(guard_env):
    guard_env["user"] = None
    with pytest.raises(HTTPException) as excinfo:
        acl_router.require_phone_query_allowed(_request(REMOTE))
    assert excinfo.value.status_code == 403


def test_guard_allows_loopback_machine_caller(guard_env):
    # 本机回环（问答服务/机器人取数）即使用户不在名单也放行
    acl_router.require_phone_query_allowed(_request(LOOPBACK))


# ---------- 3. 三个直连接口先过守卫（TestClient 端到端） ----------

from fastapi import FastAPI                            # noqa: E402
from fastapi.testclient import TestClient              # noqa: E402

from backend.services.vmax_cca.vmax_cca_client import (  # noqa: E402
    VmaxCcaNotConfigured,
)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(cca_router.router)
    app.include_router(diag_router.router)
    app.include_router(acl_router.router)
    return app


@pytest.fixture()
def client_env(guard_env, monkeypatch):
    """TestClient + 可控会话用户（guard_env 已备好名单与判定环境）。"""
    return TestClient(_app(), raise_server_exceptions=False)


def test_cca_query_403_for_unlisted_user(client_env):
    resp = client_env.get("/api/vmax-cca-query", params={"msisdn": "231777123456"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == DENY_ZH


def test_diagnosis_403_for_unlisted_user(client_env):
    resp = client_env.get("/api/vmax-diagnosis", params={"msisdn": "231777123456"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == DENY_ZH


def test_diagnosis_stream_403_for_unlisted_user(client_env):
    resp = client_env.get("/api/vmax-diagnosis/stream",
                          params={"msisdn": "231777123456"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == DENY_ZH


def test_cca_query_listed_user_reaches_upstream(client_env, guard_env, monkeypatch):
    guard_env["user"] = {"username": "admin", "role": "user"}

    class _FakeClient:
        def query(self, msisdn, start, end):
            return {"found": False, "msisdn": msisdn}

    monkeypatch.setattr(cca_router, "get_client", lambda: _FakeClient())
    resp = client_env.get("/api/vmax-cca-query", params={"msisdn": "231777123456"})
    assert resp.status_code == 200
    assert resp.json()["found"] is False


def test_diagnosis_listed_user_passes_guard(client_env, guard_env, monkeypatch):
    guard_env["user"] = {"username": "zeeshan", "role": "user"}

    def _not_configured():
        raise VmaxCcaNotConfigured("未配置（测试桩）")

    monkeypatch.setattr(diag_router, "get_xdr_client", _not_configured)
    resp = client_env.get("/api/vmax-diagnosis", params={"msisdn": "231777123456"})
    # 过了守卫才会在建上游客户端时报 503；被守卫拦下会是 403
    assert resp.status_code == 503


def test_routes_carry_guard_dependency():
    def _guarded(router, path):
        for route in router.routes:
            if route.path == path:
                return any(getattr(d, "dependency", None)
                           is acl_router.require_phone_query_allowed
                           for d in route.dependencies)
        return False

    assert _guarded(cca_router.router, "/api/vmax-cca-query")
    assert _guarded(diag_router.router, "/api/vmax-diagnosis")
    assert _guarded(diag_router.router, "/api/vmax-diagnosis/stream")


# ---------- 4. N1：GET /api/phone-query-acl 显式要求登录 ----------

def test_get_acl_requires_login(monkeypatch):
    def _require_user(request):
        raise HTTPException(status_code=401, detail="未登录")

    monkeypatch.setattr(acl_router.portal_auth, "require_user", _require_user)
    with pytest.raises(HTTPException) as excinfo:
        acl_router.get_phone_query_acl(_request(REMOTE))
    assert excinfo.value.status_code == 401


def test_get_acl_returns_config_when_logged_in(monkeypatch):
    monkeypatch.setattr(acl_router.portal_auth, "require_user",
                        lambda request: {"username": "admin", "role": "admin"})
    monkeypatch.setattr(acl_router.portal_auth, "current_user",
                        lambda request: {"username": "admin", "role": "admin"})
    monkeypatch.setattr(acl_service, "get_config",
                        lambda force_refresh=False: dict(CFG_LISTED))
    result = acl_router.get_phone_query_acl(_request(REMOTE))
    assert result["config"]["web_users"] == ["admin", "zeeshan"]
    assert result["editable"] is True

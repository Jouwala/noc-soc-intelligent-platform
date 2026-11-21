# -*- coding: utf-8 -*-
"""账号锁定相关的回归测试（2026-11-21 iPRAN `noc1` 反复被锁事故后补）。

覆盖四类改动，每一类都对应事故链上的一个真实环节：

1. `env_role`：隔离副本（`.smoke` / 便携副本）不得登录生产上游 ——
   事故里副本用 9 月的旧口令登录真实 UME，把生产账号撞锁。
2. `auth_manager` 熔断：冷却按原因分档（上游锁定 4h 起、撞锁翻倍），
   且「密码错误」不能被当成「已锁定」（两者的上游原文都带 code=4）。
3. `auto_retry`：密码错误属永久性失败，不得重投 —— 重投一次就是一次新的
   失败登录，一次故障就能凑满「连续 3 次密码错误」。
4. `credential_store`：读库失败必须抛错，**不得**降级为空口令去登录上游。
"""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services import auth_manager as am  # noqa: E402
from backend.services import auto_retry  # noqa: E402
from backend.services import env_role  # noqa: E402
from backend.services.credential import credential_store as cs  # noqa: E402

HOST, PORT, USER = "10.9.9.9", 28001, "u"

LOCKED_ERR = (
    "登录失败 - 未获取到 Z-AUTH-CODE cookie (HTTP 401 Unauthorized, 服务端原因: "
    "User has been locked (code=4))；账号已被上游锁定，重试无意义"
)
WRONG_PWD_ERR = (
    "登录失败: 401, response: {\"result\":\"1\",\"detail\":{\"code\":\"4\","
    "\"message\":\"Incorrect user name or password, the account may be locked "
    "if you enter wrong for multiple times\"}}"
)


# ============ 1. 隔离副本守卫 ============

@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    """造一个「假仓库」：带 .smoke-runner 标记，判为隔离副本。"""
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".smoke-runner").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MYAPP_REPO_ROOT", str(root))
    monkeypatch.delenv("ISOLATED_ALLOW_UPSTREAM_LOGIN", raising=False)
    monkeypatch.delenv("ISOLATED_LOGIN_ALLOW_HOSTS", raising=False)
    env_role._ROLE_CACHE.clear()
    yield root
    env_role._ROLE_CACHE.clear()


def test_isolated_copy_blocks_upstream_login(isolated_root):
    assert env_role.detect_role(force=True) == "isolated"
    assert env_role.login_blocked_reason(HOST)
    with pytest.raises(PermissionError) as ei:
        env_role.assert_upstream_login_allowed(HOST, PORT, caller="test")
    assert "隔离副本" in str(ei.value)


def test_isolated_copy_allows_loopback_and_explicit_allowlist(isolated_root, monkeypatch):
    assert env_role.login_blocked_reason("127.0.0.1") is None
    assert env_role.login_blocked_reason("localhost") is None
    monkeypatch.setenv("ISOLATED_LOGIN_ALLOW_HOSTS", HOST)
    assert env_role.login_blocked_reason(HOST) is None


def test_isolated_copy_env_override(isolated_root, monkeypatch):
    monkeypatch.setenv("ISOLATED_ALLOW_UPSTREAM_LOGIN", "1")
    assert env_role.login_blocked_reason(HOST) is None


def test_production_role_not_blocked(tmp_path, monkeypatch):
    root = tmp_path / "prod"
    root.mkdir()
    (root / "PRODUCTION.flag").write_text("prod", encoding="utf-8")
    monkeypatch.setenv("MYAPP_REPO_ROOT", str(root))
    env_role._ROLE_CACHE.clear()
    try:
        assert env_role.detect_role(force=True) == "production"
        assert env_role.login_blocked_reason(HOST) is None
    finally:
        env_role._ROLE_CACHE.clear()


# ============ 2. 熔断冷却分档 ============

@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    d = tmp_path / "auth_cache"
    d.mkdir()
    monkeypatch.setattr(am, "AUTH_CACHE_DIR", d)
    monkeypatch.setattr(am, "AUTH_LOCK_COOLDOWN_SEC", 1800)
    monkeypatch.setattr(am, "AUTH_LOCKED_COOLDOWN_SEC", 3600)
    monkeypatch.setattr(am, "AUTH_LOCKED_COOLDOWN_MAX_SEC", 14400)
    return d


def _force_expired(host: str, port: int, user: str) -> None:
    """把熔断文件改成「已过期」，模拟冷却期满后的再次撞锁。"""
    p = am._lock_state_path(host, port, user)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["until"] = time.time() - 5
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_wrong_password_is_not_upstream_locked():
    """密码错误原文带 code=4 / 'may be locked'，但**不是**账号已锁。"""
    assert am._is_upstream_locked_reason(WRONG_PWD_ERR) is False
    assert am._is_upstream_locked_reason(LOCKED_ERR) is True


def test_locked_cooldown_and_doubling(cache_dir):
    am._write_lock_state(HOST, PORT, LOCKED_ERR, USER)
    st = am._read_lock_state(HOST, PORT, USER)
    assert st and st["upstream_locked"] is True
    assert st["hits"] == 1 and st["cooldown_sec"] == 3600

    _force_expired(HOST, PORT, USER)
    # 过期后应返回 None，但文件要保留（撞锁计数不能丢）
    assert am._read_lock_state(HOST, PORT, USER) is None
    assert am._lock_state_path(HOST, PORT, USER).exists()

    am._write_lock_state(HOST, PORT, LOCKED_ERR, USER)
    st2 = am._read_lock_state(HOST, PORT, USER)
    assert st2 and st2["hits"] == 2 and st2["cooldown_sec"] == 7200


def test_locked_cooldown_capped(cache_dir, monkeypatch):
    monkeypatch.setattr(am, "AUTH_LOCKED_COOLDOWN_SEC", 4000)
    monkeypatch.setattr(am, "AUTH_LOCKED_COOLDOWN_MAX_SEC", 9000)
    for _ in range(5):
        am._write_lock_state(HOST, PORT, LOCKED_ERR, USER)
        _force_expired(HOST, PORT, USER)
    am._write_lock_state(HOST, PORT, LOCKED_ERR, USER)
    st = am._read_lock_state(HOST, PORT, USER)
    assert st["cooldown_sec"] == 9000  # 不被翻倍冲破上限


def test_wrong_password_uses_short_cooldown(cache_dir):
    am._write_lock_state(HOST, PORT, WRONG_PWD_ERR, USER)
    st = am._read_lock_state(HOST, PORT, USER)
    assert st["upstream_locked"] is False and st["cooldown_sec"] == 1800


def test_clear_lock_state_removes_file(cache_dir):
    am._write_lock_state(HOST, PORT, LOCKED_ERR, USER)
    assert am.get_lock_state(HOST, PORT, USER) is not None
    am.clear_lock_state(HOST, PORT, USER)
    assert am.get_lock_state(HOST, PORT, USER) is None


def test_do_login_runs_role_guard(monkeypatch):
    """_do_login 必须在发请求前调用角色守卫（防止有人把守卫从登录入口拆掉）。"""
    def _block(host, port=0, caller=""):
        raise PermissionError("blocked-by-role")

    monkeypatch.setattr(am, "assert_upstream_login_allowed", _block)
    with pytest.raises(PermissionError):
        am._do_login(HOST, PORT, USER, "p")


def test_clear_lock_endpoint(cache_dir, monkeypatch):
    """设置页「清除熔断」接口：存在则清除并回报 had_lock，不存在也返回 ok。"""
    from backend.routers import credentials as cr

    class _Req:
        headers = {"accept-language": "zh"}

    monkeypatch.setattr(cr, "resolve_endpoint", lambda key: {"host": HOST, "port": PORT, "source": "test"})
    monkeypatch.setattr(cr, "get_credential", lambda *a, **k: (USER, "p"))

    am._write_lock_state(HOST, PORT, LOCKED_ERR, USER)
    res = cr.clear_lock("ipran_ume", _Req())
    assert res["status"] == "ok" and res["had_lock"] is True
    assert am.get_lock_state(HOST, PORT, USER) is None

    res2 = cr.clear_lock("ipran_ume", _Req())
    assert res2["status"] == "ok" and res2["had_lock"] is False


# ============ 3. auto_retry 不重投密码错误 ============

def test_password_error_is_permanent():
    assert any(k in WRONG_PWD_ERR for k in auto_retry.NON_RETRYABLE_KEYS)
    assert any(k in "用户名或密码错误" for k in auto_retry.NON_RETRYABLE_KEYS)


def test_evaluate_candidate_refuses_password_error(monkeypatch):
    monkeypatch.setattr(auto_retry, "_is_excluded_from_retry", lambda *a, **k: False)
    info = auto_retry._evaluate_candidate(
        "ipran_kpi_sync",
        failed_at=auto_retry.datetime.now().isoformat(),
        error=WRONG_PWD_ERR,
        retry_attempts=0,
        last_retry_at=None,
        now=auto_retry.datetime.now(),
        registry={},
        interval_min=10,
    )
    assert info["will_retry"] is False
    assert "永久性" in info["reason"]


# ============ 4. 凭据读失败不得降级为空口令 ============

def test_credential_read_failure_raises(monkeypatch):
    calls = {"n": 0}

    def _boom(key):
        calls["n"] += 1
        raise RuntimeError("database is locked")

    monkeypatch.setattr(cs, "_read_db", _boom)
    monkeypatch.setattr(cs, "_read_db_with_retry", cs._read_db_with_retry)
    with pytest.raises(cs.CredentialReadError) as ei:
        cs._read_db_with_retry("ipran_ume", attempts=3, base_sleep=0)
    assert calls["n"] == 3
    assert "ipran_ume" in str(ei.value)

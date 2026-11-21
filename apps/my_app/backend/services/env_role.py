# -*- coding: utf-8 -*-
"""运行角色判定 + 「隔离副本不得登录生产上游」守卫。

背景（2026-11-21 iPRAN 账号反复被锁事故）
=========================================
`ipran_kpi_sync` 从 2026-11-20 起频繁把 UME 账号 `noc1` 撞锁，解锁后又被锁。
根因不是生产凭据错，而是**同一台机上还有一套隔离副本 `.smoke`**：

  · `.smoke` 的 `credentials.ipran_ume` 停在 2026-09-24 的旧密码（生产 11-20
    17:19 改过密码），哈希与生产不同；
  · `.smoke` 里 `ipran_kpi_sync` 仍是 `0 * * * *` 且启用；
  · 冒烟栈一起来（11-20 18:00、11-21 13:09 实测），副本里任何一条登录路径
    都会拿旧密码去撞**真实的** 10.100.2.99:28001；
  · 上游策略是「连续 3 次密码错误 → 锁 1440 分钟」，于是生产账号被副本撞锁，
    生产任务每小时去问一次又延长锁定期 —— 表现为「解锁之后又被锁」。

隔离副本存在的意义是**验证部署与界面**，不是采数。它天生持有过期快照
（`provision-smoke-runner.ps1` 只在 `-Apply` 重建时同步三张本地权威表，
副本放久就与生产分叉），所以「副本能连生产上游」本身就是个隐患：任何一个
过期的口令都会变成一次真实的失败登录计数，而且损失落在生产账号上。

守卫规则
========
  1. 角色判定：目录里有 `ISOLATED_COPY.flag` 或 `.smoke-runner` → 隔离副本；
     有 `PRODUCTION.flag` → 生产；都没有 → 未知（按生产放行，保持既有行为，
     因为大量独立脚本/测试就在仓库外跑）。
  2. 隔离副本内**禁止向非回环地址发起登录**；确需临时验证时用环境变量
     `ISOLATED_ALLOW_UPSTREAM_LOGIN=1` 显式放行，或用
     `ISOLATED_LOGIN_ALLOW_HOSTS=host1,host2` 只放行指定主机。
  3. 拦截发生在**发请求之前**，抛出的异常带明确中文原因，任务日志/告警
     能直接看出「是副本被拦了」，而不是误报成上游密码错。

⚠ 为什么放在登录入口而不是任务调度层：登录入口是所有消费者（任务、手工
触发器、凭据测试接口、门户一键登录）的公共瓶颈，逐任务加开关必然漏。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# 回环地址不算「上游」：副本内部自测（假 UME、本地桩）必须放行。
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

# 角色判定结果缓存：标记文件在进程生命周期内不会变，避免每次登录都 stat 磁盘。
_ROLE_CACHE: dict[str, str] = {}


def _repo_root() -> Path:
    """仓库根目录。

    本文件位于 `<repo>/apps/my_app/backend/services/env_role.py`：
    parents[0]=services, [1]=backend, [2]=my_app, [3]=apps, [4]=<repo>。
    允许 `MYAPP_REPO_ROOT` 覆盖，便于测试把「假仓库」指到 tmp 目录。
    """
    override = os.environ.get("MYAPP_REPO_ROOT", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[4]


def detect_role(force: bool = False) -> str:
    """返回 `production` / `isolated` / `unknown`。

    判定顺序：隔离标记优先。`.smoke` 目录同时有 `ISOLATED_COPY.flag` 和
    `.smoke-runner`，两种标记任一存在即视为隔离副本。
    """
    root = _repo_root()
    cached = _ROLE_CACHE.get(str(root))
    if cached and not force:
        return cached

    try:
        isolated = (root / "ISOLATED_COPY.flag").exists() or (root / ".smoke-runner").exists()
        production = (root / "PRODUCTION.flag").exists()
    except OSError:  # 权限异常等：按未知处理，不因角色判定挡住生产
        logger.debug("[ROLE] 读取角色标记失败，按未知角色处理", exc_info=True)
        isolated = production = False

    if isolated:
        role = "isolated"
    elif production:
        role = "production"
    else:
        role = "unknown"
    _ROLE_CACHE[str(root)] = role
    return role


def is_isolated_copy() -> bool:
    return detect_role() == "isolated"


def _allow_hosts() -> set[str]:
    raw = os.environ.get("ISOLATED_LOGIN_ALLOW_HOSTS", "")
    return {h.strip() for h in raw.split(",") if h.strip()}


def login_blocked_reason(host: str) -> str | None:
    """该主机在**当前角色**下是否禁止登录；返回拦截原因，放行返回 None。"""
    if not is_isolated_copy():
        return None
    if os.environ.get("ISOLATED_ALLOW_UPSTREAM_LOGIN", "").strip() in ("1", "true", "yes"):
        return None
    target = (host or "").strip().lower()
    if not target or target in _LOOPBACK_HOSTS:
        return None
    if target in {h.lower() for h in _allow_hosts()}:
        return None
    return (
        f"本目录是隔离副本（{_repo_root()} 下有 ISOLATED_COPY.flag/.smoke-runner），"
        f"已按策略拒绝对生产上游 {host} 发起登录：副本里的凭据是过期快照，"
        f"一次错误口令就会消耗上游的失败计数，甚至把生产账号撞锁。"
        f"确需临时验证请设置 ISOLATED_ALLOW_UPSTREAM_LOGIN=1，"
        f"或用 ISOLATED_LOGIN_ALLOW_HOSTS={host} 仅放行该主机。"
    )


def assert_upstream_login_allowed(host: str, port: int | str = "", caller: str = "") -> None:
    """登录前调用：隔离副本向生产上游登录时抛异常（在发请求之前）。

    抛 `PermissionError` 而不是 `AssertionError`：前者在任务结果里读起来是
    「被策略拒绝」，不会被误当成代码断言失败。
    """
    reason = login_blocked_reason(host)
    if not reason:
        return
    where = f"{caller} → {host}:{port}" if caller else f"{host}:{port}"
    logger.warning("[ROLE] 已拦截隔离副本的上游登录（%s）：%s", where, reason)
    raise PermissionError(f"[ROLE] 隔离副本禁止登录生产上游（{where}）：{reason}")


def describe() -> dict:
    """诊断用：当前角色、根目录、放行清单（不含任何敏感值）。"""
    return {
        "role": detect_role(force=True),
        "root": str(_repo_root()),
        "allow_hosts": sorted(_allow_hosts()),
        "env_override": bool(os.environ.get("ISOLATED_ALLOW_UPSTREAM_LOGIN", "").strip()),
    }

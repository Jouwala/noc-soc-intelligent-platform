# -*- coding: utf-8 -*-
"""号码查询权限 API（Settings → 号码查询权限 页签）。2026-11-23 新增。

GET /api/phone-query-acl   → {config, defaults, editable}（需登录；本机回环视为已授权，
                             whatsapp-kb-bot 从本机无凭据读取，靠回环判据放行）
PUT /api/phone-query-acl   → 更新名单（本机 / 持 X-API-Token / 角色为 admin 的登录用户）

名单的用途与字段见 backend/services/phone_query_acl.py。
本模块另提供 require_phone_query_allowed()：给直连号码查询接口
（/api/vmax-cca-query、/api/vmax-diagnosis）复用的名单守卫（Issue #241）。
"""
import logging

from fastapi import APIRouter, HTTPException, Request

from backend import auth_switch
from backend.security import is_privileged
from backend.services import phone_query_acl
from backend.services.portal import portal_auth

logger = logging.getLogger(__name__)

router = APIRouter(tags=["phone-query-acl"])


def require_phone_query_allowed(request: Request) -> None:
    """直连号码查询接口的名单守卫：不在 web_users 名单内 → 403 + 配置的拒绝文案。

    放行口径与对话面（机器人/问答）一致（services/phone_query_acl.check_web_user）：
    名单为空、拿不到登录用户名时一律拒绝（fail-closed），不返回任何号码数据。
    本机回环 / 持 X-API-Token 的机器间调用放行（问答服务、机器人从本机取数，
    与机器人侧「无 caller 不受限」同义）；开发免登录模式下整体鉴权已关，同样放行。
    """
    if is_privileged(request) or auth_switch.is_auth_bypass():
        return
    user = portal_auth.current_user(request) or {}
    allowed, reason = phone_query_acl.check_web_user(user.get("username"))
    if not allowed:
        logger.info("[phone-acl] 直连接口拒绝：user=%s reason=%s",
                    user.get("username") or "?", reason)
        raise HTTPException(
            status_code=403,
            detail=phone_query_acl.get_config()["deny_text_zh"],
        )


def _can_edit(request: Request) -> bool:
    """本机 / 持令牌（与 require_protected 同判据），或门户登录角色为 admin。"""
    if is_privileged(request):
        return True
    user = portal_auth.current_user(request) or {}
    return str(user.get("role") or "").lower() == "admin"


@router.get("/api/phone-query-acl")
def get_phone_query_acl(request: Request):
    # N1（Issue #241）：此前本端点自身无鉴权、仅靠全局中间件兜底，与 docstring
    # 写的「登录用户可读」不符；现显式要求登录。require_user 对本机回环返回
    # 占位身份，whatsapp-kb-bot 从本机无凭据读取不受影响。
    portal_auth.require_user(request)
    return {
        "config": phone_query_acl.get_config(),
        "defaults": phone_query_acl.DEFAULTS,
        "editable": _can_edit(request),
    }


@router.put("/api/phone-query-acl")
def update_phone_query_acl(body: dict, request: Request):
    if not _can_edit(request):
        raise HTTPException(status_code=403, detail="Forbidden: 只有管理员可以修改号码查询权限")
    cfg = phone_query_acl.update_config(body)
    return {"success": True, "config": cfg}

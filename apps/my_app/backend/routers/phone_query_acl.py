# -*- coding: utf-8 -*-
"""号码查询权限 API（Settings → 号码查询权限 页签）。2026-11-23 新增。

GET /api/phone-query-acl   → {config, defaults, editable}（登录用户可读；whatsapp-kb-bot 从本机读）
PUT /api/phone-query-acl   → 更新名单（本机 / 持 X-API-Token / 角色为 admin 的登录用户）

名单的用途与字段见 backend/services/phone_query_acl.py。
"""
from fastapi import APIRouter, HTTPException, Request

from backend.security import is_privileged
from backend.services import phone_query_acl
from backend.services.portal import portal_auth

router = APIRouter(tags=["phone-query-acl"])


def _can_edit(request: Request) -> bool:
    """本机 / 持令牌（与 require_protected 同判据），或门户登录角色为 admin。"""
    if is_privileged(request):
        return True
    user = portal_auth.current_user(request) or {}
    return str(user.get("role") or "").lower() == "admin"


@router.get("/api/phone-query-acl")
def get_phone_query_acl(request: Request):
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

# -*- coding: utf-8 -*-
"""号码查询权限（Settings → 号码查询权限 页签）。2026-11-23 新增。

WhatsApp 机器人 / 门户问答助手里，「TEL + 手机号」会实时查 VMAX CCA 的用户信息
（IMSI/IMEI/机型/流量/驻留小区）。这类电话信息只给名单里的人查，其他人回固定话术；
其余问题不受影响。

名单存 ``backend/services/phone_query_acl.json``，页面 GET/PUT 即时生效。
whatsapp-kb-bot 经 ``GET /api/phone-query-acl`` 读取（src/phoneAcl.js，缓存 60 秒），
所以改完最多一分钟生效，不用重启机器人。

字段：
  enabled            bool       总开关；关掉 = 所有人都能查（不推荐）
  web_users          list[str]  门户问答助手里允许查号码的登录用户名（不区分大小写）
  whatsapp_numbers   list[str]  WhatsApp 里允许查号码的手机号（含国家码；+ / 空格 / 横线可有可无）
  deny_text_zh       str        无权限时的中文回复
  deny_text_en       str        无权限时的英文回复（英文提问时用）
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_FILE = Path(__file__).resolve().parent / "phone_query_acl.json"

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "web_users": ["admin"],
    "whatsapp_numbers": ["+8615638281623"],
    "deny_text_zh": "你无权查询该信息，请找admin/zeeshan进行查询",
    "deny_text_en": "You are not authorized to query this information. Please contact admin/zeeshan.",
}

_MAX_ITEMS = 200
_MAX_ITEM_LEN = 64
_MAX_TEXT_LEN = 500

_lock = threading.Lock()
_cached: dict | None = None


def _clean_list(value: Any) -> list[str]:
    """规整成去重的字符串列表；接受 list 或逗号/空白分隔的字符串。"""
    if isinstance(value, str):
        items = value.replace("，", ",").replace(";", ",").replace("\n", ",").split(",")
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        s = str(raw or "").strip()[:_MAX_ITEM_LEN]
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= _MAX_ITEMS:
            break
    return out


def _clean(cfg: dict) -> dict:
    merged = {**DEFAULTS, **(cfg or {})}
    return {
        "enabled": bool(merged.get("enabled", True)),
        "web_users": _clean_list(merged.get("web_users")),
        "whatsapp_numbers": _clean_list(merged.get("whatsapp_numbers")),
        "deny_text_zh": (str(merged.get("deny_text_zh") or "").strip() or DEFAULTS["deny_text_zh"])[:_MAX_TEXT_LEN],
        "deny_text_en": (str(merged.get("deny_text_en") or "").strip() or DEFAULTS["deny_text_en"])[:_MAX_TEXT_LEN],
    }


def _load_from_file() -> dict:
    try:
        if _CONFIG_FILE.is_file():
            with open(_CONFIG_FILE, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取号码查询权限配置失败，使用默认值: %s", exc)
    return {}


def _save_to_file(cfg: dict) -> None:
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(_CONFIG_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(_CONFIG_FILE))


def get_config(force_refresh: bool = False) -> dict:
    """当前生效配置（文件 > 默认）。线程安全。"""
    global _cached
    if not force_refresh and _cached is not None:
        return dict(_cached)
    file_cfg = _load_from_file()
    with _lock:
        _cached = _clean(file_cfg)
    return dict(_cached)


def update_config(patch: dict) -> dict:
    """合并 patch → 规整 → 写文件 → 返回新配置。只接受已知字段。"""
    global _cached
    allowed = {k: v for k, v in (patch or {}).items() if k in DEFAULTS}
    with _lock:
        current = _cached if _cached is not None else _clean(_load_from_file())
        new_cfg = _clean({**current, **allowed})
        _save_to_file(new_cfg)
        _cached = new_cfg
    logger.info(
        "[phone-acl] 已更新：enabled=%s web_users=%d whatsapp_numbers=%d",
        new_cfg["enabled"], len(new_cfg["web_users"]), len(new_cfg["whatsapp_numbers"]),
    )
    return dict(new_cfg)

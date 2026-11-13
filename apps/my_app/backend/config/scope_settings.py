# -*- coding: utf-8 -*-
"""
内外网任务 scope 开关（v6 §4.2）
===============================

my_app 内外网分离改造：本机开关决定本机 worker 是否认领某 exec_scope 的任务。
存储在本机 backend/data/settings.json（不共享 PG——A/B 各自读自己的开关，
避免互相覆盖）。

取值：
- scope_internal_enabled：是否在本机认领/调度 internal 任务（默认 True）
- scope_external_enabled：是否在本机认领/调度 external 任务（默认 False）

v6 §4.2 优先级：
1. 环境变量 MY_APP_INTERNAL_TASKS / MY_APP_EXTERNAL_TASKS（0/1/false/true）—— 临时调试
2. settings.json
3. 默认值（internal=True, external=False）

⚠ 每次实时读取（remote_host.py:16 注释警告：不要 import 成模块级常量）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_SCOPE_INTERNAL = True   # 内网任务本机默认开
DEFAULT_SCOPE_EXTERNAL = False  # 外网任务本机默认关（只有 B 机开启）

_SETTINGS_FILE = Path(__file__).resolve().parent.parent / "data" / "settings.json"


def _env_truthy(name: str) -> bool | None:
    """读环境变量：1/true/yes/on 视为 True；0/false/no/off 视为 False；其他返回 None"""
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return None
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return None


def _load_settings() -> dict:
    try:
        if _SETTINGS_FILE.is_file():
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def get_scope_switch(scope: str) -> bool:
    """读本机 scope 开关（每次实时读，不缓存）。"""
    assert scope in ("internal", "external"), f"unknown scope: {scope}"
    env_key = "MY_APP_INTERNAL_TASKS" if scope == "internal" else "MY_APP_EXTERNAL_TASKS"
    env_val = _env_truthy(env_key)
    if env_val is not None:
        return env_val
    data = _load_settings()
    key = f"scope_{scope}_enabled"
    if key in data:
        return bool(data[key])
    return DEFAULT_SCOPE_INTERNAL if scope == "internal" else DEFAULT_SCOPE_EXTERNAL


def set_scope_switch(scope: str, enabled: bool) -> None:
    """原子写 settings.json。"""
    assert scope in ("internal", "external"), f"unknown scope: {scope}"
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if _SETTINGS_FILE.is_file():
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data[f"scope_{scope}_enabled"] = bool(enabled)
    tmp = _SETTINGS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _SETTINGS_FILE)


def task_enabled_by_scope(task_id: str) -> bool:
    """判断某任务在本机是否被允许执行：基于 _task_registry[task_id]['exec_scope'] 组合 get_scope_switch。"""
    # 延迟 import 避免循环依赖（task_runner 在 import 时初始化 _task_registry）
    from backend.services.task_runner import _task_registry
    task = _task_registry.get(task_id, {})
    scope = task.get("exec_scope", "internal")
    return get_scope_switch(scope)


def scope_allowed_task_ids(scope: str) -> set:
    """返回本机 scope 开关允许的 task_id 集合（v6 §4.4.1 _claim_next 用）。"""
    from backend.services.task_runner import _task_registry
    if not get_scope_switch(scope):
        return set()
    return {tid for tid, t in _task_registry.items() if t.get("exec_scope", "internal") == scope}


def get_scope_env_summary() -> dict:
    """返回当前生效配置的来源摘要（GET /api/tasks/scopes 用）—— 不含敏感值，只含 key 名。"""
    return {
        "env_internal": os.environ.get("MY_APP_INTERNAL_TASKS", ""),
        "env_external": os.environ.get("MY_APP_EXTERNAL_TASKS", ""),
        "settings_file": str(_SETTINGS_FILE),
        "default_internal": DEFAULT_SCOPE_INTERNAL,
        "default_external": DEFAULT_SCOPE_EXTERNAL,
    }

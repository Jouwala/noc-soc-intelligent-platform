# -*- coding: utf-8 -*-
"""Weathermap 手工布局存储（按拓扑层一份，落 JSON 文件）。

为什么是文件而不是新表
----------------------
布局是**一张图一份、几十 KB、整份替换**的配置，不是需要事务和查询的业务数据；
`backend/data/ui_menu_layout.json`（侧栏菜单）已经用同样的「JSON + 原子替换」模式，
这里沿用，避免为一张小配置加迁移、加表、加回滚。

结构
----
    {"v": 1,
     "layers": {"ipran": {"nodes": {"<node-id>": [x, y]},
                          "updated_at": "2026-11-19T17:40:00"}}}

坐标是前端 760×540 逻辑画布上的值（WeathermapCanvas 的 viewBox 单位），
前端拖动时存的就是它，读回来直接覆盖自动布局的位置。
"""
import json
import os
import tempfile
import threading
from datetime import datetime
from typing import Any, Optional

from backend.services.atomic_write import atomic_replace_file

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# backend/services/ipran_topo -> backend
PATH = os.path.join(BACKEND_DIR, "data", "ipran_topo_layout.json")

MAX_NODES = 500
MAX_ID_LEN = 128
MIN_COORD = -2000.0
MAX_COORD = 4000.0

_lock = threading.Lock()


def _read_all() -> dict:
    if not os.path.exists(PATH):
        return {"v": 1, "layers": {}}
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("layers"), dict):
            return {"v": 1, "layers": {}}
        return data
    except Exception:  # noqa: BLE001 —— 坏文件按「没有布局」处理，不让整页打不开
        return {"v": 1, "layers": {}}


def _write_all(data: dict) -> None:
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="ipran_topo_layout_", suffix=".tmp",
                               dir=os.path.dirname(PATH))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        atomic_replace_file(tmp, PATH)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def normalize(nodes: Any) -> dict:
    """校验并归一化 {id: [x, y]}；非法输入抛 ValueError（路由层转 400）。"""
    if nodes is None:
        return {}
    if not isinstance(nodes, dict):
        raise ValueError("nodes 必须是对象")
    if len(nodes) > MAX_NODES:
        raise ValueError(f"节点数超过上限 {MAX_NODES}")
    out: dict = {}
    for raw_id, pos in nodes.items():
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise ValueError("节点 id 必须是非空字符串")
        nid = raw_id.strip()
        if len(nid) > MAX_ID_LEN:
            raise ValueError(f"节点 id 超过 {MAX_ID_LEN} 字符")
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            raise ValueError(f"节点 {nid} 的坐标必须是 [x, y]")
        try:
            x = float(pos[0])
            y = float(pos[1])
        except (TypeError, ValueError):
            raise ValueError(f"节点 {nid} 的坐标不是数字")
        if not (MIN_COORD <= x <= MAX_COORD and MIN_COORD <= y <= MAX_COORD):
            raise ValueError(f"节点 {nid} 的坐标超出范围")
        out[nid] = [round(x, 1), round(y, 1)]
    return out


def read_layer(layer: str) -> tuple[dict, Optional[str]]:
    with _lock:
        data = _read_all()
    entry = (data.get("layers") or {}).get(layer)
    if not isinstance(entry, dict):
        return {}, None
    nodes = entry.get("nodes")
    if not isinstance(nodes, dict):
        return {}, None
    return nodes, entry.get("updated_at")


def save_layer(layer: str, raw_nodes: Any) -> tuple[dict, str]:
    nodes = normalize(raw_nodes)
    updated = datetime.now().isoformat(timespec="seconds")
    with _lock:
        data = _read_all()
        data.setdefault("layers", {})[layer] = {"nodes": nodes, "updated_at": updated}
        data["v"] = 1
        _write_all(data)
    return nodes, updated


def reset_layer(layer: str) -> None:
    with _lock:
        data = _read_all()
        layers = data.get("layers") or {}
        if layer in layers:
            layers.pop(layer, None)
            data["layers"] = layers
            _write_all(data)

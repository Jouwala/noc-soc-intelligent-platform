# -*- coding: utf-8 -*-
"""UME 拓扑图 API（四层共用，拓扑图数据一次返回，前端只画不算）。

端点
----
- `GET /api/ipran-topo/layers`                     页签清单（key / 中英名 / 是否有 UME 人工布局）
- `GET /api/ipran-topo/graph?layer=ipran&refresh=0` 完整图数据（网元 + 折叠后的边 + 元信息）

`layer` 走 `layers.LAYERS` 白名单，非法值直接 400，**不接受前端传任意 lid**。
不带 layer 时默认 `ipran`（IP RAN 光纤层），现有页面与旧调用方式行为不变。

`refresh=1` 绕过该层图缓存并重载站点坐标缓存（页面「刷新」按钮用），对应 UME
侧 8 次调用，约 1.5~3.0s；只清当前层，不会把另外三层缓存一起打掉。

`traffic=1` 额外把 KPI 端口收发速率贴到边上（`rxUtil` / `txUtil` / `utilPorts`
+ `meta.traffic`），多读 6 张 15 分钟表（实测 +0.5s）。它**单独占一个缓存槽**：
不传 traffic 的老调用方取到的响应与加这个参数之前逐字段一致。口径见
`backend/services/ipran_topo/topo_traffic.py` 模块 docstring。
取数口径、折叠规则、坐标来源与告警饱和度的说明见
`backend/services/ipran_topo/ipran_topo_service.py` 模块 docstring；层清单与
每层口径见同目录 `layers.py`。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.security import is_privileged, require_protected
from backend.services.ipran_topo import layers
from backend.services.ipran_topo import ipran_topo_service as svc
from backend.services.ipran_topo import topo_layout_store as layout_store
from backend.services.ipran_topo.layers import DEFAULT_LAYER

logger = logging.getLogger("ipran_topo_api")
router = APIRouter(prefix="/api/ipran-topo", tags=["ipran-topo"])


@router.get("/layers")
def list_layers():
    """前端页签清单。刻意不返回 lid，层映射只在后端白名单里。"""
    return {"layers": layers.public_list()}


@router.get("/graph")
def graph(
    layer: str = Query(DEFAULT_LAYER, description="拓扑层 key，见 /layers"),
    refresh: int = Query(0, ge=0, le=1, description="1 = 绕过缓存重新取数"),
    traffic: int = Query(0, ge=0, le=1, description="1 = 附带端口流量利用率（多读 KPI 库）"),
):
    if layers.resolve_layer(layer) is None:
        raise HTTPException(status_code=400, detail=f"未知拓扑层: {layer}")
    try:
        if refresh:
            svc.clear_cache(layer)
            svc.refresh_site_index()
        return svc.build_graph(layer=layer, force=bool(refresh), with_traffic=bool(traffic))
    except Exception as exc:
        logger.exception("[ipran-topo/graph] layer=%s failed: %s", layer, exc)
        return {
            "nodes": [],
            "edges": [],
            "meta": {
                "layer": layer,
                "ne": 0, "ext": 0, "rawLinks": 0, "foldedLinks": 0,
                "tierCounts": {}, "geo": {}, "geoMissSites": [], "geoFuzzyPairs": [],
                "critNe": 0, "cached": False,
                "warnings": [f"load_failed: {exc}"],
            },
        }


# --------------------------- Weathermap 手工布局 ---------------------------
# 存的是「哪个节点摆在哪」的坐标（760×540 逻辑画布），整层一份；读任何人都可以，
# 写只允许本机 / 持 X-API-Token（与侧栏菜单布局同一套 require_protected 策略）。
def _layer_or_400(layer: str):
    spec = layers.resolve_layer(layer)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"未知拓扑层: {layer}")
    return spec


@router.get("/layout")
def get_layout(request: Request, layer: str = Query(DEFAULT_LAYER)):
    """读取该层保存的手工布局；没有保存过时 nodes 为空对象（前端回落自动布局）。"""
    spec = _layer_or_400(layer)
    nodes, updated_at = layout_store.read_layer(spec.key)
    return {
        "layer": spec.key,
        "nodes": nodes,
        "updated_at": updated_at,
        "editable": is_privileged(request),
    }


@router.put("/layout", dependencies=[Depends(require_protected)])
def put_layout(payload: dict, layer: str = Query(DEFAULT_LAYER)):
    """整层覆盖保存 `{nodes: {id: [x, y]}}`。"""
    spec = _layer_or_400(layer)
    try:
        nodes, updated_at = layout_store.save_layer(spec.key, (payload or {}).get("nodes"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    logger.info("[ipran-topo/layout] layer=%s 保存 %d 个节点坐标", spec.key, len(nodes))
    return {"layer": spec.key, "nodes": nodes, "updated_at": updated_at, "editable": True}


@router.delete("/layout", dependencies=[Depends(require_protected)])
def delete_layout(layer: str = Query(DEFAULT_LAYER)):
    """删除该层手工布局，回到自动布局。"""
    spec = _layer_or_400(layer)
    layout_store.reset_layer(spec.key)
    logger.info("[ipran-topo/layout] layer=%s 已重置为自动布局", spec.key)
    return {"layer": spec.key, "nodes": {}, "updated_at": None, "editable": True}

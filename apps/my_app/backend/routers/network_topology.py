# -*- coding: utf-8 -*-
"""门户流量拓扑只读/维护接口（Issue #230）。

  GET  /api/network-topology/traffic?period=live|1h|24h|7d
  GET  /api/network-topology/nodes
  GET  /api/network-topology/links
  GET  /api/network-topology/links/{id}/history?period=...
  GET  /api/network-topology/layout
  PUT  /api/network-topology/layout          {nodes:[{id,x,y,pinned}]}   （仅 admin）
  GET  /api/network-topology/mappings
  PUT  /api/network-topology/mappings        {links:[{id,capacity_ab_mbps,...}]}（仅 admin）

写接口仅管理员可用；非法坐标/容量由 Pydantic 返回 422，service 侧二次校验失败返回 400，
且**先校验后写库**，不会把非法容量清空再镜像到 #231 的 SLA 容量。
容量缺失时利用率返回 null，前端显示「待补全」；绝不按 0 或猜测值计算。
"""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.services.portal import portal_auth
from backend.services.prtg import topology

router = APIRouter(prefix="/api/network-topology", tags=["network-topology"])


class LayoutNode(BaseModel):
    id: int
    x: float = Field(ge=0, le=1000)
    y: float = Field(ge=0, le=620)
    pinned: bool = True


class LayoutPayload(BaseModel):
    nodes: list[LayoutNode]


class MappingLink(BaseModel):
    id: int
    capacity_ab_mbps: float | None = Field(default=None, ge=0)
    capacity_ba_mbps: float | None = Field(default=None, ge=0)
    capacity_source: str | None = None
    link_type: str | None = None
    mapping_confidence: Literal["verified", "candidate", "discovered"] | None = None


class MappingPayload(BaseModel):
    links: list[MappingLink]


class NodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: Literal["device", "peer", "customer", "unknown"] = "unknown"
    x: float | None = Field(default=None, ge=0, le=1000)
    y: float | None = Field(default=None, ge=0, le=620)


class LinkCreate(BaseModel):
    sensor_objid: int
    source_node_id: int
    target_node_id: int
    link_type: str | None = None
    capacity_ab_mbps: float | None = Field(default=None, ge=0)
    capacity_ba_mbps: float | None = Field(default=None, ge=0)
    capacity_source: str = "manual"


class BendPayload(BaseModel):
    bend_offset: float = Field(ge=-300, le=300)


class AliasPayload(BaseModel):
    alias: str = Field(min_length=1, max_length=120)


def _require_admin(request: Request) -> None:
    """布局/映射是管理操作：普通登录用户只读。"""
    user = portal_auth.current_user(request) or {}
    if str(user.get("role") or "").strip().lower() != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可修改拓扑布局或映射")


@router.get("/traffic")
def network_topology_traffic(period: str = Query("live", pattern="^(live|1h|24h|7d)$")):
    return topology.traffic(period)


@router.get("/nodes")
def network_topology_nodes():
    return {"nodes": topology.traffic("live")["nodes"]}


@router.get("/links")
def network_topology_links():
    return {"links": topology.traffic("live")["links"]}


@router.get("/links/{link_id}/history")
def network_topology_history(
    link_id: int,
    period: str = Query("24h", pattern="^(live|1h|24h|7d)$"),
):
    data = topology.history(link_id, period)
    if data is None:
        raise HTTPException(status_code=404, detail="链路不存在")
    return data


@router.get("/sensors")
def network_topology_sensors():
    """Phase 2：可绑定的 Traffic 传感器清单（含是否已绑定）。"""
    return topology.sensor_options()


@router.get("/layout")
def network_topology_layout():
    return topology.layout()


@router.put("/layout")
def network_topology_update_layout(payload: LayoutPayload, request: Request):
    _require_admin(request)
    return topology.update_layout([node.model_dump() for node in payload.nodes])


@router.get("/mappings")
def network_topology_mappings():
    return topology.mappings()


@router.put("/mappings")
def network_topology_update_mappings(payload: MappingPayload, request: Request):
    _require_admin(request)
    try:
        return topology.update_mappings([link.model_dump(exclude_unset=True) for link in payload.links])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/nodes", status_code=201)
def network_topology_create_node(payload: NodeCreate, request: Request):
    _require_admin(request)
    try:
        return topology.create_node(payload.name, payload.type, payload.x, payload.y)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/nodes/{node_id}")
def network_topology_delete_node(node_id: int, request: Request):
    _require_admin(request)
    try:
        return topology.delete_node(node_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/links", status_code=201)
def network_topology_create_link(payload: LinkCreate, request: Request):
    _require_admin(request)
    try:
        return topology.create_link(
            payload.sensor_objid, payload.source_node_id, payload.target_node_id,
            payload.link_type, payload.capacity_ab_mbps, payload.capacity_ba_mbps,
            payload.capacity_source,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/links/{link_id}")
def network_topology_delete_link(link_id: int, request: Request):
    _require_admin(request)
    try:
        return topology.delete_link(link_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/links/{link_id}/direction")
def network_topology_swap_direction(link_id: int, request: Request):
    """确认上下游：交换链路 A/B 两端与分方向容量。"""
    _require_admin(request)
    try:
        return topology.swap_direction(link_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/links/{link_id}/bend")
def network_topology_set_bend(link_id: int, payload: BendPayload, request: Request):
    _require_admin(request)
    try:
        return topology.set_bend(link_id, payload.bend_offset)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/nodes/{node_id}/aliases", status_code=201)
def network_topology_add_alias(node_id: int, payload: AliasPayload, request: Request):
    _require_admin(request)
    try:
        return topology.add_alias(node_id, payload.alias)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

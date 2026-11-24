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

from backend import portal_auth
from backend.services.prtg import topology

router = APIRouter(prefix="/api/network-topology", tags=["network-topology"])


class LayoutNode(BaseModel):
    id: int
    x: float = Field(ge=0, le=1000)
    y: float = Field(ge=0, le=1000)
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

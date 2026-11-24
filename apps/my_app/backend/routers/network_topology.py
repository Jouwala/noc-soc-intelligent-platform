# -*- coding: utf-8 -*-
"""门户流量拓扑只读/维护接口（Issue #230）。

  GET  /api/network-topology/traffic?period=live|1h|24h|7d
  GET  /api/network-topology/nodes
  GET  /api/network-topology/links
  GET  /api/network-topology/links/{id}/history?period=...
  GET  /api/network-topology/layout
  PUT  /api/network-topology/layout          {nodes:[{id,x,y,pinned}]}
  GET  /api/network-topology/mappings
  PUT  /api/network-topology/mappings        {links:[{id,capacity_ab_mbps,...}]}

容量缺失时利用率返回 null，前端显示「待补全」；绝不按 0 或猜测值计算。
"""
from fastapi import APIRouter, HTTPException, Query

from backend.services.prtg import topology

router = APIRouter(prefix="/api/network-topology", tags=["network-topology"])


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
def network_topology_update_layout(payload: dict):
    items = payload.get("nodes") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="nodes 必须是数组")
    return topology.update_layout(items)


@router.get("/mappings")
def network_topology_mappings():
    return topology.mappings()


@router.put("/mappings")
def network_topology_update_mappings(payload: dict):
    items = payload.get("links") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="links 必须是数组")
    return topology.update_mappings(items)

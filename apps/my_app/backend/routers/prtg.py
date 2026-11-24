# -*- coding: utf-8 -*-
"""PRTG 链路健康度看板只读接口（Issue #227 / #228）。

端点：
  GET /api/prtg/overview?window=5m|24h|7d|30d   KPI + 全网速率趋势
  GET /api/prtg/links?window=...                25 条链路总览
  GET /api/prtg/links/{sensor_id}?window=...    单链路详情（通道时序/事件/映射）
  GET /api/prtg/anomalies?window=...            异常事件（#229 规则 + Ping 状态机）
  GET /api/prtg/collector/status                采集同步状态（排障用）

全部只读；取不到数据返回 null/空数组，前端显示「—」，不补 0。
"""
from fastapi import APIRouter, HTTPException, Query

from backend.services.prtg import dashboard

router = APIRouter(prefix="/api/prtg", tags=["prtg"])


@router.get("/overview")
def prtg_overview(window: str = Query("24h", description="5m / 24h / 7d / 30d")):
    return dashboard.overview(window)


@router.get("/links")
def prtg_links(window: str = Query("24h", description="5m / 24h / 7d / 30d")):
    return dashboard.links(window)


@router.get("/links/{sensor_id}")
def prtg_link_detail(sensor_id: int, window: str = Query("24h")):
    detail = dashboard.link_detail(sensor_id, window)
    if detail is None:
        raise HTTPException(status_code=404, detail="PRTG 传感器不存在")
    return detail


@router.get("/anomalies")
def prtg_anomalies(
    window: str = Query("24h", description="5m / 24h / 7d / 30d"),
    open_only: bool = Query(True, description="只看未结束事件"),
    limit: int = Query(200, ge=1, le=1000),
):
    """异常事件（#229 规则 + Ping 状态机），按开始时间倒序。"""
    return dashboard.anomalies(window, open_only=open_only, limit=limit)


@router.get("/sla")
def prtg_sla(
    month: str | None = Query(None, description="YYYY-MM，默认当月"),
    target: float = Query(99.5, ge=0, le=100, description="可用率目标 %"),
):
    """月度 SLA：可用率、故障时长、Down 次数、区域排名、低于目标清单、容量利用率。"""
    from backend.services.prtg import sla

    return sla.month_report(month, target)


@router.get("/capacity")
def prtg_capacity():
    """链路容量登记与利用率（无容量的链路 utilization 为 null）。"""
    from backend.services.prtg import sla

    return {"generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "capacity": sla.capacity_list()}


@router.get("/collector/status")
def prtg_collector_status():
    """采集各作业的同步状态与最近错误（不含任何凭证）。"""
    from backend.services.prtg import store

    keys = ("catalog", "live", "backfill", "aggregate", "anomaly", "history_import", "prune")
    states = []
    for key in keys:
        state = store.get_state(key)
        if state is None:
            states.append({"state_key": key, "status": "never"})
            continue
        states.append({
            "state_key": state["state_key"],
            "status": state["status"],
            "last_run_at": state["last_run_at"].isoformat() + "Z" if state["last_run_at"] else None,
            "last_success_at": state["last_success_at"].isoformat() + "Z" if state["last_success_at"] else None,
            "detail": state["detail"],
            "error": state["error"],
        })
    return {"collector": states}

# -*- coding: utf-8 -*-
"""RAN KPI 15 分钟看板 API（?page=ran_kpi_15mins）。

数据源与口径见 backend/services/ran_kpi_assurance/ran_kpi_15mins_service.py 顶部说明。

GET /api/ran-kpi-15mins/overview?tech=4G
    ?force=1    跳过 5 分钟缓存，强制重新取数
    -> {meta:{db,table,tech,count,failed,empty,elapsed_ms,cached,latest},
        tech,title,
        widgets:[{id,title,metric_name,unit,order,pct,columns,rows,error,empty,empty_reason}]}

GET /api/ran-kpi-15mins/health
    -> {ok, db, table, rows, latest} | {ok:false, error}

说明：窗口固定为「今天 + 昨天 + 7 天前」（Day0/Day1/Day7 叠画），由 SQL 里的
date('now') 决定，随日期自然滚动。4 个制式（2G/3G/4G/5G）由页面页签按需取，
不一次拉 66 张图。
"""
import logging

from fastapi import APIRouter, Query

from backend.services.ran_kpi_assurance import ran_kpi_15mins_service as svc

logger = logging.getLogger("ran_kpi_15mins")

router = APIRouter(prefix="/api/ran-kpi-15mins", tags=["ran-kpi-15mins"])


@router.get("/overview")
def overview(
    tech: str = Query("4G", description="2G / 3G / 4G / 5G"),
    force: int = Query(0, description="1=跳过缓存强制刷新"),
):
    """单个制式的整页数据（按 15 分钟粒度画 Day0/Day1/Day7）。"""
    return svc.get_overview(tech=tech.upper(), force=bool(force))


@router.get("/health")
def health():
    """远端 ran_kpi_15mins 表连通性与最新数据时间。"""
    return svc.health()

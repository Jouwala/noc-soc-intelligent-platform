# -*- coding: utf-8 -*-
"""CS / PS CN KPI 15 分钟看板 API（`?page=cs_cn_kpi_15mins` / `?page=ps_cn_kpi_15mins`）。

GET /api/cn-kpi-15mins/{board}/overview
    board = cs | ps
    &force=1    跳过 300s 缓存
    -> {meta:{db,host,board,count,failed,empty,elapsed_ms,cached,window:{d0,d1,d7}},
        title, widgets:[{id,title,chart,pivot,pct,unit,grain,dims,source_table,
                         sql,columns,rows,error,empty,empty_code}]}

GET /api/cn-kpi-15mins/health
    -> {ok, host, db, elapsed_ms} | {ok:false, error}

窗口固定为「今天 + 昨天 + 7 天前」（D0/D1/D7 叠画），横轴为 15 分钟桶，
因此本端点**不接收**日期区间 —— 三个日子由 SQL 的 date('now') 系列函数决定。
口径与组件清单见 backend/services/cn/cn_kpi_15mins_widgets.py 顶部说明。
"""
import logging

from fastapi import APIRouter, HTTPException, Query

from backend.services.cn import cn_kpi_15mins_service as svc
from backend.services.cn.cn_kpi_15mins_widgets import BOARDS

logger = logging.getLogger("cn_kpi_15mins")

router = APIRouter(prefix="/api/cn-kpi-15mins", tags=["cn-kpi-15mins"])


@router.get("/{board}/overview")
def overview(board: str, force: int = Query(0, description="1=跳过缓存强制刷新")):
    """整页数据：只含 UME 指标，每张卡按 D0/D1/D7 各一条曲线。"""
    if board not in BOARDS:
        raise HTTPException(404, f"未知看板：{board}（可选：{', '.join(BOARDS)}）")
    return svc.get_overview(board, force=bool(force))


@router.get("/health")
def health():
    """远端只读 API 连通性探测。"""
    return svc.health()

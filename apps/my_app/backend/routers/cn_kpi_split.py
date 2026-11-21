# -*- coding: utf-8 -*-
"""CS / PS CN KPI 分组看板 API（`?page=cs_cn_kpi` / `?page=ps_cn_kpi`）。

GET /api/cn-kpi-split/{board}/overview
    board = cs | ps
    ?start=YYYY-MM-DD     起始日期（含）；留空 = 近 31 天
    &end=YYYY-MM-DD       结束日期（含）；留空 = 今天
    &force=1              跳过 60s 缓存
    -> {meta:{db,host,board,start,end,count,failed,empty,truncated,elapsed_ms,cached},
        title, sections:[{key:'ems'|'ume', widgets:[...]}]}

GET /api/cn-kpi-split/health
    -> {ok, host, db, elapsed_ms} | {ok:false, error}

口径与组件清单见 backend/services/cn/cn_kpi_split_widgets.py 顶部说明；
取数/缓存见 backend/services/cn/cn_kpi_split_service.py。
"""
import logging

from fastapi import APIRouter, HTTPException, Query

from backend.services.cn import cn_kpi_split_service as svc
from backend.services.cn.cn_kpi_split_widgets import BOARDS

logger = logging.getLogger("cn_kpi_split")

router = APIRouter(prefix="/api/cn-kpi-split", tags=["cn-kpi-split"])


@router.get("/{board}/overview")
def overview(
    board: str,
    start: str = Query("", description="起始日期 YYYY-MM-DD（含），留空=近31天"),
    end: str = Query("", description="结束日期 YYYY-MM-DD（含），留空=今天"),
    force: int = Query(0, description="1=跳过缓存强制刷新"),
):
    """整页数据：按 EMS / UME 两段返回组件。"""
    if board not in BOARDS:
        raise HTTPException(404, f"未知看板：{board}（可选：{', '.join(BOARDS)}）")
    try:
        return svc.get_overview(board, start=start or None, end=end or None,
                                force=bool(force))
    except ValueError as e:
        # 日期格式非法 / 起止倒置 —— 属客户端错误
        raise HTTPException(400, str(e)) from e


@router.get("/health")
def health():
    """远端只读 API 连通性探测。"""
    return svc.health()

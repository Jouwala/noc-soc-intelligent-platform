"""TT 州维度统计（`?page=tt_state`）的只读 API。

两个端点共用 `services/tt_state_matrix.py` 的同一份取数函数：
- `GET /api/tt-state-matrix/overview`     州 × 日/周/月矩阵 + 汇总
- `GET /api/tt-state-matrix/drilldown`    某个「州 × 时间桶」的明细

口径（基础集合、时间字段、告警未清除、WO 未关闭）全部写在 service 的模块 docstring，
页面上的「口径说明」与它逐条对应。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from backend.services import tt_state_matrix as svc

log = logging.getLogger("tt_state_matrix")
router = APIRouter(prefix="/api/tt-state-matrix", tags=["tt-state-matrix"])


def _overview_error(exc: Exception, gran: str) -> dict:
    log.exception("tt-state-matrix overview failed: %s", exc)
    payload = svc._empty(gran, "", "", [str(exc)])
    payload["error"] = str(exc)
    return payload


@router.get("/overview")
def overview(
    start: str = Query("", max_length=10, description="YYYY-MM-DD，含当天"),
    end: str = Query("", max_length=10, description="YYYY-MM-DD，含当天"),
    gran: str = Query("day", max_length=8, description="day | week | month"),
    all_time: int = Query(0, alias="all", ge=0, le=1, description="1 = 全部时间，忽略 start/end"),
):
    try:
        return svc.overview(start, end, gran, all_time=bool(all_time))
    except Exception as exc:  # noqa: BLE001 - 页面要拿到结构化错误而不是 500
        return _overview_error(exc, gran)


@router.get("/drilldown")
def drilldown(
    start: str = Query("", max_length=10),
    end: str = Query("", max_length=10),
    gran: str = Query("day", max_length=8),
    state: str = Query("", max_length=160, description="州名；* = 不限州；空 = 未标注州"),
    period: str = Query("", max_length=16, description="日 YYYY-MM-DD / 周 YYYY-Www / 月 YYYY-MM；空 = 全部桶"),
    flag: str = Query("", max_length=32, description="alarm_uncleared | wo_unclosed | tt_unclosed | alarm_cleared"),
    all_time: int = Query(0, alias="all", ge=0, le=1),
    limit: int = Query(svc.MAX_DRILL_ROWS, ge=1, le=20000),
):
    try:
        return svc.drilldown(
            start, end, gran, state, period,
            all_time=bool(all_time), limit=limit, flag=flag,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("tt-state-matrix drilldown failed: %s", exc)
        return {**_empty_drill(exc), "error": str(exc)}


def _empty_drill(exc: Exception) -> dict:
    return {
        "meta": {
            "count": 0, "returned": 0, "truncated": False,
            "limit": svc.MAX_DRILL_ROWS, "warnings": [str(exc)],
        },
        "rows": [],
    }

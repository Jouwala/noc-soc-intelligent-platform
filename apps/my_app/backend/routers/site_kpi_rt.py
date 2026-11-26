# -*- coding: utf-8 -*-
"""站点 KPI 实时查询接口（投诉排查用，**不落库**）。

  GET  /api/site-kpi-rt/sites?q=&tech=      站点搜索（4G 查 app.ngi_lte_cell_param，
                                            5G 查 app.ngi_nr_cell_param）
  POST /api/site-kpi-rt/query               {site, days, gran, force, tech} -> {job_id, cached}
  GET  /api/site-kpi-rt/jobs/{job_id}       轮询任务：status/progress/message/result
  POST /api/site-kpi-rt/cache/clear         清空进程内缓存（换站点排查时手动清）
  GET  /api/site-kpi-rt/health              UME 会话可用性（不探活，只看缓存会话）

取数直接打 RAN UME。4G/5G 都优先走**服务端单网元过滤**（`filterlayer=me` +
`uilocations`，只回本站数据，一次查完整个窗口，实测 8 天约 7s）；网元表里查不到该站
（新开站、同步滞后、5G 重名站）时回退拉全网 + 本地过滤，4G 按 `eNodeBId`、5G 按网元名。
5G 的 payload 另需按 PM 模板全量指标构造（否则 UME 直接 499），并下沉到
`grouplayer=me,NRRadioInfrastructure,NRPhysicalCellDU` + `basemotid` 才是小区级
（见 `ran_ume_pm_template`）。
结果只放进程内内存（TTL 600s、最多 6 份），进程重启即全清。

为什么要异步：回退全网时一次 8 天窗口要按天切片拉 8 次全网明细，耗时可达数分钟，
超过前端 axios 的 120s 超时。保留异步是为了让两条路径共用同一套前端轮询，也避免
UME 抖动把请求卡死。
"""
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.services.ran import ran_site_kpi_rt_service as svc

logger = logging.getLogger("site_kpi_rt")

router = APIRouter(prefix="/api/site-kpi-rt", tags=["site-kpi-rt"])


class QueryPayload(BaseModel):
    site: str = Field(min_length=1, description="站点名称或站点 ID（4G: eNodeB ID / 5G: gNB ID）")
    days: int = Field(default=8, ge=1, le=14, description="回溯天数，含今天")
    gran: str = Field(default="auto", description="auto | 15min")
    force: bool = Field(default=False, description="跳过缓存强制重查")
    tech: str = Field(default="4G", description="4G（小区级）| 5G（小区级 NRPhysicalCellDU）")


@router.get("/sites")
def sites(
    q: str = Query("", description="站点名或 ID 关键字"),
    limit: int = Query(50, ge=1, le=200),
    tech: str = Query("4G", description="4G | 5G"),
):
    """按站点名/ID 模糊搜索站点。"""
    tech = tech if tech in ("4G", "5G") else "4G"
    return {"sites": svc.search_sites(q, limit, tech), "tech": tech}


@router.post("/query")
def query(payload: QueryPayload):
    """启动实时取数任务（异步）。"""
    gran = payload.gran if payload.gran in ("auto", "15min") else "auto"
    tech = payload.tech if payload.tech in ("4G", "5G") else "4G"
    try:
        return svc.start_job(payload.site, payload.days, gran, payload.force, tech)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("启动取数任务失败")
        raise HTTPException(status_code=502, detail=f"启动取数失败：{exc}") from exc


@router.get("/jobs/{job_id}")
def job(job_id: str):
    """查询任务状态与结果。"""
    j = svc.get_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return j


@router.post("/cache/clear")
def cache_clear():
    """清空进程内缓存（不涉及任何持久化数据）。"""
    return {"cleared": svc.cache_clear()}


@router.get("/health")
def health():
    """UME 会话可用性检查。"""
    try:
        return {"ok": True, **svc.ping()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

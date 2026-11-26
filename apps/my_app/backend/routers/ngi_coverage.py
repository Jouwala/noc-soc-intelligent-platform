# -*- coding: utf-8 -*-
"""NGI 覆盖网格只读 API（位置页「覆盖 RSRP」图层用）。

- `GET /api/ngi-coverage/meta`  —— 各 kpi 的格点数 / run_id / 范围 / 档位 RSRP 统计
- `GET /api/ngi-coverage/grid?kpi=LTE_RSRP` —— `NGC1` 紧凑二进制（中心点 + 档位），
  带弱 ETag；浏览器同一轮同步内只做 304 协商
- `GET /api/ngi-coverage/point?kpi=&lat=&lon=` —— 单个格点详情（点击地图时查）
- `GET /api/ngi-coverage/lte-cells` —— LTE 工参全量（列式 JSON，约 8 千小区），位置页「LTE 小区」
  扇区图层用；同样带弱 ETag。放在本路由里是为了不再改 main.py 的注册表（同属 NGI 数据）
- `GET /api/ngi-coverage/nr-cells` —— 5G NR 工参全量，列名与 lte-cells 相同，位置页「5G NR 小区」图层用

为什么是二进制、为什么不传 polygon：见 `services/ngi_data/ngi_coverage_api.py` 顶部。
取不到数据时 meta 返回 `{"available": false, "reason": ...}`，前端如实显示原因，
**不伪造空图层**（把「库里没有」画成「全图无覆盖」会误导排障）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from backend.services.ngi_data import ngi_coverage_api as svc
from backend.services.ngi_data import ngi_cell_api as cell_svc

logger = logging.getLogger("ngi_coverage")

router = APIRouter(prefix="/api/ngi-coverage", tags=["ngi-coverage"])


@router.get("/meta")
def meta(refresh: bool = Query(False, description="true = 跳过 60 s 进程内缓存")):
    try:
        return svc.meta(force=refresh)
    except svc.NotPgError as exc:
        return {"available": False, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("覆盖网格 meta 失败: %s", exc)
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}", "error": str(exc)}


@router.get("/grid")
def grid(request: Request, kpi: str = Query(..., min_length=1, max_length=64)):
    try:
        blob = svc.grid_blob(kpi)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"没有 kpi={kpi!r} 的覆盖网格") from None
    except svc.NotPgError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None

    headers = {
        "ETag": blob["etag"],
        # no-cache = 可以缓存，但每次用前先带 If-None-Match 协商；数据每周才变一次，
        # 协商命中只回 304（几十字节），换了一轮同步则自动拿到新包。
        "Cache-Control": "private, no-cache",
        "Vary": "Accept-Encoding",
        "X-Grid-Points": str(blob["points"]),
        "X-Grid-Run-Id": str(blob["run_id"]),
        "Access-Control-Expose-Headers": "ETag, X-Grid-Points, X-Grid-Run-Id",
    }
    if svc.etag_matches(request.headers.get("if-none-match"), blob["etag"]):
        return Response(status_code=304, headers=headers)
    if "gzip" in (request.headers.get("accept-encoding") or "").lower():
        return Response(content=blob["gz"], media_type="application/octet-stream",
                        headers={**headers, "Content-Encoding": "gzip"})
    return Response(content=blob["raw"], media_type="application/octet-stream", headers=headers)


@router.get("/point")
def point(
    kpi: str = Query(..., min_length=1, max_length=64),
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    grid_size_m: int | None = Query(None, ge=1, le=1000),
):
    try:
        return svc.point(kpi, lat, lon, grid_size_m)
    except svc.NotPgError as exc:
        return {"found": False, "kpi": kpi, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("覆盖格点详情失败: %s", exc)
        return {"found": False, "kpi": kpi, "error": f"{type(exc).__name__}: {exc}"}


@router.get("/lte-cells")
def lte_cells(request: Request, refresh: bool = Query(False, description="true = 跳过 60 s 进程内缓存")):
    return _cells_response(request, "LTE", refresh)


@router.get("/nr-cells")
def nr_cells(request: Request, refresh: bool = Query(False, description="true = 跳过 60 s 进程内缓存")):
    """5G NR 工参（app.ngi_nr_cell_param），列名与 /lte-cells 相同（见 ngi_cell_api 顶部）。"""
    return _cells_response(request, "NR", refresh)


def _cells_response(request: Request, rat: str, refresh: bool):
    try:
        entry = cell_svc.cells(rat, force=refresh)
    except cell_svc.NotPgError as exc:
        return {"available": False, "rat": rat, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("%s 小区图层取数失败: %s", rat, exc)
        return {"available": False, "rat": rat, "reason": f"{type(exc).__name__}: {exc}"}

    headers = {
        "ETag": entry["etag"],
        "Cache-Control": "private, no-cache",
        "Vary": "Accept-Encoding",
        "Access-Control-Expose-Headers": "ETag",
    }
    if svc.etag_matches(request.headers.get("if-none-match"), entry["etag"]):
        return Response(status_code=304, headers=headers)
    if "gzip" in (request.headers.get("accept-encoding") or "").lower():
        return Response(content=entry["gz"], media_type="application/json",
                        headers={**headers, "Content-Encoding": "gzip"})
    return Response(content=entry["raw"], media_type="application/json", headers=headers)

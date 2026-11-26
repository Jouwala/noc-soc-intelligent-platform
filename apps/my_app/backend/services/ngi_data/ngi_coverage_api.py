# -*- coding: utf-8 -*-
"""位置页「覆盖 RSRP」图层的只读取数（`app.ngi_coverage_grid` → 紧凑二进制）。

## 为什么不直接把表吐成 JSON（2026-11-24 评估结论，勿删）

一轮同步约 **17.8 万个 50 m 格点**，原 CSV 33.3 MB；`polygon` 列是
"lon lat lon lat …" 文本，整表带 polygon 转 JSON 是几十 MB。位置页地图又是
「一个要素 = 一个 React 组件 + 一个 Leaflet SVG 图层」的写法，17.8 万个 path
会把主线程卡死。所以拆成三件事：

1. **只传画图需要的三列**：`center_lon` / `center_lat` / `bin_index`。
   格点是规则 50 m 方格，前端按中心点 + 网格米数就能还原，`polygon` 不出库。
2. **二进制列式**（见下方 `pack_grid`）：每格点 9 字节，17.8 万点 ≈ 1.6 MB，
   gzip 后更小；前端零解析成本（直接 `Float32Array` 视图）。
3. **按 `run_id` 缓存 + ETag**：网格每周日才全量替换一次，同一周内浏览器
   只做 304 协商，不再重复下载；进程内也只在 `run_id` / 行数变化时重新打包。

画图在前端 `pages/location/CoverageGridLayer.tsx`（Canvas GridLayer，按瓦片绘制），
点格点看详情走 `point()` 单点查询 —— 详情列（小区、PCI、采样点、好点率）不进大包。

## 二进制格式 `NGC1`（小端）

| 偏移 | 长度 | 内容 |
| --- | --- | --- |
| 0 | 4 | 魔数 `b"NGC1"` |
| 4 | 4 | `uint32` 格点数 n |
| 8 | 4n | `float32[n]` 中心经度 |
| 8+4n | 4n | `float32[n]` 中心纬度 |
| 8+8n | n | `uint8[n]` `bin_index`（NGI legend 档位 1~7；空值 = 0） |

两段 float32 的起点都是 4 的倍数，前端可以直接 `new Float32Array(buf, off, n)`。
float32 对 ±180° 的经纬度精度约 1e-6°（≈0.1 m），对 50 m 网格绰绰有余。
改格式必须同步改前端 `pages/location/coverageGrid.ts` 的 `parseGridBlob`
（`tests/test_ngi_coverage_api.py` 与前端 vitest 各守一半）。
"""
from __future__ import annotations

import gzip
import logging
import math
import struct
import sys
import threading
import time
from array import array
from datetime import date, datetime

from backend.services.ngi_data.ngi_store import GRID_TABLE, LOG_TABLE, LTE_TABLE, NotPgError

logger = logging.getLogger("ngi_coverage_api")

MAGIC = b"NGC1"
HEADER = struct.Struct("<4sI")
DEFAULT_GRID_M = 50
META_TTL_S = 60.0
# 单点查询：一次最多回查多少个覆盖小区的名字（格点里的 cell_keys 偶尔很长）
MAX_CELL_LOOKUP = 12

_lock = threading.Lock()
_meta_cache: dict = {"at": 0.0, "value": None}
# kpi -> {"run_id", "count", "raw", "gz", "etag"}
_blob_cache: dict[str, dict] = {}


# ---------------------------------------------------------------- 纯函数（可单测）
def pack_grid(rows) -> bytes:
    """`[(lon, lat, bin), ...]` → `NGC1` 二进制。经纬度缺失的行丢弃。"""
    lon = array("f")
    lat = array("f")
    bins = bytearray()
    for r in rows:
        x, y, b = r[0], r[1], r[2]
        if x is None or y is None:
            continue
        x = float(x)
        y = float(y)
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        lon.append(x)
        lat.append(y)
        try:
            bi = int(b) if b is not None else 0
        except (TypeError, ValueError):
            bi = 0
        bins.append(bi if 0 <= bi <= 255 else 0)
    if sys.byteorder != "little":  # 格式固定小端；x86/ARM Windows 都是小端，这里只是兜底
        lon.byteswap()
        lat.byteswap()
    return HEADER.pack(MAGIC, len(lon)) + lon.tobytes() + lat.tobytes() + bytes(bins)


def unpack_grid(blob: bytes) -> tuple[list[float], list[float], list[int]]:
    """`pack_grid` 的逆过程（仅供单测/排障；线上解包在前端）。"""
    magic, n = HEADER.unpack_from(blob, 0)
    if magic != MAGIC:
        raise ValueError(f"魔数不对：{magic!r}")
    need = HEADER.size + 9 * n
    if len(blob) < need:
        raise ValueError(f"长度不足：{len(blob)} < {need}")
    lon = array("f")
    lat = array("f")
    lon.frombytes(blob[8:8 + 4 * n])
    lat.frombytes(blob[8 + 4 * n:8 + 8 * n])
    if sys.byteorder != "little":
        lon.byteswap()
        lat.byteswap()
    bins = list(blob[8 + 8 * n:8 + 9 * n])
    return list(lon), list(lat), bins


def make_etag(kpi: str, run_id, count) -> str:
    # 弱 ETag：gzip 与未压缩两种表示共用同一个标签（内容语义相同）
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(kpi))
    return f'W/"ngc-{safe}-{run_id}-{count}"'


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    tags = [t.strip() for t in if_none_match.split(",")]
    bare = etag[2:] if etag.startswith("W/") else etag
    return any(t == "*" or t == etag or t == bare or t.removeprefix("W/") == bare for t in tags)


def _iso(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------- 取数
def _connection():
    from backend.database import DB_MODE, get_raw_connection

    if DB_MODE != "pg":
        raise NotPgError(
            f"覆盖网格存在 PostgreSQL，但当前 MY_APP_DB_MODE={DB_MODE!r}；"
            "请检查 backend/.env 的 MY_APP_DB_MODE / DATABASE_URL"
        )
    return get_raw_connection()


def _table_exists(cur) -> bool:
    cur.execute("SELECT to_regclass(?) AS t", (GRID_TABLE,))
    row = cur.fetchone()
    return bool(row and row["t"])


def _load_meta() -> dict:
    with _connection() as conn:
        cur = conn.cursor()
        if not _table_exists(cur):
            return {"available": False,
                    "reason": f"{GRID_TABLE} 还不存在：NGI 覆盖同步任务（ngi_param_coverage_sync）尚未成功跑过"}

        cur.execute(
            f"SELECT kpi, count(*) AS n, max(run_id) AS run_id,"
            f" min(center_lon) AS min_lon, min(center_lat) AS min_lat,"
            f" max(center_lon) AS max_lon, max(center_lat) AS max_lat"
            f" FROM {GRID_TABLE} GROUP BY kpi ORDER BY kpi"
        )
        kpis = [{
            "kpi": r["kpi"],
            "rows": int(r["n"] or 0),
            "run_id": int(r["run_id"] or 0),
            "bbox": [_num(r["min_lon"]), _num(r["min_lat"]), _num(r["max_lon"]), _num(r["max_lat"])],
            "bins": [],
            "legend": None,
        } for r in cur.fetchall()]
        if not kpis:
            return {"available": False, "reason": f"{GRID_TABLE} 是空表"}

        by_kpi = {k["kpi"]: k for k in kpis}
        cur.execute(
            f"SELECT kpi, bin_index, count(*) AS n, min(rsrp) AS rsrp_min,"
            f" max(rsrp) AS rsrp_max, avg(rsrp) AS rsrp_avg"
            f" FROM {GRID_TABLE} GROUP BY kpi, bin_index ORDER BY kpi, bin_index"
        )
        for r in cur.fetchall():
            k = by_kpi.get(r["kpi"])
            if k is None:
                continue
            k["bins"].append({
                "bin": int(r["bin_index"]) if r["bin_index"] is not None else 0,
                "rows": int(r["n"] or 0),
                "rsrp_min": _num(r["rsrp_min"]),
                "rsrp_max": _num(r["rsrp_max"]),
                "rsrp_avg": _num(r["rsrp_avg"]),
            })

        log: dict = {}
        try:
            cur.execute(
                f"SELECT id, finished_at, grid_size_m, task_log_id, task_log_name,"
                f" task_start_time, detail FROM {LOG_TABLE}"
                " WHERE kind = 'coverage' AND status = 'success'"
                " ORDER BY finished_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                log = {k: row[k] for k in ("id", "finished_at", "grid_size_m", "task_log_id",
                                           "task_log_name", "task_start_time", "detail")}
        except Exception as e:  # noqa: BLE001  —— 日志表缺失不影响画图
            logger.warning("[NGI] 读取覆盖同步日志失败（不影响网格）: %s", e)

    detail = log.get("detail") if isinstance(log.get("detail"), dict) else {}
    for item in (detail.get("kpis") or []):
        if isinstance(item, dict) and item.get("kpi") in by_kpi and item.get("legend"):
            by_kpi[item["kpi"]]["legend"] = item["legend"]

    return {
        "available": True,
        "grid_size_m": int(log.get("grid_size_m") or DEFAULT_GRID_M),
        "synced_at": _iso(log.get("finished_at")),
        "task_log_id": log.get("task_log_id"),
        "task_log_name": log.get("task_log_name"),
        "task_start_time": _iso(log.get("task_start_time")),
        "kpis": kpis,
    }


def meta(force: bool = False) -> dict:
    """各 kpi 的行数 / run_id / 范围 / 档位统计（进程内缓存 60 s）。"""
    now = time.monotonic()
    with _lock:
        cached = _meta_cache["value"]
        if not force and cached is not None and now - _meta_cache["at"] < META_TTL_S:
            return cached
    value = _load_meta()
    with _lock:
        _meta_cache["value"] = value
        _meta_cache["at"] = time.monotonic()
    return value


def grid_blob(kpi: str) -> dict:
    """某 kpi 的 `NGC1` 二进制（含 gzip 版本与 ETag）。kpi 不存在抛 KeyError。"""
    m = meta()
    item = next((k for k in (m.get("kpis") or []) if k["kpi"] == kpi), None)
    if item is None:
        raise KeyError(kpi)
    run_id, count = item["run_id"], item["rows"]
    with _lock:
        hit = _blob_cache.get(kpi)
        if hit and hit["run_id"] == run_id and hit["count"] == count:
            return hit

    started = time.monotonic()
    with _connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT center_lon, center_lat, bin_index FROM {GRID_TABLE}"
            " WHERE kpi = ? AND center_lon IS NOT NULL AND center_lat IS NOT NULL",
            (kpi,),
        )
        rows = cur.fetchall()
    raw = pack_grid(rows)
    gz = gzip.compress(raw, compresslevel=6)
    n = HEADER.unpack_from(raw, 0)[1]
    entry = {"run_id": run_id, "count": count, "points": n, "raw": raw, "gz": gz,
             "etag": make_etag(kpi, run_id, count)}
    with _lock:
        _blob_cache[kpi] = entry
    logger.info("[NGI] 覆盖网格打包 kpi=%s run_id=%s points=%d raw=%.1fKB gz=%.1fKB（%.0f ms）",
                kpi, run_id, n, len(raw) / 1024, len(gz) / 1024,
                (time.monotonic() - started) * 1000)
    return entry


def _cell_names(cur, keys: list[str]) -> dict[str, str]:
    """`eNodeBID_CellID` → 小区名（查 LTE 工参；查不到就不给，不影响主结果）。"""
    pairs = []
    for k in keys[:MAX_CELL_LOOKUP]:
        if "_" in k:
            enb, cid = k.split("_", 1)
            pairs.append((enb.strip(), cid.strip(), k))
    if not pairs:
        return {}
    try:
        cur.execute(
            f"SELECT enodeb_id, cell_id, cell_name FROM {LTE_TABLE} WHERE enodeb_id = ANY(?)",
            (sorted({p[0] for p in pairs}),),
        )
        found = {(str(r["enodeb_id"]), str(r["cell_id"])): r["cell_name"] for r in cur.fetchall()}
    except Exception as e:  # noqa: BLE001
        logger.warning("[NGI] 覆盖格点回查小区名失败: %s", e)
        return {}
    return {k: found[(enb, cid)] for enb, cid, k in pairs if found.get((enb, cid))}


def point(kpi: str, lat: float, lon: float, grid_size_m: int | None = None) -> dict:
    """离 (lat, lon) 最近、且在半个格点之内的那一格的详情。"""
    gm = float(grid_size_m or DEFAULT_GRID_M)
    dlat = gm * 0.75 / 111_320.0
    coslat = max(0.2, math.cos(math.radians(lat)))
    dlon = dlat / coslat
    with _connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT earthid, offsetx, offsety, bin_index, cell_keys, pcis,"
            f" top_sample_points, total_sample_points, rsrp, good_ratio, center_lon, center_lat"
            f" FROM {GRID_TABLE}"
            " WHERE kpi = ? AND center_lat BETWEEN ? AND ? AND center_lon BETWEEN ? AND ?"
            " ORDER BY (center_lat - ?) * (center_lat - ?)"
            " + (center_lon - ?) * (center_lon - ?) * ? LIMIT 1",
            (kpi, lat - dlat, lat + dlat, lon - dlon, lon + dlon,
             lat, lat, lon, lon, coslat * coslat),
        )
        r = cur.fetchone()
        if not r:
            return {"found": False, "kpi": kpi}
        keys = [k.strip() for k in str(r["cell_keys"] or "").split(";") if k.strip()]
        names = _cell_names(cur, keys)
    return {
        "found": True,
        "kpi": kpi,
        "cell": {
            "earthid": r["earthid"],
            "offsetx": r["offsetx"],
            "offsety": r["offsety"],
            "bin": r["bin_index"],
            "rsrp": _num(r["rsrp"]),
            "good_ratio": _num(r["good_ratio"]),
            "top_sample_points": r["top_sample_points"],
            "total_sample_points": r["total_sample_points"],
            "lon": _num(r["center_lon"]),
            "lat": _num(r["center_lat"]),
            "cells": [{"key": k, "name": names.get(k)} for k in keys],
            "pcis": [p.strip() for p in str(r["pcis"] or "").split(";") if p.strip()],
        },
    }


def clear_cache() -> None:
    """同步任务写完网格后可调用，让下一次请求立即重新打包（不调也会在 60 s 内自愈）。"""
    with _lock:
        _meta_cache["value"] = None
        _meta_cache["at"] = 0.0
        _blob_cache.clear()


__all__ = ["MAGIC", "pack_grid", "unpack_grid", "make_etag", "etag_matches",
           "meta", "grid_blob", "point", "clear_cache", "NotPgError"]

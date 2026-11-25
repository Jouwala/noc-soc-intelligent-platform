# -*- coding: utf-8 -*-
"""位置页「LTE 小区」/「5G NR 小区」扇区图层的只读取数（NGI 工参 → 列式 JSON）。

## 设计（2026-11-25）

- LTE 工参约 8 227 个小区、NR 约 101 个，一次全量下发：列式
  `{"columns": [...], "rows": [[...], ...]}`，比逐行 dict 少一半以上体积。
  弹窗要的字段（站名、PCI、下倾…）一并带上，点击扇区**不再回查后端**。
- **两张表输出同一套列名**（`COLUMNS`）：NR 表的 `gnb_id / gnb_name / gnb_lat / gnb_lon /
  ssb_arfcn / freq_band_index / dl_bandwidth` 在 SQL 里别名成 LTE 的列名，没有的列
  （`sector_id` / `tac` / `active`）给 NULL。这样前端 `pages/location/lteCells.ts`
  的解包、画法、点选**一份代码两层共用**。
- 工参每周日才全量替换一次（`ngi_param_coverage_sync`），所以和覆盖网格一样：
  进程内按「行数 + max(fetched_at)」判断是否要重新打包，浏览器靠弱 ETag 做 304 协商。
  两个制式各自缓存、各自 ETag（前缀 `ngl` / `ngn`）。

坐标优先用 `cell_lat/cell_lon`，缺失时退回站点坐标（由前端处理，这里原样给）。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import math
import threading
import time
from datetime import date, datetime

from backend.services.ngi_data.ngi_store import LTE_TABLE, NR_TABLE, NotPgError

logger = logging.getLogger("ngi_cell_api")

# 前端 lteCells.ts::parseCellPayload 按列名取值；新增列只需两边都加，不必管顺序
COLUMNS: tuple[str, ...] = (
    "cell_uid", "enodeb_id", "enodeb_name", "cell_id", "cell_name", "sector_id",
    "pci", "tac", "dl_center_freq", "dl_earfcn", "band_indicator", "bandclass",
    "dl_system_bw", "azimuth", "height_m", "mech_downtilt", "elec_downtilt",
    "cell_lat", "cell_lon", "site_lat", "site_lon", "cover_type", "active", "vendor",
)

# 输出列 → 该制式表里的取值表达式（只允许列名 / NULL / COALESCE，单测会校验列名存在）
LTE_SELECT: dict[str, str] = {c: c for c in COLUMNS}
NR_SELECT: dict[str, str] = {
    "cell_uid": "cell_uid",
    "enodeb_id": "gnb_id",
    "enodeb_name": "gnb_name",
    "cell_id": "cell_id",
    "cell_name": "cell_name",
    "sector_id": "NULL",                 # NR 工参没有扇区号：前端按方位角分组
    "pci": "pci",
    "tac": "NULL",
    "dl_center_freq": "COALESCE(dl_center_freq, dl_frequency)",
    "dl_earfcn": "ssb_arfcn",            # 弹窗里显示成 SSB-ARFCN
    "band_indicator": "freq_band_index",
    "bandclass": "band_class",
    "dl_system_bw": "dl_bandwidth",
    "azimuth": "azimuth",
    "height_m": "height_m",
    "mech_downtilt": "mech_downtilt",
    "elec_downtilt": "elec_downtilt",
    "cell_lat": "cell_lat",
    "cell_lon": "cell_lon",
    "site_lat": "gnb_lat",
    "site_lon": "gnb_lon",
    "cover_type": "cover_type",
    "active": "NULL",
    "vendor": "vendor",
}

SPECS: dict[str, dict] = {
    "LTE": {"table": LTE_TABLE, "select": LTE_SELECT, "etag": "ngl",
            "order": "enodeb_id, sector_id, dl_center_freq, cell_id"},
    "NR": {"table": NR_TABLE, "select": NR_SELECT, "etag": "ngn",
           "order": "gnb_id, azimuth, dl_center_freq, cell_id"},
}

CHECK_TTL_S = 60.0

_lock = threading.Lock()
_cache: dict[str, dict] = {rat: {"checked_at": 0.0, "sig": None, "entry": None} for rat in SPECS}


# ---------------------------------------------------------------- 纯函数（可单测）
def _cell(v):
    """单元格 → JSON 安全值：NaN/Inf → None，日期 → ISO，其余原样。"""
    if v is None:
        return None
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, (int, str, bool)):
        return v
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    try:  # Decimal 等
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return str(v)


def build_payload(rows, *, snapshot_date=None, fetched_at=None, rat: str = "LTE") -> dict:
    """`rows`（与 COLUMNS 同序的序列）→ 列式 payload。"""
    out_rows = [[_cell(r[i]) for i in range(len(COLUMNS))] for r in rows]
    return {
        "available": True,
        "rat": rat,
        "columns": list(COLUMNS),
        "rows": out_rows,
        "count": len(out_rows),
        "snapshot_date": _cell(snapshot_date),
        "fetched_at": _cell(fetched_at),
    }


def make_etag(count, fetched_at, prefix: str = "ngl") -> str:
    """弱 ETag。`fetched_at` 正常是时间戳，降级时是中文原因串。

    HTTP 头只能放 latin-1：中文原因串直接拼进去会让 Starlette 在写
    `Response.raw_headers` 时抛 `UnicodeEncodeError`，把「表缺失 → available:false」
    的降级路径变成 500。这里只保留 ASCII 安全字符；一旦原文含其它字符（中文、
    空白、引号），改用原文的短哈希，既保证 ASCII 又保证原因变化时 ETag 跟着变。
    """
    raw = str(_cell(fetched_at) or "none")
    stamp = "".join(ch for ch in raw if ch.isascii() and (ch.isalnum() or ch in "-:._TZ+"))
    if len(stamp) != len(raw):
        stamp = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f'W/"{prefix}-{count}-{stamp}"'


def encode(payload: dict) -> tuple[bytes, bytes]:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return raw, gzip.compress(raw, compresslevel=6)


def select_sql(rat: str) -> str:
    spec = SPECS[rat]
    cols = ", ".join(f"{spec['select'][c]} AS {c}" for c in COLUMNS)
    return f"SELECT {cols} FROM {spec['table']} ORDER BY {spec['order']}"


# ---------------------------------------------------------------- 取数
def _connection():
    from backend.database import DB_MODE, get_raw_connection

    if DB_MODE != "pg":
        raise NotPgError(
            f"NGI 工参存在 PostgreSQL，但当前 MY_APP_DB_MODE={DB_MODE!r}；"
            "请检查 backend/.env 的 MY_APP_DB_MODE / DATABASE_URL"
        )
    return get_raw_connection()


def _unavailable(rat: str, reason: str) -> dict:
    raw, gz = encode({"available": False, "rat": rat, "reason": reason})
    return {"available": False, "reason": reason, "raw": raw, "gz": gz,
            "etag": make_etag(0, reason, SPECS[rat]["etag"]), "count": 0}


def cells(rat: str = "LTE", force: bool = False) -> dict:
    """返回 `{"raw", "gz", "etag", "count", "available"}`；60 s 内只做一次「是否变了」的轻查询。"""
    if rat not in SPECS:
        raise KeyError(rat)
    spec = SPECS[rat]
    table = spec["table"]
    slot = _cache[rat]
    now = time.monotonic()
    with _lock:
        entry = slot["entry"]
        if not force and entry is not None and now - slot["checked_at"] < CHECK_TTL_S:
            return entry

    with _connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass(?) AS t", (table,))
        row = cur.fetchone()
        if not (row and row["t"]):
            entry = _unavailable(rat, f"{table} 还不存在：NGI 工参同步任务（ngi_param_coverage_sync）尚未成功跑过")
            sig = None
        else:
            cur.execute(f"SELECT count(*) AS n, max(fetched_at) AS f, max(snapshot_date) AS d FROM {table}")
            m = cur.fetchone()
            count, fetched_at, snap = int(m["n"] or 0), m["f"], m["d"]
            sig = (count, str(fetched_at))
            with _lock:
                if not force and slot["sig"] == sig and slot["entry"] is not None:
                    slot["checked_at"] = time.monotonic()
                    return slot["entry"]
            if count == 0:
                entry = _unavailable(rat, f"{table} 是空表")
            else:
                started = time.monotonic()
                cur.execute(select_sql(rat))
                payload = build_payload(cur.fetchall(), snapshot_date=snap, fetched_at=fetched_at, rat=rat)
                raw, gz = encode(payload)
                entry = {"available": True, "raw": raw, "gz": gz, "count": payload["count"],
                         "etag": make_etag(payload["count"], fetched_at, spec["etag"])}
                logger.info("[NGI] %s 小区打包 cells=%d raw=%.0fKB gz=%.0fKB（%.0f ms）",
                            rat, payload["count"], len(raw) / 1024, len(gz) / 1024,
                            (time.monotonic() - started) * 1000)

    with _lock:
        slot.update(checked_at=time.monotonic(), sig=sig, entry=entry)
    return entry


def lte_cells(force: bool = False) -> dict:
    return cells("LTE", force)


def nr_cells(force: bool = False) -> dict:
    return cells("NR", force)


def clear_cache() -> None:
    with _lock:
        for slot in _cache.values():
            slot.update(checked_at=0.0, sig=None, entry=None)


__all__ = ["COLUMNS", "LTE_SELECT", "NR_SELECT", "SPECS", "build_payload", "make_etag", "encode",
           "select_sql", "cells", "lte_cells", "nr_cells", "clear_cache", "NotPgError"]

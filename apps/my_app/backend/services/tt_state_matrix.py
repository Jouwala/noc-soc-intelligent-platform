"""按州 × 日/周/月统计「有 WO 的 TT」：告警未清除 / WO 未关闭 / TT 未关闭 / 总数。

页面：`?page=tt_state`（`frontend/src/pages/TtStateMatrix.tsx`）。

数据源与口径（页面的「口径说明」与这里逐条对应，改一处必须改另一处）：

- **基础集合**：`eflow_tt` 里 `wo_no` 非空的行 —— 一行 = 一张已派出 WO 的 TT。
  实测当前库 `wo_no` 唯一（16164 行 / 16164 个 WO），因此「行数 = WO 数」。
- **时间字段**：`alarm_occur_time`（与 `page=tt` 的 TT 统计同一字段），窗口左闭右开。
- **州**：`region_level1`（eFlow 的州/县字段）；空值归入 ``__unknown__``，
  由前端显示为「未标注州」。
- **告警未清除**：`alarm_clear_time` 为 NULL / 空串。
- **WO 未关闭**：`LEFT JOIN eflow_wo ON wo_no` 后 `eflow_wo.status` 不以 `Closed`
  开头；**关联不到 WO 记录时按未关闭计**（没有关闭证据），并在
  ``meta.wo_missing`` 与常驻告警里报出 —— 这是「算不全」，不能藏进收起的口径说明。
- **TT 未关闭**：`eflow_tt.status` 不以 `Closed` 开头。TT 要等 WO 确认后才 Closed，
  上游常有「WO 已 Closed、TT 仍 Waiting」的行，所以该值通常**远大于** WO 未关闭；
  两者是两个独立集合，页面同时展示，便于对账。

为什么不在 SQL 里 GROUP BY：日/周/月三种桶在 SQLite 与 PG 的写法不同，而本库是
SQLite/PG 双模（`MY_APP_DB_MODE`）。这里一次取回窗口内的行（全量约 1.6 万行，
30 天窗口实测 < 2000 行），用同一个 `_period_label` 在 Python 里分桶 —— 与
`routers/tt_wo_dashboard.py` 的 MTTR 趋势同一做法，**概览与下钻因此天然同源**
（两者用同一份 `_load_rows`，不存在「表格数字与下钻行数对不上」）。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from backend.database import get_raw_connection
from backend.dialect_helpers import list_table_names, table_columns

log = logging.getLogger(__name__)

GRANULARITIES = ("day", "week", "month")
DEFAULT_DAYS = 30
MAX_DRILL_ROWS = 5000
UNKNOWN_STATE = "__unknown__"

# 结构探测结果按连接类型缓存：生产库的 schema 不会在运行中变化，每次请求重查
# information_schema 只是白付一次往返。
_schema_cache: dict | None = None


# ────────────────────────── 结构探测 ──────────────────────────

def _schema(conn) -> dict:
    global _schema_cache
    if _schema_cache is not None:
        return _schema_cache
    tables = set(list_table_names(conn))
    tt_cols = set(table_columns(conn, "eflow_tt")) if "eflow_tt" in tables else set()
    wo_cols = set(table_columns(conn, "eflow_wo")) if "eflow_wo" in tables else set()
    _schema_cache = {
        "has_tt": "eflow_tt" in tables,
        "has_wo": "eflow_wo" in tables,
        "has_region": "region_level1" in tt_cols,
        "has_occur": "alarm_occur_time" in tt_cols,
        "has_clear": "alarm_clear_time" in tt_cols,
        "has_tt_status": "status" in tt_cols,
        "has_wo_status": "status" in wo_cols,
        "has_wo_no": "wo_no" in tt_cols,
        "has_tt_no": "tt_no" in tt_cols,
        "has_site_name": "site_name" in tt_cols,
    }
    return _schema_cache


# ────────────────────────── 时间窗口 ──────────────────────────

def _parse_day(value: str) -> date | None:
    try:
        return datetime.strptime(str(value or "").strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _window(start: str, end: str, all_time: bool) -> tuple[str | None, str | None]:
    """返回**含首尾**的 [start, end]；全部时间返回 (None, None)。

    前端传的是 ``YYYY-MM-DD``，`end` 是含当天的自然日；查询时由 `_load_rows`
    把 end +1 天变成右开区间（本函数只负责「页面显示什么区间」与「SQL 用哪些天」一致）。
    任一参数不合法时回落到默认近 30 天 —— 不猜一个可能漏数据的时间窗。
    """
    if all_time:
        return None, None
    s, e = _parse_day(start), _parse_day(end)
    if not s or not e or s > e:
        e = date.today()
        s = e - timedelta(days=DEFAULT_DAYS)
    return s.isoformat(), e.isoformat()


def _period_label(value, gran: str) -> str:
    """把一行的时间值归到日/ISO 周/月桶；解析不了返回空串（调用方计 skipped）。"""
    dt = _as_dt(value)
    if dt is None:
        return ""
    if gran == "month":
        return dt.strftime("%Y-%m")
    if gran == "week":
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    return dt.strftime("%Y-%m-%d")


def _as_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "").replace("T", " "))
    except ValueError:
        return None


# ────────────────────────── 取数 ──────────────────────────

def _load_rows(conn, S: dict, start: str | None, end: str | None) -> list[dict]:
    """窗口内「wo_no 非空」的 TT 行，附 WO 状态。

    列顺序即下方解包顺序；`LEFT JOIN` 保证 67 张在 eflow_wo 里没有记录的单
    不会从总数里消失（它们按未关闭计，并在 meta.wo_missing 里报出）。
    """
    region = "t.region_level1" if S["has_region"] else "''"
    occur = "t.alarm_occur_time" if S["has_occur"] else "NULL"
    clear = "t.alarm_clear_time" if S["has_clear"] else "NULL"
    tt_status = "t.status" if S["has_tt_status"] else "NULL"
    tt_no = "t.tt_no" if S["has_tt_no"] else "''"
    site_name = "t.site_name" if S["has_site_name"] else "''"
    join = "LEFT JOIN eflow_wo w ON w.wo_no = t.wo_no" if S["has_wo"] else ""
    wo_status = "w.status" if (S["has_wo"] and S["has_wo_status"]) else "NULL"

    where = ["t.wo_no IS NOT NULL", "TRIM(t.wo_no) <> ''"]
    params: list = []
    if start and end and S["has_occur"]:
        # end 是「含当天」，SQL 用右开区间，避免把 end 当天 00:00 之后的单漏掉
        end_excl = (datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        where.append("t.alarm_occur_time >= ? AND t.alarm_occur_time < ?")
        params.extend([start, end_excl])

    sql = f"""
        SELECT {region}, {occur}, {clear}, {tt_status}, {tt_no}, {site_name},
               t.wo_no, {wo_status}
        FROM eflow_tt t
        {join}
        WHERE {' AND '.join(where)}
    """
    rows = []
    for r in conn.execute(sql, params).fetchall():
        raw_state = str(r[0] or "").strip()
        rows.append({
            "state": raw_state or UNKNOWN_STATE,
            "occur": r[1],
            "clear_raw": r[2],
            "tt_status": r[3],
            "tt_no": r[4],
            "site_name": r[5],
            "wo_no": r[6],
            "wo_status": r[7],
        })
    return rows


def _blank() -> dict:
    return {"total": 0, "alarm_uncleared": 0, "wo_unclosed": 0, "tt_unclosed": 0}


def _is_closed(status) -> bool:
    return str(status or "").strip().lower().startswith("closed")


def _alarm_uncleared(clear_raw) -> bool:
    return not str(clear_raw or "").strip()


# ────────────────────────── 聚合 ──────────────────────────

def overview(start: str = "", end: str = "", gran: str = "day", all_time: bool = False) -> dict:
    """州 × 时间桶矩阵 + 汇总。只返回有数据的格子，前端按 0 补齐。"""
    gran = gran if gran in GRANULARITIES else "day"
    s, e = _window(start, end, all_time)
    warnings: list[str] = []
    with get_raw_connection() as conn:
        S = _schema(conn)
        if not S["has_tt"]:
            return _empty(gran, s, e, ["eflow_tt 表不存在，无法统计"])
        rows = _load_rows(conn, S, s, e)

    if not S["has_wo"]:
        warnings.append("eflow_wo 表不存在：WO 未关闭回退按 eflow_tt.status 判定")
    elif not S["has_wo_status"]:
        warnings.append("eflow_wo 无 status 列：WO 未关闭回退按 eflow_tt.status 判定")
    if not S["has_occur"]:
        warnings.append("eflow_tt 无 alarm_occur_time 列：无法按日/周/月分桶")

    state_stats: dict[str, dict] = {}
    cell_stats: dict[tuple[str, str], dict] = {}
    periods: set[str] = set()
    skipped = wo_missing = 0
    summary = _blank()

    for r in rows:
        period = _period_label(r["occur"], gran)
        if not period:
            skipped += 1
            continue
        st = r["state"]
        alarm_open = _alarm_uncleared(r["clear_raw"])
        tt_open = not _is_closed(r["tt_status"])
        if S["has_wo"] and S["has_wo_status"]:
            wo_open = not _is_closed(r["wo_status"])
            if r["wo_status"] is None:
                wo_missing += 1
        else:
            wo_open = tt_open

        for bucket in (state_stats.setdefault(st, _blank()),
                       cell_stats.setdefault((st, period), _blank()),
                       summary):
            bucket["total"] += 1
            bucket["alarm_uncleared"] += 1 if alarm_open else 0
            bucket["wo_unclosed"] += 1 if wo_open else 0
            bucket["tt_unclosed"] += 1 if tt_open else 0
        periods.add(period)

    if skipped:
        warnings.append(f"{skipped} 行 alarm_occur_time 无法解析，未计入任何桶")
    if wo_missing:
        warnings.append(
            f"{wo_missing} 张 TT 有 WO 号但在 eflow_wo 里找不到对应工单，已按「WO 未关闭」计"
        )

    states = [
        {"state": st, **_with_cleared(v)}
        for st, v in sorted(state_stats.items(), key=lambda kv: (-kv[1]["total"], kv[0]))
    ]
    cells = [
        {"state": st, "period": p, **_with_cleared(v)}
        for (st, p), v in sorted(cell_stats.items())
    ]
    return {
        "meta": {
            "start": s or "",
            "end": e or "",
            "gran": gran,
            "all_time": bool(all_time),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "row_count": len(rows),
            "skipped_rows": skipped,
            "wo_missing": wo_missing,
            "warnings": warnings,
        },
        "periods": sorted(periods),
        "states": states,
        "cells": cells,
        "summary": _with_cleared(summary),
    }


def _with_cleared(v: dict) -> dict:
    """补一个「告警已清除」派生值（= 总数 − 告警未清除），供 KPI 卡直接取用。"""
    return {**v, "alarm_cleared": max(v["total"] - v["alarm_uncleared"], 0)}


def drilldown(
    start: str = "", end: str = "", gran: str = "day", state: str = "",
    period: str = "", all_time: bool = False, limit: int = MAX_DRILL_ROWS,
    flag: str = "",
) -> dict:
    """某个「州 × 时间桶」的明细。过滤条件与 `overview` 完全同源（同一份 rows）。

    ``state="*"`` 表示不限州（KPI 卡下钻用）；``flag`` 收窄到某一类未关闭：
    ``alarm_uncleared`` / ``wo_unclosed`` / ``tt_unclosed`` / ``alarm_cleared``。
    四者与矩阵里的 A/B/C/D 是同一判据，所以下钻行数必然等于对应数字。
    """
    gran = gran if gran in GRANULARITIES else "day"
    s, e = _window(start, end, all_time)
    with get_raw_connection() as conn:
        S = _schema(conn)
        if not S["has_tt"]:
            return _empty_drill(["eflow_tt 表不存在，无法下钻"])
        rows = _load_rows(conn, S, s, e)

    use_wo = S["has_wo"] and S["has_wo_status"]
    matched = []
    skipped = 0
    for r in rows:
        p = _period_label(r["occur"], gran)
        if not p:
            skipped += 1
            continue
        if state and state != "*" and r["state"] != state:
            continue
        if period and p != period:
            continue
        alarm_open = _alarm_uncleared(r["clear_raw"])
        tt_open = not _is_closed(r["tt_status"])
        wo_open = (not _is_closed(r["wo_status"])) if use_wo else tt_open
        if flag == "alarm_uncleared" and not alarm_open:
            continue
        if flag == "wo_unclosed" and not wo_open:
            continue
        if flag == "tt_unclosed" and not tt_open:
            continue
        if flag == "alarm_cleared" and alarm_open:
            continue
        r["_alarm_open"], r["_wo_open"], r["_tt_open"] = alarm_open, wo_open, tt_open
        matched.append(r)

    matched.sort(key=lambda r: _as_dt(r["occur"]) or datetime.min, reverse=True)
    truncated = len(matched) > limit
    out = []
    for r in matched[:limit]:
        out.append({
            "state": r["state"],
            "tt_no": r["tt_no"] or "",
            "wo_no": r["wo_no"] or "",
            "site_name": r["site_name"] or "",
            "alarm_occur_time": str(r["occur"] or ""),
            "alarm_clear_time": str(r["clear_raw"] or ""),
            "tt_status": str(r["tt_status"] or ""),
            "wo_status": str(r["wo_status"] or ""),
            "alarm_uncleared": r["_alarm_open"],
            "wo_closed": not r["_wo_open"],
            "tt_unclosed": r["_tt_open"],
        })
    return {
        "meta": {
            "start": s or "",
            "end": e or "",
            "gran": gran,
            "all_time": bool(all_time),
            "state": state,
            "period": period,
            "flag": flag,
            "count": len(matched),
            "returned": len(out),
            "truncated": truncated,
            "limit": limit,
            "skipped_rows": skipped,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "rows": out,
    }


# ────────────────────────── 空态 / 错误 ──────────────────────────

def _empty(gran: str, s, e, warnings: list[str]) -> dict:
    return {
        "meta": {
            "start": s or "", "end": e or "", "gran": gran, "all_time": False,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "row_count": 0, "skipped_rows": 0, "wo_missing": 0, "warnings": warnings,
        },
        "periods": [], "states": [], "cells": [],
        "summary": _with_cleared(_blank()),
    }


def _empty_drill(warnings: list[str]) -> dict:
    return {
        "meta": {
            "count": 0, "returned": 0, "truncated": False, "limit": MAX_DRILL_ROWS,
            "warnings": warnings, "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "rows": [],
    }

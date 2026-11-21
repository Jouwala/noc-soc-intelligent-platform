# -*- coding: utf-8 -*-
"""CS / PS CN KPI 分组看板数据服务（`?page=cs_cn_kpi` / `?page=ps_cn_kpi`）。

组件清单与 SQL 在 `cn_kpi_split_widgets.py`；本模块只负责：
    取数（并发、单组件失败隔离）→ 缓存（按「看板 + 区间」分行）→ 组装响应。

与 `cn_kpi_service.py` 的关系
    复用它的三件事，避免两套实现漂移：
        · `query_remote()`   —— 远端只读 SQL API（连接、超时、只读守卫）
        · `_apply_range()`   —— 把 SQL 里的 {{LO}}/{{HI}} 换成已校验的日期字面量
        · `_has_any_value()` —— 「有行但全空」不算有数据
    粒度推断用本模块的 `_detect_grain()`（多出 15 分钟 / 小时两档，见该函数说明）。

    区别在取数策略：这里**不做 EMS/UME 换源降级**。两个新页面按来源分段展示，
    某张表缺数就空着并写明原因，不拿另一条流水线顶替（见 widgets 模块头）。

空态约定
    `empty_code` 是给前端做双语文案的枚举：
        no_table      远端没有这张表（同步任务还没跑过 / 模板未纳入同步）
        not_collected 上游模板存在但项目里没有采集配置（如 DNS_SUCCESS_RATIO）
        no_rows       表在、但当前日期区间内没有数据
    前端据此显示原因；**不得把空数据显示成 0**（AGENTS.md 三.6）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date, timedelta as _timedelta
from typing import Any, Dict, List, Optional

from backend.config.remote_host import get_api_base_url
from backend.services.cn.cn_kpi_service import (
    REMOTE_DB,
    _apply_range,
    _has_any_value,
    _validate_date,
    query_remote,
)
from backend.services.cn.cn_kpi_split_widgets import get_board

logger = logging.getLogger("cn_kpi_split")

# 单个看板一次并发取数；远端 db_writer_api 是单机服务，别一次打太多连接
MAX_WORKERS = 6
# 看板数据最细到小时（15 分钟表由同步任务每 15 分钟写一次），60s 缓存足够
CACHE_TTL = int(os.environ.get("CN_KPI_SPLIT_CACHE_TTL", "60"))

_cache: Dict[tuple, tuple] = {}
_lock = threading.Lock()


def _validate_day(v: Optional[str], field: str) -> Optional[str]:
    """严格校验 YYYY-MM-DD，并且必须是真实存在的日历日。

    `cn_kpi_service._validate_date` 只做正则（2026-13-01 也能过），这里再走一次
    `date.fromisoformat`：月份 13、2 月 30 日这类输入直接 400，而不是带着一个
    永远查不到数据的区间去跑满 20 个远端查询。
    """
    s = _validate_date(v, field)
    if not s:
        return None
    try:
        _date.fromisoformat(s)
    except ValueError as e:
        raise ValueError(f"{field} 不是有效日期：{s}") from e
    return s


def _empty_widget(w: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": w["id"],
        "title": w["title"],
        "chart": w["chart"],
        "pivot": bool(w["pivot"]),
        "pct": bool(w["pct"]),
        "unit": w["unit"],
        "source_table": w["table"],
        "fixed_range": bool(w["fixed_range"]),
        "sql": "",
        "columns": [],
        "rows": [],
        "error": "",
        "empty": False,
        "empty_code": "",
        "truncated": False,
        "grain": "",
    }


def _detect_grain(rows: List[List[Any]]) -> str:
    """从返回数据推断时间粒度：minute / hour / day / month。

    与 `cn_kpi_service._detect_granularity()` 的区别：那边只分 day / month
    （小时级也算 day，因为原看板横轴只到天）；这两个页面同时有 15 分钟、
    小时、日、月四种表，卡片上的「实际粒度」必须说准，否则用户会把
    15 分钟的点当成小时点。

    ⚠ 用**原始**值判断（不能先截前 10 位）：小时级 '2026-09-01 00:00' 截断后
    日部分是 01，会被误判成月粒度。
    """
    vals = [str(r[0]) for r in rows if r and r[0] is not None]
    if len(vals) < 2:
        return "day"
    if any(":" in v for v in vals):
        mins = {v[14:16] for v in vals if len(v) >= 16 and v[14:16].isdigit()}
        return "hour" if mins <= {"00"} else "minute"
    days = sorted({v[:10] for v in vals})
    if len(days) < 2:
        return "day"
    if all(len(d) == 10 and d[8:10] == "01" for d in days):
        return "month"
    return "day"


def _fetch_widget(w: Dict[str, Any], start: Optional[str],
                  end: Optional[str]) -> Dict[str, Any]:
    """取单个组件；失败 / 空数据只标记该组件，不影响同页其它组件。"""
    out = _empty_widget(w)

    # 明确未采集的模板：不发注定 500 的查询，直接给空态
    if not w["sql"]:
        out["empty"] = True
        out["empty_code"] = w["missing_code"] or "not_collected"
        return out

    # 月表固定近 13 个月；其余组件按页面日期区间
    sql = w["sql"] if w["fixed_range"] else _apply_range(w["sql"], start, end)
    try:
        res = query_remote(sql, limit=int(w["limit"]))
    except Exception as e:  # noqa: BLE001 — 表不存在 / 网络失败都落成空态或组件错误
        msg = str(e)
        if "no such table" in msg.lower():
            out["empty"] = True
            out["empty_code"] = "no_table"
            logger.info("CN KPI 分组组件 %s 表尚未生成（%s）", w["id"], w["table"])
        else:
            out["error"] = msg
            logger.warning("CN KPI 分组组件 %s(%s) 取数失败：%s", w["id"], w["title"], msg)
        return out

    if not res.get("success", True):
        out["error"] = res.get("error") or "远端返回 success=false"
        return out

    cols = list(res.get("columns") or [])
    rows = [list(r) for r in (res.get("rows") or [])]
    out.update({
        "sql": sql,
        "columns": cols,
        "rows": rows,
        "truncated": bool(res.get("truncated")),
    })
    if not rows or not _has_any_value(rows, len(cols)):
        out["empty"] = True
        out["empty_code"] = "no_rows"
        return out

    out["grain"] = _detect_grain(rows)
    return out


def _compute(board: str, start_v: Optional[str], end_v: Optional[str]) -> Dict[str, Any]:
    t0 = time.time()
    board_def = get_board(board)

    # 展平成 (section_idx, widget_def)，并发取数后按原结构装回
    flat: List[tuple] = []
    for si, sec in enumerate(board_def["sections"]):
        for w in sec["widgets"]:
            flat.append((si, w))

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(lambda it: _fetch_widget(it[1], start_v, end_v), flat))

    sections: List[Dict[str, Any]] = [
        {"key": sec["key"], "widgets": []} for sec in board_def["sections"]
    ]
    for (si, _w), res in zip(flat, results):
        sections[si]["widgets"].append(res)

    all_widgets = [w for sec in sections for w in sec["widgets"]]
    return {
        "meta": {
            "db": REMOTE_DB,
            "host": get_api_base_url(),
            "board": board,
            "start": start_v,
            "end": end_v,
            "count": len(all_widgets),
            "failed": sum(1 for w in all_widgets if w["error"]),
            "empty": sum(1 for w in all_widgets if w["empty"]),
            "truncated": sum(1 for w in all_widgets if w["truncated"]),
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
            "cached": False,
        },
        "title": board_def["title"],
        "sections": sections,
    }


def get_overview(board: str, start: Optional[str] = None,
                 end: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """整页数据。start/end 为 YYYY-MM-DD（含）；留空 = 近 31 天（与 CN KPI 一致）。"""
    start_v = _validate_day(start, "start")
    end_v = _validate_day(end, "end")
    if start_v and end_v and start_v > end_v:
        raise ValueError(f"开始日期不能晚于结束日期：{start_v} > {end_v}")

    key = (board, start_v or "", end_v or "")
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if not force and hit is not None and (now - hit[0]) < CACHE_TTL:
            data = dict(hit[1])
            data["meta"] = {**data["meta"], "cached": True}
            return data

    # 页面上的「结束日期」是**含**的，SQL 的占位符按 < 右端点用：
    # 这里把结束日期 +1 天再交给 SQL，meta 仍回传用户选的那一天。
    end_excl = end_v
    if end_v:
        end_excl = (_date.fromisoformat(end_v) + _timedelta(days=1)).isoformat()

    # 取数在锁外做：单个组件最多几秒，两个标签页同时冷启动时各算一遍，
    # 比让所有请求排队等一个慢查询更稳（远端单机、并发上限已由线程池控制）。
    data = _compute(board, start_v, end_excl)
    # meta 回传用户选的结束日（不含 +1），SQL 用的是 end_excl
    data["meta"]["end"] = end_v
    with _lock:
        _cache[key] = (time.time(), data)
    return data


def health() -> Dict[str, Any]:
    """远端只读 API 连通性探测（供页面诊断提示用）。"""
    try:
        res = query_remote("SELECT 1 AS ok", limit=1)
        return {"ok": True, "host": get_api_base_url(), "db": REMOTE_DB,
                "elapsed_ms": res.get("elapsed_ms")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "host": get_api_base_url(), "db": REMOTE_DB,
                "error": str(e)}

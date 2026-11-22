# -*- coding: utf-8 -*-
"""CS / PS CN KPI 15 分钟看板数据服务（`?page=cs_cn_kpi_15mins` / `?page=ps_cn_kpi_15mins`）。

组件清单与 SQL 在 `cn_kpi_15mins_widgets.py`；本模块只负责：
    并发取数（单组件失败隔离）→ 按看板 TTL 缓存 + 单飞 → 组装响应。

与相邻模块的关系
    · `cn_kpi_split_service`：参考页（EMS / UME 分段、可选日期区间、日/小时/月聚合）。
      本页只取它的 UME 指标，但窗口固定为 D0/D1/D7、横轴固定 15 分钟桶，
      因此不复用它的 SQL 与取数函数。
    · `cn_kpi_5mins_service`：同样做 D0/D1/D7 叠画，但它是 5 分钟档 + 混合粒度、
      不区分 CS/PS。**列名守卫直接复用它的 `_verify_columns`**：SQLite 会把
      不存在的双引号标识符当字符串字面量，写错列名不报错、结果恒为 0，
      这道守卫是防止「看起来正常、其实全 0」的唯一防线，不能出现第二份实现。

空态约定（AGENTS.md 三.6：取不到显示 —，不得显示成 0）
    empty_code:
        no_table   远端没有这张表（同步任务还没跑过 / 模板未纳入同步）
        no_rows    表在、但 D0/D1/D7 三个日子里没有数据
    前端据此显示原因；查询报错走 error（红色状态），与业务空态分开。
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from backend.config.remote_host import get_api_base_url
from backend.services.cn.cn_kpi_5mins_service import _verify_columns
from backend.services.cn.cn_kpi_service import (
    REMOTE_DB,
    data_source_label,
    query_remote,
)
from backend.services.db.pg_query import query_data
from backend.services.cn.cn_kpi_15mins_widgets import BOARDS, get_board

logger = logging.getLogger("cn_kpi_15mins")

# 远端 db_writer_api 是单机服务；6 路并发与参考页一致
MAX_WORKERS = 6
# 数据最细 15 分钟、同步任务 15 分钟一轮；300s 缓存与数据粒度同档
CACHE_TTL = 300

_cache: Dict[str, tuple] = {}
_cache_lock = threading.Lock()
# 每块看板一条单飞链：刷新间隔短于取数耗时的时候，后来的请求等这一轮，
# 不再各开 8~9 路查询把远端写锁期间本就紧张的读能力打满。
_flight_lock = threading.Lock()
_flights: Dict[str, tuple] = {}
_FLIGHT_WAIT = 180


def _window_dates() -> Dict[str, str]:
    """响应里回传三个日子，便于页头显示（与 SQL 的 date('now') 同一基准，UTC）。"""
    today = datetime.now(timezone.utc).date()
    return {
        "d0": today.isoformat(),
        "d1": (today - timedelta(days=1)).isoformat(),
        "d7": (today - timedelta(days=7)).isoformat(),
    }


def _fetch_widget(w: Dict[str, Any]) -> Dict[str, Any]:
    """取单个组件；空表 / 无数据算空态，不影响同页其它组件。"""
    out: Dict[str, Any] = {
        "id": w["id"],
        "title": w["title"],
        "chart": "line",
        "pivot": True,          # 四列 [时刻, 序列, 日, 值]
        "pct": bool(w["pct"]),
        "unit": w["unit"],
        "grain": w["grain"],
        "dims": list(w["dims"]),
        "source_table": w["table"],
        "sql": w["sql"],
        "columns": ["Time", "Series", "Day", "Value"],
        "rows": [],
        "error": "",
        "empty": False,
        "empty_code": "",
    }

    # 列名守卫：先确认 SQL 引用的标识符都是真实列，避免「不报错但恒为 0」
    col_err = _verify_columns(w)
    if col_err:
        out["error"] = col_err
        logger.error("CN KPI 15mins 组件 %s 列名校验失败：%s", w["id"], col_err)
        return out

    try:
        # 本机 PG 优先、远端回退（2026-11-22）
        res = query_data(w["sql"], db=REMOTE_DB, limit=int(w["limit"]))
    except Exception as e:  # noqa: BLE001 — 表不存在 / 网络失败都落成空态或组件错误
        msg = str(e)
        if "no such table" in msg.lower():
            out["empty"] = True
            out["empty_code"] = "no_table"
            logger.info("CN KPI 15mins 组件 %s 表尚未生成（%s）", w["id"], w["table"])
        else:
            out["error"] = msg
            logger.warning("CN KPI 15mins 组件 %s(%s) 取数失败：%s",
                           w["id"], w["title"], msg)
        return out

    if not res.get("success", True):
        out["error"] = res.get("error") or "远端返回 success=false"
        return out

    out["columns"] = list(res.get("columns") or out["columns"])
    out["rows"] = [list(r) for r in (res.get("rows") or [])]
    if not out["rows"]:
        out["empty"] = True
        out["empty_code"] = "no_rows"
    return out


def _expand_group(base: Dict[str, Any], res: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把一次查询的结果按 series 拆成多张「一指标一卡」。

    取数仍按组（一张表一次 UNION ALL 查询），拆分只发生在内存里 —— 2026-11-22
    实测「每张卡各发一条 SQL」会让 PS 页冷加载 48 秒，合并成 9 条后约 1 秒。
    """
    parts = base.get("parts") or [
        {"id": base["id"], "title": base["title"],
         "dim": (base.get("dims") or [""])[0]}
    ]
    out: List[Dict[str, Any]] = []
    for part in parts:
        dim = part["dim"]
        rows = [r for r in res["rows"] if len(r) > 1 and str(r[1]) == dim]
        child = {**res, "id": part["id"], "title": part["title"],
                 "dims": [dim], "rows": rows}
        # 组内有数据、但这个维度整段全空（如 Data Subscribers 的 2G）：
        # 按空态处理，不画一条贴底的 0 线（AGENTS.md 三.6）
        if not res["error"] and not res["empty"]:
            has_value = any((len(r) > 3 and r[3] not in (None, "")) for r in rows)
            if not rows:
                child["empty"] = True
                child["empty_code"] = "no_rows"
            elif not has_value:
                # 同组其它指标有数据、但这个维度整段全空（如 Data Subscribers 的 2G）：
                # 单独给它一个原因，避免用户以为整张表没数
                child["empty"] = True
                child["empty_code"] = "no_values"
        out.append(child)
    return out


def _compute(board: str) -> Dict[str, Any]:
    t0 = time.time()
    board_def = get_board(board)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        base_results = list(pool.map(_fetch_widget, board_def["widgets"]))

    widgets: List[Dict[str, Any]] = []
    for base, res in zip(board_def["widgets"], base_results):
        widgets.extend(_expand_group(base, res))

    failed = sum(1 for w in widgets if w["error"])
    empty = sum(1 for w in widgets if w["empty"])
    return {
        "meta": {
            "db": REMOTE_DB,
            "host": data_source_label(),
            "board": board,
            "count": len(widgets),
            "failed": failed,
            "empty": empty,
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
            "cached": False,
            "window": _window_dates(),
        },
        "title": board_def["title"],
        "widgets": widgets,
    }


def get_overview(board: str, force: bool = False) -> Dict[str, Any]:
    """整页数据。board = cs | ps；窗口固定 D0/D1/D7，因此不接收日期参数。"""
    if board not in BOARDS:
        raise KeyError(board)

    now = time.time()
    if not force:
        with _cache_lock:
            hit = _cache.get(board)
            if hit is not None and (now - hit[0]) < CACHE_TTL:
                data = hit[1]
                data["meta"] = {**data["meta"], "cached": True}
                return data

    with _flight_lock:
        flight = _flights.get(board)
        if flight is not None:
            ev, box = flight
            leader = False
        else:
            ev = threading.Event()
            box: Dict[str, Any] = {}
            _flights[board] = (ev, box)
            leader = True

    if not leader:
        if not ev.wait(timeout=_FLIGHT_WAIT):
            raise RuntimeError("CN KPI 15mins 仍在取数，请稍后重试")
        if "error" in box:
            raise box["error"]
        data = box.get("data")
        if data is None:
            raise RuntimeError("CN KPI 15mins 取数未完成")
        return data

    try:
        data = _compute(board)
        box["data"] = data
        return data
    except Exception as e:
        box["error"] = e
        raise
    finally:
        ev.set()
        with _flight_lock:
            cur = _flights.get(board)
            if cur is not None and cur[0] is ev:
                _flights.pop(board, None)
        with _cache_lock:
            if "data" in box:
                _cache[board] = (time.time(), box["data"])


def health() -> Dict[str, Any]:
    """远端只读 API 连通性探测（供页面诊断提示用）。"""
    try:
        res = query_remote("SELECT 1 AS ok", limit=1)
        return {"ok": True, "host": get_api_base_url(), "db": REMOTE_DB,
                "elapsed_ms": res.get("elapsed_ms")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "host": get_api_base_url(), "db": REMOTE_DB,
                "error": str(e)}

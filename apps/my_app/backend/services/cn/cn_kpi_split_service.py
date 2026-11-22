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
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date as _date, datetime as _datetime, timedelta as _timedelta
from typing import Any, Dict, List, Optional

from backend.config.remote_host import get_api_base_url
from backend.services.cn.cn_kpi_service import (
    REMOTE_DB,
    _apply_range,
    _has_any_value,
    _validate_date,
    data_source_label,
    query_remote,
)
from backend.services.cn.cn_kpi_split_widgets import get_board
from backend.services.db.pg_query import query_data

logger = logging.getLogger("cn_kpi_split")

# 单个看板一次并发取数；远端 db_writer_api 是单机服务，别一次打太多连接
MAX_WORKERS = 6

# 整页取数预算（秒）。2026-11-22 实测：远端只读 API 与采集/同步任务共用一台
# 机器，连 `SELECT 1` 都会偶发等 3~5s，单个组件能从 20ms 涨到 15s。原实现只有
# 「单条查询 60s 超时」、没有整页预算，20 个组件 / 6 并发最坏能把整页拖到 4 分钟
# —— 浏览器 120s 就断开（`?page=cs_cn_kpi`，2026-11-22 08:42 实测 499）。
# 到达预算后，未完成的组件按「取数超时」返回，页面先出已完成的图，不再整页干等。
BUILD_BUDGET = float(os.environ.get("CN_KPI_SPLIT_BUILD_BUDGET", "20"))

# 结果缓存 TTL。数据最细 15 分钟更新一次，60s 太短：整页重建要 10~20s，
# 等于每分钟第一个打开页面的人都要等一次冷取数。300s 仍短于上游更新周期；
# 手动刷新走 `?force=1`，不依赖 TTL。
CACHE_TTL = int(os.environ.get("CN_KPI_SPLIT_CACHE_TTL", "300"))

# 过期缓存最多还能当兜底用多久（秒）。超过这个年龄说明上游本身可能长期没更新，
# 不再拿旧值冒充本次结果。
STALE_MAX = int(os.environ.get("CN_KPI_SPLIT_STALE_MAX", str(6 * 3600)))

# 缓存条数上限：区间是用户可选参数，不设上限会被「换着日期查」撑大内存。
MAX_CACHE = int(os.environ.get("CN_KPI_SPLIT_MAX_CACHE", "32"))

_cache: Dict[tuple, tuple] = {}          # key -> (built_at, payload)
_state_lock = threading.Lock()
_key_locks: Dict[tuple, threading.Lock] = {}


def _cache_entry(key: tuple) -> Optional[tuple]:
    with _state_lock:
        return _cache.get(key)


def _cache_put(key: tuple, data: Dict[str, Any]) -> None:
    with _state_lock:
        _cache[key] = (time.time(), data)
        while len(_cache) > MAX_CACHE:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)


def _key_lock(key: tuple) -> threading.Lock:
    """每个「看板 + 区间」一把锁：冷启动只让第一个请求真去取数。

    原实现刻意在锁外取数（让并发请求各算一遍）。但远端 API 是共享单机，
    两个标签页 × 20 个组件同时打过去，正是把它拖慢的原因之一。现在并发请求
    在锁上等同一个结果，锁最多等 `BUILD_BUDGET + 10s`。
    """
    with _state_lock:
        lk = _key_locks.get(key)
        if lk is None:
            lk = _key_locks[key] = threading.Lock()
        return lk


def _cached_view(entry: tuple, stale: bool = False) -> Dict[str, Any]:
    """缓存条目 → 响应，meta 标明来源与年龄（不修改缓存里的原对象）。"""
    ts, data = entry
    meta = {
        **data["meta"],
        "cached": True,
        "stale": stale,
        "age_s": round(max(0.0, time.time() - ts), 1),
        "built_at": _datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
    }
    return {**data, "meta": meta}


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
        "timeout": False,
        "grain": "",
    }


def _failed_widget(w: Dict[str, Any], message: str) -> Dict[str, Any]:
    """整页预算用完时，给未完成组件一个明确错误，而不是让它永远转圈。"""
    out = _empty_widget(w)
    out["error"] = message
    out["timeout"] = True
    return out


def _timeout_message(budget: float) -> str:
    return f"取数超时（整页预算 {budget:.0f}s 已用完，远端数据源繁忙，请稍后重试）"


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
                  end: Optional[str], timeout: float = 60.0) -> Dict[str, Any]:
    """取单个组件；失败 / 空数据只标记该组件，不影响同页其它组件。

    `timeout` 是这条查询的剩余预算（秒），由 `_compute` 按整页 deadline 逐条算出来，
    保证被放弃的组件线程不会在后台继续占满 60s。
    """
    out = _empty_widget(w)

    # 明确未采集的模板：不发注定 500 的查询，直接给空态
    if not w["sql"]:
        out["empty"] = True
        out["empty_code"] = w["missing_code"] or "not_collected"
        return out

    # 月表固定近 13 个月；其余组件按页面日期区间
    sql = w["sql"] if w["fixed_range"] else _apply_range(w["sql"], start, end)
    try:
        # 本机 PG 优先、远端回退（2026-11-22）；整页预算 timeout 继续透传
        res = query_data(sql, db=REMOTE_DB, limit=int(w["limit"]),
                         timeout=max(1.0, timeout))
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


def _assemble(board: str, board_def: Dict[str, Any], start_v: Optional[str],
              end_v: Optional[str], results: Dict[str, Dict[str, Any]],
              t0: float, degraded: int) -> Dict[str, Any]:
    """按看板定义把「组件 id → 结果」装回 sections 结构。"""
    sections: List[Dict[str, Any]] = [
        {"key": sec["key"], "widgets": []} for sec in board_def["sections"]
    ]
    for si, sec in enumerate(board_def["sections"]):
        for w in sec["widgets"]:
            sections[si]["widgets"].append(results[w["id"]])

    all_widgets = [w for sec in sections for w in sec["widgets"]]
    return {
        "meta": {
            "db": REMOTE_DB,
            "host": data_source_label(),
            "board": board,
            "start": start_v,
            "end": end_v,
            "count": len(all_widgets),
            "failed": sum(1 for w in all_widgets if w["error"]),
            "empty": sum(1 for w in all_widgets if w["empty"]),
            "truncated": sum(1 for w in all_widgets if w["truncated"]),
            "degraded": degraded,
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
            "cached": False,
        },
        "title": board_def["title"],
        "sections": sections,
    }


def _busy_payload(board: str, start_v: Optional[str],
                  end_v: Optional[str]) -> Dict[str, Any]:
    """等锁超时且没有可回退的旧结果时，返回全组件带错误说明的页。"""
    board_def = get_board(board)
    msg = "数据源繁忙（取数排队超时），请稍后重试"
    results = {
        w["id"]: _failed_widget(w, msg)
        for sec in board_def["sections"] for w in sec["widgets"]
    }
    return _assemble(board, board_def, start_v, end_v, results,
                     time.time(), len(results))


def _compute(board: str, start_v: Optional[str], end_v: Optional[str],
             budget: float = BUILD_BUDGET) -> Dict[str, Any]:
    t0 = time.time()
    deadline = t0 + budget
    board_def = get_board(board)

    # 展平成 (section_idx, widget_def)，并发取数后按原结构装回
    flat: List[tuple] = []
    for si, sec in enumerate(board_def["sections"]):
        for w in sec["widgets"]:
            flat.append((si, w))

    results: Dict[str, Dict[str, Any]] = {}
    pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    try:
        pending = {}
        for _si, w in flat:
            remain = deadline - time.time()
            if remain <= 0:
                results[w["id"]] = _failed_widget(w, _timeout_message(budget))
                continue
            # 每条查询的超时 = 它启动时的剩余预算，避免被放弃的线程在后台继续占 60s
            pending[pool.submit(_fetch_widget, w, start_v, end_v, remain)] = w

        done, _not_done = wait(pending, timeout=max(0.0, deadline - time.time()))
        for fut, w in pending.items():
            if fut in done and not fut.cancelled():
                try:
                    results[w["id"]] = fut.result()
                except Exception as exc:  # noqa: BLE001 — 兜底，正常路径不应走到
                    out = _empty_widget(w)
                    out["error"] = f"{type(exc).__name__}: {exc}"[:300]
                    results[w["id"]] = out
            else:
                results[w["id"]] = _failed_widget(w, _timeout_message(budget))
    finally:
        # 不等待仍在跑的远端查询：预算已到，让它们在后台自行超时；
        # 队列里尚未开始的直接取消，避免继续加压力。
        pool.shutdown(wait=False, cancel_futures=True)

    degraded = sum(1 for w in results.values() if w.get("timeout"))
    if degraded:
        logger.warning("CN KPI 分组 %s 整页预算 %.0fs 用完：%d/%d 个组件超时",
                       board, budget, degraded, len(flat))
    return _assemble(board, board_def, start_v, end_v, results, t0, degraded)


def get_overview(board: str, start: Optional[str] = None,
                 end: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """整页数据。start/end 为 YYYY-MM-DD（含）；留空 = 近 31 天（与 CN KPI 一致）。

    并发与慢源处理（2026-11-22）：
    - 同一 `(board,start,end)` 只允许一个请求真去取数，其余等同一个结果；
      原实现让并发请求各算一遍，远端共享单机被 2 个标签页 × 20 个组件打爆，
      正是偶发 10~15s 的放大器。
    - 整页最多等 `BUILD_BUDGET` 秒，超时组件带明确错误返回，不再让浏览器干等
      到 120s 自己断开（2026-11-22 08:42 `?page=cs_cn_kpi` 实测 499）。
    - 只有**完整**结果才进缓存；残缺页不覆盖上一份完整结果，避免把一次抖动
      冻结 5 分钟。全部组件失败时回退上一份完整结果（标记 `stale`）。
    """
    start_v = _validate_day(start, "start")
    end_v = _validate_day(end, "end")
    if start_v and end_v and start_v > end_v:
        raise ValueError(f"开始日期不能晚于结束日期：{start_v} > {end_v}")

    key = (board, start_v or "", end_v or "")
    t_req = time.time()
    hit = _cache_entry(key)
    if hit is not None and not force:
        age = t_req - hit[0]
        if age < CACHE_TTL:
            return _cached_view(hit)
        if age > STALE_MAX:
            hit = None      # 太旧，不再当兜底

    # 页面上的「结束日期」是**含**的，SQL 的占位符按 < 右端点用：
    # 这里把结束日期 +1 天再交给 SQL，meta 仍回传用户选的那一天。
    end_excl = end_v
    if end_v:
        end_excl = (_date.fromisoformat(end_v) + _timedelta(days=1)).isoformat()

    lock = _key_lock(key)
    if not lock.acquire(timeout=BUILD_BUDGET + 10):
        logger.warning("CN KPI 分组 %s 等锁超过 %.0fs，返回繁忙页",
                       board, BUILD_BUDGET + 10)
        return _busy_payload(board, start_v, end_v)

    try:
        fresh = _cache_entry(key)
        if fresh is not None:
            age = time.time() - fresh[0]
            # 等锁期间别人刚算完：普通请求直接复用；force 请求只要结果比它到达更晚，
            # 也复用它（用户要的是「比刚才新」，不是「再算一遍」）。
            if age < CACHE_TTL and (not force or fresh[0] >= t_req):
                return _cached_view(fresh)

        data = _compute(board, start_v, end_excl, BUILD_BUDGET)
        data["meta"]["end"] = end_v
        if data["meta"]["degraded"] == 0:
            _cache_put(key, data)
        else:
            logger.warning("CN KPI 分组 %s 本次有 %d/%d 个组件超时，结果不进缓存",
                           board, data["meta"]["degraded"], data["meta"]["count"])
            if data["meta"]["failed"] == data["meta"]["count"]:
                fallback = _cache_entry(key)
                if fallback is not None and (time.time() - fallback[0]) <= STALE_MAX:
                    logger.warning("CN KPI 分组 %s 全部组件失败，回退上一份完整结果", board)
                    return _cached_view(fallback, stale=True)
        return data
    finally:
        lock.release()


def health() -> Dict[str, Any]:
    """远端只读 API 连通性探测（供页面诊断提示用）。"""
    try:
        res = query_remote("SELECT 1 AS ok", limit=1)
        return {"ok": True, "host": get_api_base_url(), "db": REMOTE_DB,
                "elapsed_ms": res.get("elapsed_ms")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "host": get_api_base_url(), "db": REMOTE_DB,
                "error": str(e)}

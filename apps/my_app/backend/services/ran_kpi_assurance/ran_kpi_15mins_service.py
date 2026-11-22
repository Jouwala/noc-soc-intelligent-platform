# -*- coding: utf-8 -*-
"""RAN KPI 15 分钟看板数据服务（?page=ran_kpi_15mins）

=========================== 这个服务是什么 ===========================

从 **RAN UME** 取 4 个 NOC Platform 模板的 **15 分钟粒度、Object Group = all
（全网）** PM 数据，落远端 SQLite `ran_kpi_daily.sqlite3` 的 `ran_kpi_15mins`
长表，供页面按 2G/3G/4G/5G 四个页签画 **Day0 / Day1 / Day7** 三条曲线。

4 个模板（`templates.txt`，2026-11-22 实测 `gr=900` / `grname=15 minutes`）：

    2G  2G_NW_KPI_Monitor_NOC_Platform   id=1638004472760  motid=gsmPm.cell
    3G  3G_NW_KPI_Monitor_NOC_Platform   id=1638004472762  motid=wPm.UtranCell,wPm.LogicCell
    4G  4G_NW_KPI_Monitor_NOC_Platform   id=1638004472763  motid=EUtranCellFDD
    5G  5G_NW_KPI_Monitor_NOC_Platform   id=1638004472759  motid=NRCellCU,NRCellDU,NRPhysicalCellDU

取数链路的细节（Object Group=all 的 payload、模板定义为何不写死、xlsx 如何解析、
同步窗口怎么算）在 `ran_kpi_15mins_query.py` 顶部；本文件负责**页面读取**与
**任务入口**。

=========================== 落库形状（长表） ===========================

`ran_kpi_daily.sqlite3.ran_kpi_15mins`：

    tech          2G / 3G / 4G / 5G
    metric_key    模板 items 里的指标 id（稳定主键）
    metric_name   模板 items 里的显示名（页面标题，原始全名在卡头 ⓘ）
    metric_order  指标在模板里的顺序（页面排序）
    unit          单位（模板给什么就用什么，可能为空）
    is_pct        1 = 百分比（模板 datatypeshow=7；值域 0~1，页面 ×100 显示）
    begin_time    "YYYY-MM-DD HH:MM:SS"
    end_time      同上（15 分钟窗口右端）
    granularity   "15 minutes"
    value         指标值

主键 `(tech, metric_key, begin_time)`，upsert 幂等。**为什么存长表**：4 个制式的
指标完全不同（13/11/18/24 个），宽表要么四张、要么 60+ 列大半 NULL；长表一张
装下，页面 SQL 也简单。单轮全量约 5.4 万行，SQLite 无压力。

=========================== Day0 / Day1 / Day7 ===========================

与 `cn_kpi_5mins` 同一口径：`substr("end_time",1,10)` 与 SQLite `date('now')`
比较，分三档打标签；`substr("end_time",12,5)` 做 HH:MM 横轴。

⚠ `date('now')` 取的是 **UTC** 日期。本机/浏览器时区是
`Atlantic/Reykjavik`（全年 UTC+0，无夏令时），两者一致；换时区前必须改这里。

Day7 = 7 天前（同一星期几）。同步窗口首轮覆盖 8 天，之后每轮只补最近
`OVERLAP_HOURS` 小时（默认 3 小时，见 query 模块），历史靠 upsert 累积 ——
Day7 能画出来取决于表里攒了几天，页面在缺 Day7 时会明说。

=========================== 缓存 / 单飞 ===========================

按制式 TTL 300s（任务 15 分钟一轮，5 分钟缓存不损失新鲜度），同一制式同一时刻
只允许一轮查询。页面四个页签按需取数（不一次拉 66 张图），切换页签才打远端。
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from backend.services.cn.cn_kpi_service import query_remote
from backend.services.db.pg_query import query_data
from backend.services.ran_kpi_assurance import ran_kpi_15mins_query as q

logger = logging.getLogger("ran_kpi_15mins")

CACHE_TTL = 300
EXPORT_LIMIT = 20000       # 单制式单次页面查询上限（24 指标 × 288 点 ≈ 6912）

# 长表 -> 页面宽表：三列 [HH:MM, 'Day0'|'Day1'|'Day7', 值]
# ⚠ 与 cn_kpi_5mins_service 的 _DAY_LABEL / _TIME_KEY 同口径（那边是 5 分钟表）
_DAY_LABEL = (
    "CASE WHEN substr(\"end_time\",1,10) = date('now') THEN 'Day0' "
    "WHEN substr(\"end_time\",1,10) = date('now','-1 day') THEN 'Day1' "
    "WHEN substr(\"end_time\",1,10) = date('now','-7 day') THEN 'Day7' END"
)


def run_ran_kpi_15mins_sync(full_backfill: bool = False) -> Dict[str, Any]:
    """任务入口（TASK_SPECS 的 loader 指向这里）：4 个制式并行同步。"""
    return q.run_sync(full_backfill=full_backfill)


# ============================================================
# 页面取数
# ============================================================
def _query_tech(tech: str) -> Dict[str, Any]:
    t0 = time.time()
    sql = (
        f'SELECT metric_key, metric_name, metric_order, unit, is_pct, '
        f'substr("end_time",12,5) AS t, {_DAY_LABEL} AS day, value '
        f'FROM "{q.TABLE}" '
        f'WHERE tech = \'{tech}\' AND substr("end_time",1,10) IN '
        f"(date('now'), date('now','-1 day'), date('now','-7 day')) "
        f"ORDER BY metric_order, metric_key, t"
    )
    # 本机 PG 优先、远端回退（2026-11-22）
    res = query_data(sql, db=q.REMOTE_DB, limit=EXPORT_LIMIT)
    if not res.get("success", True):
        raise RuntimeError(res.get("error") or "远端返回 success=false")
    rows = res.get("rows") or []

    widgets: Dict[str, dict] = {}
    for r in rows:
        if len(r) < 8:
            continue
        key, name, order, unit, is_pct, t, day, value = r[:8]
        if not day or str(day) in ("None", "null", "NULL"):
            continue
        w = widgets.get(str(key))
        if w is None:
            w = {
                "id": str(key), "title": str(name or key), "metric_name": str(name or key),
                "unit": unit or "", "order": int(order) if order is not None else 9999,
                "pct": bool(int(is_pct or 0)),
                "columns": ["Time", "Day", "Value"], "rows": [],
                "error": "", "empty": False, "empty_reason": "",
            }
            widgets[str(key)] = w
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = None
        w["rows"].append([str(t), str(day), v])

    out = sorted(widgets.values(), key=lambda w: (w["order"], w["title"]))
    for w in out:
        if not w["rows"]:
            w["empty"] = True
            w["empty_reason"] = "当前窗口（今天 + 昨天 + 7 天前）内暂无 15 分钟数据。"

    # 水位优先读本机 PG（页面展示用）；PG 异常时再问远端
    latest = ""
    try:
        _r = query_data(
            f'SELECT MAX("end_time") AS mx FROM "{q.TABLE}" WHERE tech = \'{tech}\'',
            db=q.REMOTE_DB, limit=1)
        _rows = _r.get("rows") or []
        latest = str(_rows[0][0]) if _rows and _rows[0] and _rows[0][0] else ""
    except Exception:  # noqa: BLE001 — 落回远端同一查询
        latest = q.remote_latest(tech)
    today = datetime.now(timezone.utc).date()
    return {
        "meta": {
            "db": q.REMOTE_DB,
            "table": q.TABLE,
            "tech": tech,
            "template": q.TEMPLATE_NAMES[tech],
            "days": {
                "d0": today.isoformat(),
                "d1": (today - timedelta(days=1)).isoformat(),
                "d7": (today - timedelta(days=7)).isoformat(),
            },
            "count": len(out),
            "failed": sum(1 for w in out if w["error"]),
            "empty": sum(1 for w in out if w["empty"]),
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
            "cached": False,
            "latest": latest,
        },
        "tech": tech,
        "title": f"RAN_KPI_15mins_{tech}",
        "widgets": out,
    }


_cache_lock = threading.Lock()
_cache: Dict[str, Dict[str, Any]] = {}
_flight: Dict[str, dict] = {}


def get_overview(tech: str, force: bool = False) -> Dict[str, Any]:
    """单个制式的整页数据。支持 TTL 缓存与单飞（同制式只跑一轮查询）。"""
    if tech not in q.TEMPLATES:
        raise ValueError(f"不支持的制式：{tech}")
    now = time.time()
    if not force:
        with _cache_lock:
            hit = _cache.get(tech)
            if hit is not None and (now - hit["at"]) < CACHE_TTL:
                data = hit["data"]
                data["meta"] = {**data["meta"], "cached": True}
                return data

    with _cache_lock:
        fl = _flight.get(tech)
        if fl is not None:
            ev, box = fl["ev"], fl["box"]
            leader = False
        else:
            ev, box = threading.Event(), {}
            _flight[tech] = {"ev": ev, "box": box}
            leader = True

    if not leader:
        if not ev.wait(timeout=q.POLL_TIMEOUT + 60):
            raise RuntimeError(f"{tech} 仍在取数，请稍后重试")
        if "error" in box:
            raise box["error"]
        return box["data"]

    try:
        data = _query_tech(tech)
        box["data"] = data
        with _cache_lock:
            _cache[tech] = {"at": time.time(), "data": data}
        return data
    except Exception as e:
        box["error"] = e
        raise
    finally:
        ev.set()
        with _cache_lock:
            cur = _flight.get(tech)
            if cur is not None and cur["ev"] is ev:
                _flight.pop(tech, None)


def health() -> Dict[str, Any]:
    """远端表连通性与最新数据时间。"""
    try:
        res = query_remote(
            f'SELECT COUNT(*) AS n, MAX("end_time") AS mx FROM "{q.TABLE}"',
            db=q.REMOTE_DB, limit=1)
        rows = res.get("rows") or []
        n, mx = (rows[0][0], rows[0][1]) if rows else (0, None)
        return {"ok": True, "db": q.REMOTE_DB, "table": q.TABLE, "rows": int(n or 0),
                "latest": mx}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "db": q.REMOTE_DB, "table": q.TABLE, "error": str(e)}

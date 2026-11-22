# -*- coding: utf-8 -*-
"""RAN KPI 15 分钟同步的取数链路（RAN UME -> xlsx -> 远端长表）。

从 `ran_kpi_15mins_service.py` 拆出来只为控制单文件体积；口径说明、落库形状、
Day0/Day1/Day7 定义都在 service 顶部，这里只做「登录 -> 查模板 -> 查询 -> 导出 ->
解析 -> upsert」和同步窗口计算。

=========================== Object Group = all ===========================

实测（2026-11-22）：`filterlayer="group"` + 空 `uilocations` 会被 UME 拒绝
（`0001610001 The object information can't be empty`）。本页要的是「全网汇总」，
对应 UME 的 **Object Group = all**：`filterlayer="all"` + `grouplayer="all"` +
`uilocations=[]`。每个 15 分钟窗口只返回一行全网值。

=========================== 模板定义不写死 ===========================

每轮先 `GET /api/pm-model/v1/templates/detail?id=<id>` 取指标清单（items /
unit / datatypeshow / position），模板在 UME 侧被改后下一轮自动跟随。这样不会
出现「代码里的列名和模板对不上、SQLite 把未知列名当字符串、曲线恒为 0」的事故。
"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.services.cn.cn_kpi_service import query_remote
from backend.services.db.db_writer_client import DbWriterClient
from backend.services.ran import ran_ume_service as ume

logger = logging.getLogger("ran_kpi_15mins")

REMOTE_DB = "ran_kpi_daily.sqlite3"
TABLE = "ran_kpi_15mins"
GR_15MINS = "900"
MAX_WORKERS = 4            # 4 个制式并发；RAN UME 单账号，量级与 main_kpi 一致
POLL_TIMEOUT = int(os.environ.get("RAN_KPI_15M_POLL_TIMEOUT", "600"))
LOOKBACK_DAYS = 8          # 首轮回补：覆盖今天 + 昨天 + 7 天前
# 增量窗口：从「最新数据 − N 小时」起。3 小时 = 12 个节拍，容忍连续失败；
# 任务停跑更久时窗口会自动变宽（起点跟着最新数据走），直到 LOOKBACK_DAYS 封底。
OVERLAP_HOURS = int(os.environ.get("RAN_KPI_15M_OVERLAP_HOURS", "3"))

# 模板清单（id 来自 templates.txt）
TEMPLATES: Dict[str, str] = {
    "2G": "1638004472760",
    "3G": "1638004472762",
    "4G": "1638004472763",
    "5G": "1638004472759",
}
TECHS: Tuple[str, ...] = ("2G", "3G", "4G", "5G")
TEMPLATE_NAMES = {
    "2G": "2G_NW_KPI_Monitor_NOC_Platform",
    "3G": "3G_NW_KPI_Monitor_NOC_Platform",
    "4G": "4G_NW_KPI_Monitor_NOC_Platform",
    "5G": "5G_NW_KPI_Monitor_NOC_Platform",
}

# 长表列；主键 (tech, metric_key, begin_time)，upsert 幂等
COLUMNS = ["tech", "metric_key", "metric_name", "metric_order", "unit", "is_pct",
           "begin_time", "end_time", "granularity", "value"]
PK = ["tech", "metric_key", "begin_time"]


# ============================================================
# UME 查询
# ============================================================
def _auth() -> Tuple[str, str]:
    """取 RAN UME 会话（cookie, forgery_token），复用 auth_manager 共享缓存。"""
    a = ume._login()
    return a.get("cookie", ""), a.get("forgery_token", "")


def _http(method: str, path: str, auth: Tuple[str, str],
          payload: Optional[dict] = None) -> Tuple[str, bytes]:
    body = (json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None else None)
    st, _h, _raw, data = ume._http_request(
        method, path, headers_extra=ume._pm_auth_headers(*auth), body=body)
    return st, data


def _fetch_template(tech: str, auth: Tuple[str, str]) -> Dict[str, Any]:
    """拉模板详情（指标清单的真相源）。"""
    st, body = _http("GET", f"/api/pm-model/v1/templates/detail?id={TEMPLATES[tech]}", auth)
    if "200" not in st:
        raise RuntimeError(f"取 {tech} 模板详情失败：{st} {body[:200]}")
    d = json.loads(body.decode("utf-8", errors="replace"))
    if not d.get("items"):
        raise RuntimeError(f"{tech} 模板详情没有 items，无法构造查询")
    return d


def _build_payload(tpl: Dict[str, Any], tech: str, start_iso: str, end_iso: str) -> dict:
    """15 分钟 + Object Group=all 的 history 查询 payload。"""
    items = tpl["items"]
    payload = {
        "arraycounterinfos": [],
        "datatype": 0,
        "displaymode": 0,
        "favoriteconditionname": tpl["templatename"],
        "gr": GR_15MINS,
        "groupattributetype": 0,
        "groupbygroup": False,
        "groupbyplmn": False,
        "grouptype": 0,
        "items": [it["id"] for it in items],
        "itemsposition": [it.get("position", 0) for it in items],
        "itemsposition1": [it.get("position1", 0) for it in items],
        "motid": tpl["motid"],
        "nfctid": tpl["nfctid"],
        "plmnpdfdisplaymode": False,
        "queryallbusiness": False,
        "showbusinesscolumn": bool(tpl.get("showbusinesscolumn")),
        "showintegrity": False,
        "straightquery": False,
        "templatename": tpl["templatename"],
        "templateid": TEMPLATES[tech],
        "needdeployfilter": False,
        "timeinfo": {
            "mode": 1, "start": start_iso, "end": end_iso, "relative": -1,
            "timezonetype": "1", "clienttimezone": "Atlantic/Reykjavik",
            "showtimezoneoffset": False,
            "querylongtime": {"type": 3, "value": 1},
            "timespanfilter": "00:00:00-24:00:00", "dayfiltertype": 0,
            "dayinmonthfilter": "1111111111111111111111111111111",
        },
        "indexfillingtype": tpl.get("indexfillingtype") or "default",
        "thresholdinfos": [],
        "templatetype": 0,
        # Object Group = all：全网汇总，不是某个 rangroup
        "filterlayer": "all",
        "grouplayer": "all",
        "containvcell": int(tpl.get("containvcell") or 0),
        "resfilterattribute": "",
        "uilocations": [],
        "reslocation": [],
        "inexistenceres": False,
        "firstDayOfWeek": 7,
        "iswireless": True,
        "hidehistorystopbutton": True,
        "opencustomizehistoryexport": False,
        "opentemplatecustomcolumn": False,
    }
    if tpl.get("businessmotid"):
        payload["businessmotid"] = tpl["businessmotid"]
        payload["groupbusinessids"] = []
        payload["businessfilterscope"] = []
    if tpl.get("itempositionbusiness"):
        payload["itempositionbusiness"] = tpl["itempositionbusiness"]
    return payload


def _wait_and_export(auth: Tuple[str, str], queryid: str, filename: str) -> bytes:
    """轮询到查询完成并导出 xlsx 字节。"""
    deadline = time.time() + POLL_TIMEOUT
    last = ""
    while time.time() < deadline:
        st, body = _http("GET", f"/api/pm-analysis/v1/history?type=0&queryid={queryid}", auth)
        if "200" in st:
            try:
                pd = json.loads(body.decode("utf-8", errors="replace"))
            except Exception:
                pd = {}
            last = f"state={pd.get('querystate')} progress={pd.get('progress')}"
            if pd.get("querystate") == 1 or (pd.get("progress") or 0) >= 100:
                break
        time.sleep(5)
    else:
        raise TimeoutError(f"查询 {queryid} 轮询超时（{POLL_TIMEOUT}s，{last}）")

    export_payload = {
        "id": queryid, "filename": filename, "chartlayout": 1, "contentmode": 0,
        "customizefieldtype": "0", "exportfiletype": 1, "exportserialnumber": False,
        "exporttableheaders": False, "formatwithts": 0, "imageinfo": [],
        "itemshowtype": 0, "tableheaders": {},
    }
    st, body = _http("POST", "/api/pm-analysis/v3/history/export", auth, export_payload)
    if "200" not in st:
        raise RuntimeError(f"导出失败：{st} {body[:200]}")
    return body


def _read_xlsx(data: bytes) -> Tuple[List[str], List[list]]:
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
    try:
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        headers = [str(c).strip() if c is not None else f"col_{i}"
                   for i, c in enumerate(next(rows))]
        body = [list(r) for r in rows if not all(v is None for v in r)]
    finally:
        wb.close()
    return headers, body


def _to_rows(tech: str, tpl: Dict[str, Any], headers: List[str],
             body: List[list]) -> List[list]:
    """xlsx 宽表 -> 长表行。指标列优先按名字匹配，其次按模板顺序。"""
    items = tpl["items"]
    idx = {h: i for i, h in enumerate(headers)}
    mapping: List[Tuple[int, Dict[str, Any], int]] = []
    for order, it in enumerate(items):
        col = idx.get(str(it["name"]))
        if col is None:
            guess = 3 + order
            if guess >= len(headers):
                logger.warning("[RAN15][%s] 指标 %s 不在导出表头里", tech, it["name"])
                continue
            col = guess
        mapping.append((col, it, order))

    out: List[list] = []
    for r in body:
        if len(r) < 3:
            continue
        begin, end, gran = r[0], r[1], r[2]
        if not begin or not end:
            continue
        for col, it, order in mapping:
            if col >= len(r):
                continue
            v = r[col]
            if v is None or v == "":
                continue
            try:
                val = float(v)
            except (TypeError, ValueError):
                continue
            out.append([
                tech, str(it["id"]), it["name"], order, it.get("unit") or "",
                1 if it.get("datatypeshow") == 7 else 0,
                str(begin), str(end), str(gran or "15 minutes"), val,
            ])
    return out


# ============================================================
# 同步窗口与单制式同步
# ============================================================
def remote_latest(tech: str) -> str:
    """该制式在远端表里的最新 end_time；查不到返回空串。"""
    try:
        # tech 来自 TEMPLATES 白名单，拼进 SQL 无注入面；远端 /api/query 不收参数绑定
        res = query_remote(
            f'SELECT MAX("end_time") AS mx FROM "{TABLE}" WHERE tech = \'{tech}\'',
            db=REMOTE_DB, limit=1)
    except Exception:  # noqa: BLE001 — 表还没建/远端抖动都按「无水位」处理
        return ""
    rows = res.get("rows") or []
    return str(rows[0][0]) if rows and rows[0] and rows[0][0] else ""


def window_start(tech: str, now: datetime, full: bool) -> datetime:
    """补数起点。

    首轮（无水位）或 full=True：回补 LOOKBACK_DAYS 天，保证 Day7 有数据。
    之后：从「最新数据 − OVERLAP_HOURS 小时」起，最多回看 LOOKBACK_DAYS 天 ——
    重叠窗口保证连续两次之间不缺档；任务停跑几天时起点会跟着最新数据一起后移，
    缺口不会漏在中间。
    """
    floor = now - timedelta(days=LOOKBACK_DAYS)
    if full:
        return floor.replace(hour=0, minute=0, second=0, microsecond=0)
    latest = remote_latest(tech)
    if not latest:
        return floor.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        dt = datetime.strptime(latest, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return floor.replace(hour=0, minute=0, second=0, microsecond=0)
    return max(dt - timedelta(hours=OVERLAP_HOURS), floor)


def run_one(tech: str, full: bool = False) -> Dict[str, Any]:
    """同步单个制式：取模板详情 -> 查询 -> 导出 -> 解析 -> upsert。"""
    t0 = time.time()
    auth = _auth()
    tpl = _fetch_template(tech, auth)
    now = datetime.now(timezone.utc)
    end_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    start_iso = window_start(tech, now, full).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    logger.info("[RAN15][%s] 查询 %s -> %s", tech, start_iso, end_iso)
    st, body = _http("POST", "/api/pm-analysis/v2/history", auth,
                     _build_payload(tpl, tech, start_iso, end_iso))
    if "200" not in st:
        raise RuntimeError(f"[{tech}] 创建查询失败：{st} {body[:300]}")
    data = json.loads(body.decode("utf-8", errors="replace"))
    qid = data.get("queryid") or data.get("id")
    if not qid:
        raise RuntimeError(f"[{tech}] 创建查询未返回 queryid：{body[:300]}")

    filename = f"{tech}_NW_KPI_Monitor_NOC_Platform_15mins_{now:%Y%m%d%H%M%S}"
    xlsx = _wait_and_export(auth, qid, filename)
    headers, body_rows = _read_xlsx(xlsx)
    rows = _to_rows(tech, tpl, headers, body_rows)
    if not rows:
        # 上游无数据不是故障（与 cnn_kpi_sync_5mins 一致：0 行跳过、不视为失败）
        logger.warning("[RAN15][%s] 导出 0 行，跳过写库", tech)
        return {"tech": tech, "rows": 0, "metrics": len(tpl["items"]),
                "elapsed": round(time.time() - t0, 1), "skipped": True}

    DbWriterClient().write_data(db=REMOTE_DB, table=TABLE, columns=COLUMNS, rows=rows,
                                mode="upsert", pk_columns=PK)
    logger.info("[RAN15][%s] 写入 %d 行（%d 指标，%s -> %s）", tech, len(rows),
                len(tpl["items"]), start_iso, end_iso)
    return {"tech": tech, "rows": len(rows), "metrics": len(tpl["items"]),
            "elapsed": round(time.time() - t0, 1), "skipped": False}


def run_sync(full_backfill: bool = False) -> Dict[str, Any]:
    """4 个制式并行同步。返回汇总（含每制式行数/耗时/错误）。"""
    full = full_backfill or os.environ.get("RAN_KPI_15M_FULL", "0") == "1"
    t0 = time.time()
    result: Dict[str, Any] = {"success": False, "full": full, "techs": {}, "errors": [],
                              "total_rows": 0, "elapsed_seconds": 0}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(run_one, tech, full): tech for tech in TECHS}
        for fut in as_completed(futures):
            tech = futures[fut]
            try:
                r = fut.result()
                result["techs"][tech] = r
                result["total_rows"] += r["rows"]
            except Exception as e:  # noqa: BLE001 — 单制式失败不拖垮其余制式
                logger.exception("[RAN15][%s] 同步失败", tech)
                result["errors"].append(f"{tech}: {e}")
                result["techs"][tech] = {"tech": tech, "error": str(e)}
    result["success"] = not result["errors"]
    result["elapsed_seconds"] = round(time.time() - t0, 1)
    logger.info("[RAN15] 同步结束：%s 行，错误 %d 条，耗时 %ss",
                result["total_rows"], len(result["errors"]), result["elapsed_seconds"])
    return result

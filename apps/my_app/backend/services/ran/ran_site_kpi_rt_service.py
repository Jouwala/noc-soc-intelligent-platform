# -*- coding: utf-8 -*-
"""按站点实时查询 RAN UME 4G 小区级 KPI（**不落库、不建表**）。

## 场景

投诉排查：知道用户所在站点，需要看该站点每个扇区、每个载波最近 7 天的 15 分钟曲线，
判断是"一直这样"还是"忙时才这样"。

## 为什么是"拉全网再本地过滤"

实测（2026-11-24，live UME 10.100.28.27）确认 UME 不支持按单个站点过滤历史查询：

| 手段 | 实测结果 |
| --- | --- |
| `resfilterattribute = "eNodeBId=4102"` / JSON 形式 | 无效，`totalcount` 仍是全网 27490 |
| `uilocations=[网元UUID]` + `filterlayer=all` | 被忽略，仍 27490 |
| `uilocations=[网元UUID]` + `filterlayer=group` | 直接返回 0 行 |
| `queryfilters` 带内容（4 种格式） | HTTP 499 拒绝 |

所以只能按 `grouplayer=me,eNodeBFunction,EUtranCellFDD` 拉小区级明细，**在本地按
`eNodeBId` 过滤出目标站点**。好在返回行自带 `eNodeB ID` / `eNodeB Name` /
`E-UTRAN FDD Cell Name` / `cellId`，不需要额外映射。

## 取数策略（性能约束来自实测）

全网 15 分钟粒度约 6950 行/时间点（96 点/天 → 约 66 万行/天）。连续 8 天纯 15 分钟
≈ 530 万行，单次导出既慢又会被 UME 截断（`cuthappened`）。因此按**天切片**取数，
每片取回后立刻过滤只剩目标站点（约 3~5 个小区 × 96 点），其余丢弃，内存恒定。

粒度默认 `auto`：近 24 小时用 15 分钟，更早的日期用 1 小时（图形上仍是同一条连续
时间轴，只是旧数据点稀疏）。传 `gran=15min` 可强制全程 15 分钟（慢，且可能超时）。

## 扇区归组

`SectorID` 来自 PG `app.ngi_lte_cell_param.sector_id`（同一扇区下挂多个载波小区，
例如 Wood Camp2 的 Sector 1 挂了 801/1815.1/1832.2/2310/2670 五个频点）。
工参未覆盖的小区（实测 928/6950，约 13%）退化处理：按小区名后缀数字归扇区，
并在 `warnings` 里明确标注，绝不静默编造。

## 缓存

进程内 `OrderedDict` + TTL（默认 600s）+ 条数上限（默认 6），不写任何磁盘。
换站点时分片 key 不同自然分开，过期即淘汰，进程重启即全清 —— 符合"临时存一下、
下次有新站点就清理"的要求。

## 为什么不用 `ran_ume_main_kpi._http_request_with_retry`

那套裸 socket 实现只按 `Content-Length` 判断响应结束；UME 对较大的 JSON 响应改用
`Transfer-Encoding: chunked`，于是每次请求都要**干等到 90s 读超时**才认为收完。
实测（7 指标 / 1 小时窗口）：

| 实现 | create | 取回 27k 行 |
| --- | --- | --- |
| 裸 socket | 90.4s | 92.1s |
| `http.client`（原生解 chunked） | 0.4s | 1.8s |

所以本模块自己用 `http.client.HTTPSConnection` 收发（它原生支持 chunked 与 keep-alive），
只复用 `mk._auth_headers` 产出的鉴权头。**不改动生产同步任务在用的那套实现。**
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import re
import ssl
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.database import get_raw_connection
from backend.services.auth_manager import get_auth
from backend.services.ran import ran_ume_main_kpi as mk
from backend.services.ran import ran_ume_pm_template as pm_template
from backend.services.ran.ran_ume_ne_filter import apply_ne_filter, resolve_ne

logger = logging.getLogger("ran_site_kpi_rt")

HOST, PORT = mk.HOST, mk.PORT
HISTORY_PATH = mk.HISTORY_PATH          # /api/pm-analysis/v2/history
PAGE_PATH = "/api/pm-analysis/v1/history/page"

# ---- 4G 小区级 grouplayer / 指标定义（全部实测确认） ----
GROUP_LAYER = "me,eNodeBFunction,EUtranCellFDD"
NFCTID = "ITBBU,SDR,#"
MOTID = "EUtranCellFDD"
BUSINESS_MOTID = "FDDV3.EUtranCellFDD,EUtranCellFDD,CUEUtranCellFDDLTE"

# (key, UME 指标 id, itemsposition1, itempositionbusiness, 单位)
METRICS_4G: Tuple[Tuple[str, str, int, int, str], ...] = (
    ("dl_prb", "1638004469342", 2, 1, "%"),
    ("ul_prb", "1638004469343", 2, 1, "%"),
    ("dl_traffic_gb", "1638004469330", 2, 1, "GB"),
    ("ul_traffic_gb", "1638004469331", 2, 1, "GB"),
    ("dl_thr_mbps", "1638004469332", 2, 1, "Mbps"),
    ("ul_thr_mbps", "1638004469785", 2, 1, "Mbps"),
    ("active_users", "C373384373", 1, 0, ""),
)
METRIC_LABELS = {
    "dl_prb": "DL PRB 利用率",
    "ul_prb": "UL PRB 利用率",
    "dl_traffic_gb": "下行业务量",
    "ul_traffic_gb": "上行业务量",
    "dl_thr_mbps": "下行小区吞吐",
    "ul_thr_mbps": "上行小区吞吐",
    "active_users": "活跃用户数",
    "dl_user_thr_mbps": "下行单用户速率",
    "ul_user_thr_mbps": "上行单用户速率",
    "active_ues": "活跃 UE 数",
}

# ---- 5G：小区(NRPhysicalCellDU)级。实测 2026-11-25 ----
# * `relative` 必须是 -7（和 4G 一致）。用 -1 时 UME 只返回窗口里的**第一个时间片**，
#   看起来像"5G 没有历史"，其实是参数问题；改 -7 后一天 96 个 15 分钟点齐全。
# * 小区级必须用 UME 页面抓包里的完整层级：`grouplayer=me,NRRadioInfrastructure,
#   NRPhysicalCellDU` + `basemotid=NRPhysicalCellDU`。只写 `grouplayer=me` 会退回到
#   网元级（实测同站 288 行 → 3351 行全网）；只下沉 `NRCellDU` 又只回 11 个时间片。
#   2026-11-25 曾据此误判"5G 只能到网元级"，用户抓包纠正后实测：单站 1 天 288 行
#   = 96 时间片 × 3 小区，每个小区都有完整曲线。
# * UME 的小区标识是 `NRPhysicalCellDU-1/2/3`，工参的小区名是 `站名-S0/S1/S2`。
#   映射键取 `masterOperatorId`（形如 `61807-4004-50`）的尾号 = 工参 `cell_id`，
#   两站实测吻合（Vai Town 50/51/52、Orange 21/22/23），不是靠顺序猜。
METRICS_5G: Tuple[Tuple[str, str, int, int, str], ...] = (
    ("dl_prb", "1656651157492", 2, 0, "%"),
    ("ul_prb", "1656651157491", 2, 0, "%"),
    ("dl_traffic_gb", "1656651157490", 2, 0, "GB"),      # 原始 MByte，落库前 /1024
    ("ul_traffic_gb", "1702277044952", 1, 0, "GB"),      # 原始 MByte，落库前 /1024
    ("dl_thr_mbps", "1656651157488", 2, 0, "Mbps"),
    ("ul_thr_mbps", "1656651157487", 2, 0, "Mbps"),
    ("active_ues", "1702277044516", 0, 0, ""),
    ("dl_user_thr_mbps", "1656651157484", 1, 0, "Mbps"),  # 5G 原生单用户速率
    ("ul_user_thr_mbps", "1656651157485", 1, 0, "Mbps"),
)

TECH_SPECS: Dict[str, Dict[str, Any]] = {
    "4G": {
        "metrics": METRICS_4G,
        "group_layer": GROUP_LAYER,
        "nfctid": NFCTID,
        "motid": MOTID,
        "business_motid": BUSINESS_MOTID,
        "show_business_column": True,
        "index_filling": "zeroDenominator",
        "filter_col": "eNodeBId",              # 值是纯站点号
        "param_table": "app.ngi_lte_cell_param",
        "site_id_col": "enodeb_id",
        "site_name_col": "enodeb_name",
        "cell_name_col": "cell_name",
        "divisor": {},                        # 无需换算
    },
    "5G": {
        "metrics": METRICS_5G,
        # 小区级三层：me → NRRadioInfrastructure → NRPhysicalCellDU（缺 basemotid 会退回网元级）
        "group_layer": "me,NRRadioInfrastructure,NRPhysicalCellDU",
        "base_motid": "NRPhysicalCellDU",
        "cell_col": "NRPhysicalCellDU",       # 'NRPhysicalCellDU-1/2/3'
        "cell_id_col": "masterOperatorId",    # '61807-4004-50' 尾号 = 工参 cell_id
        "nfctid": "ITBBU",
        "motid": "NRCellCU,NRCellDU,NRPhysicalCellDU",
        "business_motid": None,
        "show_business_column": True,
        # 取自模板详情（/api/pm-model/v1/templates/detail?id=1638004472759）：
        # 模板值是 zeroDenominator，写 default 时 UME 直接 499 "unknown error"。
        "index_filling": "zeroDenominator",
        "filter_col": "me",                   # 'BHP Congo Town(4032)'，值含括号 ID
        "param_table": "app.ngi_nr_cell_param",
        "site_id_col": "gnb_id",
        "site_name_col": "gnb_name",
        "cell_name_col": "cell_name",
        "divisor": {"dl_traffic_gb": 1024.0, "ul_traffic_gb": 1024.0},  # MByte -> GB
    },
}

# ---- 返回列 id（实测） ----
COL_BEGIN = "begintime"
COL_ENODEB_ID = "eNodeBId"
COL_CELL_NAME = "EUtranCellFDD"
COL_CELL_ID = "cellId"

DAY_MS = 24 * 3600
POLL_INTERVAL = 5
POLL_MAX = 90                 # 单片最长等 450s
PAGE_ROWS = int(os.environ.get("SITE_KPI_RT_PAGE_ROWS", "150000"))
CHUNK_DAYS = 1                # 按天切片
AUTO_15M_HOURS = int(os.environ.get("SITE_KPI_RT_AUTO_15M_HOURS", "24"))
CACHE_TTL = int(os.environ.get("SITE_KPI_RT_CACHE_TTL", "600"))
CACHE_MAX = int(os.environ.get("SITE_KPI_RT_CACHE_MAX", "6"))
JOB_TTL = int(os.environ.get("SITE_KPI_RT_JOB_TTL", "1800"))

_lock = threading.Lock()
_cache: "OrderedDict[str, Tuple[float, Dict[str, Any]]]" = OrderedDict()
_jobs: Dict[str, Dict[str, Any]] = {}


# ============================================================
# 工具
# ============================================================
def _norm(s: Any) -> str:
    """UME 的列名里混了 \\xa0（不换行空格），统一归一化后再比较。"""
    return re.sub(r"\s+", " ", str(s).replace("\xa0", " ")).strip()


def _parse_num(s: Any) -> Optional[float]:
    """UME 数值是字符串且百分比带 '%'：'6.89%' -> 6.89。"""
    if s is None:
        return None
    t = str(s).strip().replace("%", "").replace(",", "")
    if not t or t.lower() in ("null", "none", "-", "--", "nan"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _headers() -> str:
    """取会话并构造请求头。get_auth(probe=None) 不探活，缓存命中即返回。"""
    r = get_auth(HOST, PORT, *mk._runtime_credentials(), probe=None)
    if not r:
        raise RuntimeError("无法获取 RAN UME 会话（凭据或登录失败）")
    return mk._auth_headers(r["cookie"], r["forgery_token"])


# ---- HTTP：http.client 原生解 chunked，避免 UME 大响应时的 90s 干等 ----
_SSL_CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE
try:
    _SSL_CTX.set_ciphers("ALL:@SECLEVEL=0")   # 与 mk._create_ssl_socket 保持一致
except ssl.SSLError:                           # pragma: no cover
    pass

_http_local = threading.local()


def _header_dict() -> Dict[str, str]:
    """把 mk._auth_headers 的 `K: V\\r\\n` 串转成 dict，并强制 identity（不接 gzip）。"""
    out: Dict[str, str] = {}
    for line in _headers().split("\r\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    out["Accept-Encoding"] = "identity"
    return out


def _drop_conn() -> None:
    conn = getattr(_http_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    _http_local.conn = None


def _conn() -> http.client.HTTPSConnection:
    conn = getattr(_http_local, "conn", None)
    if conn is None:
        conn = http.client.HTTPSConnection(HOST, PORT, context=_SSL_CTX, timeout=300)
        _http_local.conn = conn
    return conn


def _post(path: str, body: dict, headers: Optional[Dict[str, str]] = None) -> dict:
    """POST JSON 并返回解析后的 dict。

    两类失败分开处理：
    * 连接层异常（HTTPException/OSError）→ 丢连接重试一次；
    * UME 业务错误 `0001619999 / HTTP 499 "An unknown error occurs"` → 实测是 UME
      侧限流/内部抖动（连续查询后必现，隔一会儿自愈），退避重试；
      而 `0001610001 "Query Condition is not Supported"` 是参数错，**不重试**直接抛。
    """
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    hdrs = dict(headers or _header_dict())
    hdrs["Content-Length"] = str(len(payload))
    last_exc: Optional[Exception] = None
    for attempt in (1, 2, 3):
        try:
            conn = _conn()
            conn.request("POST", path, body=payload, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read()
            text = data.decode("utf-8", "replace")
            if resp.status != 200:
                _drop_conn()
                permanent = "0001610001" in text or "not Supported" in text
                if permanent or attempt == 3:
                    raise RuntimeError(f"UME {path} 返回 HTTP {resp.status}: {text[:300]}")
                last_exc = RuntimeError(f"UME {path} 返回 HTTP {resp.status}: {text[:200]}")
                time.sleep(6 * attempt)      # UME 抖动：退避后重试
                continue
            try:
                return json.loads(text)
            except ValueError:
                raise RuntimeError(f"UME {path} 返回非 JSON：{text[:200]}")
        except (http.client.HTTPException, OSError) as exc:
            last_exc = exc
            _drop_conn()
            if attempt == 3:
                raise RuntimeError(f"UME {path} 请求失败：{exc}") from exc
            time.sleep(1.5)
    raise RuntimeError(f"UME {path} 请求失败：{last_exc}")  # pragma: no cover


def _get(path: str, headers: Optional[Dict[str, str]] = None) -> dict:
    """GET 并返回解析后的 dict（目前只用于拉 PM 模板详情，失败即抛，不做退避重试）。"""
    hdrs = dict(headers or _header_dict())
    try:
        conn = _conn()
        conn.request("GET", path, headers=hdrs)
        resp = conn.getresponse()
        text = resp.read().decode("utf-8", "replace")
    except (http.client.HTTPException, OSError) as exc:
        _drop_conn()
        raise RuntimeError(f"UME {path} 请求失败：{exc}") from exc
    if resp.status != 200:
        _drop_conn()
        raise RuntimeError(f"UME {path} 返回 HTTP {resp.status}: {text[:300]}")
    try:
        return json.loads(text)
    except ValueError:
        raise RuntimeError(f"UME {path} 返回非 JSON：{text[:200]}")


# ============================================================
# 站点与小区元数据（PG app.ngi_lte_cell_param）
# ============================================================
def search_sites(keyword: str = "", limit: int = 50, tech: str = "4G") -> List[Dict[str, Any]]:
    """按站点名/站点 ID 模糊搜索站点（tech=4G 查 LTE 工参，5G 查 NR 工参）。"""
    spec = TECH_SPECS.get(tech) or TECH_SPECS["4G"]
    table, sid, sname = spec["param_table"], spec["site_id_col"], spec["site_name_col"]
    kw = (keyword or "").strip()
    limit = max(1, min(int(limit or 50), 200))
    if tech == "5G":
        sql = (
            f'SELECT {sid} AS enodeb_id, MAX({sname}) AS name, COUNT(*) AS cells, '
            f'       COUNT(DISTINCT "azimuth") AS sectors, MAX(gnb_lat) AS lat, MAX(gnb_lon) AS lon '
            f"FROM {table} WHERE 1=1 "
        )
    else:
        sql = (
            f'SELECT {sid} AS enodeb_id, MAX({sname}) AS name, COUNT(*) AS cells, '
            f'       COUNT(DISTINCT sector_id) AS sectors, MAX(site_lat) AS lat, MAX(site_lon) AS lon '
            f"FROM {table} WHERE 1=1 "
        )
    params: List[Any] = []
    if kw:
        sql += f"AND ({sname} ILIKE ? OR {sid} = ? OR {sid} LIKE ?) "
        params += [f"%{kw}%", kw, f"{kw}%"]
    sql += f"GROUP BY {sid} ORDER BY ({sid} = ?) DESC, cells DESC LIMIT ?"
    params += [kw, limit]
    try:
        with get_raw_connection() as conn:
            cur = conn.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001 — 缺表时按空结果降级，不拦住页面
        logger.warning("站点搜索失败（%s 工参不可用）：%s", tech, exc)
        return []


def load_site_cells(enodeb_id: str, tech: str = "4G") -> List[Dict[str, Any]]:
    """取该站点在工参里的全部小区（含扇区号、cell_id 与频点，4G/5G 同构）。

    2026-11-25 修：5G 分支曾写死 `NULL AS sector_id`，但 `app.ngi_nr_cell_param`
    实际有 `sector_id`（如 16th Street-S0/S1/S2 分别是 1/2/3），导致 5G 永远拿不到
    扇区号。既然 5G 已 upgrade 到小区级，这里必须把真实扇区带出来。
    """
    spec = TECH_SPECS.get(tech) or TECH_SPECS["4G"]
    table, sid = spec["param_table"], spec["site_id_col"]
    if tech == "5G":
        sql = (
            f'SELECT cell_name, cell_id, sector_id, azimuth, dl_center_freq, '
            f'       dl_bandwidth, cell_lat, cell_lon FROM {table} WHERE {sid} = ? '
            f"ORDER BY sector_id, dl_center_freq, cell_name"
        )
    else:
        sql = (
            f'SELECT cell_name, cell_id, sector_id, azimuth, dl_center_freq, '
            f'       dl_system_bw, cell_lat, cell_lon FROM {table} WHERE {sid} = ? '
            f"ORDER BY sector_id, dl_center_freq, cell_name"
        )
    try:
        with get_raw_connection() as conn:
            cur = conn.execute(sql, [str(enodeb_id)])
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取工参小区失败 site=%s tech=%s：%s", enodeb_id, tech, exc)
        return []


def _resolve_site(site: str, tech: str = "4G") -> Dict[str, Any]:
    """把用户输入（站点 ID 或站点名）解析成唯一站点。"""
    spec = TECH_SPECS.get(tech) or TECH_SPECS["4G"]
    table, sid, sname = spec["param_table"], spec["site_id_col"], spec["site_name_col"]
    s = (site or "").strip()
    if not s:
        raise ValueError("请提供站点名称或站点 ID")

    rows: List[Dict[str, Any]] = []
    try:
        with get_raw_connection() as conn:
            cur = conn.execute(
                f"SELECT {sid}, MAX({sname}) FROM {table} WHERE {sid} = ? GROUP BY {sid}",
                [s],
            )
            rows = [{"enodeb_id": r[0], "name": r[1]} for r in cur.fetchall()]
            if not rows:
                cur = conn.execute(
                    f"SELECT {sid}, MAX({sname}) FROM {table} WHERE {sname} ILIKE ? "
                    f"GROUP BY {sid} ORDER BY {sid} LIMIT 5",
                    [f"%{s}%"],
                )
                rows = [{"enodeb_id": r[0], "name": r[1]} for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("站点解析查询失败：%s", exc)

    if not rows:
        raise ValueError(f"{tech} 工参里找不到站点「{s}」（可先用搜索接口确认）")
    if len(rows) > 1:
        names = "、".join(f"{r['name']}({r['enodeb_id']})" for r in rows[:5])
        raise ValueError(f"「{s}」匹配到多个站点，请用站点 ID 指定：{names}")
    return rows[0]


# ============================================================
# UME 取数
# ============================================================
_tpl_cache: Dict[str, Tuple[float, dict]] = {}


def _load_template(tech: str) -> dict:
    """拉 PM 模板详情（5G 的指标真相源），进程内缓存 10 分钟。

    模板在 UME 侧被改动后最迟 10 分钟自动跟随；拉取失败直接抛，不静默降级 ——
    5G 的 payload 缺指标会被 UME 499，拿旧模板硬猜只会掩盖问题。
    """
    tid = pm_template.TEMPLATE_IDS.get(tech)
    if not tid:
        raise RuntimeError(f"{tech} 没有登记 PM 模板 id")
    hit = _tpl_cache.get(tech)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    tpl = _get(f"/api/pm-model/v1/templates/detail?id={tid}")
    if not (tpl.get("items") or []):
        raise RuntimeError(f"{tech} 模板详情没有 items（id={tid}）")
    _tpl_cache[tech] = (time.time(), tpl)
    return tpl


def _build_payload(start: datetime, end: datetime, gran: str, tech: str = "4G",
                   ne: Optional[Dict[str, Any]] = None) -> dict:
    """构造历史查询请求体。ne 非空时改为服务端按网元过滤（见 ran_ume_ne_filter）。

    注意：`ne` 必须同时改 filterlayer 和 uilocations 才生效，只改一个会被 UME
    静默忽略并回全网数据 —— 两件事都封装在 apply_ne_filter 里，不要在这里拆开写。
    """
    if tech == "5G":
        # 5G 走模板驱动：本页只用 9 个指标，但必须把模板 24 项全部发过去，
        # 少一项 UME 就 499 即时拒绝（详见 ran_ume_pm_template 模块文档）。
        # build_from 默认 cell_level=True：grouplayer 下沉到 NRPhysicalCellDU 并带
        # basemotid，这才是小区级；relative=-7 保证时间片齐全。
        return apply_ne_filter(
            pm_template.build_from(_load_template("5G"), "5G", start, end, gran), ne)

    spec = TECH_SPECS.get(tech) or TECH_SPECS["4G"]
    metrics = spec["metrics"]
    items = [m[1] for m in metrics]
    payload: dict = {
        "arraycounterinfos": [], "datatype": 0, "displaymode": 0,
        "favoriteconditionname": "Site KPI RT",
        "gr": gran, "groupattributetype": 0, "groupbygroup": False,
        "groupbyplmn": False, "grouptype": 0,
        "items": items,
        "itemsposition": [0] * len(items),
        "itemsposition1": [m[2] for m in metrics],
        "itempositionbusiness": [m[3] for m in metrics],
        "motid": spec["motid"], "nfctid": spec["nfctid"],
        "plmnpdfdisplaymode": False, "queryallbusiness": False,
        "showbusinesscolumn": spec["show_business_column"], "showintegrity": False,
        "straightquery": False, "versiondatacompare": False, "needdeployfilter": False,
        "timeinfo": {
            # 4G 用 -7：用 -1 时 UME 只返回窗口内第一个时间片（2026-11-24 实测）。
            # 5G 不复现该问题，走模板驱动时用模板自身的 -1（见 ran_ume_pm_template）。
            "mode": 1, "start": _iso_z(start), "end": _iso_z(end), "relative": -7,
            "timezonetype": "1", "clienttimezone": "Atlantic/Reykjavik",
            "showtimezoneoffset": False,
            "querylongtime": {"type": 3, "value": 1},
            "timespanfilter": "00:00:00-24:00:00",
            "dayfiltertype": 0, "dayinmonthfilter": "1" * 31,
        },
        "indexfillingtype": spec["index_filling"],
        "templatetype": 0, "filterlayer": "all",
        "grouplayer": spec["group_layer"], "containvcell": 0,
        "resfilterattribute": "", "uilocations": [], "reslocation": [],
        "inexistenceres": False, "groupbusinessids": [], "businessfilterscope": [],
        "firstDayOfWeek": 7, "iswireless": True, "hidehistorystopbutton": True,
        "opentemplatecustomcolumn": False,
    }
    if spec.get("business_motid"):
        payload["businessmotid"] = spec["business_motid"]
    return apply_ne_filter(payload, ne)


def _me_base(name: str) -> str:
    """UME 网元名形如 'BHP Congo Town(4032)'，去掉尾部括号 ID 后与工参站名比对。"""
    return _norm(re.sub(r"\(\d+\)\s*$", "", _norm(name)))


def fetch_chunk(start: datetime, end: datetime, gran: str, site_key: str,
                tech: str = "4G", ne: Optional[Dict[str, Any]] = None,
                site_id: Optional[str] = None) -> List[list]:
    """取一个时间片的小区级明细（4G / 5G 都是小区级），只保留目标站点的行。

    `ne` 非空时请求本身就走服务端单网元过滤，回包只有该站数据（实测 55s→3.9s）；
    `ne` 为空（网元表里查不到该站）时拉全网，靠下面的 `_keep` 本地过滤。

    `site_id` 只用于 5G 的小区映射：UME 返回 `NRPhysicalCellDU-1/2/3` + `masterOperatorId`
    （尾号 = 工参 `cell_id`），据此换成工参小区名（`Vai Town-S0`），`build_cells` 才能
    读出扇区号和频点。省略时会从 `site_key` 解析一次。

    分页取（每页 PAGE_ROWS 行）而不是一次性 `pagesize=total`：全网一天 15 分钟粒度
    有 66 万行、约 165MB，整包解析会瞬间吃掉 1GB+ 内存。分页后每页解析完立刻丢弃
    非目标站点行，内存恒定在几十 MB。走单网元过滤时一天只有千行级，一页就取完。
    """
    spec = TECH_SPECS.get(tech) or TECH_SPECS["4G"]
    metrics = spec["metrics"]
    headers = _header_dict()
    resp = _post(HISTORY_PATH, _build_payload(start, end, gran, tech, ne), headers)
    qid = resp.get("queryid") or resp.get("id")
    if not qid:
        raise RuntimeError(f"创建查询失败：{str(resp)[:200]}")

    col_ids = [str(c.get("id")) for c in resp.get("columns") or []]
    missing = [m[1] for m in metrics if m[1] not in col_ids]
    if missing:
        # UME 对不支持的指标是「HTTP 200 + 静默丢列」，必须显式失败而不是当成空数据
        raise RuntimeError(f"UME 未返回指标 {missing}，实际列={col_ids}")

    idx = {cid: i for i, cid in enumerate(col_ids)}
    i_begin = idx[COL_BEGIN]
    i_key = idx[spec["filter_col"]]
    i_metrics = [idx[m[1]] for m in metrics]
    need = max(i_metrics)
    want = _me_base(site_key) if tech == "5G" else str(site_key).strip()

    # 必须用 UME 返回的**真实小区名**作为曲线标识。2026-11-25 修：此前 4G 这里
    # 统一写成站点 ID（want），导致一个站的 15 个小区全归成同一个 key → 永远只有
    # "1 cells"。5G 现已下沉到 NRPhysicalCellDU，同样要用真实小区标识。
    i_cell = idx.get(spec.get("cell_col") or COL_CELL_NAME)
    if i_cell is None:  # 列名大小写/后缀变动时按关键字兜底
        kw = "nrphysicalcelldu" if tech == "5G" else "eutrancell"
        i_cell = next((i for cid, i in idx.items() if kw in str(cid).lower()), None)

    # 5G：把 UME 的 NRPhysicalCellDU-1/2/3 映射成工参小区名（Vai Town-S0/...），
    # 这样 build_cells 才能从工参读出 sector_id 与 dl_center_freq。映射键是
    # masterOperatorId 的尾号（= 工参 cell_id），不是顺序猜。
    cell_id_map: Dict[str, str] = {}
    i_cell_id = None
    if tech == "5G":
        i_cell_id = idx.get(spec.get("cell_id_col"))
        cid_sid = site_id
        if not cid_sid:
            try:
                cid_sid = _resolve_site(site_key, tech)["enodeb_id"]
            except Exception as exc:  # noqa: BLE001 — 映射不到就保留 UME 原始标签
                logger.warning("5G 小区映射：解析站点 %s 失败：%s", site_key, exc)
        if cid_sid:
            for c in load_site_cells(str(cid_sid), tech):
                if c.get("cell_id") and c.get("cell_name"):
                    cell_id_map[str(c["cell_id"]).strip()] = str(c["cell_name"])

    ready = None
    for _ in range(POLL_MAX):
        d = _post(PAGE_PATH, {"type": 1, "queryfilters": [], "pagesize": 5, "page": 1,
                              "queryid": qid, "pagesortfields": []}, headers)
        if d.get("querystate") == 1 or (d.get("progress") or 0) >= 100:
            ready = d
            break
        time.sleep(POLL_INTERVAL)
    if ready is None:
        raise TimeoutError(f"查询 {qid} 未在 {POLL_MAX * POLL_INTERVAL}s 内就绪")

    total = int(ready.get("totalcount") or 0)
    divisors = spec.get("divisor") or {}
    out: List[list] = []

    def _keep(v: list) -> bool:
        if len(v) <= need:
            return False
        raw = v[i_key]
        # 5G 按网元名比对，必须忽略大小写：UME 写 'Liscr(4162)' 而工参写 'LISCR'，
        # 精确比对会把整站数据全部丢掉（2026-11-25 实测 LISCR 回全网后 rows=0）。
        # 4G 比的是 eNodeBId（纯数字），不受影响。
        return _me_base(raw).lower() == want.lower() if tech == "5G" else str(raw).strip() == want

    page = 1
    while True:
        d = _post(PAGE_PATH, {"type": 1, "queryfilters": [], "pagesize": PAGE_ROWS,
                              "page": page, "queryid": qid, "pagesortfields": []}, headers)
        if d.get("cuthappened"):
            raise RuntimeError(f"数据被 UME 截断（total={total}, page={page}）")
        rows = d.get("rows") or []
        if not rows:
            break
        for row in rows:
            v = row.get("values") or []
            if not _keep(v):
                continue          # 本地按站点过滤：UME 不支持服务端过滤
            vals = []
            for m, i in zip(metrics, i_metrics):
                x = _parse_num(v[i])
                div = divisors.get(m[0])
                if x is not None and div:
                    x = x / div
                vals.append(x)
            cell_label = want
            if i_cell is not None and i_cell < len(v):
                # _norm 顺带清掉 UME 列值里混入的 \xa0，保证与工参键一致
                cell_label = _norm(v[i_cell]) or want
            if tech == "5G" and cell_id_map and i_cell_id is not None and i_cell_id < len(v):
                # '61807-4004-50' -> '50' -> 工参 'Vai Town-S0'
                tail = _norm(v[i_cell_id]).rsplit("-", 1)[-1]
                mapped = cell_id_map.get(tail)
                if mapped:
                    cell_label = mapped
            out.append([v[i_begin], cell_label, None] + vals)
        if len(rows) < PAGE_ROWS or page * PAGE_ROWS >= total:
            break
        page += 1
    return out


# ============================================================
# 扇区/载波归组
# ============================================================
def _sector_from_name(cell_name: str) -> Optional[int]:
    """工参缺失时的兜底：从小区名后缀数字推扇区（如 'X-S0' -> 1）。"""
    m = re.search(r"-([A-Za-z]+)(\d+)\s*$", cell_name or "")
    if not m:
        return None
    try:
        return int(m.group(2)) + 1
    except ValueError:
        return None


def build_cells(enodeb_id: str, observed: List[str], tech: str = "4G") -> Tuple[List[Dict[str, Any]], List[str]]:
    """把工参小区与 UME 实际出现的小区合并成 cells 元数据（4G/5G 同构）。

    5G 在 2026-11-25 已升级到 NRPhysicalCellDU 小区级，不再返回"单个伪小区/整站
    合计"：fetch_chunk 已把 UME 的小区标识按 `masterOperatorId` 尾号映射成工参小区名，
    这里照常从工参读 sector_id 与频点即可。若该站不在 5G 工参里，则退化为按名后缀
    推断扇区并给出 warning，和 4G 的兜底口径一致。
    """
    spec = TECH_SPECS.get(tech) or TECH_SPECS["4G"]
    param_cells = load_site_cells(enodeb_id, tech)
    warnings: List[str] = []

    # 工参与 UME 的大小写/空白常不一致（工参 '10th street-S0' vs UME '10th Street-S0'），
    # 精确匹配会让整个扇区退化成 derived 并丢掉中心频点。用归一化键（空白折叠 + 小写）。
    meta: Dict[str, Dict[str, Any]] = {}
    for c in param_cells:
        meta.setdefault(_norm(c.get("cell_name")).lower(), c)
    warnings: List[str] = []
    cells: List[Dict[str, Any]] = []
    seen = set()

    for name in sorted(observed):
        key = (name or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        m = meta.get(_norm(key).lower())
        if m:
            sector = m.get("sector_id")
            freq = m.get("dl_center_freq")
            cell_id = m.get("cell_id")
            cell_type = "param"
        else:
            sector = _sector_from_name(key)
            freq, cell_id, cell_type = None, None, "derived"
        cells.append({
            "cell_name": key,
            "cell_id": cell_id,
            "sector": sector if sector is not None else 0,
            "carrier_mhz": freq,
            "carrier_label": (f"{freq:g} MHz" if isinstance(freq, (int, float)) else "未登记频点"),
            "source": cell_type,
        })

    derived = [c["cell_name"] for c in cells if c["source"] == "derived"]
    if derived:
        warnings.append(
            f"{len(derived)} 个小区不在 NGI 工参里，扇区号由小区名后缀推断："
            + "、".join(derived[:5]) + ("…" if len(derived) > 5 else "")
        )
    cells.sort(key=lambda c: (c["sector"] or 0, -(c["carrier_mhz"] or 0), c["cell_name"]))
    return cells, warnings


# ============================================================
# 主流程
# ============================================================
def _chunks(start: datetime, end: datetime) -> List[Tuple[datetime, datetime]]:
    out: List[Tuple[datetime, datetime]] = []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=CHUNK_DAYS), end)
        out.append((cur, nxt))
        cur = nxt
    return out


def _gran_for(span_end: datetime, now: datetime) -> str:
    """近 AUTO_15M_HOURS 小时用 15 分钟，更早用 1 小时（可用 gran=15min 强制全程）。"""
    return "900" if (now - span_end) <= timedelta(hours=AUTO_15M_HOURS) else "3600"


def collect_series(
    site: str,
    days: int = 8,
    gran: str = "auto",
    progress_cb=None,
    tech: str = "4G",
) -> Dict[str, Any]:
    """拉取站点 KPI 序列（不落库）。"""
    t0 = time.time()
    days = max(1, min(int(days or 8), 14))
    tech = tech if tech in TECH_SPECS else "4G"
    spec = TECH_SPECS[tech]
    metrics_def = spec["metrics"]
    resolved = _resolve_site(site, tech)
    eid = str(resolved["enodeb_id"])
    site_key = str(resolved.get("name") or eid) if tech == "5G" else eid

    now = _utc_now().replace(second=0, microsecond=0)
    end = now
    start = (now.replace(hour=0, minute=0) - timedelta(days=days - 1))

    # 服务端单网元过滤：整轮解析一次并复用。查不到（新开站 / 网元表同步滞后 /
    # 5G 编号体系不同）就回退全网 + 本地过滤，功能不因映射缺失而不可用。
    ne = resolve_ne(tech, eid, resolved.get("name"))
    rows: List[list] = []
    errors: List[str] = []

    if ne is not None:
        # 单站数据量小（8 天 15 分钟约 8.8 千行），**一次查完整个窗口**：省掉按天
        # 切片时每片都要重建查询 + 轮询的开销（实测 8 片 52.3s → 单次 7.0s），
        # 并且全程都是 15 分钟粒度 —— 全网路径做不到这点（一天 66 万行会吃掉 1GB+ 内存）。
        if progress_cb:
            progress_cb(0, 1)
        try:
            # site_id 供 5G 把 NRPhysicalCellDU 映射成工参小区名（拿扇区/频点）
            rows = fetch_chunk(start, end, "900", site_key, tech, ne, site_id=eid)
            if progress_cb:
                progress_cb(1, 1)
        except Exception as exc:  # noqa: BLE001
            # 不在这里报 100%，否则降级后进度会从满格退回，前端进度条来回跳
            logger.warning("单网元跨窗口查询失败（%s），回退按天切片 + 全网", exc)
            ne, rows = None, []

    if ne is None:
        # 全网路径必须按天切片，否则单片 66 万行会撑爆内存
        spans = _chunks(start, end)
        for i, (a, b) in enumerate(spans, 1):
            g = "900" if gran == "15min" else _gran_for(b, now)
            try:
                rows.extend(fetch_chunk(a, b, g, site_key, tech, None, site_id=eid))
            except Exception as exc:  # noqa: BLE001 — 单片失败不拖垮整体
                logger.warning("时间片 %s~%s 取数失败：%s", a, b, exc)
                errors.append(f"{a:%m-%d %H:%M}~{b:%H:%M} 取数失败：{exc}")
            if progress_cb:
                progress_cb(i, len(spans))

    if not rows and errors:
        raise RuntimeError("全部时间片取数失败：" + "；".join(errors[:2]))

    observed = sorted({str(r[1]) for r in rows})
    cells, warns = build_cells(eid, observed, tech)
    warnings = warns + errors

    # 组织成 {cell: {metric: [[ts, v], ...]}}
    series: Dict[str, Dict[str, List[list]]] = {c["cell_name"]: {} for c in cells}
    for r in rows:
        cell = str(r[1]).strip()
        bucket = series.setdefault(cell, {})
        ts = str(r[0])
        for k, m in enumerate(metrics_def):
            bucket.setdefault(m[0], []).append([ts, _parse_num(r[3 + k])])

    # 派生指标：单用户速率 = 小区吞吐 / 活跃用户数（5G 用原生单用户速率，不重算）
    if tech == "4G":
        for cell, bucket in series.items():
            for src, dst in (("dl_thr_mbps", "dl_user_thr_mbps"), ("ul_thr_mbps", "ul_user_thr_mbps")):
                au = dict((p[0], p[1]) for p in bucket.get("active_users", []))
                pts = []
                for ts, v in bucket.get(src, []):
                    u = au.get(ts)
                    pts.append([ts, round(v / u, 3) if v is not None and u not in (None, 0) else None])
                bucket[dst] = pts

    for bucket in series.values():
        for k in list(bucket.keys()):
            bucket[k].sort(key=lambda p: p[0])

    # 有数据但元数据里没有的桶（cells 的 cell_name 与行标签对不上）不会显示在前端，
    # 这类"静默空图"极难排查，宁可显式告警。
    stray = [k for k in series if k not in {c["cell_name"] for c in cells}]
    if stray:
        warnings.append(
            f"有 {len(stray)} 个小区名与工参元数据对不上，前端不会显示这些曲线：{stray[:3]}")

    # 统一 15 分钟时间轴（UME 返回的时间串就是 UTC 'YYYY-MM-DD HH:MM:SS'）。
    # 混合粒度（近 24h 15 分钟、更早 1 小时）如果直接取时间点并集，分类轴会把旧日期
    # 压缩成 1/4 宽，时间比例失真；所以给出等距网格，前端按网格对齐、缺值留 null。
    grid: List[str] = []
    g = start.replace(second=0, microsecond=0)
    g = g - timedelta(minutes=g.minute % 15)
    while g <= end:
        grid.append(g.strftime("%Y-%m-%d %H:%M:%S"))
        g += timedelta(minutes=15)

    return {
        "site": {"enodeb_id": eid, "name": resolved.get("name")},
        "tech": tech,
        # True = UME 侧已按网元过滤（只回本站数据）；False = 拉全网后本地过滤
        "server_filtered": ne is not None,
        "granularity_note": f"{tech} 为小区级",
        "range": {"start": _iso_z(start), "end": _iso_z(end), "days": days,
                  "gran": ("15分钟(全程)" if ne is not None else
                           "15min" if gran == "15min" else
                           "auto(近%d小时15分钟)" % AUTO_15M_HOURS)},
        "generated_at": _iso_z(_utc_now()),
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "row_count": len(rows),
        "grid": grid,
        "cells": cells,
        "metrics": [{"key": m[0], "label": METRIC_LABELS.get(m[0], m[0]), "unit": m[4]}
                    for m in metrics_def],
        "series": series,
        "warnings": warnings,
    }


# ============================================================
# 缓存 + 异步任务
# ============================================================
def _cache_key(site: str, days: int, gran: str, tech: str = "4G") -> str:
    return f"{tech}|{site.strip().lower()}|{days}|{gran}"


def cache_get(key: str) -> Optional[Dict[str, Any]]:
    with _lock:
        hit = _cache.get(key)
        if not hit:
            return None
        if time.time() - hit[0] > CACHE_TTL:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return hit[1]


def cache_put(key: str, value: Dict[str, Any]) -> None:
    with _lock:
        _cache[key] = (time.time(), value)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


def cache_clear() -> int:
    with _lock:
        n = len(_cache)
        _cache.clear()
        return n


def _gc_jobs() -> None:
    cutoff = time.time() - JOB_TTL
    with _lock:
        for jid in [j for j, v in _jobs.items() if v.get("created_at", 0) < cutoff and v.get("status") != "running"]:
            _jobs.pop(jid, None)


def start_job(site: str, days: int, gran: str, force: bool = False, tech: str = "4G") -> Dict[str, Any]:
    """启动异步取数任务；命中缓存则直接返回完成态。"""
    _gc_jobs()
    tech = tech if tech in TECH_SPECS else "4G"
    key = _cache_key(site, days, gran, tech)
    if not force:
        cached = cache_get(key)
        if cached:
            jid = uuid.uuid4().hex
            with _lock:
                _jobs[jid] = {"status": "done", "progress": 100, "message": "缓存命中",
                              "result": cached, "cached": True,
                              "created_at": time.time(), "updated_at": time.time()}
            return {"job_id": jid, "cached": True}

    jid = uuid.uuid4().hex
    job: Dict[str, Any] = {"status": "running", "progress": 0, "message": "开始取数…",
                           "result": None, "cached": False,
                           "created_at": time.time(), "updated_at": time.time()}
    with _lock:
        _jobs[jid] = job

    def _run() -> None:
        def cb(done: int, total: int) -> None:
            with _lock:
                job["progress"] = int(done * 100 / max(total, 1))
                job["message"] = f"已取 {done}/{total} 个时间片"
                job["updated_at"] = time.time()

        try:
            result = collect_series(site, days, gran, progress_cb=cb, tech=tech)
            cache_put(key, result)
            with _lock:
                job.update(status="done", progress=100, message="完成", result=result,
                           updated_at=time.time())
        except Exception as exc:  # noqa: BLE001
            logger.exception("站点 KPI 取数失败 site=%s", site)
            with _lock:
                job.update(status="error", message=str(exc), updated_at=time.time())

    threading.Thread(target=_run, name=f"site-kpi-{jid[:6]}", daemon=True).start()
    return {"job_id": jid, "cached": False}


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        return {
            "job_id": job_id,
            "status": job["status"],
            "progress": job.get("progress", 0),
            "message": job.get("message", ""),
            "cached": job.get("cached", False),
            "result": job.get("result"),
        }


def ping() -> Dict[str, Any]:
    t0 = time.time()
    r = get_auth(HOST, PORT, *mk._runtime_credentials(), probe=None)
    if not r:
        raise RuntimeError("无可用会话")
    return {"host": HOST, "port": PORT, "elapsed_ms": round((time.time() - t0) * 1000, 1)}

# -*- coding: utf-8 -*-
"""
丢包看板 API（Packet Loss Dashboard）
数据源: data/packet_lost/lte_pl.sqlite3（由 packet_loss_service 写入）
设计参考 alarm_dashboard.py：带 TTL 的内存缓存 + 下钻明细。
"""
import time
import re
import os
import sqlite3
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Query
from backend.database import DB_PATH as APP_DB_PATH, get_raw_connection
from backend.dialect_helpers import list_table_names

logger = logging.getLogger("packet_loss_dashboard")


def _rows_as_dicts(cursor):
    """cursor -> [dict, ...]（双模：sqlite Row / PG 元组均兼容）"""
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r)) for r in cursor.fetchall()]


router = APIRouter(prefix="/api/packet-loss-dashboard", tags=["packet-loss-dashboard"])

# 路径按仓库位置推导（2026-11-19 收口）：写库方 packet_loss_service.py 用的是本树 data/packet_lost。
PL_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "packet_lost" / "lte_pl.sqlite3"

STATUS_ORDER = ["Critical[>5]", "Major[1-5]", "Minor[0.5-1]", "Warning[0.1-0.5]", "No Packet loss[<0.1]"]

# ========== 站点类型（eflow_site.site_type：ERA / COOL / NORMAL） ==========
# 「站点类型」= 主数据 `eflow_site`.`site_type` 列，与 GIS / 人员页的「站点等级
# ERA/COOL/NORMAL」是**同一列同一口径**（不是 Indoor/Outdoor 那个「站型」）。
# 用户口述的 `COOLI` 在库里就是 `COOL`（docs/manual/11-feature-guide.md「要求原话」
# 一节已同样记载），界面一律显示库里的真值，不做同义词替换。
#: 主数据里查不到站点类型的行（实测 721 个丢包站点中有 4 个主数据缺档）归到这一档，
#: 免得「ERA + NORMAL 之和 ≠ 全部」被当成统计错误。
SITE_TYPE_UNKNOWN = "(unmapped)"
#: 下拉里的档位顺序（与 GIS 页的 ERA/COOL/NORMAL 固定顺序一致，不按字母序）
SITE_TYPE_ORDER = ["ERA", "COOL", "NORMAL"]
#: 站点类型索引缓存：eflow_site 每小时刷新一次，10 分钟足够新
_SITE_TYPE_TTL = 600

# `Managed Element` 形如 'Aureus Mining(4255)'，末尾括号里是站点 ID
_ME_CODE_RE = re.compile(r"\((\d+)\)\s*$")


# ========== 简易 TTL 缓存 ==========
_cache: dict = {}
_CACHE_TTL = 60
# 整页聚合结果（overview）要扫 packet_loss_data 最近 7 天 + 24 小时的全部读数，
# 重算一次约 1.9s；而源数据是小时级更新。给它更长的 TTL，避免用户每隔一分钟
# 打开页面就要等 2 秒（实测 60s TTL 下，长时间使用时会周期性地撞上冷重算）。
_OVERVIEW_TTL = 600


def _cache_get(key: str, ttl: int | None = None):
    item = _cache.get(key)
    if item and time.time() - item[0] < (ttl if ttl is not None else _CACHE_TTL):
        return item[1]
    return None


def _cache_set(key: str, value):
    _cache[key] = (time.time(), value)
    return value


def _conn():
    # 注意：不能用 with get_raw_connection(...)（退出时会提交并关闭连接）
    conn = get_raw_connection(str(PL_DB_PATH), timeout=30)
    if isinstance(conn, sqlite3.Connection):
        conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn, name: str) -> bool:
    return name in set(list_table_names(conn))


def _median(xs) -> float:
    """数值列表的中位值（忽略 None）；空列表返回 0.0。"""
    vals = [x for x in xs if x is not None]
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    m = n // 2
    med = s[m] if n % 2 else (s[m - 1] + s[m]) / 2
    return round(med, 2)


def _norm_site_type(value) -> str:
    """站点类型归一：去空格 + 大写；空值统一成 `SITE_TYPE_UNKNOWN`。

    只统一**同一档的写法**（`era` / ` ERA ` → `ERA`），**不做语义映射**
    （不把 `REGULAR` 当 `NORMAL`）：`eflow_site.site_type` 的值域实测只有
    ERA / COOL / NORMAL 三个值，多做映射反而会让页面档位与主数据对不上。
    真出现没见过的取值时，它会作为一个新档位自动出现在下拉里 —— 这是有意的，
    与 GIS 页 `gis.py::_site_type_stats()`「未知档按数量追加、不静默吞掉」同一取舍。
    """
    text = str(value or "").strip()
    if not text:
        return SITE_TYPE_UNKNOWN
    # 哨兵档本身不大写：`SITE_TYPE_UNKNOWN` 是小写写法，转大写后会与
    # `_attach_site_type` 写进行里的值对不上，筛「未匹配」会永远筛出 0 行。
    if text.lower() == SITE_TYPE_UNKNOWN.lower():
        return SITE_TYPE_UNKNOWN
    return text.upper()


def _site_type_index() -> dict:
    """站点类型索引：`Site_Code` / 站点名（小写）→ ERA / COOL / NORMAL（TTL 600s）。

    **为什么读主库实时表而不是 `site_pl_summary`**：那张表由 `packet_loss_sync`
    每天 06:30 整表重写，本列若只靠同步落库，改完代码要等到次日早上才有值 ——
    当天打开页面下拉是空的，看起来像功能没上线。改读 `eflow_site`（每小时刷新）
    后站点属性变更也能立刻反映；丢了主数据关联的站点归 `SITE_TYPE_UNKNOWN`。

    取数走 `get_raw_connection(APP_DB_PATH)`，SQLite / PG 双模都可用（PG 模式按
    路径映射到 `app` schema），与 `packet_loss_service._load_site_info()` 同源。
    """
    cached = _cache_get("site_type_index", _SITE_TYPE_TTL)
    if cached is not None:
        return cached
    index: dict = {"code": {}, "name": {}, "total": {}}
    try:
        conn = get_raw_connection(str(APP_DB_PATH), timeout=30)
        try:
            cur = conn.execute("SELECT site_id, site_name, site_type FROM eflow_site")
            rows = cur.fetchall() if cur.description else []
        finally:
            conn.close()
        for site_id, site_name, site_type in rows:
            raw = str(site_type or "").strip()
            st = _norm_site_type(site_type)
            code = str(site_id or "").strip()
            name = str(site_name or "").strip().lower()
            if code:
                index["code"][code] = st
            if name:
                index["name"][name] = st
            # 主数据里每个档位的站点总数（下拉显示「被监控 / 主数据」用）。
            # 空值不计入任何档位 —— 它不是一种「站点类型」，混进去会让各档之和
            # 悄悄超过主数据总数。
            if raw:
                index["total"][st] = index["total"].get(st, 0) + 1
    except Exception:  # noqa: BLE001 主数据取不到时降级为「全部未匹配」，不影响看板其它数字
        logger.warning("[丢包看板] 站点类型读取失败（eflow_site），筛选降级为未匹配", exc_info=True)
    return _cache_set("site_type_index", index)


def _attach_site_type(rows: list) -> list:
    """给汇总行补 `site_type` 字段（先按 Site_Code、再按站点名小写匹配）。"""
    index = _site_type_index()
    for row in rows:
        code = str(row.get("Site_Code") or "").strip()
        name = str(row.get("Site_Name") or "").strip().lower()
        row["site_type"] = index["code"].get(code) or index["name"].get(name) or SITE_TYPE_UNKNOWN
    return rows


def _site_type_want(site_type: str):
    """筛选参数 → 归一后的目标类型；`None` = 不筛选（全部）。

    `""` 与 `all`（大小写不敏感）都表示「全部」—— 下钻弹窗与地图请求会原样
    透传前端的当前选择，不能因为大小写差异把「全部」当成一个不存在的档位。
    """
    raw = str(site_type or "").strip()
    if not raw or raw.lower() == "all":
        return None
    return _norm_site_type(raw)


def _filter_site_type(rows: list, site_type: str) -> list:
    want = _site_type_want(site_type)
    return rows if want is None else [r for r in rows if r.get("site_type") == want]


def _site_type_counts(rows: list) -> dict:
    """各站点类型**被丢包监控**的站点数（下拉里的分子）。

    ⚠️ 与上一版的关键差别（2026-11-17 用户提问「为什么没有 COOLI 的统计」）：
    上一版只列出「有丢包读数」的档位，于是 COOL 直接从下拉里消失 —— 用户看到
    站点类型里没有 COOLI，第一反应是「功能漏做了」。现在**主数据里存在的档位
    一律列出**，没有读数就显示 0，并配合 `site_type_totals` 显示成 `0 / 64`：
    数字本身就把「有这类站点、但没被丢包模板覆盖」讲清楚了。

    实测（2026-11-17）：主数据 ERA 216 / COOL 64 / NORMAL 600，其中被丢包监控的
    是 197 / 0 / 520 —— 64 个 COOL 站在 UME 网元清单里**都在**（moc=SDR），但
    SDR / IPRAN 两个 IPPD 模板对它们一条读数都没有，所以不是筛选或关联的 bug。
    """
    monitored: dict = {}
    for row in rows:
        key = row.get("site_type") or SITE_TYPE_UNKNOWN
        monitored[key] = monitored.get(key, 0) + 1
    totals = _site_type_index().get("total", {})
    # 顺序：ERA / COOL / NORMAL 固定在前（用户口径），其余档位按主数据/读数合并后
    # 的集合追加，未匹配档恒排最后 —— 与 GIS 页 `_site_type_stats()` 同一取舍。
    known = set(monitored) | set(totals)
    order = [k for k in SITE_TYPE_ORDER if k in known]
    order += sorted(k for k in known if k not in order and k != SITE_TYPE_UNKNOWN)
    if SITE_TYPE_UNKNOWN in known:
        order.append(SITE_TYPE_UNKNOWN)
    return {k: monitored.get(k, 0) for k in order}


def _site_type_totals(counts: dict) -> dict:
    """各站点类型在主数据里的站点总数（下拉里的分母）。

    未匹配档没有主数据分母，用它的读数站点数顶上，保证前端「分子/分母」恒有值、
    不用在前端写 `?? 0` 这类会让缺数看起来像 0 的兜底。
    """
    totals = dict(_site_type_index().get("total", {}))
    out = {}
    for key, n in counts.items():
        out[key] = totals.get(key, n) if key != SITE_TYPE_UNKNOWN else n
    return out


def _me_in_site_type(me, codes: set, names: set) -> bool:
    """`Managed Element`（'Name(CODE)'）是否属于选定的站点集合。

    与 overview 里 Top 站点的关联口径一致：优先取末尾 (CODE)，没有 code 或 code
    不在集合里时退回按站点名（小写比较）。
    """
    text = str(me or "").strip()
    m = _ME_CODE_RE.search(text)
    if m:
        if m.group(1) in codes:
            return True
        return text[:m.start()].strip().lower() in names
    return text.lower() in names


def _load_summary_rows():
    """站点级汇总行（TTL 60s），每行补上 `site_type`（见 `_attach_site_type`）。"""
    cached = _cache_get("summary_rows")
    if cached is not None:
        return cached
    if not PL_DB_PATH.exists():
        return _cache_set("summary_rows", [])
    conn = _conn()
    try:
        if not _table_exists(conn, "site_pl_summary"):
            return _cache_set("summary_rows", [])
        rows = _rows_as_dicts(conn.execute(
            'SELECT * FROM site_pl_summary ORDER BY "LastDayMaxPL" DESC'))
    finally:
        conn.close()
    return _cache_set("summary_rows", _attach_site_type(rows))


def _load_meta():
    cached = _cache_get("meta")
    if cached is not None:
        return cached
    meta = {}
    if PL_DB_PATH.exists():
        conn = _conn()
        try:
            if _table_exists(conn, "pl_meta"):
                for r in _rows_as_dicts(conn.execute("SELECT key, value FROM pl_meta")):
                    meta[r["key"]] = r["value"]
        finally:
            conn.close()
    return _cache_set("meta", meta)


def _compute_overview(site_type: str) -> dict:
    """整页聚合（**纯计算，不碰缓存**）：状态分布 / Top站点 / 区域分布 / 每日趋势。

    `site_type` 一参到底：状态卡、饼图、区域分布、每日趋势、Top 站点**同时**按它
    过滤。刻意不做「只过滤状态卡」——同一页上一半数字筛过、一半没筛，用户对不上账
    只会当成 bug（与「下钻 WHERE 必须与概览一致」同一条纪律）。

    抽成独立函数是为了让预热能「算完再整体替换缓存」：如果预热先 pop 缓存再调带缓存的
    入口，重算的几秒里所有用户请求都会落空、一起冷算（每 8 分钟一次惊群）。
    """
    want = _site_type_want(site_type)
    rows_all = _load_summary_rows()
    rows = _filter_site_type(rows_all, site_type)
    meta = _load_meta()
    # 下拉的分子/分母只跟主数据走，与当前选中档位无关（切档后数字不变）
    _st_counts = _site_type_counts(rows_all)

    status_counts = {s: 0 for s in STATUS_ORDER}
    region_counts: dict = {}
    for r in rows:
        st = r.get("FinalStatus") or "No Packet loss[<0.1]"
        status_counts[st] = status_counts.get(st, 0) + 1
        if st != "No Packet loss[<0.1]":
            region = r.get("region") or "Unknown"
            bucket = region_counts.setdefault(region, {"sites": 0, "critical": 0, "max_pl": 0.0})
            bucket["sites"] += 1
            if st == "Critical[>5]":
                bucket["critical"] += 1
            bucket["max_pl"] = max(bucket["max_pl"], r.get("LastDayMaxPL") or 0)

    total_sites = len(rows)
    status_pie = [{"status": s, "count": status_counts.get(s, 0),
                   "pct": round(status_counts.get(s, 0) / total_sites * 100, 2) if total_sites else 0}
                  for s in STATUS_ORDER]

    # 按站点类型筛过的原始读数只算「选中的站点」：每日趋势与 Top 站点都要跟着筛，
    # 否则图上写着「受影响 300 站」、而页面顶部说选中档位只有 200 站。
    sel_codes = {str(r.get("Site_Code") or "").strip() for r in rows} if want else set()
    sel_names = {str(r.get("Site_Name") or "").strip().lower() for r in rows} if want else set()

    # 中位数口径说明（避免两类陷阱）：
    # 1) 对所有读数取中位会被 0 拉成 0（逐小时 pl 约 59% 为 0，零膨胀）→ 失去意义。
    #    解决：daily_trend 是「全网当天丢包强度」聚合，取非零中位 = 有丢包时的典型值，合理。
    # 2) 但站点级（top_sites）若也取非零中位，会被偶发的 100% 全断小时顶到 100——
    #    实际上「半天正常半天全断」的站点和「整天全断」的站点都成 100，丧失区分度。
    #    解决：站点级取「所有整点读数（含 0）」的中位 = 当天典型小时丢包率。
    #    已按 FinalStatus != No Packet loss 预过滤掉健康站点，所以中位不为 0；
    #    持续全断 → 100（事实），间歇严重 → 介于 0~100（区分出来）。
    site_median: dict = {}
    daily_trend = []
    # 与报告/汇总层口径一致：只展示完整自然日，当天未过完的数据不展示
    today_str = datetime.now().strftime("%Y-%m-%d")
    if PL_DB_PATH.exists():
        conn = _conn()
        try:
            if _table_exists(conn, "packet_loss_data"):
                # 每日：按天聚合 day -> 非零 pl 列表 + 受影响站点集合，用中位值替代求和
                max_row = _rows_as_dicts(conn.execute('SELECT MAX("Begin Time") AS mt FROM packet_loss_data'))
                cutoff7 = ""
                if max_row and max_row[0].get("mt"):
                    try:
                        max_dt = datetime.strptime(str(max_row[0]["mt"])[:19], "%Y-%m-%d %H:%M:%S")
                        cutoff7 = (max_dt - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
                    except (TypeError, ValueError):
                        cutoff7 = ""
                day_data: dict = {}
                if cutoff7:
                    for r in _rows_as_dicts(conn.execute(
                        'SELECT substr("Begin Time",1,10) AS day, '
                        '"Packet Loss Rate(%)" AS pl, "Managed Element" AS me '
                        'FROM packet_loss_data '
                        f'WHERE "Begin Time" >= \'{cutoff7}\' '
                        f'AND substr("Begin Time",1,10) < \'{today_str}\'')):
                        if want is not None and not _me_in_site_type(r["me"], sel_codes, sel_names):
                            continue
                        try:
                            pl_val = float(r["pl"] or 0)
                        except (TypeError, ValueError):
                            pl_val = 0.0
                        if pl_val > 0:
                            d = day_data.setdefault(r["day"], {"nz": [], "aff": set()})
                            d["nz"].append(pl_val)
                            if pl_val > 0.1:
                                d["aff"].add(r["me"])
                for day in sorted(day_data.keys()):
                    d = day_data[day]
                    daily_trend.append({"day": day, "median_pl": _median(d["nz"]),
                                        "affected_sites": len(d["aff"])})
                daily_trend = daily_trend[-7:]  # 仅保留最近 7 个完整自然日，与报告一致

                # Top Sites：最近 24 个完整小时（剔除当天）为窗口，计算每站点「所有读数」的中位值（含 0）。
                # packet_loss_data."Managed Element" 形如 "Name(CODE)"，而 site_pl_summary 用 Site_Name + Site_Code，
                # 故按末尾 (CODE) 提取后按 Site_Code 关联，失败再退回按 Site_Name 关联。
                cut = _rows_as_dicts(conn.execute(
                    'SELECT "Begin Time" FROM packet_loss_data '
                    f'WHERE "Begin Time" < \'{today_str}\' '
                    'GROUP BY "Begin Time" ORDER BY "Begin Time" DESC LIMIT 24'))
                if cut:
                    cutoff = cut[-1]["Begin Time"]  # 第 24 新的时刻 = 窗口起点
                    me_by_code: dict = {}
                    me_by_name: dict = {}
                    for r in _rows_as_dicts(conn.execute(
                        'SELECT "Managed Element" AS me, "Packet Loss Rate(%)" AS pl '
                        f'FROM packet_loss_data WHERE "Begin Time" >= \'{cutoff}\' AND "Begin Time" < \'{today_str}\'')):
                        me = r["me"]
                        if want is not None and not _me_in_site_type(me, sel_codes, sel_names):
                            continue
                        try:
                            pl_val = float(r["pl"] or 0)
                        except (TypeError, ValueError):
                            pl_val = 0.0
                        m = re.search(r"\((\d+)\)\s*$", me)
                        if m:
                            me_by_code.setdefault(m.group(1), []).append(pl_val)
                            me_by_name.setdefault(me[:m.start()].strip(), []).append(pl_val)
                        else:
                            me_by_name.setdefault(me, []).append(pl_val)
                    for code, vals in me_by_code.items():
                        site_median[code] = _median(vals)
                    for nm, vals in me_by_name.items():
                        site_median.setdefault(nm, _median(vals))
        finally:
            conn.close()

    def _resolve_median(row) -> float:
        code = str(row.get("Site_Code") or "")
        if code and code in site_median:
            return site_median[code]
        nm = site_median.get(row["Site_Name"])
        return nm if nm is not None else 0.0

    top_sites = [
        {"site_name": r["Site_Name"], "site_code": r.get("Site_Code") or "",
         "region": r.get("region") or "", "gateway": r.get("gateway") or "",
         "status": r.get("FinalStatus") or "",
         "last_day_max_pl": round(r.get("LastDayMaxPL") or 0, 2),
         "last_day_median_pl": round(_resolve_median(r), 2),
         "affected_hours": r.get("AffectedHours") or 0}
        for r in rows if (r.get("FinalStatus") or "") != "No Packet loss[<0.1]"
    ]
    top_sites.sort(key=lambda x: x["last_day_median_pl"], reverse=True)
    top_sites = top_sites[:15]

    # 要求3：Affected Sites = Critical + Major（严格按此定义，不含 Minor/Warning）
    affected_total = status_counts.get("Critical[>5]", 0) + status_counts.get("Major[1-5]", 0)
    return {
        "generated_at": meta.get("last_sync_time"),
        "report_path": meta.get("report_path"),
        # 当前生效的站点类型（"all" = 全部），前端据此回显下拉；
        # site_type_counts / site_type_totals 恒为**不过滤**的分布，供下拉展示
        # 「被丢包监控的站点数 / 主数据站点总数」—— COOL 档分子为 0 但有分母，
        # 用户一眼能看到「这类站点存在、只是没有丢包读数」。
        "site_type": want or "all",
        "site_type_counts": _st_counts,
        "site_type_totals": _site_type_totals(_st_counts),
        "total_sites": total_sites,
        "affected_sites": affected_total,
        "status_counts": status_counts,
        "status_pie": status_pie,
        "top_sites": top_sites,
        "region_dist": dict(sorted(region_counts.items(), key=lambda kv: -kv[1]["sites"])),
        "daily_trend": daily_trend,
    }


@router.get("/overview")
def pl_overview(site_type: str = Query(
        "", description="站点类型（eflow_site.site_type）：ERA / COOL / NORMAL；空或 all = 全部")):
    """丢包看板总览（带 600s 缓存；缓存键按站点档位分开，互不复用）。"""
    want = _site_type_want(site_type)
    cached = _cache_get(f"overview:{want or 'ALL'}", _OVERVIEW_TTL)
    if cached is not None:
        return cached
    return _cache_set(f"overview:{want or 'ALL'}", _compute_overview(site_type))


#: 周期预热间隔（秒）。必须**显著小于** `_OVERVIEW_TTL`（600s），否则「缓存刚过期」
#: 和「下一轮还没预热」会撞在一起，用户仍然撞上冷算。480s = 提前 2 分钟刷。
#: 成本（2026-11-24 在 MSNOC1 实测线上后端）：单档冷算 2.5–4.7s；页面下拉 5 个键
#: （ALL + ERA/COOL/NORMAL/未匹配）→ 一轮约 15–25s，摊到 480s 约 3–5% 的单线程占用；
#: 换来的是每次打开页面都走 1.5ms 的缓存命中（实测重复请求 1.5ms）。
#: ⚠ 该接口实测只有约 4 次/天，这笔账是「用后台空转换用户 5–9s 的等待」——
#: 11-19~11-23 有 31% 的请求以 499 收场（用户没等到就关了），所以按用户等待优先。
#: 嫌重就调大 `PL_WARMUP_REFRESH_SEC`，或设 0 只保留启动预热。
_WARMUP_REFRESH_SEC_DEFAULT = 480
#: 允许的间隔区间：低于 60s 等于后台连续扫全表；高于 TTL-30 会露出「已过期但没预热」的窗口。
_WARMUP_REFRESH_SEC_MIN = 60
_warmup_lock = threading.Lock()
_warmup_started = False


def _warmup_refresh_sec() -> int:
    """周期预热间隔：`PL_WARMUP_REFRESH_SEC` 覆盖，0 = 只做一次启动预热、不周期刷新。

    越界的值不静默接受：`_WARMUP_REFRESH_SEC_MIN` 以下是「后台连续扫表」，
    `_OVERVIEW_TTL-30` 以上会露出过期窗口；两者都 warning 后夹到边界。
    （超大值还会让 `time.sleep` 抛 OverflowError，把预热线程直接弄死。）
    """
    raw = (os.environ.get("PL_WARMUP_REFRESH_SEC") or "").strip()
    if not raw:
        return _WARMUP_REFRESH_SEC_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        logger.warning("[丢包看板] PL_WARMUP_REFRESH_SEC=%r 不是整数，回退默认 %ss",
                       raw, _WARMUP_REFRESH_SEC_DEFAULT)
        return _WARMUP_REFRESH_SEC_DEFAULT
    if value <= 0:
        return 0
    upper = max(_WARMUP_REFRESH_SEC_MIN, _OVERVIEW_TTL - 30)
    if value < _WARMUP_REFRESH_SEC_MIN or value > upper:
        clamped = min(max(value, _WARMUP_REFRESH_SEC_MIN), upper)
        logger.warning("[丢包看板] PL_WARMUP_REFRESH_SEC=%s 超出 [%s, %s]，按 %ss 执行",
                       value, _WARMUP_REFRESH_SEC_MIN, upper, clamped)
        return clamped
    return value


def _warmup_site_types() -> list:
    """要预热的档位：ALL + 下拉里实际会出现的每个档位（含「未匹配」档）。

    直接取 `_site_type_counts()` 的键 —— 它就是页面下拉的内容。多热一个不存在的档
    只会白扫一遍 7 天读数；少热一个，那个档就永远是冷算（本次要修的就是这个）。
    """
    types = [""]  # "" = ALL（pl_overview 的默认口径）
    try:
        types.extend(k for k in _site_type_counts(_load_summary_rows()) if k)
    except Exception:  # noqa: BLE001  档位解析失败时退回「只热全部」，不影响其它预热
        logger.warning("[丢包看板] 预热档位解析失败，本轮只预热「全部」档", exc_info=True)
    return types


def _warm_once() -> None:
    """把每个档位的 overview 各重算一遍。

    ⚠ **必须先失效缓存**（2026-11-24 审核发现的空转 bug）：`pl_overview` 一进来就查
    `_OVERVIEW_TTL`（600s）缓存，命中即 return —— 预热间隔 480s 时每轮都命中，既不重算
    也不推进缓存时间戳，等于什么都没做；真正的刷新周期被拖到 960s，中间还有约 360s
    缓存已过期。所以这里按与 `pl_overview` 完全相同的键先 pop 掉，再让它现算并重新落缓存。
    """
    for st in _warmup_site_types():
        t0 = time.time()
        try:
            # 走纯计算入口、算完再整体替换缓存：不会在重算期间把用户的缓存打空。
            payload = _compute_overview(st)
        except Exception:  # noqa: BLE001  单个档位失败不能拖垮整轮
            logger.warning("[丢包看板] 预热 %s 失败（继续下一档）", st or "ALL", exc_info=True)
            continue
        _cache_set(f"overview:{_site_type_want(st) or 'ALL'}", payload)
        logger.info("[丢包看板] 预热 %s 完成 %.0fms", st or "ALL", (time.time() - t0) * 1000)


def warmup(*, periodic: bool = True) -> None:
    """启动期预热 +（可选）后台周期刷新（2026-11-24 改）。

    旧行为只预热 `site_type=""`（ALL）。但 nginx access.log（11-19 切生产 ~ 11-23）
    实测 29 次 `/overview` 里 28 次带 `site_type=NORMAL` —— 预热的那个键谁也没命中，
    用户打开页面仍是 5.9–8.4 秒的冷算，31% 的请求（499）没等到就关了。

    现在：
      1. 按页面下拉里的**每个档位**预热（`_warmup_site_types()`），且每档先失效缓存再算；
      2. 每 `PL_WARMUP_REFRESH_SEC`（默认 480s，小于 600s 的 overview TTL）重算一轮，
         缓存到期前已经算好，用户请求始终命中缓存。

    `periodic=False` 时只预热一次（main.py 用它给「第二个仅 HTTP 实例」关掉周期线程，
    与 `pg_mirror_daemon` 同一套 `_got_lock` 门控）。
    线程创建失败会退回同步预热，不会把 `_warmup_started` 永久置位。
    """
    global _warmup_started
    with _warmup_lock:
        if _warmup_started:  # 真重复调用时不要起第二个线程
            return

    def _job() -> None:
        _warm_once()
        interval = _warmup_refresh_sec()
        if not periodic or interval <= 0:
            logger.info("[丢包看板] 周期预热已关闭（periodic=%s, interval=%ss）", periodic, interval)
            return
        while True:
            time.sleep(interval)
            _warm_once()

    try:
        threading.Thread(target=_job, name="pl-warmup", daemon=True).start()
    except Exception:  # noqa: BLE001  线程起不来就当场同步热一轮，别让 _warmup_started 卡死
        logger.warning("[丢包看板] 预热线程创建失败，改为同步预热一次", exc_info=True)
        _warm_once()
    else:
        with _warmup_lock:
            _warmup_started = True


@router.get("/map-sites")
def pl_map_sites(include_ok: bool = Query(False),
                 site_type: str = Query("", description="站点类型过滤：ERA / COOL / NORMAL；空或 all = 全部")):
    """地图点位：有经纬度的站点 + 丢包状态（仅返回有丢包的站点 + 可选全部）"""
    rows = _filter_site_type(_load_summary_rows(), site_type)
    sites = []
    for r in rows:
        st = r.get("FinalStatus") or "No Packet loss[<0.1]"
        if st == "No Packet loss[<0.1]" and include_ok is not True:
            continue
        if r.get("latitude") is None or r.get("longitude") is None:
            continue
        sites.append({
            "site_name": r["Site_Name"], "site_code": r.get("Site_Code") or "",
            "lat": r["latitude"], "lng": r["longitude"],
            "region": r.get("region") or "", "province_county": r.get("province_county") or "",
            "gateway": r.get("gateway") or "", "network_type": r.get("network_type") or "",
            "site_type": r.get("site_type") or SITE_TYPE_UNKNOWN,
            "status": st, "last_day_max_pl": round(r.get("LastDayMaxPL") or 0, 2),
            "total_pl": round(r.get("TotalPL") or 0, 2),
            "affected_hours": r.get("AffectedHours") or 0,
        })
    return {"sites": sites, "total": len(sites)}


@router.get("/drilldown")
def pl_drilldown(status: str = Query(""), search: str = Query(""),
                 site_type: str = Query("", description="站点类型过滤：ERA / COOL / NORMAL；空或 all = 全部"),
                 limit: int = Query(500)):
    """下钻明细：按状态/关键字/站点类型过滤站点汇总

    站点类型必须与 overview 的 `site_type` 同一口径（同一个 `_filter_site_type`），
    否则「状态卡上的数字」与「点下去的行数」对不上。
    """
    rows = _filter_site_type(_load_summary_rows(), site_type)
    if status and status != "all":
        rows = [r for r in rows if r.get("FinalStatus") == status]
    if search:
        kw = search.lower()
        rows = [r for r in rows
                if kw in str(r.get("Site_Name", "")).lower()
                or kw in str(r.get("Site_Code", "")).lower()
                or kw in str(r.get("region", "")).lower()]
    total = len(rows)
    rows = rows[:limit]
    title = status if status and status != "all" else "All Sites"
    return {"title": title, "total": total, "data": rows}


@router.get("/site-detail")
def pl_site_detail(site_code: str = Query(""), site_name: str = Query("")):
    """单站点下钻（地图圆点点击）：汇总 + 近7完整日逐日聚合 + 最近完整日逐小时明细。

    窗口与 overview 完全一致：只取完整自然日（剔除当天），起点 = MAX(Begin Time)-7天。
    站点身份与 overview 的 _resolve_median 同口径：优先按 Managed Element 末尾 (CODE)
    匹配，无 code 时退回按名称精确匹配。只读 SELECT，参数化查询防注入。
    逐日/逐小时中位取「全部读数（含 0）」——站点级口径，与 top_sites 一致（见 overview 注释）。
    """
    key = f"site_detail:{site_code}|{site_name}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    rows = _load_summary_rows()
    summary = None
    if site_code:
        summary = next((r for r in rows if str(r.get("Site_Code") or "") == site_code), None)
    if summary is None and site_name:
        summary = next((r for r in rows if r.get("Site_Name") == site_name), None)

    empty = {"site": None, "daily": [], "hourly": [], "hourly_day": "", "total": 0}
    if summary is None or not PL_DB_PATH.exists():
        return _cache_set(key, empty)

    code = str(summary.get("Site_Code") or "")
    name = summary.get("Site_Name") or ""
    today_str = datetime.now().strftime("%Y-%m-%d")
    conn = _conn()
    try:
        if not _table_exists(conn, "packet_loss_data"):
            return _cache_set(key, empty)
        max_row = _rows_as_dicts(conn.execute('SELECT MAX("Begin Time") AS mt FROM packet_loss_data'))
        cutoff7 = ""
        if max_row and max_row[0].get("mt"):
            try:
                max_dt = datetime.strptime(str(max_row[0]["mt"])[:19], "%Y-%m-%d %H:%M:%S")
                cutoff7 = (max_dt - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                cutoff7 = ""
        if not cutoff7:
            return _cache_set(key, empty)
        # 参数化：code 优先（末尾 (CODE)），否则名称精确匹配
        if code:
            cond, param = '"Managed Element" LIKE ?', f"%({code})"
        else:
            cond, param = '"Managed Element" = ?', name
        raw = _rows_as_dicts(conn.execute(
            'SELECT substr("Begin Time",1,10) AS day, substr("Begin Time",12,2) AS hour, '
            '"Packet Loss Rate(%)" AS pl '
            f'FROM packet_loss_data WHERE {cond} '
            'AND "Begin Time" >= ? AND substr("Begin Time",1,10) < ? '
            'ORDER BY day, hour', (param, cutoff7, today_str)))
    finally:
        conn.close()

    by_day: dict = {}
    for r in raw:
        try:
            pl_val = float(r["pl"] or 0)
        except (TypeError, ValueError):
            pl_val = 0.0
        d = by_day.setdefault(r["day"], {})
        d.setdefault(r["hour"], []).append(pl_val)

    daily = []
    for day in sorted(by_day.keys()):
        hours = by_day[day]
        all_vals = [v for vs in hours.values() for v in vs]
        daily.append({
            "day": day,
            "max_pl": round(max(all_vals), 2) if all_vals else 0.0,
            "median_pl": _median(all_vals),
            "affected_hours": sum(1 for vs in hours.values() if max(vs) > 0.1),
            "readings": len(all_vals),
        })

    hourly = []
    hourly_day = ""
    if daily:
        hourly_day = daily[-1]["day"]
        for hour in sorted(by_day[hourly_day].keys()):
            vs = by_day[hourly_day][hour]
            hourly.append({
                "hour": f"{hour}:00",
                "max_pl": round(max(vs), 2),
                "median_pl": _median(vs),
                "readings": len(vs),
                "affected": 1 if max(vs) > 0.1 else 0,
            })

    site = {
        "site_name": name, "site_code": code,
        "status": summary.get("FinalStatus") or "No Packet loss[<0.1]",
        "region": summary.get("region") or "", "province_county": summary.get("province_county") or "",
        "gateway": summary.get("gateway") or "", "network_type": summary.get("network_type") or "",
        "site_type": summary.get("site_type") or SITE_TYPE_UNKNOWN,
        "last_day_max_pl": round(summary.get("LastDayMaxPL") or 0, 2),
        "total_pl": round(summary.get("TotalPL") or 0, 2),
        "affected_hours": summary.get("AffectedHours") or 0,
    }
    return _cache_set(key, {"site": site, "daily": daily, "hourly": hourly,
                            "hourly_day": hourly_day, "total": len(hourly)})

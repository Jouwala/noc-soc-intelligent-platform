# -*- coding: utf-8 -*-
"""CS / PS CN KPI **15 分钟**看板的组件清单（D0 / D1 / D7 叠画）。

=========================== 这两个页面是什么 ===========================

`?page=cs_cn_kpi_15mins` / `?page=ps_cn_kpi_15mins` 是
`?page=cs_cn_kpi` / `?page=ps_cn_kpi` 的**细粒度对照版**：

    · 只取 UME（自动同步）那一段，**不出现 EMS**（用户 2026-11-22 明确要求）；
    · 指标与参考页的 UME 段一一对应（标题、单位、数据表、列名都不改）；
    · 每张卡把 **D0（今天）/ D1（昨天）/ D7（7 天前，同一星期几）**
      三条曲线叠在同一根「时刻」轴上，用来判断同一时刻是涨是跌；
    · 横轴按 **15 分钟桶**（00:00 / 00:15 / … / 23:45）。

=========================== 15 分钟数据从哪来 ===========================

远端 db_ps_cs_cn 里**没有**一张统一的 15 分钟宽表，同一页的卡片按上游能力
分三档取数（`grain` 字段，前端在卡头用角标写明，避免把小时点误读成 15 分钟点）：

    15min  上游模板本身就是 15 分钟档：`5mins_xGW_CPU_USAGE_*`、
           `5mins_xGW_LICENSE_OCCUPANCY_*`、`5mins_3G_FDD_*`。
    5min   上游是 5 分钟档，这里按 15 分钟桶聚合（一桶 3 个采样）：
           VLR / Voice / Data Subscribers / 4G FDD / Free Rating。
    1h     上游只有小时档（`cn_hourly_kpi_sync`，gr=3600）：
           MGW/MRFP 利用率与容量、xGW 吞吐/会话容量、DNS。
           这类卡片只会在整点出现数据点（横轴仍是 15 分钟刻度），
           **不做前向填充**：没有 15 分钟数据就是没有，不造点。

为什么参考页里 xGW CPU / License 的「日粒度」卡不搬过来：那是同一指标的
另一种时间视角（按天平均），放到 15 分钟页上是恒定的直线，没有信息量；
日/月口径仍在 `ps_cn_kpi` 页看。指标本身（CPU、License、RG 流量）一张不少。

=========================== 聚合口径 ===========================

    比率 / 带百分号的量（利用率、CPU、License、DNS） → AVG（算术平均，
        不是加权、不是峰值 —— 与参考页 `_MGW_UTIL_COLS` / `ps_ume_dns` 一致）；
    用户数 / 占用量这类**同一时刻多网元取合计**的 gauge → SUM(值)/COUNT(不同时刻)，
        即「先按时刻把网元加起来，再对 15 分钟桶内的采样取平均」；
    流量字节 → SUM(值)/1e9（GB；与参考页 /1e9 一致）；
    Voice 的 Seized Traffic(Erl) 是话务强度 → AVG，**不跨采样相加**
        （参考页对日表用 SUM 是因为日表一天只有一行，SUM 等于原值）。

=========================== 时间键与窗口 ===========================

    · 日切 / 横轴都取 **Begin Time**（统计区间左端点），与 `cn_kpi_split_widgets`
      的 UME 段一致；`cn_kpi_5mins` 取 End Time 是历史口径，本页不复用。
    · 窗口固定为「今天 + 昨天 + 7 天前」三个**日子**，不是连续 7 天：
      `date('now')` / `date('now','-1 day')` / `date('now','-7 day')`。
    · D7 能不能画出来取决于库里已攒几天（各表最早 10-14 / 10-18 / 10-20），
      缺哪天就少哪条线，前端按实际返回的标签画，不补零。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# ----------------------------------------------------------------------
# SQL 片段
# ----------------------------------------------------------------------
# 上游数值列是 TEXT，空串 / "-" 必须变成 NULL（断线），不能 CAST 成 0 混进均值。
def _num(col: str) -> str:
    q = f'"{col}"'
    return f"CASE WHEN TRIM({q}) GLOB '*[0-9]*' THEN CAST(TRIM({q}) AS REAL) END"


# 15 分钟桶的左端点：'YYYY-MM-DD HH:MM'（分钟向下取整到 00/15/30/45）
_BUCKET = (
    'substr("Begin Time",1,14) '
    "|| printf('%02d', (CAST(substr(\"Begin Time\",15,2) AS INTEGER)/15)*15)"
)
# 输出只保留 HH:MM，D0/D1/D7 三个日子共用同一根时刻轴
_TIME_KEY = f"substr({_BUCKET},12,5)"

# 日标签：今天 / 昨天 / 7 天前（同一星期几）。用 SQLite 的 date('now')（UTC），
# 与采集侧写库的 UTC 时间基准一致。
_DAY_LABEL = (
    'CASE WHEN substr("Begin Time",1,10) = date(\'now\') THEN \'D0\' '
    'WHEN substr("Begin Time",1,10) = date(\'now\',\'-1 day\') THEN \'D1\' '
    'WHEN substr("Begin Time",1,10) = date(\'now\',\'-7 day\') THEN \'D7\' END'
)
# 只扫这三个日子：中间 5 天不进来，避免 GROUP BY 把无意义的日子也聚出来
_WINDOW = (
    'substr("Begin Time",1,10) IN '
    "(date('now'), date('now','-1 day'), date('now','-7 day'))"
)

# 聚合方式 → SQL 表达式模板（{n} 由 _num() 替换）
_AGG = {
    # 百分比 / 比率：对桶内所有采样（含多网元）取算术平均
    "avg": "AVG({n})",
    # 同一时刻多网元取合计，再对桶内采样取平均（用户数、占用信道数等 gauge）
    "total": 'SUM({n})/COUNT(DISTINCT "Begin Time")',
    # 用量型计数：桶内所有行求和（跨网元、跨 5 分钟采样）
    "sum": "SUM({n})",
}


def _melt(table: str, dims: List[tuple], agg: str = "avg",
          where: str = "", scale: float = 0, dec: int = 2) -> str:
    """把同一张表的若干列反透视成 [时刻, 序列, 日, 值] 四列。

    dims: [(列名, 序列显示名)]；agg: `_AGG` 的键；scale>0 时在聚合后除以此值
    （流量字节 → GB 用 1e9）。
    """
    w = f" AND {where}" if where else ""
    branches: List[str] = []
    for col, label in dims:
        expr = _AGG[agg].format(n=_num(col))
        if scale:
            expr = f"({expr})/{scale}"
        branches.append(
            f"SELECT {_TIME_KEY} AS t, '{label}' AS series, "
            f"{_DAY_LABEL} AS day, ROUND({expr},{dec}) AS v "
            f'FROM "{table}" WHERE {_WINDOW}{w} GROUP BY t, day'
        )
    return " UNION ALL ".join(branches) + " ORDER BY t"


def _one(table: str, col: str, label: str, agg: str = "avg",
         where: str = "", scale: float = 0, dec: int = 2) -> str:
    """单序列组件的薄封装。"""
    return _melt(table, [(col, label)], agg=agg, where=where, scale=scale, dec=dec)


# ----------------------------------------------------------------------
# 列名清单（与参考页 cn_kpi_split_widgets.py 的 UME 段逐字一致）
# ----------------------------------------------------------------------
_MGW_UTIL_COLS = [
    ("Utilization Ratio of TONE Resource(%)", "TONE"),
    ("Utilization Ratio of TC Resource(%)", "TC"),
    ("Utilization Ratio of RTP Resource(%)", "RTP"),
]
_MGW_CAP_COLS = [
    ("Average Number of TONE being Used", "TONE used"),
    ("Average Number of TC being Used", "TC used"),
    ("Average Number of RTP being Used", "RTP used"),
]
_DATA_SUB_COLS = [
    ("Mean number of attached subscribers(GSM)", "2G"),
    ("Mean number of attached subscribers(UMTS)", "3G"),
    ("Mean Number of EPS Attach subscribers", "4G"),
    ("Mean Number of EPS Attach NSA subscribers", "5G"),
]
_XGW_CPU_COLS = [
    ("Current usage ratio of UP's vCPU(%)", "Current"),
    ("Mean usage ratio of UP's vCPU(%)", "Mean"),
    ("Peak usage ratio of UP's vCPU(%)", "Peak"),
]
_XGW_LICENSE_COLS = [
    ("PFCP Sessions Usage(%)", "PFCP"),
    ("Throughput Usage(%)", "Throughput"),
]
_RG_MO = [("6", "RG-6"), ("11", "RG-11"), ("12", "RG-12"), ("30", "RG-30")]


def _rg_branches(table: str) -> str:
    """RG-6/11/12/30 四条线：同一张表按 Measurement Object 拆开。"""
    branches = []
    for mo, label in _RG_MO:
        branches.append(
            f"SELECT {_TIME_KEY} AS t, '{label}' AS series, {_DAY_LABEL} AS day, "
            'ROUND(SUM('
            f'{_num("Bytes of flows of downlink packets based RG(Byte)")}'
            ')/1000000000.0,3) AS v '
            f'FROM "{table}" WHERE {_WINDOW} AND "Measurement Object" = \'{mo}\' '
            "GROUP BY t, day"
        )
    return " UNION ALL ".join(branches) + " ORDER BY t"


# ----------------------------------------------------------------------
# CS（语音域）—— 与 cs_cn_kpi 的 UME 段 8 张卡一一对应
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# 组件拆分：**一张卡只画一个指标**（3 条线 = D0/D1/D7）
# ----------------------------------------------------------------------
# 关键设计：拆分只发生在**服务端组装阶段**，取数仍按「一个模板一次查询」：
#   · 一个 _group = 一次远端查询（SQL 里用 UNION ALL 返回该组全部维度序列）；
#   · group["parts"] 描述这组要拆成哪几张卡（id / title / dim）；
#   · service._compute 取完一次后按 series 过滤成多张卡。
# 为什么不每张卡各发一条 SQL：2026-11-22 实测 20 张卡各查一次时，PS 页冷加载
# 48 秒（5 分钟表按维度重复扫）；合并成 9 条 SQL 后回到 ~1 秒。
def _slug(s: str) -> str:
    """给拆分后的组件生成稳定 id（导出 sheet 名 / React key 都用它）。"""
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _group(base_id: str, title: str, table: str, parts: List[tuple],
           agg: str = "avg", grain: str = "", pct: bool = False,
           unit: str = "", scale: float = 0, dec: int = 2,
           where: str = "", sql: str = "") -> Dict[str, Any]:
    """一个多维度组件 = 一次查询 + 多个「一指标一卡」的 parts。

    parts: [(列名, 序列显示名[, 附加 where])]。sql 为空时用 `_melt` 生成
    UNION ALL；RG 这类每序列有独立过滤条件的组传自定义 sql。
    """
    single = len(parts) == 1
    children = [
        {
            # 单指标组不再重复后缀（VLR 组就是 VLR，不叫「VLR · VLR」），
            # 导出 sheet 名也保持 cs_15m_vlr / ps_15m_dns 这种短名
            "id": base_id if single else f"{base_id}_{_slug(p[1])}",
            "title": title if single else f"{title} · {p[1]}",
            "dim": p[1],
        }
        for p in parts
    ]
    if not sql:
        sql = _melt(table, [(p[0], p[1]) for p in parts], agg=agg,
                    where=where, scale=scale, dec=dec)
    return {
        "id": base_id, "title": title, "table": table, "grain": grain,
        "pct": pct, "unit": unit, "dims": [p[1] for p in parts],
        "parts": children, "sql": sql,
    }


# ----------------------------------------------------------------------
# CS（语音域）—— 参考页 UME 段 8 个指标组，拆成 20 张卡
#   MRFP96 / MGW92 / MGW91 × (Utilization 的 TONE/TC/RTP + Capacity 的
#   TONE/TC/RTP used) = 18 张；VLR、Voice 各 1 张。
# ----------------------------------------------------------------------
_CS_15M: List[Dict[str, Any]] = []
for _ne, _short in (("MRFP_96", "MRFP96"), ("MGW_92", "MGW92"), ("MGW_91", "MGW91")):
    _CS_15M.append(_group(
        f"cs_15m_{_short.lower()}_util", f"CS_{_short} Util(%)",
        "CS_MRFP96_Resource_Utilization_OSS_UME", _MGW_UTIL_COLS,
        agg="avg", grain="1h", pct=True, unit="%", where=f"NE = '{_ne}'",
    ))
    _CS_15M.append(_group(
        f"cs_15m_{_short.lower()}_cap", f"CS_{_short} Capacity",
        "CS_MRFP96_Resource_Capacity_OSS_UME", _MGW_CAP_COLS,
        agg="avg", grain="1h", unit="count", where=f"NE = '{_ne}'", dec=0,
    ))
_CS_15M += [
    _group("cs_15m_vlr", "VLR Subscribers",
           "5mins_VLR_SUBSCRIBERS_TEMPLATE_OSS_UME",
           [("Number of Subscribers in VLR", "VLR")],
           agg="total", grain="5m", unit="subs", dec=0),
    # Erl 是话务强度，跨采样相加没有物理意义 → AVG
    _group("cs_15m_voice", "Voice Traffic(Erl)",
           "5mins_VOICE_TRAFFIC_TEMPLATE_OSS_UME",
           [("Seized Traffic(Erl)", "Voice")],
           agg="avg", grain="5m", unit="Erl"),
]

# ----------------------------------------------------------------------
# PS（分组域）—— 参考页 UME 段 12 个指标组，去掉 3 张日/月重复视角后拆成 20 张卡
#   Throughput 2 + Session 2 + CPU 3 + License 2 + Data 4 + DNS 1 + RG 4 +
#   4G 1 + 3G 1 = 20。
# ----------------------------------------------------------------------
_RG_TABLE = "5mins_FREE_RATING_GROUP_TEMPLATE_OSS_update_UME"
_PS_15M: List[Dict[str, Any]] = [
    _group("ps_15m_xgw_tp", "xGW Throughput",
           "PS_xGW_Throughput_Capacity_OSS_UME",
           [("Number of Throughput(Mbps) Used", "Used"),
            ("Number of Throughput(Mbps) Authorized", "Authorized")],
           agg="total", grain="1h", unit="Mbps"),
    _group("ps_15m_xgw_sess", "xGW Sessions",
           "PS_xGW_Session_Capacity_OSS_UME",
           [("Number of PFCP Sessions Used", "Used"),
            ("Number of PFCP Sessions Authorized", "Authorized")],
           agg="total", grain="1h", unit="sessions", dec=0),
    _group("ps_15m_xgw_cpu", "xGW CPU(%)",
           "5mins_xGW_CPU_USAGE_TEMPLATE_OSS_UME_UME",
           _XGW_CPU_COLS, agg="avg", grain="15m", pct=True, unit="%"),
    _group("ps_15m_xgw_lic", "xGW Occupancy(%)",
           "5mins_xGW_LICENSE_OCCUPANCY_TEMPLATE_OSS_UME",
           _XGW_LICENSE_COLS, agg="avg", grain="15m", pct=True, unit="%"),
    _group("ps_15m_data_subs", "Data Subscribers",
           "5mins_DATA_SUBSCRIBERS_TEMPLATE_OSS_UME", _DATA_SUB_COLS,
           agg="total", grain="5m", unit="subs", dec=0),
    _group("ps_15m_dns", "DNS Success Ratio(%)", "DNS_SUCCESS_RATIO_OSS_UME",
           [("Ratio of successful DNS query analysis(%)", "DNS")],
           agg="avg", grain="1h", pct=True, unit="%"),
    # RG 每个组有独立过滤条件，用 _rg_branches 的现成 SQL；parts 只描述拆分
    _group("ps_15m_rg", "RG Traffic(GB)", _RG_TABLE,
           [("Bytes of flows of downlink packets based RG(Byte)", "RG-6"),
            ("Bytes of flows of downlink packets based RG(Byte)", "RG-11"),
            ("Bytes of flows of downlink packets based RG(Byte)", "RG-12"),
            ("Bytes of flows of downlink packets based RG(Byte)", "RG-30")],
           grain="5m", unit="GB", sql=_rg_branches(_RG_TABLE)),
    _group("ps_15m_4g_fdd", "4G FDD Traffic(GB)",
           "5mins_4G_FDD_DATA_TRAFFIC_TEMPLATE_OSS_UME",
           [("Byte of downlink data packets received via SGi interface per APN(Byte)",
             "4G FDD")],
           agg="sum", grain="5m", unit="GB", scale=1000000000.0, dec=3),
    # ⚠ 3G 的列名是 Bytes（复数），4G 是 Byte（单数）；写错时 SQLite 不报错而恒 0
    _group("ps_15m_3g_fdd", "3G FDD Traffic(GB)",
           "5mins_3G_FDD_DATA_TRAFFIC_TEMPLATE_OSS_UME",
           [("Bytes of downlink data packets received via Gi interface per APN(Byte)",
             "3G FDD")],
           agg="sum", grain="15m", unit="GB", scale=1000000000.0, dec=3),
]


# ----------------------------------------------------------------------
# 对外：两个看板
# ----------------------------------------------------------------------
def _norm(ws: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """补默认字段，避免调用方到处 .get()。"""
    out = []
    for w in ws:
        d = {
            "chart": "line", "pct": False, "unit": "", "grain": "",
            "limit": 20000, "dims": [],
        }
        d.update(w)
        out.append(d)
    return out


BOARDS: Dict[str, Dict[str, Any]] = {
    "cs": {"title": "CS CN KPI · 15min", "widgets": _norm(_CS_15M)},
    "ps": {"title": "PS CN KPI · 15min", "widgets": _norm(_PS_15M)},
}


def get_board(board: str) -> Dict[str, Any]:
    """取看板定义；未知 board 抛 KeyError（路由转 404）。"""
    return BOARDS[board]

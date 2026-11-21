# -*- coding: utf-8 -*-
"""CS / PS CN KPI 分组看板的组件清单（SQL 与图表元数据）。

=========================== 这两个页面是什么 ===========================

`?page=cs_cn_kpi` / `?page=ps_cn_kpi` 按**业务分组**重新组织 CN KPI：

    · 每个页面分 EMS / UME 两段
        - EMS 段：人工从网管导出的历史性能 CSV（`*_EMS` 表，日/小时粒度，
          由 PS_CS_CORE 脚本整表 full_replace 写入，oss 不导就停更）
        - UME 段：自动同步的 `*_UME` 表（日/小时/15 分钟粒度，由
          `cnn_kpi_sync` / `cnn_kpi_sync_5mins` / `cn_hourly_kpi_sync` 写入）

与 `cn_kpi_service.WIDGETS`（原 FineBI「CN KPI」1:1 重建，23 个组件）的区别：
    · 那边按原看板的组件顺序排；这边按 **CS 语音域 / PS 分组域** 与 **来源** 排；
    · 这里**不降级换源**：EMS 段只读 EMS 表、UME 段只读 UME 表。换源会让
      「这张图到底是谁的数」变得不可知，而这正是这两个页面要回答的问题；
      表缺了就是空态（`empty_code`），不拿另一条流水线顶替、不填 0。
    · 时间键统一取统计区间的**左端点**：EMS 用 `substr("Start Time",1,10)`、
      UME 用 `substr("Begin Time",1,10)`。原 CN KPI 页为对齐老图用的是
      `End Time`（区间右端点，标签比统计日早一天），这两个新页面按正确口径取。

=========================== 粒度与聚合 ===========================

    · 日表（`*_EMS` / UME 日模板）：按天 SUM（流量/用户数）或 AVG（比率）。
    · 小时表：MGW32 按天 AVG（容量/利用率不能跨小时相加）；xGW Capacity 保留
      小时点（吞吐/会话的用量与授权随时段变化）；xGW License 小时表按天 AVG。
    · 15 分钟表（`5mins_*`）：保留原始时间点（看毛刺），x 轴为分钟级时间戳。
    · 月表（`1month_*`）：固定「近 13 个月」，不受页面日期区间影响（标 `fixed_range`）。

=========================== 空态而不是 0 ===========================

上游模板没有采集、或表还没生成时（如刚补入的模板尚未跑过同步），组件返回
`empty_code="no_table"` / `"not_collected"`，页面显示明确原因（AGENTS.md 三.6：
取不到显示 —，不得显示成 0）。
"""
from __future__ import annotations

from typing import Any, Dict, List

# ----------------------------------------------------------------------
# SQL 片段工具
# ----------------------------------------------------------------------
# 上游数值列是 TEXT，空串 / "-" 这类非数值必须变成 NULL（断线），
# 不能 CAST 成 0 混进均值 —— 与 cn_kpi_service 的 c14/c17 同一道守卫。
def _num(col: str) -> str:
    q = f'"{col}"'
    return f"CASE WHEN TRIM({q}) GLOB '*[0-9]*' THEN CAST(TRIM({q}) AS REAL) END"


def _day_series(table: str, time_col: str, cols: List[tuple],
                agg: str = "SUM", dec: int = 2, where: str = "",
                not_today: bool = False) -> str:
    """按天聚合的多序列 SQL：第一列 t，其余每列一条序列。"""
    exprs = ", ".join(f'ROUND({agg}({_num(c)}),{dec}) AS "{alias}"' for c, alias in cols)
    extra = " AND t < date('now')" if not_today else ""
    w = f" WHERE {where}" if where else ""
    return (
        f'SELECT substr("{time_col}",1,10) AS t, {exprs} '
        f'FROM "{table}"{w} GROUP BY t '
        f"HAVING t >= {{{{LO}}}} AND t < {{{{HI}}}}{extra} ORDER BY t"
    )


def _hour_series(table: str, cols: List[tuple], agg: str = "SUM",
                 dec: int = 1, where: str = "", not_today: bool = False) -> str:
    """按小时聚合的多序列 SQL（x=YYYY-MM-DD HH:00）。"""
    exprs = ", ".join(f'ROUND({agg}({_num(c)}),{dec}) AS "{alias}"' for c, alias in cols)
    extra = " AND t < strftime('%Y-%m-%d %H:00', 'now')" if not_today else ""
    w = f" WHERE {where}" if where else ""
    return (
        f'SELECT substr("Begin Time",1,13) || \':00\' AS t, {exprs} '
        f'FROM "{table}"{w} GROUP BY t '
        f"HAVING t >= {{{{LO}}}} AND t < {{{{HI}}}}{extra} ORDER BY t"
    )


def _pivot_series(table: str, time_expr: str, value_expr: str,
                  series_expr: str = "NE", where: str = "",
                  dec: int = 2) -> str:
    """透视 SQL：[t, series, v] 三列，前端按 series 拆成多条线。"""
    w = f" WHERE {where}" if where else ""
    return (
        f'SELECT {time_expr} AS t, {series_expr} AS series, '
        f'ROUND(AVG({value_expr}),{dec}) AS v '
        f'FROM "{table}"{w} GROUP BY t, series '
        f"HAVING t >= {{{{LO}}}} AND t < {{{{HI}}}} ORDER BY t"
    )


def _melt_hourly(table: str, ne: str, cols: List[tuple]) -> str:
    """同一网元的三条资源列 → [t, series, v] 长表（UNION ALL 反透视）。"""
    parts = [
        f"SELECT substr(\"Begin Time\",1,13) || ':00' AS t, '{alias}' AS series, "
        f'ROUND(AVG({_num(c)}),2) AS v FROM "{table}" '
        f"WHERE NE = '{ne}' GROUP BY t"
        for c, alias in cols
    ]
    union = " UNION ALL ".join(parts)
    # 占位符替换发生在最外层（_apply_range 只做文本替换），这里把区间放到最外层
    return (
        f"SELECT t, series, v FROM ({union}) "
        f"WHERE t >= {{{{LO}}}} AND t < {{{{HI}}}} ORDER BY t"
    )


# MGW 三张资源利用率表的列名（与 CS_MRFP96_Resource_Utilization_OSS_UME 实测一致）
_MGW_UTIL_COLS = [
    ("Utilization Ratio of TONE Resource(%)", "TONE"),
    ("Utilization Ratio of TC Resource(%)", "TC"),
    ("Utilization Ratio of RTP Resource(%)", "RTP"),
]
# Capacity 表只画「占用量」三条线：可用量（Number of Available …）在同一张表里
# 基本恒定（TONE 10,800 / TC 33,320~42,160 / RTP 79,200~104,400），
# 与占用量放同一根轴上会把三条占用量全压成贴底的直线；利用率卡已经直接给出
# used/available 的百分比，这里保留绝对量即可。可用量写进卡头 ⓘ 的口径里。
_MGW_CAP_COLS = [
    ("Average Number of TONE being Used", "TONE used"),
    ("Average Number of TC being Used", "TC used"),
    ("Average Number of RTP being Used", "RTP used"),
]

_RG_MO = "('6','11','12','30')"


def _rg_day_sql(table: str) -> str:
    """RG-6/11/12/30 四组免费资源流量（GB/天）。"""
    return (
        'SELECT substr("Begin Time",1,10) AS t, '
        'CASE mo WHEN \'6\' THEN \'RG-6\' WHEN \'11\' THEN \'RG-11\' '
        "WHEN '12' THEN 'RG-12' ELSE 'RG-30' END AS series, "
        'ROUND(SUM(b)/1000.0/1000.0/1000.0, 2) AS v FROM (\n'
        'SELECT "Begin Time", "Measurement Object" AS mo, '
        f'{_num("Bytes of flows of downlink packets based RG(Byte)")} AS b '
        f'FROM "{table}" WHERE "Measurement Object" IN {_RG_MO}\n'
        ") GROUP BY t, series "
        f"HAVING t >= {{{{LO}}}} AND t < {{{{HI}}}} ORDER BY t"
    )


def _rg_month_sql(table: str) -> str:
    """RG-6/11/12/30 月粒度（近 13 个月，固定区间）。"""
    return (
        'SELECT substr("Begin Time",1,10) AS t, '
        'CASE mo WHEN \'6\' THEN \'RG-6\' WHEN \'11\' THEN \'RG-11\' '
        "WHEN '12' THEN 'RG-12' ELSE 'RG-30' END AS series, "
        'ROUND(SUM(b)/1000.0/1000.0/1000.0, 2) AS v FROM (\n'
        'SELECT "Begin Time", "Measurement Object" AS mo, '
        f'{_num("Bytes of flows of downlink packets based RG(Byte)")} AS b '
        f'FROM "{table}" WHERE "Measurement Object" IN {_RG_MO}\n'
        ") GROUP BY t, series "
        "HAVING t >= date('now','-13 month') ORDER BY t"
    )


# ----------------------------------------------------------------------
# CS KPI（语音域）
# ----------------------------------------------------------------------
_CS_EMS: List[Dict[str, Any]] = [
    {
        "id": "cs_ems_voice", "title": "Voice Traffic(Erl)", "unit": "Erl",
        "table": "VOICE_TRAFFIC_TEMPLATE_OSS_EMS",
        "sql": _day_series("VOICE_TRAFFIC_TEMPLATE_OSS_EMS", "Start Time",
                           [("Seized Traffic(Erl)", "Voice Traffic(Erl)")]),
    },
    {
        "id": "cs_ems_vlr", "title": "VLR Subscribers", "unit": "subs",
        "table": "VLR_SUBSCRIBERS_TEMPLATE_OSS_EMS",
        "sql": _day_series("VLR_SUBSCRIBERS_TEMPLATE_OSS_EMS", "Start Time",
                           [("Number of Subscribers in VLR", "VLR Subscribers")],
                           dec=0),
    },
    {
        "id": "cs_ems_ussd", "title": "USSD Success Ratio(%)", "pct": True, "unit": "%",
        "table": "USSD_RATIO_TEMPLATE_OSS_EMS",
        "sql": _day_series("USSD_RATIO_TEMPLATE_OSS_EMS", "Start Time",
                           [("USSD SUCCESS RATIO", "USSD Success Ratio(%)")], agg="AVG"),
    },
    {
        "id": "cs_ems_locupd", "title": "Location Update Success Ratio(%)", "pct": True, "unit": "%",
        "table": "LOCATION_UPDATE_TEMPLATE_OSS_EMS",
        "sql": _day_series("LOCATION_UPDATE_TEMPLATE_OSS_EMS", "Start Time",
                           [("Successful ratio of location update(%)",
                             "Location Update Success Ratio(%)")], agg="AVG"),
    },
    {
        "id": "cs_ems_license", "title": "License Occupancy(%)", "pct": True, "unit": "%",
        "table": "LICENSE_OCCUPANCY_TEMPLATE_OSS_EMS",
        "sql": _day_series("LICENSE_OCCUPANCY_TEMPLATE_OSS_EMS", "Start Time",
                           [("License Occupancy Rates(%)", "License Occupancy(%)")], agg="AVG"),
    },
    {
        "id": "cs_ems_mtn_mou", "title": "MTN MOU", "unit": "min",
        "table": "MTN_Outgoing_MOU_OSS_EMS",
        "sql": _day_series("MTN_Outgoing_MOU_OSS_EMS", "Start Time",
                           [("Outgoing MOU", "MTN Outgoing MOU"),
                            ("Incoming MOU", "MTN Incoming MOU")]),
    },
    {
        "id": "cs_ems_cht", "title": "MTN Outgoing Call Holding Time(s)", "unit": "s",
        "table": "MTN_Outgoing_Call_Holding_Time_OSS_EMS",
        "sql": _day_series("MTN_Outgoing_Call_Holding_Time_OSS_EMS", "Start Time",
                           [("CHT", "Call Holding Time(s)")], agg="AVG"),
    },
    {
        "id": "cs_ems_inc_ans", "title": "Incoming Call Answer Ratio(%)", "pct": True, "unit": "%",
        "table": "Incoming_Call_Answer_Ratio_OSS_EMS",
        "sql": _day_series("Incoming_Call_Answer_Ratio_OSS_EMS", "Start Time",
                           [("(Incoming Call)Answer Ratio(%)", "Incoming Answer Ratio(%)")],
                           agg="AVG"),
    },
    {
        "id": "cs_ems_out_ans", "title": "Outgoing Call Answer Ratio(%)", "pct": True, "unit": "%",
        "table": "Outgoing_Call_Answer_Ratio_OSS_EMS",
        "sql": _day_series("Outgoing_Call_Answer_Ratio_OSS_EMS", "Start Time",
                           [("(Outgoing Call)Answer Ratio(%)", "Outgoing Answer Ratio(%)")],
                           agg="AVG"),
    },
    {
        "id": "cs_ems_intl_mou", "title": "International MOU", "unit": "min",
        "table": "International_MOU_OSS_EMS",
        "sql": _day_series("International_MOU_OSS_EMS", "Start Time",
                           [("Outgoing MOU", "Intl Outgoing MOU"),
                            ("Incoming MOU", "Intl Incoming MOU")]),
    },
    {
        "id": "cs_ems_mgw32_util", "title": "MGW32 Resource Utilization(%)",
        "pct": True, "unit": "%",
        "table": "CS_MGW32_Resource_Utilization_OSS_EMS",
        # 小时表按天取平均；不含今天（今天只有已完成的若干小时，会把日均拉低）
        "sql": _day_series("CS_MGW32_Resource_Utilization_OSS_EMS", "Start Time",
                           _MGW_UTIL_COLS, agg="AVG", not_today=True),
    },
    {
        "id": "cs_ems_mgw32_cap", "title": "MGW32 Resource Usage(count)", "unit": "count",
        "table": "CS_MGW32_Resource_Capacity_OSS_EMS",
        "sql": _day_series("CS_MGW32_Resource_Capacity_OSS_EMS", "Start Time",
                           [("Average Number of TONE being Used", "TONE used"),
                            ("Average Number of TC being Used", "TC used"),
                            ("Average Number of RTP being Used", "RTP used")],
                           agg="AVG", dec=0, not_today=True),
    },
]

# UME 三台 MGW/MRFP 的 Utilization 与 Capacity 各共用一张表（三张模板参数完全一致，
# 2026-11-18 用户口径：落库只跑 CS_MRFP96 一张），页面按 NE 过滤区分网元。
_CS_UME: List[Dict[str, Any]] = [
    {
        "id": "cs_ume_mrfp96_util", "title": "CS_MRFP96 Resource Utilization(%)",
        "pct": True, "unit": "%", "pivot": True, "limit": 20000,
        "table": "CS_MRFP96_Resource_Utilization_OSS_UME",
        "sql": _melt_hourly("CS_MRFP96_Resource_Utilization_OSS_UME", "MRFP_96",
                            _MGW_UTIL_COLS),
    },
    {
        "id": "cs_ume_mgw92_util", "title": "CS_MGW92 Resource Utilization(%)",
        "pct": True, "unit": "%", "pivot": True, "limit": 20000,
        "table": "CS_MRFP96_Resource_Utilization_OSS_UME",
        "sql": _melt_hourly("CS_MRFP96_Resource_Utilization_OSS_UME", "MGW_92",
                            _MGW_UTIL_COLS),
    },
    {
        "id": "cs_ume_mgw91_util", "title": "CS_MGW91 Resource Utilization(%)",
        "pct": True, "unit": "%", "pivot": True, "limit": 20000,
        "table": "CS_MRFP96_Resource_Utilization_OSS_UME",
        "sql": _melt_hourly("CS_MRFP96_Resource_Utilization_OSS_UME", "MGW_91",
                            _MGW_UTIL_COLS),
    },
    {
        "id": "cs_ume_mgw92_cap", "title": "CS_MGW92 Resource Capacity",
        "unit": "count", "pivot": False,
        "table": "CS_MRFP96_Resource_Capacity_OSS_UME",
        "sql": _day_series("CS_MRFP96_Resource_Capacity_OSS_UME", "Begin Time",
                           _MGW_CAP_COLS, agg="AVG", dec=0, where="NE = 'MGW_92'"),
    },
    {
        "id": "cs_ume_mgw91_cap", "title": "CS_MGW91 Resource Capacity",
        "unit": "count", "pivot": False,
        "table": "CS_MRFP96_Resource_Capacity_OSS_UME",
        "sql": _day_series("CS_MRFP96_Resource_Capacity_OSS_UME", "Begin Time",
                           _MGW_CAP_COLS, agg="AVG", dec=0, where="NE = 'MGW_91'"),
    },
    {
        "id": "cs_ume_mrfp96_cap", "title": "CS_MRFP96 Resource Capacity",
        "unit": "count", "pivot": False,
        "table": "CS_MRFP96_Resource_Capacity_OSS_UME",
        "sql": _day_series("CS_MRFP96_Resource_Capacity_OSS_UME", "Begin Time",
                           _MGW_CAP_COLS, agg="AVG", dec=0, where="NE = 'MRFP_96'"),
    },
    {
        "id": "cs_ume_vlr", "title": "VLR Subscribers (UME)", "unit": "subs",
        "table": "VLR_SUBSCRIBERS_TEMPLATE_OSS_UME",
        "sql": _day_series("VLR_SUBSCRIBERS_TEMPLATE_OSS_UME", "Begin Time",
                           [("Number of Subscribers in VLR", "VLR Subscribers (UME)")],
                           dec=0),
    },
    {
        "id": "cs_ume_voice", "title": "Voice Traffic(Erl) (UME)", "unit": "Erl",
        "table": "VOICE_TRAFFIC_TEMPLATE_OSS_UME",
        "sql": _day_series("VOICE_TRAFFIC_TEMPLATE_OSS_UME", "Begin Time",
                           [("Seized Traffic(Erl)", "Voice Traffic(Erl) (UME)")]),
    },
]

# ----------------------------------------------------------------------
# PS KPI（分组域）
# ----------------------------------------------------------------------
_PS_EMS: List[Dict[str, Any]] = [
    {
        "id": "ps_ems_tdd", "title": "TDD Traffic(GB)", "unit": "GB",
        "table": "TDD_DATA_TRAFFIC_TEMPLATE_OSS_EMS",
        "sql": (
            'SELECT substr("Start Time",1,10) AS t, '
            'ROUND(SUM(b)/1000.0/1000.0/1000.0, 2) AS "TDD Traffic(GB)" FROM (\n'
            'SELECT "Start Time", '
            f'{_num("Byte of downlink data packets received via SGI interface per APN(Byte)")} AS b '
            'FROM "TDD_DATA_TRAFFIC_TEMPLATE_OSS_EMS"\n'
            ") GROUP BY t "
            "HAVING t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
]

_PS_UME: List[Dict[str, Any]] = [
    {
        "id": "ps_ume_xgw_tp", "title": "PS_xGW Throughput Capacity(Mbps)", "unit": "Mbps", "limit": 20000,
        "table": "PS_xGW_Throughput_Capacity_OSS_UME",
        "sql": _hour_series("PS_xGW_Throughput_Capacity_OSS_UME",
                            [("Number of Throughput(Mbps) Used", "Throughput Used(Mbps)"),
                             ("Number of Throughput(Mbps) Authorized",
                              "Throughput Authorized(Mbps)")]),
    },
    {
        "id": "ps_ume_xgw_sess", "title": "PS_xGW Session Capacity", "unit": "sessions", "limit": 20000,
        "table": "PS_xGW_Session_Capacity_OSS_UME",
        "sql": _hour_series("PS_xGW_Session_Capacity_OSS_UME",
                            [("Number of PFCP Sessions Used", "PFCP Sessions Used"),
                             ("Number of PFCP Sessions Authorized", "PFCP Sessions Authorized")]),
    },
    {
        "id": "ps_ume_cpu_15m", "title": "xGW CPU Utilization(%) · 15min",
        "pct": True, "unit": "%", "pivot": True, "limit": 20000,
        "table": "5mins_xGW_CPU_USAGE_TEMPLATE_OSS_UME_UME",
        # 15 分钟档保留原始时间点（看毛刺），按 NE 各一条线
        "sql": _pivot_series("5mins_xGW_CPU_USAGE_TEMPLATE_OSS_UME_UME",
                             '"Begin Time"', _num("Peak usage ratio of UP's vCPU(%)")),
    },
    {
        "id": "ps_ume_license_15m", "title": "xGW Session Occupancy(%) · 15min",
        "pct": True, "unit": "%", "pivot": True, "limit": 20000,
        "table": "5mins_xGW_LICENSE_OCCUPANCY_TEMPLATE_OSS_UME",
        "sql": _pivot_series("5mins_xGW_LICENSE_OCCUPANCY_TEMPLATE_OSS_UME",
                             '"Begin Time"', _num("PFCP Sessions Usage(%)")),
    },
    {
        "id": "ps_ume_data_subs", "title": "Data Subscribers", "unit": "subs",
        "table": "DATA_SUBSCRIBERS_TEMPLATE_OSS_UME",
        "sql": _day_series(
            "DATA_SUBSCRIBERS_TEMPLATE_OSS_UME", "Begin Time",
            [("Mean number of attached subscribers(GSM)", "2G"),
             ("Mean number of attached subscribers(UMTS)", "3G"),
             ("Mean Number of EPS Attach subscribers", "4G"),
             ("Mean Number of EPS Attach NSA subscribers", "5G")],
            agg="SUM", dec=0),
    },
    {
        # 2026-11-20 起由 cn_hourly_kpi_sync 采集（此前只有模板、没有配置）。
        # 模板：DNS_SUCCESS_RATIO_OSS_updated（templateid 1649220598196 / motid 4927 /
        # items cn.xgw.49270004），gr=3600，按 NE 每小时一行（xGWU22 / xGWU122）；
        # 导出列名实测 "Ratio of successful DNS query analysis(%)"。
        # 图上按小时对网元取算术平均（两台 xGW 等权），百分比不做加权。
        "id": "ps_ume_dns", "title": "DNS Success Ratio(%)", "pct": True, "unit": "%",
        "table": "DNS_SUCCESS_RATIO_OSS_UME",
        "sql": _hour_series(
            "DNS_SUCCESS_RATIO_OSS_UME",
            [("Ratio of successful DNS query analysis(%)", "DNS Success Ratio(%)")],
            agg="AVG", dec=2),
    },
    {
        "id": "ps_ume_free_rating", "title": "RG-6/11/12/30 Traffic(GB)", "unit": "GB",
        "pivot": True,
        "table": "FREE_RATING_GROUP_TEMPLATE_OSS_update_UME",
        "sql": _rg_day_sql("FREE_RATING_GROUP_TEMPLATE_OSS_update_UME"),
    },
    {
        "id": "ps_ume_4g_fdd", "title": "4G FDD Traffic(GB)", "unit": "GB",
        "table": "4G_FDD_DATA_TRAFFIC_TEMPLATE_OSS_UME",
        "sql": (
            'SELECT substr("Begin Time",1,10) AS t, '
            'ROUND(SUM(b)/1000.0/1000.0/1000.0, 2) AS "4G FDD Traffic(GB)" FROM (\n'
            'SELECT "Begin Time", '
            f'{_num("Byte of downlink data packets received via SGI interface per APN(Byte)")} AS b '
            'FROM "4G_FDD_DATA_TRAFFIC_TEMPLATE_OSS_UME"\n'
            ") GROUP BY t "
            "HAVING t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
    {
        "id": "ps_ume_3g_fdd", "title": "3G FDD Traffic(GB)", "unit": "GB",
        "table": "3G_FDD_DATA_TRAFFIC_TEMPLATE_OSS_UME",
        "sql": (
            'SELECT substr("Begin Time",1,10) AS t, '
            'ROUND(SUM(b)/1000.0/1000.0/1000.0, 2) AS "3G FDD Traffic(GB)" FROM (\n'
            'SELECT "Begin Time", '
            f'{_num("Bytes of downlink data packets received via Gi interface per APN(Byte)")} AS b '
            'FROM "3G_FDD_DATA_TRAFFIC_TEMPLATE_OSS_UME"\n'
            ") GROUP BY t "
            "HAVING t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
    {
        "id": "ps_ume_license_daily", "title": "xGW License Occupancy(%) · daily",
        "pct": True, "unit": "%",
        "table": "xGW_LICENSE_OCCUPANCY_TEMPLATE_OSS_UME",
        # 该模板上游是 gr=3600（小时落库），这里按天取平均，作为 DAILY 视角的概览
        "sql": _day_series("xGW_LICENSE_OCCUPANCY_TEMPLATE_OSS_UME", "Begin Time",
                           [("PFCP Sessions Usage(%)", "PFCP Sessions Usage(%)"),
                            ("Throughput Usage(%)", "Throughput Usage(%)")], agg="AVG"),
    },
    {
        "id": "ps_ume_cpu_daily", "title": "xGW CPU Utilization(%) · daily",
        "pct": True, "unit": "%",
        "table": "xGW_CPU_USAGE_TEMPLATE_OSS_UME_UME",
        "sql": _day_series("xGW_CPU_USAGE_TEMPLATE_OSS_UME_UME", "Begin Time",
                           [("Current usage ratio of UP's vCPU(%)", "Current(%)"),
                            ("Mean usage ratio of UP's vCPU(%)", "Mean(%)"),
                            ("Peak usage ratio of UP's vCPU(%)", "Peak(%)")], agg="AVG"),
    },
    {
        "id": "ps_ume_free_rating_monthly", "title": "RG Traffic(GB) · monthly",
        "unit": "GB", "pivot": True, "fixed_range": True,
        "table": "1month_FREE_RATING_GROUP_TEMPLATE_OSS_update_UME",
        "sql": _rg_month_sql("1month_FREE_RATING_GROUP_TEMPLATE_OSS_update_UME"),
    },
]


# ----------------------------------------------------------------------
# 对外：两个看板的结构
# ----------------------------------------------------------------------
def _norm(ws: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """补默认字段，避免调用方到处 .get()。"""
    out = []
    for w in ws:
        d = {
            "chart": "line", "pivot": False, "pct": False, "unit": "",
            "limit": 5000, "fixed_range": False, "missing_code": "",
            "sql": "", "table": "",
        }
        d.update(w)
        out.append(d)
    return out


BOARDS: Dict[str, Dict[str, Any]] = {
    "cs": {
        "title": "CS CN KPI",
        "sections": [
            {"key": "ems", "widgets": _norm(_CS_EMS)},
            {"key": "ume", "widgets": _norm(_CS_UME)},
        ],
    },
    "ps": {
        "title": "PS CN KPI",
        "sections": [
            {"key": "ems", "widgets": _norm(_PS_EMS)},
            {"key": "ume", "widgets": _norm(_PS_UME)},
        ],
    },
}


def get_board(board: str) -> Dict[str, Any]:
    """取看板定义；未知 board 抛 KeyError（路由转 400）。"""
    return BOARDS[board]

# -*- coding: utf-8 -*-
"""CN KPI 看板数据服务（原 my-finebi「CN KPI」看板原生重建）。

=========================== 数据源与统计口径 ===========================

数据源
    远端只读 SQL API（backend/config/remote_host.py → 默认 http://192.168.100.19:8765）
    逻辑库 db_ps_cs_cn.sqlite3（FineBI 连接名 db_ps_cs_cn，PS/CS CN KPI）

    **不再依赖 my-finebi 进程**：my-finebi 门户/后端停掉不影响本页；
    只要求远端 8765 只读 API 可达（.19 开机且 db_writer_api 在跑）。

来源与口径
    18 个组件的 SQL 与图表类型，逐条解析自 FineBI 原看板（公开链接 iYiQ）的
    数据集定义，与原版 1:1 对齐（见 my-finebi 的 backend/seed_cnkpi.json）。
    2026-11-20 按业务要求**另加 5 个**原看板没有的组件，整页共 **23 个组件**：
      c14/c15 呼入·呼出应答率（Incoming/Outgoing_Call_Answer_Ratio_OSS_EMS）
      c16 国际 MOU（International_MOU_OSS_EMS）
      c17 MGW32 资源利用率、c18 MGW32 资源占用（两张 CS_MGW32_*_OSS_EMS 小时表）
    - 折线类：GROUP BY substr("End Time",1,10)，默认近 31 天
    - pivot 类：SQL 返回 [时间, NE, 值] 三列，前端按 NE 透视成多序列
    - 单位换算：Byte→GB 除以 1000/1000/1000（1 GB = 1e9 字节）。
      2026-11-20 实测上游列确实是字节（单日 4G FDD 约 4.2e14 B = 42 万 GB），
      原写法 /1e6 会放大 1000 倍；与 cn_kpi_5mins_service（/1e9）统一。

=========================== EMS / UME 配对回退 ===========================
    同一个 KPI 在 db_ps_cs_cn 里有两张同名不同源的表：

        <T>_EMS  ← Task\\PS_CS_CORE_Scripts\\ps_cs_core.py
                   oss 用户从网管导出的 History Performance CSV（人工导）
        <T>_UME  ← backend\\services\\cnn\\ccn_ume_kpi_service.py
                   task_registry 的 `cnn_kpi_sync`（每天 06:00 自动跑）

    **两者是两套独立流水线**：UME 那条自动，EMS 那条依赖人工导出 ——
    oss 不导，EMS 表就永远停在最后一次导入（实测 VLR/USSD/LOCATION 三张
    停在 2026-08-27，早已滑出「近 31 天」默认窗口，于是图表全空）。

    更麻烦的是 EMS 表的**粒度由导出时选的区间决定**（不是固定日粒度）：
    oss 在导出向导里选了 2026-04-01~10-01，整表就被 1Month(s) 的 6 行
    **整表覆盖**（ps_cs_core.write_via_api 用的 mode="full_replace"）。
    实测 LICENSE_OCCUPANCY / DATA_SUBSCRIBERS 两张就是被这样覆盖成了月表。

    所以本服务对每张 EMS 表提供可选的 `fallback`（UME 表 + 时间列）：
      · EMS 表在当前窗口内取不到行 → 自动改读 fallback 表，照常出图；
      · EMS 表**有**行 → 用 EMS。哪怕两边都只剩月粒度，EMS 的行数也必然
        少于 UME 的日粒度行，所以「行数少者优先」能让 EMS 恢复日粒度后
        自动夺回主数据源，无需改代码。
      · 仍然一张都取不到 → 组件置空并写明原因（前端显示空态 + 告警条）。

    这不是「兼容层」，是数据源降级：EMS 与 UME 测的是同一批核心网 KPI，
    缺哪一个都应该出图，而不是让看板开天窗。
=====================================================================

=========================== 粒度漂移的风险 ===========================
    上游表粒度会变，因此**任何跨表 JOIN 都必须先按天聚合再连接**，
    否则月度表(N 行)与日度表(M 行)按「天」连接会退化得面目全非。
    历史缺陷：c2 MSC Utilization 用 LEFT JOIN 直接拼两张 VLR 表，
    月度表 6 行 × 日度表 93 行 → 笛卡尔积，整条曲线被向下拉平。
=====================================================================

只读保障
    远端 /api/query 在连接层强制 PRAGMA query_only=ON；本服务只发 SELECT。
    本地再做一次语句形状守卫（必须 SELECT/WITH 开头、禁止分号），双保险。

时间范围
    默认近 31 天（与原版 FineBI 一致）。页面可传 start / end（YYYY-MM-DD）
    自定义区间 —— 由 _apply_range() 把 SQL 里的 {{LO}} / {{HI}} 占位符替换为
    **经过严格正则校验的字面量**（只允许 YYYY-MM-DD），绝不拼接任意用户输入。

缓存
    整页 23 个组件并发取数，结果按「时间区间」维度 TTL（默认 60s）缓存，
    避免每次刷新都打 18 次远端；单组件失败只标记该组件，不拖垮整页。
======================================================================
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from backend.config.remote_host import get_api_base_url

logger = logging.getLogger("cn_kpi")

# 远端逻辑库名（FineBI 连接名 db_ps_cs_cn → 远端 E:\01_RPA\LBR_Tools\KPI\db_ps_cs_cn.sqlite3）
REMOTE_DB = "db_ps_cs_cn.sqlite3"

# 取数超时（秒）：单组件远端查询；整页并发受此上限约束
QUERY_TIMEOUT = 60

# 整页结果 TTL（秒）——看板数据是日粒度，60s 足够新
CACHE_TTL = 60

# 并发上限：远端 db_writer_api 是单机服务，避免一次性打太多连接
MAX_WORKERS = 6

# 显式禁用代理：内网地址绝不能被 HTTP_PROXY 劫持（沿用 query_engine 的做法）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 只读语句守卫（本地一道，远端另有一道）
_GUARD_RE = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


# ----------------------------------------------------------------------
# EMS → UME 配对回退表（详见文件头「EMS / UME 配对回退」）
# ----------------------------------------------------------------------
# 只有确实存在 UME 同名表、且语义等价的 KPI 才登记在这里。
# 对应关系与 ccn_ume_kpi_service.CNN_KPI_QUERY_CONFIGS 的 table_name 一致。
EMS_FALLBACK_TABLE: Dict[str, str] = {
    "VOICE_TRAFFIC_TEMPLATE_OSS_EMS": "VOICE_TRAFFIC_TEMPLATE_OSS_UME",
    "VLR_SUBSCRIBERS_TEMPLATE_OSS_EMS": "VLR_SUBSCRIBERS_TEMPLATE_OSS_UME",
    "DATA_SUBSCRIBERS_TEMPLATE_OSS_EMS": "DATA_SUBSCRIBERS_TEMPLATE_OSS_UME",
    "4G_FDD_DATA_TRAFFIC_TEMPLATE_OSS_UME": "4G_FDD_DATA_TRAFFIC_TEMPLATE_OSS_UME",
}

# 上面这张表覆盖不到的「人工导出」KPI：先按名字猜同名 UME 表存不存在，
# 存在才启用回退（表名由正则严格限定，不存在注入面）。
_EMS_SUFFIX = "_EMS"
_UME_SUFFIX = "_UME"
# 表名白名单字符集：上游表名只由大写字母/数字/下划线组成
_TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


# ----------------------------------------------------------------------
# 组件清单：SQL 与展示元数据，逐条对齐 FineBI 原看板（公开链接 iYiQ）
# ----------------------------------------------------------------------
# title   : 组件标题（与原版一致）
# chart   : line / kpi / text
# pivot   : True 表示 SQL 返回三列 [时间, 系列名, 值]，需按第 2 列透视
# pct     : True 表示纵轴为百分比（两位小数）
#
# SQL 里的 {{LO}} / {{HI}} 是时间区间占位符（默认近 31 天），
# 由 _apply_range() 依据请求参数替换；无占位符的 SQL 不受区间影响。
#
# 顺序与 seed_cnkpi.json 的 widgets 顺序保持一致，便于日后比对。
WIDGETS: List[Dict[str, Any]] = [
    {
        "id": "kpi_time",
        "title": "TIme",
        "chart": "kpi",
        "sql": (
            'SELECT MAX(substr("End Time",1,10)) AS "Date" '
            'FROM "VLR_SUBSCRIBERS_TEMPLATE_OSS_EMS"'
        ),
    },
    {
        "id": "c1",
        "title": "VLR Subscribers",
        "chart": "line",
        # 2026-11-18 业务口径（用户确认）：EMS 表 = **HQ** 站点，UME 表 = **ODC** 站点
        # （UME 侧只有 MSC71，语音另含 MGCF76，正是一个站点的网元集合）。
        # 所以两张表是**两个站点的同一 KPI**，可以相加得全网 Total；页面按
        # HQ / ODC / Total 三条线显示（EMS→HQ、UME→ODC 的改名是用户要求）。
        #   · 旧写法「同日优先 EMS、缺日取 UME」把两个站点压成一条线，换源日
        #     出现台阶（10-18 UME 1,002,263 → 10-19 EMS 1,231,875，+22.9%）；
        #   · 现在用**日历骨架 + 三条线**：只在各自有数据的日期有点，缺数即断线
        #     （不用另一来源顶替、不插值）；Total 只在两站都有数的日期成立。
        # 日历骨架同时保证「没有数据的日期」也有一行（值为 NULL），ECharts 才能断线。
        # 递归 CTE 是 SQLite 内置能力（远端 3.50.4），无额外版本依赖。
        "source_table": "VLR_SUBSCRIBERS_TEMPLATE_OSS_EMS",
        # self_contained：SQL 已自带两张表 + 日历，不再走「主表→UME→月表」三级降级
        # （否则 tier-2 的 _rewrite_source_table 会把 EMS 改写成 UME，两列变成同一张表）。
        "self_contained": True,
        "source_split": {
            "ems": "VLR_SUBSCRIBERS_TEMPLATE_OSS_EMS",
            "ume": "VLR_SUBSCRIBERS_TEMPLATE_OSS_UME",
        },
        "sql": (
            "WITH RECURSIVE cal(d) AS (\n"
            "SELECT date({{LO}})\n"
            "UNION ALL SELECT date(d,'+1 day') FROM cal "
            "WHERE d < date({{HI}},'-1 day'))\n"
            ', ems AS (SELECT substr("End Time",1,10) AS d, '
            'SUM("Number of Subscribers in VLR") AS v '
            'FROM "VLR_SUBSCRIBERS_TEMPLATE_OSS_EMS" GROUP BY 1)\n'
            ', ume AS (SELECT substr("End Time",1,10) AS d, '
            'SUM("Number of Subscribers in VLR") AS v '
            'FROM "VLR_SUBSCRIBERS_TEMPLATE_OSS_UME" GROUP BY 1)\n'
            'SELECT cal.d AS t, ems.v AS "HQ", ume.v AS "ODC",\n'
            # 只有两站都有数时才成立；任一缺失给 NULL（前端断线，不画半截总和）
            'CASE WHEN ems.v IS NULL OR ume.v IS NULL THEN NULL '
            'ELSE ems.v + ume.v END AS "Total" FROM cal\n'
            "LEFT JOIN ems ON ems.d = cal.d\n"
            "LEFT JOIN ume ON ume.d = cal.d\n"
            "ORDER BY cal.d"
        ),
    },
    {
        "id": "c4",
        "title": "Voice Traffic(Erl)",
        "chart": "line",
        # 同 c1：日历骨架 + HQ（EMS 表）/ ODC（UME 表）/ Total 三条线。
        # 2026-11-18 回补后 UME 覆盖 08-31~11-18（含 9 月），EMS 仍只有人工导出的
        # 10-19~11-18 —— 两条线的长短本身就是「谁缺数」的直接证据；
        # Total 只在两站都有数的日期成立（断档/单站日期不画总和的半截值）。
        "source_table": "VOICE_TRAFFIC_TEMPLATE_OSS_EMS",
        "self_contained": True,
        "source_split": {
            "ems": "VOICE_TRAFFIC_TEMPLATE_OSS_EMS",
            "ume": "VOICE_TRAFFIC_TEMPLATE_OSS_UME",
        },
        "sql": (
            "WITH RECURSIVE cal(d) AS (\n"
            "SELECT date({{LO}})\n"
            "UNION ALL SELECT date(d,'+1 day') FROM cal "
            "WHERE d < date({{HI}},'-1 day'))\n"
            ', ems AS (SELECT substr("End Time",1,10) AS d, '
            'SUM("Seized Traffic(Erl)") AS v '
            'FROM "VOICE_TRAFFIC_TEMPLATE_OSS_EMS" GROUP BY 1)\n'
            ', ume AS (SELECT substr("End Time",1,10) AS d, '
            'SUM("Seized Traffic(Erl)") AS v '
            'FROM "VOICE_TRAFFIC_TEMPLATE_OSS_UME" GROUP BY 1)\n'
            'SELECT cal.d AS t, ems.v AS "HQ", ume.v AS "ODC",\n'
            'CASE WHEN ems.v IS NULL OR ume.v IS NULL THEN NULL '
            'ELSE ems.v + ume.v END AS "Total" FROM cal\n'
            "LEFT JOIN ems ON ems.d = cal.d\n"
            "LEFT JOIN ume ON ume.d = cal.d\n"
            "ORDER BY cal.d"
        ),
    },
    {
        "id": "c12",
        "title": "USSD SUCCESS RATIO",
        "chart": "line",
        "pct": True,
        # 上游**: 只有人工导出的 _EMS 有 USSD，且日粒度仅剩 2 行（2026-08-25/26），
        # 早已滑出「近 31 天」默认窗口 → 默认必然空图。
        # 没有自动同步的 UME 孪生表（见下方 _TWIN 说明），故回退到**月粒度**同源表。
        "source_table": "USSD_RATIO_TEMPLATE_OSS_EMS",
        "fallback": "USSD_RATIO_TEMPLATE_OSS_UME",
        "monthly_table": "1month_USSD_RATIO_TEMPLATE_OSS_EMS",
        "sql": (
            'SELECT substr("End Time",1,10) AS t, '
            'ROUND(AVG(v),2) AS "USSD SUCCESS RATIO" FROM (\n'
            'SELECT "End Time", "USSD SUCCESS RATIO" AS v '
            'FROM "USSD_RATIO_TEMPLATE_OSS_EMS"\n'
            ")  GROUP BY substr(\"End Time\",1,10) "
            "HAVING t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
    {
        "id": "c13",
        "title": "Successful ratio of location update(%)",
        "chart": "line",
        "pct": True,
        # 同 c12：日粒度仅剩 2026-08-25/26 两行且 08-26 的值为空字符串，
        # 实际唯一有效点只有 2026-08-25。完整数据在月粒度表里。
        "source_table": "LOCATION_UPDATE_TEMPLATE_OSS_EMS",
        "fallback": "LOCATION_UPDATE_TEMPLATE_OSS_UME",
        "monthly_table": "1month_LOCATION_UPDATE_TEMPLATE_OSS_EMS",
        "sql": (
            'SELECT substr("End Time",1,10) AS t, '
            'ROUND(AVG(v),2) AS "Location Update(%)" FROM (\n'
            'SELECT "End Time", "Successful ratio of location update(%)" AS v '
            'FROM "LOCATION_UPDATE_TEMPLATE_OSS_EMS"\n'
            ")  GROUP BY substr(\"End Time\",1,10) "
            "HAVING t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
    {
        "id": "c3",
        "title": "Data Subscribers",
        "chart": "line",
        # 主表是自动同步的 UME（通常也够新）；DATA_SUBSCRIBERS 的 _EMS 实测已被
        # 人工导出覆盖成月粒度，说明这条人工链路不稳定，故再兜一层月表。
        #
        # 2026-11-18 按业务要求改口径与命名：
        #   · 序列名改成制式名（GSM→2G、UMTS→3G、EPS→4G），并新增 5G；
        #   · 5G 取自 C466010001（导出列 "Mean Number of EPS Attach NSA subscribers"，
        #     该指标项与位置已加入 ccn_ume_kpi_service.CNN_KPI_QUERY_CONFIGS 的
        #     DATA_SUBSCRIBERS，同步写库后才会有这一列）。
        # ⚠ Total **仍只合计 2G+3G+4G**，不含 5G：5G NSA 用户是「EPS 附着里支持 NSA
        # 的那部分」，与 4G EPS Attach 是**包含关系而非并列**，加进去会重复计数
        # （实测同一 NE 同日 4G=293027 / 5G=20879，5G 从未大于 4G）。
        # 因此图上 5G 单列一条曲线用于看趋势，Total 保持"不重复计数的用户总数"。
        "source_table": "DATA_SUBSCRIBERS_TEMPLATE_OSS_UME",
        "monthly_table": "1month_DATA_SUBSCRIBERS_TEMPLATE_OSS_UME",
        "sql": (
            'SELECT substr("End Time",1,10) AS t, SUM(gsm) AS "2G", '
            'SUM(umts) AS "3G", SUM(eps) AS "4G", SUM(nsa) AS "5G", '
            'SUM(gsm+umts+eps) AS "Total" FROM (\n'
            'SELECT "End Time", "Mean number of attached subscribers(GSM)" AS gsm, '
            '"Mean number of attached subscribers(UMTS)" AS umts, '
            '"Mean Number of EPS Attach subscribers" AS eps, '
            '"Mean Number of EPS Attach NSA subscribers" AS nsa '
            'FROM "DATA_SUBSCRIBERS_TEMPLATE_OSS_UME"\n'
            ")  GROUP BY substr(\"End Time\",1,10) "
            "HAVING t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
    {
        "id": "c5",
        "title": "FDD Traffic(GB)",
        "chart": "line",
        "sql": (
            'SELECT substr("End Time",1,10) AS t, '
            'ROUND(SUM(b)/1000.0/1000.0/1000.0, 2) AS "FDD Traffic(GB)" FROM (\n'
            'SELECT "End Time", "Byte of downlink data packets received via SGI '
            'interface per APN(Byte)" AS b FROM "4G_FDD_DATA_TRAFFIC_TEMPLATE_OSS_UME"\n'
            ")  GROUP BY substr(\"End Time\",1,10) "
            "HAVING t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
    {
        "id": "c6",
        "title": "TDD Traffic(GB)",
        "chart": "line",
        # 主表是人工导出的 _EMS（会随导出区间漂移粒度）；有自动同步的 UME 孪生表，
        # 再兜一层月表。UME 侧列名同为 "Byte of downlink ... per APN(Byte)"。
        "source_table": "TDD_DATA_TRAFFIC_TEMPLATE_OSS_EMS",
        "fallback": "TDD_DATA_TRAFFIC_TEMPLATE_OSS_UME",
        "monthly_table": "1month_TDD_DATA_TRAFFIC_TEMPLATE_OSS_EMS",
        "sql": (
            'SELECT substr("End Time",1,10) AS t, '
            'ROUND(SUM(b)/1000.0/1000.0/1000.0, 2) AS "TDD Traffic(GB)" FROM (\n'
            'SELECT "End Time", "Byte of downlink data packets received via SGI '
            'interface per APN(Byte)" AS b FROM "TDD_DATA_TRAFFIC_TEMPLATE_OSS_EMS"\n'
            ")  GROUP BY substr(\"End Time\",1,10) "
            "HAVING t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
    {
        "id": "rg30",
        "title": "RG-30",
        "chart": "line",
        "sql": (
            'SELECT substr("Begin Time",1,10) AS t, '
            'ROUND(SUM(b)/1000.0/1000.0/1000.0, 2) AS "RG-30(GB)" FROM (\n'
            'SELECT "Begin Time", "Bytes of flows of downlink packets based RG(Byte)" '
            'AS b, "Measurement Object" AS mo '
            'FROM "FREE_RATING_GROUP_TEMPLATE_OSS_update_UME"\n'
            ") WHERE mo = '30' GROUP BY substr(\"Begin Time\",1,10) "
            "HAVING t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
    {
        "id": "rg12",
        "title": "RG-12",
        "chart": "line",
        "sql": (
            'SELECT substr("Begin Time",1,10) AS t, '
            'ROUND(SUM(b)/1000.0/1000.0/1000.0, 2) AS "RG-12(GB)" FROM (\n'
            'SELECT "Begin Time", "Bytes of flows of downlink packets based RG(Byte)" '
            'AS b, "Measurement Object" AS mo '
            'FROM "FREE_RATING_GROUP_TEMPLATE_OSS_update_UME"\n'
            ") WHERE mo = '12' GROUP BY substr(\"Begin Time\",1,10) "
            "HAVING t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
    {
        "id": "rg11",
        "title": "RG-11",
        "chart": "line",
        "sql": (
            'SELECT substr("Begin Time",1,10) AS t, '
            'ROUND(SUM(b)/1000.0/1000.0/1000.0, 2) AS "RG-11(GB)" FROM (\n'
            'SELECT "Begin Time", "Bytes of flows of downlink packets based RG(Byte)" '
            'AS b, "Measurement Object" AS mo '
            'FROM "FREE_RATING_GROUP_TEMPLATE_OSS_update_UME"\n'
            ") WHERE mo = '11' GROUP BY substr(\"Begin Time\",1,10) "
            "HAVING t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
    {
        "id": "rg6",
        "title": "RG-6",
        "chart": "line",
        "sql": (
            'SELECT substr("Begin Time",1,10) AS t, '
            'ROUND(SUM(b)/1000.0/1000.0/1000.0, 2) AS "RG-6(GB)" FROM (\n'
            'SELECT "Begin Time", "Bytes of flows of downlink packets based RG(Byte)" '
            'AS b, "Measurement Object" AS mo '
            'FROM "FREE_RATING_GROUP_TEMPLATE_OSS_update_UME"\n'
            ") WHERE mo = '6' GROUP BY substr(\"Begin Time\",1,10) "
            "HAVING t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
    {
        "id": "c11",
        "title": "USPP Utilization(%)",
        "chart": "line",
        "pct": True,
        # ⚠ 这里**不能**用 MAX(v)。MAX 是 FineBI 原看板为日粒度表写的聚合，
        # 一旦 LICENSE_OCCUPANCY_TEMPLATE_OSS_EMS 被人工导出覆盖成月粒度
        # （实测就是如此：整表只剩 1Month(s) 的 6 行，2026-04~10），
        # 「近 31 天」窗口里只落得下 1 行，MAX 就退化成该月的单点值，
        # 于是曲线从 31 个点塌成 1~2 个点（用户看到的「数据不全」）。
        # 改用 AVG + GROUP BY 天的语义。
        # ⚠ 这张表（LICENSE_OCCUPANCY）的粒度会漂移：人工导出选长区间时整表被覆盖成
        # 月粒度 6 行，「近 31 天」窗口里只落得下 1~2 个点，就是用户看到的「数据不全」。
        # 粒度**不写死**，由 _detect_granularity() 从返回数据实际推断（见该函数说明）。
        "source_table": "LICENSE_OCCUPANCY_TEMPLATE_OSS_EMS",
        "monthly_table": "1month_LICENSE_OCCUPANCY_TEMPLATE_OSS_EMS",
        "sql": (
            'SELECT substr("End Time",1,10) AS t, '
            'ROUND(AVG(v),2) AS "License Occupancy(%)" FROM (\n'
            'SELECT "End Time", "License Occupancy Rates(%)" AS v '
            'FROM "LICENSE_OCCUPANCY_TEMPLATE_OSS_EMS"\n'
            ")  GROUP BY substr(\"End Time\",1,10) "
            "HAVING t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
    {
        "id": "c2",
        "title": "MSC Utilization(%)",
        "chart": "line",
        "pct": True,
        # ⚠ 必须两段「先按天聚合、再连接」。
        # 上游表的粒度会漂移（EMS 可能被人工导出覆盖成 1Month(s)），
        # 直接 LEFT JOIN 两张原始表会得到 M×N 的笛卡尔积 ——
        # 2026-10 实测：EMS 6 行(月) × UME 93 行(日) → 每条日序列被拉平到全区间均值，
        # 曲线彻底失真且不报错。聚合到「天」之后每侧至多一行，连接才有意义。
        # 用 UME 的日历作为基准日（skeleton），保证 UME 有数据的日子一定出点。
        "sql": (
            "WITH sk AS (\n"
            '  SELECT DISTINCT substr("End Time",1,10) AS d '
            'FROM "VLR_SUBSCRIBERS_TEMPLATE_OSS_UME"\n'
            "),\n"
            "e AS (\n"
            '  SELECT substr("End Time",1,10) AS d, '
            'AVG("Number of Subscribers in VLR") AS v '
            'FROM "VLR_SUBSCRIBERS_TEMPLATE_OSS_EMS" '
            "GROUP BY substr(\"End Time\",1,10)\n"
            "),\n"
            "u AS (\n"
            '  SELECT substr("End Time",1,10) AS d, '
            'AVG("Number of Subscribers in VLR") AS v '
            'FROM "VLR_SUBSCRIBERS_TEMPLATE_OSS_UME" '
            "GROUP BY substr(\"End Time\",1,10)\n"
            ")\n"
            "SELECT sk.d AS t, "
            'ROUND(e.v/3000000.0*100, 2) AS "EMS", '
            'ROUND(u.v/4000000.0*100, 2) AS "UME" '
            "FROM sk LEFT JOIN e ON e.d = sk.d LEFT JOIN u ON u.d = sk.d "
            # ⚠ 外层是**非聚合**查询（sk/e/u 都已在 CTE 里聚合过），
            # 所以只能用 WHERE 过滤，写 HAVING 会被 SQLite 拒绝：
            # "HAVING clause on a non-aggregate query"。
            "WHERE t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
    {
        "id": "c7",
        "title": "Data Throughput(Mbps)",
        "chart": "line",
        "pivot": True,
        "sql": (
            'SELECT substr("End Time",1,13) || \':00\' AS t, NE, '
            '"Number of Throughput(Mbps) Used" AS v '
            'FROM "xGW_LICENSE_OCCUPANCY_TEMPLATE_OSS_UME" '
            'WHERE "End Time" >= {{LO}} AND "End Time" < {{HI}} ORDER BY t'
        ),
    },
    {
        "id": "c8",
        "title": "xGW Throughput Utilization(%)",
        "chart": "line",
        "pivot": True,
        "pct": True,
        "sql": (
            'SELECT substr("End Time",1,13) || \':00\' AS t, NE, '
            '"Throughput Usage(%)" AS v '
            'FROM "xGW_LICENSE_OCCUPANCY_TEMPLATE_OSS_UME" '
            'WHERE "End Time" >= {{LO}} AND "End Time" < {{HI}} ORDER BY t'
        ),
    },
    {
        "id": "c10",
        "title": "xGW Session Utilization(%)",
        "chart": "line",
        "pivot": True,
        "pct": True,
        "sql": (
            'SELECT substr("End Time",1,13) || \':00\' AS t, NE, '
            '"PFCP Sessions Usage(%)" AS v '
            'FROM "xGW_LICENSE_OCCUPANCY_TEMPLATE_OSS_UME" '
            'WHERE "End Time" >= {{LO}} AND "End Time" < {{HI}} ORDER BY t'
        ),
    },
    {
        "id": "c9",
        "title": "xGW CPU Utilization(%)",
        "chart": "line",
        "pivot": True,
        "pct": True,
        "sql": (
            'SELECT substr("End Time",1,10) AS t, NE, '
            '"Peak usage ratio of UP\'s vCPU(%)" AS v '
            'FROM "xGW_CPU_USAGE_TEMPLATE_OSS_UME_UME" '
            'WHERE "End Time" >= {{LO}} AND "End Time" < {{HI}} ORDER BY t'
        ),
    },
    {
        # 2026-11-20 新增（用户要求）：呼入应答率。
        #
        # 数据源：db_ps_cs_cn 的 Incoming_Call_Answer_Ratio_OSS_EMS —— 与 c1/c4 同一条
        # **人工导出**链路（ps_cs_core 整表 full_replace），当前是 1Day(s) 日粒度，
        # 31 行 / 2026-10-20~11-19、单一网元 ZTEMSCs,11，指标项原名
        # "(Incoming Call)Answer Ratio(%)"（值形态如 41.4024）。
        #
        # ⚠ 这两张表**既没有 _UME 孪生表，也没有 1month_* 月表**（2026-11-20 实测
        #    sqlite_master 只有 *_OSS_EMS 一张）。所以**不登记** source_table /
        #    fallback / monthly_table，走「单级取数」：
        #      · 不登记 source_table → _fallback_candidate() 为 False，不会去探测/重试
        #        UME 表（省两次注定失败的远端往返）；
        #      · oss 停止导出时曲线直接空掉，页面显示空态，**不拿 0 填补**
        #        （AGENTS.md 三.6：取不到显示 —，不得显示成 0）。
        #
        # 口径与全页一致：日期键取 substr("End Time",1,10)（End Time 是区间右端点，
        # 比统计日早一天 —— 全页 18 个老组件同此约定，为了横轴能对齐，这里不改）。
        # 值的取法比老组件多一道数值守卫：上游列是 TEXT，空串或 "-" 这类非数值
        # 必须变成 NULL（断线），**不能 CAST 成 0 混进均值**。
        "id": "c14",
        "title": "Incoming Call Answer Ratio(%)",
        "chart": "line",
        "pct": True,
        "sql": (
            'SELECT substr("End Time",1,10) AS t, '
            'ROUND(AVG(v),2) AS "Incoming Call Answer Ratio(%)" FROM (\n'
            'SELECT "End Time", CASE WHEN TRIM("(Incoming Call)Answer Ratio(%)") '
            "GLOB '*[0-9]*' THEN "
            'CAST(TRIM("(Incoming Call)Answer Ratio(%)") AS REAL) END AS v '
            'FROM "Incoming_Call_Answer_Ratio_OSS_EMS"\n'
            ")  GROUP BY substr(\"End Time\",1,10) "
            "HAVING t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
    {
        # 呼出应答率（2026-11-20 新增，与 c14 成对）。
        # 表名与列名是 Outgoing（不是用户口述的 Outcoming），口径、粒度、
        # 单级取数的理由与 c14 完全相同，见上面的注释。
        "id": "c15",
        "title": "Outgoing Call Answer Ratio(%)",
        "chart": "line",
        "pct": True,
        "sql": (
            'SELECT substr("End Time",1,10) AS t, '
            'ROUND(AVG(v),2) AS "Outgoing Call Answer Ratio(%)" FROM (\n'
            'SELECT "End Time", CASE WHEN TRIM("(Outgoing Call)Answer Ratio(%)") '
            "GLOB '*[0-9]*' THEN "
            'CAST(TRIM("(Outgoing Call)Answer Ratio(%)") AS REAL) END AS v '
            'FROM "Outgoing_Call_Answer_Ratio_OSS_EMS"\n'
            ")  GROUP BY substr(\"End Time\",1,10) "
            "HAVING t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
    {
        # 2026-11-20 新增：国际去话 / 来话 MOU（时长，单位分钟）。
        #
        # 表由 PS_CS_CORE 的 CSV_EXTRA 收录（keyword `International_MOU_OSS`，mode
        # `daily_sum`）：oss 导出的原始 CSV 是 **1Day(s) 但按 MO Location 分行**
        # （每天约 17 行），入库时按天把两个 MOU 列加总成一天一行，`MO Location` 合并掉。
        # 所以这张表已经是「每天一行、Outgoing/Incoming 两列同表」—— 一张图两条线，
        # 不需要拆成两张卡（与 MTN MOU 那张表同构）。
        #
        # 数据源只有人工导出的 _EMS 一张表（没有 _UME 孪生表、也没有 1month_* 月表）
        # → 不登记 source_table / fallback / monthly_table，走单级取数：oss 停导就空。
        #
        # 日期键沿用全页口径 substr("End Time",1,10)（End Time 是区间右端点 = 次日 00:00，
        # 标签比统计日早一天），这样与相邻的 VLR / Voice / 应答率卡片横轴能对齐。
        # SUM 而非 MAX：正常一天只有一行，SUM 只为「将来多网元」时不出错。
        "id": "c16",
        "title": "International MOU",
        "chart": "line",
        "sql": (
            'SELECT substr("End Time",1,10) AS t, ROUND(SUM(o),2) AS "Outgoing MOU", '
            'ROUND(SUM(i),2) AS "Incoming MOU" FROM (\n'
            'SELECT "End Time", '
            "CASE WHEN TRIM(\"Outgoing MOU\") GLOB '*[0-9]*' THEN "
            'CAST(TRIM("Outgoing MOU") AS REAL) END AS o, '
            "CASE WHEN TRIM(\"Incoming MOU\") GLOB '*[0-9]*' THEN "
            'CAST(TRIM("Incoming MOU") AS REAL) END AS i '
            'FROM "International_MOU_OSS_EMS"\n'
            ")  GROUP BY substr(\"End Time\",1,10) "
            "HAVING t >= {{LO}} AND t < {{HI}} ORDER BY t"
        ),
    },
    {
        # 2026-11-20 新增：MGW32 物理资源利用率（TONE / TC / RTP 三个百分比）。
        #
        # ⚠ 这张表是 **1Hour(s) 小时粒度**（31 天 × 24 行 = 744 行，单网元
        #   V4_MGW-NGMGW_W_32,32），所以与全页其它「日表」两处不同：
        #   1) **按天取算术平均**，不是求和 —— 百分比相加会得到 300%+；
        #      也不是峰值（峰值要看容量瓶颈的话得另开一列 MAX，见手册 §33.3e）。
        #   2) **按 Start Time 归日**：每小时属于它开始的那天。若用 End Time，
        #      23:00–24:00 那一小时（End Time = 次日 00:00）会被算进第二天，
        #      并凭空多出一个「只含 1 个样本」的末日点，看图会像突然掉档。
        #   两处都在口径条里写明，避免把「日均」读成「峰值」、把归日问题当数据异常。
        #
        # 同样没有 _UME 孪生表 / 1month_* 月表（自动链路那张 CS_MRFP96_…_UME 是
        # **另一种设备**的资源利用率，不是这张表的回退源）→ 单级取数。
        "id": "c17",
        "title": "MGW32 Resource Utilization(%)",
        "chart": "line",
        "pct": True,
        "sql": (
            'SELECT substr("Start Time",1,10) AS t, '
            'ROUND(AVG(tone),2) AS "TONE", ROUND(AVG(tc),2) AS "TC", '
            'ROUND(AVG(rtp),2) AS "RTP" FROM (\n'
            'SELECT "Start Time", '
            "CASE WHEN TRIM(\"Utilization Ratio of TONE Resource(%)\") GLOB '*[0-9]*' "
            'THEN CAST(TRIM("Utilization Ratio of TONE Resource(%)") AS REAL) END AS tone, '
            "CASE WHEN TRIM(\"Utilization Ratio of TC Resource(%)\") GLOB '*[0-9]*' "
            'THEN CAST(TRIM("Utilization Ratio of TC Resource(%)") AS REAL) END AS tc, '
            "CASE WHEN TRIM(\"Utilization Ratio of RTP Resource(%)\") GLOB '*[0-9]*' "
            'THEN CAST(TRIM("Utilization Ratio of RTP Resource(%)") AS REAL) END AS rtp '
            'FROM "CS_MGW32_Resource_Utilization_OSS_EMS"\n'
            ")  GROUP BY substr(\"Start Time\",1,10) "
            # t < date('now')：**不含今天**。小时表里今天是「已完成到当前小时」的部分样本，
            # 拿去算日均会被部分小时拉低，看着像资源占用骤降；等今天过完自然进图。
            "HAVING t >= {{LO}} AND t < {{HI}} AND t < date('now') ORDER BY t"
        ),
    },
    {
        # 2026-11-20 新增：MGW32 资源**占用量**（信道数，capacity 模板）。
        #
        # 与 c17 是同一台 MGW、同一次测量的两种表达：
        #   c17 = Utilization Ratio(%)，来源 CS_MGW32_Resource_Utilization_OSS_EMS；
        #   c18 = Average Number ... being Used（占用的信道数），来源
        #         CS_MGW32_Resource_Capacity_OSS_EMS（同表还有 Number of Available X，
        #         实测恒为 TONE 4056 / TC 74160 / RTP 90000，即分母）。
        # 所以这张卡给的是**绝对量**（多少条信道在用），c17 给的是占比；
        # 要「还剩多少余量」用 Available - Used，两张卡对着看即可。
        #
        # ⚠ 三种资源量级差两个数量级（日均 TONE≈400 / TC≈9000 / RTP≈19000），
        #   画在一根轴上 TONE 会贴着底走 —— 这是数据本身的比例，不是丢数；
        #   横向比「各自的高低走势」，不要比三条线的高度差。
        #
        # 同样是 1Hour(s) 小时表（每天 24 行）→ 按天取平均、按 Start Time 归日
        # （理由与 c17 完全一致）。计数不能求和（同一天 24 个小时相加没有意义）。
        "id": "c18",
        "title": "MGW32 Resource Usage(count)",
        "chart": "line",
        "sql": (
            'SELECT substr("Start Time",1,10) AS t, '
            'ROUND(AVG(tone),0) AS "TONE used", ROUND(AVG(tc),0) AS "TC used", '
            'ROUND(AVG(rtp),0) AS "RTP used" FROM (\n'
            'SELECT "Start Time", '
            "CASE WHEN TRIM(\"Average Number of TONE being Used\") GLOB '*[0-9]*' "
            'THEN CAST(TRIM("Average Number of TONE being Used") AS REAL) END AS tone, '
            "CASE WHEN TRIM(\"Average Number of TC being Used\") GLOB '*[0-9]*' "
            'THEN CAST(TRIM("Average Number of TC being Used") AS REAL) END AS tc, '
            "CASE WHEN TRIM(\"Average Number of RTP being Used\") GLOB '*[0-9]*' "
            'THEN CAST(TRIM("Average Number of RTP being Used") AS REAL) END AS rtp '
            'FROM "CS_MGW32_Resource_Capacity_OSS_EMS"\n'
            ")  GROUP BY substr(\"Start Time\",1,10) "
            # 同上：不含今天（今天只有已完成的那几个小时，日均会被拉低）
            "HAVING t >= {{LO}} AND t < {{HI}} AND t < date('now') ORDER BY t"
        ),
    },
]

# 标题栏（原版 text 组件的 HTML）。
# 注意：原版含指向 FineBI（http://192.168.100.19/webroot/decision/link/iYiQ）的外链，
# 本页已不依赖 FineBI，故去掉超链接，仅保留标题文本与样式。
TITLE_HTML = (
    '<div style="width:100%;display:flex;flex-direction:column">'
    '<div><p style="text-align:center;margin:0">'
    '<span style="font-size:30px;color:#ffffff">❯❯❯ '
    "CCN_Daily_KPI"
    ' ❮❮❮</span></p></div></div>'
)


# ----------------------------------------------------------------------
# 只读守卫与远端查询
# ----------------------------------------------------------------------
def _guard(sql: str) -> str:
    """本地只读守卫：必须 SELECT/WITH 开头、禁止分号（单语句）。"""
    s = (sql or "").strip()
    if not s:
        raise ValueError("SQL 不能为空")
    if ";" in s:
        raise ValueError("仅允许单条语句（不允许分号多语句）")
    if not _GUARD_RE.match(s):
        raise ValueError("仅允许 SELECT / WITH 只读查询")
    return s


# 日期字面量严格校验：只接受 YYYY-MM-DD，杜绝任何注入可能
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# SQL 里的时间区间占位符（原 SQL 的 date('now','-31 day') / datetime('now','-31 day')
# 已全部改为这两个占位符，由 _apply_range() 按请求区间替换）
_LO_TOKEN = "{{LO}}"
_HI_TOKEN = "{{HI}}"

# 默认区间：近 31 天（与原版 FineBI 口径一致）
_DEFAULT_LO = "date('now','-31 day')"
_DEFAULT_HI = "date('now','+1 day')"

# 月粒度默认区间：近 13 个月。
# 为什么单独一套：月表的点落在每月 1 号，用「近 31 天」过滤最多只剩 1~2 个点
# （2026-10-01 实测就是 2 个），曲线等于没有 —— 降级到月表就失去意义了。
# 取 13 个月可覆盖「去年同月 ~ 本月」，够看出同比趋势又不至于拖回太久。
_MONTHLY_LO = "date('now','-13 month')"
_MONTHLY_HI = "date('now','+1 month')"


def _validate_date(v: Optional[str], field: str) -> Optional[str]:
    """校验 YYYY-MM-DD；空值返回 None。非法即抛错（不静默忽略）。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if not _DATE_RE.match(s):
        raise ValueError(f"{field} 格式必须是 YYYY-MM-DD，收到：{v!r}")
    return s


def _apply_range(sql: str, start: Optional[str], end: Optional[str],
                 monthly: bool = False) -> str:
    """把 SQL 里的 {{LO}} / {{HI}} 占位符替换为已校验的日期字面量。

    - 无占位符的 SQL（如 KPI 单值）原样返回，不受区间影响。
    - start/end 均为 None 时回退默认区间：日粒度近 31 天（与原版 FineBI 一致），
      monthly=True 时改用近 13 个月（月表在 31 天窗口里只剩 1~2 个点）。
    - 替换值经 _validate_date 严格校验（仅 YYYY-MM-DD），不含引号/分号，
      故不存在注入面；时间列形如 'YYYY-MM-DDTHH:MM:SS' 文本，
      按 substr(...,1,10) 生成的 'YYYY-MM-DD' 做字典序比较即为日期比较。
    """
    if _LO_TOKEN not in sql and _HI_TOKEN not in sql:
        return sql
    lo = _validate_date(start, "start")
    hi = _validate_date(end, "end")
    if lo:
        lo_sql = f"'{lo}'"
    else:
        lo_sql = _MONTHLY_LO if monthly else _DEFAULT_LO
    if hi:
        hi_sql = f"'{hi}'"
    else:
        hi_sql = _MONTHLY_HI if monthly else _DEFAULT_HI
    return sql.replace(_LO_TOKEN, lo_sql).replace(_HI_TOKEN, hi_sql)


def data_source_label() -> str:
    """页面 meta 的「数据源」标签：PG 优先时显示 local PG，而不是 .19 地址。"""
    try:
        from backend.services.db.pg_query import primary_source_label

        return primary_source_label()
    except Exception:  # noqa: BLE001 — 标签失败不影响取数
        return get_api_base_url()


def query_remote(sql: str, db: str = REMOTE_DB, limit: int = 5000,
                 timeout: Optional[float] = None) -> Dict[str, Any]:
    """调用远端只读 SQL API 执行一条 SELECT。

    返回远端原始结构：{success, columns, rows, count, truncated, elapsed_ms}

    `timeout`（2026-11-22 加）：单条查询的 socket 超时，缺省用模块级
    `QUERY_TIMEOUT`（60s）。整页取数带预算的调用方（如 cn_kpi_split_service）
    应把「剩余预算」传进来，避免被放弃的组件线程在后台继续占 60s。
    """
    sql = _guard(sql)
    url = f"{get_api_base_url()}/api/query"
    payload = json.dumps({"db": db, "sql": sql, "limit": limit},
                         ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _OPENER.open(req, timeout=timeout or QUERY_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception as exc:
            logger.debug("读取远端错误响应体失败: %s", exc)
        raise RuntimeError(f"远端数据源返回 HTTP {e.code}: {detail or e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"无法连接远端数据源 {url}：{getattr(e, 'reason', e)}"
        ) from e


def _rewrite_source_table(sql: str, src: str, dst: str) -> str:
    """把 SQL 里的源表名整体替换为回退表名。

    src 来自本模块内置的 WIDGETS 定义（非用户输入），dst 由 _resolve_fallback()
    经白名单正则校验后产生，故此处字符串替换不存在注入面。
    """
    return sql.replace(f'"{src}"', f'"{dst}"')


def _detect_granularity(rows: List[List[Any]]) -> str:
    """从返回行推断时间粒度：'month' | 'day' | ''。

    为什么靠数据推断而不是靠表名/配置：上游同一张表会在日粒度与月粒度之间
    来回翻转（人工导出选的时间区间决定整表粒度，ps_cs_core 是整表 full_replace）。
    写死任何一个都会在下次漂移时变成假话。

    判据：看**原始** x 值的形态（第一列）。
      · 含 ':' 或 'T'（如 '2026-09-01 00:00'）→ 小时级，属 day 档
      · 形如 'YYYY-MM-DD' 且日部分全为 01      → month
      · 其余                                    → day

    ⚠ 必须用原始值判断，不能先截成前 10 位：小时级数据 '2026-09-01 00:00' 截断后
    是 '2026-09-01'，日部分恰好是 '01'，会被误判成月粒度 ——
    c7/c8/c10 三张 xGW 图是小时粒度且都落在每月 1 号起始，正是这个坑。
    """
    vals = [str(r[0]) for r in rows if r and r[0] is not None]
    if len(vals) < 2:
        return "day"
    # 有任何一条带时分 → 小时级，直接归 day 档
    if any(":" in v or "T" in v for v in vals):
        return "day"
    days = [v[:10] for v in vals]
    # 各组件 SQL 都 GROUP BY 到天/月，x 值必唯一；去重后只剩 1 个点无法区分粒度，
    # 按 day 处理（窗口问题由 tier-3 的近 13 个月区间解决，不在这里猜）。
    uniq = sorted(set(days))
    if len(uniq) < 2:
        return "day"
    if all(len(d) == 10 and d[8:10] == "01" for d in uniq):
        return "month"
    return "day"


def _fallback_candidate(src: str) -> bool:
    """是否存在**可能**的 UME 回退表（不探测，仅看命名规则 + 内置映射）。

    用于决定要不要把「回退」作为一级加入尝试链。这里刻意不做远端探测：
    真正的存在性由 _resolve_fallback() 在需要时判断，避免每个组件都多打一次查询。
    """
    if not src:
        return False
    if src in EMS_FALLBACK_TABLE:
        return True
    if not src.endswith(_EMS_SUFFIX):
        return False
    return bool(_TABLE_NAME_RE.match(src[: -len(_EMS_SUFFIX)] + _UME_SUFFIX))


def _resolve_fallback(src: str) -> Optional[str]:
    """给定 EMS 表名，返回可用的同名 UME 回退表名；没有则 None。

    先查内置映射表；未命中则按 `_EMS` → `_UME` 规则猜一个，
    并**实际探测该表是否存在**（ccn_ume_kpi_service 的表名规则见其
    CNN_KPI_QUERY_CONFIGS 的 table_name 字段）。探测失败按「不存在」处理。

    已知**没有** UME 孪生表的 KPI：USSD_RATIO、LOCATION_UPDATE ——
    ccn_ume_kpi_service.CNN_KPI_QUERY_CONFIGS 只覆盖 8 个模板，不含这两个，
    所以 _resolve_fallback() 会返回 None，它们只能靠 monthly_table 兜底。
    （注意 2026-10-01 观测到 USSD_RATIO_TEMPLATE_OSS_EMS 被上游刷新回日粒度，
    但这不代表它有 UME 孪生表 —— 只是人工导出恰好覆盖到了近期，随时可能再停。）
    """
    if not src.endswith(_EMS_SUFFIX):
        return None
    mapped = EMS_FALLBACK_TABLE.get(src)
    if mapped:
        return mapped
    cand = src[: -len(_EMS_SUFFIX)] + _UME_SUFFIX
    if not _TABLE_NAME_RE.match(cand):
        return None
    # ⚠ 必须实际探测：EMS→UME 的命名替换只是猜测，缺失时返回 None，
    # 否则每次取数都会先打一次注定 500 的查询（多一次无谓的远端往返 + 噪音日志）。
    try:
        query_remote(f'SELECT COUNT(*) AS n FROM "{cand}"', limit=1)
    except Exception as e:  # noqa: BLE001 — 表不存在/名称不符都算没有回退
        logger.info("CN KPI 回退表探测失败 %s: %s", cand, e)
        return None
    return cand


def _has_any_value(rows: List[List[Any]], ncols: int) -> bool:
    """行里是否至少有一个非空值。

    为什么需要：c1/c4 改成「日历骨架」SQL 后，即使两张源表都没有数，
    日历也会返回一堆 NULL 行。不能把这种「有行但全空」当成取数成功，
    否则会跳过降级/报错分支，页面显示一张空图而不是「暂无数据」。

    ⚠ 单值 KPI（如 kpi_time 的 `SELECT MAX(...)`）只有一列，那一列本身就是值，
    不能按「第 1 列是日期、跳过」处理 —— 否则会被误判成空数据。
    """
    if ncols <= 1:
        return any(r and r[0] is not None and r[0] != "" for r in rows)
    return any(v is not None and v != "" for r in rows for v in r[1:])


def _series_coverage(cols: List[str], rows: List[List[Any]]) -> Dict[str, Any]:
    """统计每个来源序列的覆盖（起止日期 + 有数天数）与两源都没数的日期。

    2026-11-18 起 c1/c4 的 SQL 返回日历骨架 + [EMS, UME] 两列：某天某来源
    没有行时该列为 NULL，前端据此**断线**（不用另一来源顶替、不插值）。
    这里只做统计、不重算数值，页面用「EMS 31 天 / UME 80 天」把谁缺数说清。
    """
    cov: Dict[str, Dict[str, Any]] = {}
    for ci in range(1, len(cols)):
        days = [str(r[0])[:10] for r in rows if ci < len(r) and r[ci] not in (None, "")]
        if days:
            cov[str(cols[ci])] = {"from": days[0], "to": days[-1], "days": len(days)}
    missing = [
        str(r[0])[:10] for r in rows
        if all(ci >= len(r) or r[ci] in (None, "") for ci in range(1, len(cols)))
    ]
    return {"coverage": cov, "missing_days": missing}


def _fetch_widget(w: Dict[str, Any], start: Optional[str] = None,
                  end: Optional[str] = None) -> Dict[str, Any]:
    """取单个组件的数据；失败只标记该组件，不影响整页。

    三级取数（逐级降级，任何一级有行就用它）：
      1) 主表（_EMS，日粒度，人工导出）           —— 最新但常停摆
      2) fallback 表（_UME，自动同步）            —— 仅部分 KPI 存在
      3) monthly_table（1month_*，月粒度）        —— 兜底，保证有历史可看

    降级生效时在 out["degraded"] / out["granularity"] / out["fallback_from"]
    标注，前端据此给出「非日粒度」提示，避免把月值误读成日值。
    """
    out: Dict[str, Any] = {
        "id": w["id"],
        "title": w["title"],
        "chart": w["chart"],
        "pivot": bool(w.get("pivot")),
        "pct": bool(w.get("pct")),
        "sql": "",
        "columns": [],
        "rows": [],
        "error": "",
        # 降级信息："" = 用主表原样取到；day_ume = 回退到自动同步表；
        # monthly = 降级到月粒度表
        "degraded": "",
        "fallback_from": "",
        "granularity": "",
        # 分源覆盖（只对带 source_split 的组件，如 c1 VLR / c4 Voice 填充）：
        # coverage = {序列名: {from,to,days}}；missing_days = 两源都没有数的日期
        "coverage": {},
        "missing_days": [],
    }
    if w["chart"] == "text":
        return out

    def _run(sql_text: str) -> tuple[List[str], List[List[Any]]]:
        # 本机 PG 优先、远端回退（2026-11-22）；PG 缺表/空结果时行为与改造前一致
        from backend.services.db.pg_query import query_data

        res = query_data(sql_text, db=REMOTE_DB)
        if not res.get("success", True):
            raise RuntimeError(res.get("error") or "远端返回 success=false")
        cols = list(res.get("columns") or [])
        rows = [list(r) for r in (res.get("rows") or [])]
        return cols, rows

    src = w.get("source_table", "")

    # 逐级尝试：主表 → UME 回退表 → 月粒度表。
    # 每一级都独立 try：**查询报错与返回空行一样要降级**。
    # 早期版本只在「返回 0 行」时降级，主表一旦改名/被删就直接抛错开天窗 ——
    # 而上游表被人工导出重命名正是本页的常态，所以错误必须同样触发降级。
    tiers: List[tuple] = [("day", w["sql"], None)]
    # self_contained（c1/c4）：SQL 自带两张源表 + 日历骨架，不能再走降级 ——
    # 降级会把 SQL 里的 EMS 表名整体改写成 UME，两条序列变成同一张表。
    if not w.get("self_contained"):
        if w.get("fallback") or _fallback_candidate(src):
            tiers.append(("day_ume", w["sql"], w.get("fallback") or _resolve_fallback(src)))
        if w.get("monthly_table"):
            tiers.append(("monthly", w["sql"], w["monthly_table"]))

    errors: List[str] = []
    for kind, base_sql, dst in tiers:
        # 月粒度那一级重算区间（近 13 个月）；日粒度两级用请求区间
        monthly = kind == "monthly"
        use_sql = _apply_range(base_sql, start, end, monthly=monthly)
        if dst:
            if not _TABLE_NAME_RE.match(dst):
                logger.warning("CN KPI 组件 %s 目标表名不合法：%r", w["id"], dst)
                continue
            use_sql = _rewrite_source_table(use_sql, src, dst)
        try:
            cols, rows = _run(use_sql)
        except Exception as e:  # noqa: BLE001 — 该级失败则试下一级
            logger.warning("CN KPI 组件 %s(%s) 数据源 %s 取数失败：%s",
                           w["id"], w["title"], dst or src or "(主表)", e)
            errors.append(f"{dst or src}: {e}")
            continue
        # 「日历骨架」会返回全 NULL 行：有行 ≠ 有数据，必须再判一次。
        if rows and _has_any_value(rows, len(cols)):
            out["sql"] = use_sql
            out["columns"] = cols
            out["rows"] = rows
            # 粒度按数据实际推断（月表那一级必然是 month，日粒度两级交给检测）
            gran = "month" if monthly else _detect_granularity(rows)
            out["granularity"] = gran
            if kind != "day":
                out["degraded"] = kind
                out["fallback_from"] = src or "(主表)"
                logger.info("CN KPI 组件 %s(%s) 降级到 %s（%s，%s，%d 行）",
                            w["id"], w["title"], dst, kind, gran, len(rows))
            elif gran == "month":
                # 主表本身就是月粒度：不算「降级」，但必须标注粒度
                logger.info("CN KPI 组件 %s(%s) 主表为月粒度数据（%d 行）",
                            w["id"], w["title"], len(rows))
            # 分源组件（c1/c4）：回传每个来源的覆盖天数与两源都缺的日期。
            # 只做统计，不改数值；页面据此显示「EMS 31 天 / UME 80 天」。
            if kind == "day" and gran == "day" and w.get("source_split"):
                extra = _series_coverage(cols, rows)
                out.update(extra)
                if extra["missing_days"]:
                    logger.info("CN KPI 组件 %s(%s) 两源都缺 %d 天",
                                w["id"], w["title"], len(extra["missing_days"]))
            return out

    # 所有级别都没有数据
    if errors:
        # 每一级都失败 —— 报第一个错误，但把级别数写进日志便于定位
        logger.warning("CN KPI 组件 %s(%s) 全部 %d 级数据源均失败",
                       w["id"], w["title"], len(tiers))
        out["error"] = errors[0]
    else:
        logger.warning("CN KPI 组件 %s(%s) 全部 %d 级数据源在窗口内均无数据",
                       w["id"], w["title"], len(tiers))
    return out


# ----------------------------------------------------------------------
# 缓存（按时间区间分行）—— stale-while-revalidate
#
# 2026-11-18 切生产后治理「冷缓存 504」（TODO P1）：
#   旧行为：TTL 到期后下一个请求**同步**跑 18 个组件（6 并发、单条 60s、三级回退），
#   冷时超过 nginx `/api/` 的 300s proxy_read_timeout → 浏览器 504，`?page=cn_kpi`
#   只剩空壳；等后台算完再点一次才 0.02s 出数。
#   新行为：
#     · 热（< TTL）：直接返回，与原来完全一致；
#     · 有旧值（内存或磁盘 last-known-good）：**立即**返回旧值，meta 带
#       `stale=True / refreshing=True / data_at`，后台单飞线程刷新；
#     · 连旧值都没有（首次部署/换区间）：立即返回 `meta.computing=True`（空 widgets）
#       并后台刷新，前端显示「正在计算」，轮询到出数。
#   刷新结果落盘 `data/cn_kpi/<区间>.json`，进程重启后第一屏也有旧值可用。
#   这样任何请求都不会再阻塞到 nginx 超时，冷缓存不再是「空壳」而是明确状态。
# ----------------------------------------------------------------------
CACHE_TTL = int(os.environ.get("CN_KPI_CACHE_TTL", "60"))
# 两次后台刷新之间的最小间隔：上游故障时避免「每个请求都起一个线程」的踩踏
REFRESH_MIN_INTERVAL = int(os.environ.get("CN_KPI_REFRESH_MIN_INTERVAL", "30"))
LAST_GOOD_DIR = Path(__file__).resolve().parents[3] / "data" / "cn_kpi"

_cache_lock = threading.Lock()
_cache: Dict[str, Dict[str, Any]] = {}
_last_good: Dict[str, Dict[str, Any]] = {}
_inflight: set[str] = set()
_last_attempt: Dict[str, float] = {}


def _key_slug(key: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "_", key).strip("_") or "all"


def _last_good_path(key: str) -> Path:
    return LAST_GOOD_DIR / f"{_key_slug(key)}.json"


def _load_last_good(key: str) -> Optional[Dict[str, Any]]:
    """读磁盘旧值（每个区间只读一次，之后走内存）。"""
    if key in _last_good:
        return _last_good[key]
    p = _last_good_path(key)
    if not p.is_file():
        return None
    try:
        entry = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(entry, dict) or "data" not in entry or "at" not in entry:
            return None
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 CN KPI 旧缓存失败（%s）：%s", p, e)
        return None
    _last_good[key] = entry
    return entry


def _save_last_good(key: str, entry: Dict[str, Any]) -> None:
    """原子落盘；失败只记日志（缓存写不进去不该影响出数）。"""
    try:
        LAST_GOOD_DIR.mkdir(parents=True, exist_ok=True)
        p = _last_good_path(key)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:  # noqa: BLE001
        logger.warning("写 CN KPI 旧缓存失败（%s）：%s", key, e)


def _compute(key: str, start_v: Optional[str], end_v: Optional[str]) -> Dict[str, Any]:
    """真正取数（23 个组件并发）。只在后台线程里跑，不直接服务请求。"""
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        widgets = list(pool.map(lambda w: _fetch_widget(w, start_v, end_v), WIDGETS))

    failed = sum(1 for w in widgets if w["error"])
    data = {
        "meta": {
            "db": REMOTE_DB,
            "host": data_source_label(),
            "count": len(widgets),
            "failed": failed,
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
            "cached": False,
            "stale": False,
            "refreshing": False,
            "start": start_v,
            "end": end_v,
        },
        "title": "CCN_Daily_KPI",
        "title_html": TITLE_HTML,
        "widgets": widgets,
    }
    entry = {"at": time.time(), "data": data}
    with _cache_lock:
        _cache[key] = entry
        _last_good[key] = entry
    _save_last_good(key, entry)
    return data


def _refresh_async(key: str, start_v: Optional[str], end_v: Optional[str]) -> bool:
    """起一个后台刷新线程（同一区间单飞）。返回是否真的起了。"""
    now = time.time()
    with _cache_lock:
        if key in _inflight:
            return False
        if now - _last_attempt.get(key, 0.0) < REFRESH_MIN_INTERVAL:
            return False
        _inflight.add(key)
        _last_attempt[key] = now

    def _run() -> None:
        try:
            _compute(key, start_v, end_v)
            logger.info("CN KPI 后台刷新完成 key=%s", key)
        except Exception:  # noqa: BLE001
            logger.exception("CN KPI 后台刷新失败 key=%s", key)
        finally:
            with _cache_lock:
                _inflight.discard(key)

    threading.Thread(target=_run, name=f"cn-kpi-refresh-{_key_slug(key)}", daemon=True).start()
    return True


def _with_meta(data: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    """浅拷贝 meta 后返回；widgets 不复制（只读）。"""
    out = dict(data)
    out["meta"] = {**data.get("meta", {}), **extra}
    return out


def get_overview(
    force: bool = False,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Dict[str, Any]:
    """整页数据：立即返回（热值 / 旧值 / computing），刷新在后台做。

    start / end：YYYY-MM-DD；留空表示近 31 天（与原版 FineBI 一致）。
    返回 {meta:{db,host,count,failed,elapsed_ms,cached,stale,refreshing,computing?,data_at?,start,end},
          title, title_html, widgets:[...]}
    """
    # 先校验区间（非法直接抛错 → 路由转 400，不进入取数）
    start_v = _validate_date(start, "start")
    end_v = _validate_date(end, "end")
    if start_v and end_v and start_v > end_v:
        raise ValueError(f"开始日期不能晚于结束日期：{start_v} > {end_v}")

    key = f"{start_v or ''}~{end_v or ''}"
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)

    if not force and hit is not None and (now - hit["at"]) < CACHE_TTL:
        return _with_meta(hit["data"], cached=True)

    entry = hit or _load_last_good(key)
    if entry is not None:
        # 有旧值：立刻给页面，后台刷新（冷缓存不再阻塞到 504）
        started = _refresh_async(key, start_v, end_v)
        with _cache_lock:
            busy = key in _inflight
        data_at = datetime.fromtimestamp(entry["at"]).strftime("%Y-%m-%d %H:%M:%S")
        return _with_meta(entry["data"], cached=True, stale=True,
                          refreshing=bool(started or busy), computing=False, data_at=data_at)

    # 连旧值都没有：告诉前端「正在计算」，别给空壳
    _refresh_async(key, start_v, end_v)
    return {
        "meta": {
            "db": REMOTE_DB,
            "host": data_source_label(),
            "count": 0,
            "failed": 0,
            "elapsed_ms": 0.0,
            "cached": False,
            "stale": False,
            "refreshing": True,
            "computing": True,
            "start": start_v,
            "end": end_v,
        },
        "title": "CCN_Daily_KPI",
        "title_html": TITLE_HTML,
        "widgets": [],
    }


def health() -> Dict[str, Any]:
    """远端连通性探测（供页面诊断提示用）。"""
    try:
        res = query_remote("SELECT 1 AS ok", limit=1)
        return {"ok": True, "host": get_api_base_url(), "db": REMOTE_DB,
                "elapsed_ms": res.get("elapsed_ms")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "host": get_api_base_url(), "db": REMOTE_DB, "error": str(e)}

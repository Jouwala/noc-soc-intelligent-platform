# -*- coding: utf-8 -*-
"""CS / PS CN KPI 15 分钟看板的静态守卫（2026-11-23 加「流量桶必须采满」时建立）。

背景（用户报障）：`?page=ps_cn_kpi_15mins` 上 RG Traffic(GB) 与 4G FDD Traffic(GB)
的 **D0 最后一个 15 分钟粒度总是掉一大截**，实际没有下降。原因是 5 分钟档聚到
15 分钟桶时，D0 最新桶只有 2/3 个采样，SUM 出来正好是满桶的 2/3（实测 09:15 桶
RG-6 1.357 vs 满桶约 2.0、4G FDD 1553 vs 约 2285）。修法是让这两类 SUM 卡要求
桶内 3 个采样齐全，未采满的桶整桶不画。

这个用例锁住三件事，防止将来改 SQL 时又把假下跌带回来：

1. **SUM 流量卡**（RG-6/11/12/30、4G FDD）必须带「桶内有效采样数」列
   （`COUNT(DISTINCT CASE WHEN <值> IS NOT NULL THEN "Begin Time" END) AS n`）
   与外层 `WHERE n = 3`，且**只投影 [t, series, day, v] 四列**（多一列会破坏前端
   与导出约定的列序）；只数 `Begin Time` 不数数值有效性的话，一条 `-` 值照样让
   SUM 少 1/3 而躲过过滤；
2. **非 SUM 卡**（AVG / gauge / 原生 15 分钟档的 3G FDD）不得带这道条件 ——
   它们对采样个数不敏感，去掉最新点反而少画一格；
3. SQL 仍必须是单条只读 SELECT（走 `cn_kpi_service._guard`）。

不联网、不 import 需要运行环境的模块：`cn_kpi_15mins_widgets` 模块级只做字符串
拼接，`cn_kpi_service` 的模块级也只定义常量，因此 CI 里可以跑。
"""
import re

import pytest

from backend.services.cn import cn_kpi_service as svc
from backend.services.cn.cn_kpi_15mins_widgets import BOARDS

# 5m→15m 且按桶求和的两张卡（前者 4 条 RG 线、后者 1 条）
SUM_5M_GROUPS = {"ps_15m_rg", "ps_15m_4g_fdd"}
# 明确不该带「采满」条件的组：AVG 比率、gauge、原生 15 分钟档
NON_SUM_GROUPS = {
    "ps_15m_xgw_cpu", "ps_15m_xgw_lic", "ps_15m_data_subs", "ps_15m_dns",
    "ps_15m_3g_fdd", "cs_15m_vlr", "cs_15m_voice",
}

_FULL_BUCKET = re.compile(
    r'COUNT\(DISTINCT CASE WHEN .+? IS NOT NULL THEN "Begin Time" END\) AS n', re.I)
_OUTER_FILTER = re.compile(r"SELECT t, series, day, v FROM \(.+\) AS _full_bucket "
                           r"WHERE n = 3", re.I | re.S)


def _groups():
    return [w for board in ("cs", "ps") for w in BOARDS[board]["widgets"]]


def _group(gid):
    for w in _groups():
        if w["id"] == gid:
            return w
    raise AssertionError(f"组件 {gid} 不存在")


@pytest.mark.parametrize("gid", sorted(SUM_5M_GROUPS))
def test_sum_traffic_requires_full_bucket(gid):
    """SUM 流量卡：桶内 3 个采样齐全才输出，且投影仍是四列。"""
    w = _group(gid)
    assert w["grain"] == "5m"
    # RG 是 4 条独立分支，4G FDD 是 1 条 → 分支数 = parts 数
    assert len(_FULL_BUCKET.findall(w["sql"])) == len(w["parts"])
    assert _OUTER_FILTER.search(w["sql"]), w["sql"]
    # 只投影四列：多出的 n 列会破坏前端 [时刻, 维度, 日, 值] 的列序约定
    assert not re.search(r"SELECT \* FROM", w["sql"], re.I)


@pytest.mark.parametrize("gid", sorted(NON_SUM_GROUPS))
def test_non_sum_cards_keep_last_point(gid):
    """AVG / gauge / 原生 15 分钟档不加「采满」条件（否则会白丢最新一格）。"""
    assert "AS n" not in _group(gid)["sql"]
    assert "_full_bucket" not in _group(gid)["sql"]


def test_traffic_cards_are_grouped_sql_not_melted():
    """RG 组的 SQL 是自定义拼的（每序列独立 where），防止有人误删 expect_samples。"""
    sql = _group("ps_15m_rg")["sql"]
    for mo in ("6", "11", "12", "30"):
        assert f'"Measurement Object" = \'{mo}\'' in sql
    assert "SELECT t, series, day, v FROM" in sql


def test_all_board_sql_is_single_read_only_select():
    """只读守卫：所有组件 SQL 必须是单条 SELECT / WITH，没有分号。"""
    for w in _groups():
        out = svc._guard(w["sql"])
        assert out == w["sql"]
        assert ";" not in out
        assert re.match(r"^\s*(select|with)\b", out, re.I)

# -*- coding: utf-8 -*-
"""CN KPI 组件定义的静态守卫（2026-11-20 新增 4 个组件时建立）。

背景：`?page=cn_kpi` 的 22 个组件是**纯数据驱动**的（后端 `WIDGETS` → 前端泛化渲染），
所以「新增一张图」几乎只改一个字典。好处是没有渲染代码要抄，坏处是**没有编译期检查**：
表名写错、把降级源登记错、往 SQL 里塞了分号或多语句，都要等到页面空掉/取数报错才发现。

这里用**静态断言**锁住 2026-11-20 新增的四个组件（c14/c15 呼入·呼出应答率、
c16 国际 MOU、c17 MGW32 资源利用率）：

1. id / 标题 / 百分比标记 / 读的表名 / 引用的指标列必须对得上
   （表名或列名写错会静默变成空图，比报错更难查）；
2. 它们**必须不登记** source_table / fallback / monthly_table ——
   这四张 `_EMS` 表都没有 `_UME` 孪生表、也没有 `1month_*` 月表，
   登记了就会让 `_fallback_candidate()` 去探测不存在的表（多两次注定失败的远端往返），
   或者被 tier-2 重写表名后**静默变成空图**；
3. SQL 必须过本地只读守卫（`_guard`：SELECT/WITH 开头、单语句、无分号）；
4. 值必须带数值守卫（GLOB 过滤空串/非数值），否则「没有数」会被写成 0。

不联网、不 import 业务运行环境之外的东西：`cn_kpi_service` 的模块级代码只做常量定义，
不建立连接（远端地址在函数调用时才读），所以这个用例在任何机器、CI 上都能跑。
"""
import re

import pytest

from backend.services.cn import cn_kpi_service as svc

# 2026-11-20 新增的四条：id → 期望定义
NEW_WIDGETS = {
    "c14": {
        "title": "Incoming Call Answer Ratio(%)",
        "pct": True,
        "table": "Incoming_Call_Answer_Ratio_OSS_EMS",
        "columns": ["(Incoming Call)Answer Ratio(%)"],
    },
    "c15": {
        "title": "Outgoing Call Answer Ratio(%)",
        "pct": True,
        "table": "Outgoing_Call_Answer_Ratio_OSS_EMS",
        "columns": ["(Outgoing Call)Answer Ratio(%)"],
    },
    "c16": {
        # 国际去话/来话 MOU：一张表两列 → 一张卡两条线；MOU 是时长，不是百分比
        "title": "International MOU",
        "pct": False,
        "table": "International_MOU_OSS_EMS",
        "columns": ["Outgoing MOU", "Incoming MOU"],
    },
    "c17": {
        # MGW32 资源利用率：小时表、三个百分比列，图上按天取平均
        "title": "MGW32 Resource Utilization(%)",
        "pct": True,
        "table": "CS_MGW32_Resource_Utilization_OSS_EMS",
        "columns": [
            "Utilization Ratio of TONE Resource(%)",
            "Utilization Ratio of TC Resource(%)",
            "Utilization Ratio of RTP Resource(%)",
        ],
    },
    "c18": {
        # MGW32 资源占用（信道数）：同一台 MGW 的同一次测量，按天取平均
        "title": "MGW32 Resource Usage(count)",
        "pct": False,
        "table": "CS_MGW32_Resource_Capacity_OSS_EMS",
        "columns": [
            "Average Number of TONE being Used",
            "Average Number of TC being Used",
            "Average Number of RTP being Used",
        ],
    },
}


def _widget(wid):
    for w in svc.WIDGETS:
        if w["id"] == wid:
            return w
    return None


def test_widget_ids_are_unique():
    """组件 id 是前端 key 与 Excel 表名，重复会让 React 报 duplicate key、导出串表。"""
    ids = [w["id"] for w in svc.WIDGETS]
    dup = {i for i in ids if ids.count(i) > 1}
    assert not dup, f"WIDGETS 里 id 重复：{sorted(dup)}"


@pytest.mark.parametrize("wid", sorted(NEW_WIDGETS))
def test_new_widget_definition(wid):
    """标题、表名、指标列、百分比标记都对得上（写错就是一张静默空图）。"""
    expect = NEW_WIDGETS[wid]
    w = _widget(wid)
    assert w is not None, f"WIDGETS 里没有 {wid}（2026-11-20 新增的组件被删掉了？）"
    assert w["title"] == expect["title"]
    assert w["chart"] == "line"
    assert bool(w.get("pct")) is expect["pct"], (
        f"{wid} 的 pct 标记不对：应答率/利用率是百分比（True），MOU 不是（False）"
    )
    assert f'"{expect["table"]}"' in w["sql"], (
        f"{wid} 的 SQL 没读 {expect['table']}（表名写错会静默变空图）"
    )
    for col in expect["columns"]:
        assert f'"{col}"' in w["sql"], f"{wid} 的 SQL 没引用指标项 {col}"


@pytest.mark.parametrize("wid", sorted(NEW_WIDGETS))
def test_new_widget_has_no_degraded_source(wid):
    """必须保持「单级取数」：不登记 source_table / fallback / monthly_table。

    这四张表没有 UME 孪生表、也没有 1month_* 月表。
    登记 source_table 会让 `_fallback_candidate()` 去探测 `*_UME`，
    再让 tier-2 把表名重写成不存在的表 → 页面只看到空图，日志里只有一堆 warning。
    """
    w = _widget(wid)
    assert not w.get("source_table"), f"{wid} 不该登记 source_table（没有可降级的源）"
    assert not w.get("fallback"), f"{wid} 不该登记 fallback（没有 _UME 孪生表）"
    assert not w.get("monthly_table"), f"{wid} 不该登记 monthly_table（没有 1month_* 月表）"
    assert not w.get("self_contained"), (
        f"{wid} 不需要 self_contained —— 那是给『SQL 自带两张源表 + 日历骨架』的 c1/c4 用的"
    )


@pytest.mark.parametrize("wid", sorted(NEW_WIDGETS))
def test_new_widget_sql_is_read_only_single_statement(wid):
    """过本地只读守卫：仅 SELECT/WITH、无分号（远端 db_writer_api 还有第二道）。"""
    sql = _widget(wid)["sql"]
    assert svc._guard(sql), "SQL 应原样返回（_guard 命中失败会抛错，不会走到这里）"
    assert "{{LO}}" in sql and "{{HI}}" in sql, "四张图都要跟页面的时间区间联动"


@pytest.mark.parametrize("wid", sorted(NEW_WIDGETS))
def test_new_widget_guards_non_numeric_values(wid):
    """空串 / '-' 这类非数值必须变 NULL（断线），不能被 CAST 成 0。"""
    sql = _widget(wid)["sql"]
    assert re.search(r"GLOB\s+'\*\[0-9\]\*'", sql), (
        f"{wid} 的 SQL 缺数值守卫：直接 CAST 空串会得到 0，"
        "把「没有数」画成「0%」或「0 分钟」（AGENTS.md 三.6）"
    )


def test_new_widgets_appended_after_existing_ones():
    """新组件追加在末尾，不打乱既有卡片位置（用户已形成的阅读顺序）。"""
    ids = [w["id"] for w in svc.WIDGETS]
    new_ids = ["c14", "c15", "c16", "c17", "c18"]
    assert ids.index("c14") > ids.index("c9"), "新组件应追加在原有 18 个之后"
    assert ids[-5:] == new_ids, f"新组件应连续排在末尾，实际末尾是 {ids[-5:]}"

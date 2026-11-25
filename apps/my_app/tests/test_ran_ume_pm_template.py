# -*- coding: utf-8 -*-
"""5G PM 查询 payload 的模板驱动构造（`ran_ume_pm_template`）回归测试。

## 背景（2026-11-25）

5G 的 payload 曾硬编码 9 个指标，UME 一律回 **HTTP 499 `0001619999`**
（0.0s 即时拒绝）。实测把 `items` 换成模板完整的 24 项就正常 —— 5G 模板的指标之间有
派生依赖，少一项整条查询就失败。所以改成「拉模板详情 → 原样用模板的 items /
position / position1 / motid / nfctid / indexfillingtype 构造」。

本测试锁死三件事：

1. **items 必须全量**（一条都不能漏，漏一条线上就是 499）；
2. `position` / `position1` 必须逐项取自模板（曾经套用 4G 的值导致 5G 取数失败）；
3. `relative` 用 -7（用 -1 时 UME 只回窗口第一个时间片，看起来像"没有历史"）；
4. 小区级三层分组 + `basemotid` 缺一不可：只写 `grouplayer=me` 会静默退回网元级；
5. `showbusinesscolumn` 必须 True，否则 UME 不返回 `masterOperatorId` 等小区维列，
   小区映射只能退化成 `NRPhysicalCellDU-1/2/3`。

运行：
    cd D:/02_olbr_portal/apps/my_app
    python -m pytest tests/test_ran_ume_pm_template.py -q
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.ran.ran_ume_pm_template import build_from  # noqa: E402

START = datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)
END = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)

# 最小但形状真实的模板（真实模板 24 项，position1 全为 0）
TPL = {
    "templatename": "5G_NW_KPI_Monitor_NOC_Platform",
    "motid": "NRCellCU,NRCellDU,NRPhysicalCellDU",
    "nfctid": "ITBBU",
    "indexfillingtype": "zeroDenominator",
    "containvcell": 0,
    "showbusinesscolumn": False,
    "items": [
        {"id": "1656651157492", "position": 2, "position1": 0},
        {"id": "1656651157491", "position": 2, "position1": 0},
        {"id": "1702277044516", "position": 0, "position1": 0},
    ],
}


def test_items_are_passed_through_completely():
    p = build_from(TPL, "5G", START, END, "900")
    assert p["items"] == ["1656651157492", "1656651157491", "1702277044516"]
    assert len(p["items"]) == len(TPL["items"])


def test_positions_follow_template_not_hardcoded():
    p = build_from(TPL, "5G", START, END, "900")
    assert p["itemsposition"] == [2, 2, 0]
    assert p["itemsposition1"] == [0, 0, 0]


def test_template_identity_fields():
    p = build_from(TPL, "5G", START, END, "900")
    assert p["templatename"] == "5G_NW_KPI_Monitor_NOC_Platform"
    assert p["templateid"] == "1638004472759"
    assert p["motid"] == "NRCellCU,NRCellDU,NRPhysicalCellDU"
    assert p["nfctid"] == "ITBBU"
    assert p["indexfillingtype"] == "zeroDenominator"


def test_relative_defaults_to_minus_seven():
    """relative=-7：用 -1 时 UME 只返回窗口里的第一个时间片（2026-11-25 实测）。"""
    p = build_from(TPL, "5G", START, END, "900")
    assert p["timeinfo"]["relative"] == -7
    assert p["timeinfo"]["start"] == "2026-11-25T00:00:00.000Z"
    assert p["timeinfo"]["end"] == "2026-11-25T12:00:00.000Z"


def test_defaults_to_cell_level_grouping():
    """默认是全网 + 小区级三层分组，单网元过滤由 apply_ne_filter 叠加。

    `grouplayer` 与 `basemotid` 必须同时给：只写 grouplayer=me 时 UME 静默按网元级
    返回（同站 288 行 → 3351 行），不报错，极易被当成"5G 只能到网元级"。
    """
    p = build_from(TPL, "5G", START, END, "900")
    assert p["filterlayer"] == "all"
    assert p["uilocations"] == []
    assert p["grouplayer"] == "me,NRRadioInfrastructure,NRPhysicalCellDU"
    assert p["basemotid"] == "NRPhysicalCellDU"


def test_business_columns_forced_on():
    """业务列必须开：关掉后 UME 不返回 masterOperatorId 等小区维列。"""
    p = build_from(TPL, "5G", START, END, "900")
    assert p["showbusinesscolumn"] is True

    # 模板里写 False 也不能服从 —— 这是 UME 的坑，不是可配置项
    tpl_off = dict(TPL, showbusinesscolumn=False)
    assert build_from(tpl_off, "5G", START, END, "900")["showbusinesscolumn"] is True


def test_cell_level_can_be_disabled():
    """显式 cell_level=False 时退回网元级（仅供对照/排障，正常路径不用）。"""
    p = build_from(TPL, "5G", START, END, "900", cell_level=False)
    assert p["grouplayer"] == "me"
    assert "basemotid" not in p


def test_empty_items_is_rejected():
    """空模板宁可报错，也不要发出一条注定 499 的查询。"""
    with pytest.raises(RuntimeError, match="没有 items"):
        build_from({"items": []}, "5G", START, END, "900")


def test_unknown_tech_has_no_template_id():
    p = build_from(TPL, "4G", START, END, "900")
    assert p["templateid"] == "", "未登记的制式不应凭空生成模板 id"

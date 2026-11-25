# -*- coding: utf-8 -*-
"""用 UME **模板详情**构造 5G 的 PM 历史查询 payload。

## 为什么 5G 不能像 4G 那样硬编码指标

4G 的 7 个指标写死在 `METRICS_4G` 里能正常工作，但 5G 不行：只挑 9 个指标发过去，
UME 直接回 **HTTP 499 `0001619999 An unknown error occurs`**（0.0s 即时拒绝，不是超时）。
同一时间窗内，把 `items` 换成模板完整的 24 项就正常返回 —— 5G 模板的指标之间存在
派生依赖，少给一个就整条查询失败。2026-11-25 实测：

| payload | 结果 |
| --- | --- |
| 硬编码 9 项 + `grouplayer=me` | 499 即时拒绝 |
| 硬编码 9 项 + 各种字段微调 | 全部 499 |
| 模板 24 项 + `grouplayer=me` | 200，3 小时 12 行（单网元） |

所以 5G 改成「先 `GET /api/pm-model/v1/templates/detail?id=<id>` 拿模板，再用模板的
items / position / position1 / motid / nfctid / indexfillingtype 原样构造」。副作用是好的：
模板在 UME 侧被改后，下一次查询自动跟随，不会出现「代码里的指标 id 与模板对不上」。

本模块只做**纯构造**（不发起 HTTP），拉取与缓存放在调用方，方便单测。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

# 5G 模板（id 来自 UME 的模板清单，与 ran_kpi_15mins_query.TEMPLATES 保持一致）
TEMPLATE_IDS: Dict[str, str] = {"5G": "1638004472759"}


def _iso_z(dt: datetime) -> str:
    """UME 要的 ISO 时间串（毫秒 + Z），与 4G 路径同一格式。"""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_from(tpl: Dict[str, Any], tech: str, start: datetime, end: datetime,
               gran: str, relative: int = -7, cell_level: bool = True) -> Dict[str, Any]:
    """按模板详情构造 5G payload。

    `relative=-7`：实测 -1 时 UME 只回窗口第一个时间片（一天只有 1 点），-7 才齐全。
    2026-11-25 早期误用 -1，一度以为"5G 无历史数据"。

    `cell_level=True`（默认）按 UME 页面抓包下沉到小区级：
    `grouplayer=me,NRRadioInfrastructure,NRPhysicalCellDU` + `basemotid=NRPhysicalCellDU`。
    只写 `grouplayer=me` 会退回网元级（每时间片 1 行，没有小区维度），而只下沉到
    `NRCellDU` 又只回 11 个时间片 —— 这两条都是实测踩过的坑。

    `filterlayer` 默认是「全网」，调用方若要走单网元再套
    `ran_ume_ne_filter.apply_ne_filter` —— 注意只改 `filterlayer` 不给
    `uilocations` 会被 UME 拒（4G/5G 都是）。
    """
    items = tpl.get("items") or []
    if not items:
        raise RuntimeError(f"{tech} 模板详情没有 items，无法构造查询")

    payload: Dict[str, Any] = {
        "arraycounterinfos": [], "datatype": 0, "displaymode": 0,
        "favoriteconditionname": tpl.get("templatename") or "",
        "gr": gran, "groupattributetype": 0, "groupbygroup": False,
        "groupbyplmn": False, "grouptype": 0,
        # 模板的完整指标清单，一项都不能少（少一项整条查询 499）
        "items": [it["id"] for it in items],
        "itemsposition": [it.get("position", 0) for it in items],
        "itemsposition1": [it.get("position1", 0) for it in items],
        "motid": tpl.get("motid"), "nfctid": tpl.get("nfctid"),
        "plmnpdfdisplaymode": False, "queryallbusiness": False,
        # 必须 True：`masterOperatorId` / `nrCarrierGroupId` / `nrPhysicalCellDUId` 这些
        # 小区维列归在「业务列」里，关掉后 UME 直接不返回 → 5G 小区映射拿不到 cell_id，
        # 只能退化成 NRPhysicalCellDU-1/2/3（2026-11-25 实测踩坑）。模板自身的取值不可信。
        "showbusinesscolumn": True,
        "showintegrity": False, "straightquery": False,
        "templatename": tpl.get("templatename"),
        "templateid": str(TEMPLATE_IDS.get(tech) or ""),
        "versiondatacompare": False,
        "needdeployfilter": False,
        "timeinfo": {
            "mode": 1, "start": _iso_z(start), "end": _iso_z(end), "relative": relative,
            "timezonetype": "1", "clienttimezone": "Atlantic/Reykjavik",
            "showtimezoneoffset": False,
            "querylongtime": {"type": 3, "value": 1},
            "timespanfilter": "00:00:00-24:00:00",
            "dayfiltertype": 0, "dayinmonthfilter": "1" * 31,
        },
        "indexfillingtype": tpl.get("indexfillingtype") or "zeroDenominator",
        "thresholdinfos": [],
        "templatetype": 0,
        "filterlayer": "all",
        "grouplayer": ("me,NRRadioInfrastructure,NRPhysicalCellDU"
                       if cell_level else "me"),
        "containvcell": int(tpl.get("containvcell") or 0),
        "resfilterattribute": "", "uilocations": [], "reslocation": [],
        "enumattrfilter": [], "inexistenceres": False, "firstDayOfWeek": 7,
        "iswireless": True, "hidehistorystopbutton": True,
        "opencustomizehistoryexport": False, "opentemplatecustomcolumn": False,
        "invaliditems": [],
    }
    if cell_level:
        # 少了 basemotid 只是"分组层级不生效"，UME 会悄悄按网元级返回，不报错
        payload["basemotid"] = "NRPhysicalCellDU"
    if tpl.get("businessmotid"):
        payload["businessmotid"] = tpl["businessmotid"]
        payload["groupbusinessids"] = []
        payload["businessfilterscope"] = []
    if tpl.get("itempositionbusiness"):
        payload["itempositionbusiness"] = tpl["itempositionbusiness"]
    return payload

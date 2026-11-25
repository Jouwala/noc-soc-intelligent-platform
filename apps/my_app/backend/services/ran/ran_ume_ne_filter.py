# -*- coding: utf-8 -*-
"""按网元（UILocation）在 UME 服务端过滤 PM 历史查询。

## 为什么需要

`POST /api/pm-analysis/v2/history` 默认返回**全网**数据。4G 小区级 15 分钟粒度
一天就是 66 万行 / 约 165MB，按站点查一次要 55s 且白读 99.8% 的数据。

UME 支持服务端按网元过滤，但只有**这一个组合**生效：

```json
{"filterlayer": "me",
 "uilocations": [{"haslogicmoc": false, "id": "<网元 GUID>",
                  "moc": "ITBBU", "model": "ranme"}]}
```

实测（2026-11-25，`5300` 单站单日 15 分钟粒度，同一时间切片）：

| 模式 | totalcount | 就绪 | 全量取完 |
| --- | --- | --- | --- |
| `filterlayer=all` + `uilocations=[]` | 662,661 | 36.2s | 54.7s |
| `filterlayer=me` + 上述 uilocations | 1,152 | 3.8s | 3.9s |

只改 `filterlayer`（如 `all` / `group`）而不带 `uilocations`，或者带 `uilocations`
却仍用 `filterlayer=all`，都会被 UME **静默忽略**并回全网数据 —— 2026-11-24 曾据此
误判「UME 不支持按站点过滤」，实际是组合没凑对。

## GUID 从哪来

`GET /api/res/v1/ranmes?queryDn=model:subnetwork` 返回的 `id` 就是这里的 GUID，
已由每日 23:00 的同步任务落到 `app.ran_element_management`：

| 列 | 含义 | 与工参的关系 |
| --- | --- | --- |
| `ne_id` | 网元 GUID，即 `uilocations[].id` | — |
| `nbi_id` | 网元编号 | **4G 多数站点等于工参 `enodeb_id`**，但并非全部（见下） |
| `name` | 网元名 | 4G 兜底键 / 5G 主键，可比对 `enodeb_name` / `gnb_name` |
| `moc` | 网元类型 | 4G 为 `ITBBU`（还有 SDR/MRNC/MEC，属 2G/3G） |

## 4G 的两级映射：`nbi_id` 优先，站名兜底（2026-11-25 补）

`nbi_id == enodeb_id` 只覆盖 942 个 4G 工参站点里的 **806 个**。剩下 136 个站点的
编号在 UME 侧是另一套：

| | 工参 | 网元表 | 结果 |
| --- | --- | --- | --- |
| `16th Street` | `enodeb_id=4245`（12 个小区） | `nbi_id=5245`（唯一同名 ITBBU） | 按站名兜底命中，服务端过滤有效：8 天 **8880 行 / 8.7s**，回的正是 4245 的 12 个小区 |
| 没有网元记录 | 64 个站点 | — | 回退全网（本地过滤），功能正常但慢 |

兜底规则刻意收紧成 **「站名忽略大小写唯一命中 **且** `moc='ITBBU'`」**：

* 不唯一（同名 ITBBU 多条）就返回 None，不猜；
* `SDR` / `MRNC` / `MEC` 是 2G/3G 网元，用它们过滤 4G 会拿不到数据，所以 SQL 里直接排除。

> 2026-11-25 教训：只按 `nbi_id` 匹配时，`16th Street(4245)` 每次查询都退回全网，
> 8 天窗口 **270s**（用户体感「原来 8 秒，现在被弄坏了」）。同一站点用站名兜底后
> 8.7s —— 慢的根因是映射缺失，不是取数链路变慢。

## 5G 的映射方式（2026-11-25 启用）

5G 同样支持 `filterlayer=me` + `uilocations`，实测 3 小时窗口从 419 行（全网）降到
12 行（单网元）。但网元映射不能照抄 4G：

| | 4G | 5G |
| --- | --- | --- |
| 匹配键 | `nbi_id == enodeb_id` 优先，站名 + `ITBBU` 兜底 | **网元名精确匹配**（编号不同体系） |
| 编号实例 | 工参 `5300` → 网元 `5300` | 工参 `6004` → 网元 `4004`，且 6010 → 5010，差值不成规律 |
| 覆盖 | 942 个站点：806 直接命中 + 52 站名兜底 | 34 个 gNB 中 26 个唯一命中 |

重名的站点（`Orange` 同时命中 `Orange` / `Orange Tower` / `Qcell Orange`）一律不猜，
返回 None 走全网回退 —— 5G 全网数据量小（约 400 行/3 小时），猜错比慢更糟。

**另一处 5G 专属问题**：payload 必须用模板的**完整 24 项指标**，硬编码 9 项会被 UME
499 即时拒绝。构造逻辑在 `ran_ume_pm_template.py`，与本模块无关但常被一起排查。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from backend.database import get_raw_connection

logger = logging.getLogger("ran_site_kpi_rt")

NE_TABLE = "app.ran_element_management"
NE_MODEL = "ranme"   # 无线接入网网元，对应抓包里 uilocations[].model


def resolve_ne(tech: str, site_id: str, site_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """按工参站点找 UME 网元记录，找不到（或不能唯一确定）返回 None，调用方回退全网。

    * **4G**：先按 `nbi_id == enodeb_id` 精确匹配；匹配不到时按**站名**兜底
      （忽略大小写、唯一命中、且 `moc='ITBBU'`）。两级都拿不到就返回 None 走全网。
    * **5G**：`nbi_id` 和工参 `gnb_id` **不是同一套编号**（工参 `6004` 对应网元 `4004`，
      且差值不成规律：6041→4041 但 6010→5010），只能按网元名精确匹配。实测 34 个 gNB
      里 26 个能唯一命中；重名的（如 `Orange` 同时命中 `Orange` / `Orange Tower` /
      `Qcell Orange`）一律不猜，返回 None 走全网回退。
    """
    if tech not in ("4G", "5G"):
        return None

    if tech == "5G":
        name = str(site_name or "").strip()
        if not name:
            return None
        try:
            with get_raw_connection() as conn:
                # 忽略大小写：UME 网元写 'Liscr'，工参写 'LISCR'；LOWER 后仍多条才算重名
                cur = conn.execute(
                    f"SELECT ne_id, nbi_id, name, moc FROM {NE_TABLE} "
                    f"WHERE LOWER(name) = LOWER(?)",
                    [name],
                )
                rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取网元表失败 gnb_name=%s：%s", name, exc)
            return None
        if len(rows) != 1:
            logger.info("5G 站点 %r 在网元表命中 %d 条，不做猜测，回退全网查询", name, len(rows))
            return None
        ne = rows[0]
        if not ne.get("ne_id") or not ne.get("moc"):
            logger.warning("网元记录缺 ne_id/moc（%s），回退全网查询", ne)
            return None
        return ne

    sid = str(site_id or "").strip()
    if not sid:
        return None
    name = str(site_name or "").strip()
    used_name = False
    try:
        with get_raw_connection() as conn:
            cur = conn.execute(
                f"SELECT ne_id, nbi_id, name, moc FROM {NE_TABLE} "
                f"WHERE nbi_id = ? LIMIT 2",
                [sid],
            )
            rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
            if not rows and name:
                # 兜底：**工参 `enodeb_id` 与 UME `nbi_id` 并非在所有站点上都相等**。
                # 实测 2026-11-25：`16th Street` 工参是 4245，网元表里只有 nbi_id=5245
                # 的 ITBBU；而用该网元做服务端过滤，回的正是 4245 的 12 个小区
                # （8 天 8880 行 / 8.7s）。此前只按 nbi_id 匹配 → 这类站点永远找不到
                # 网元 → 每次查询都退回「拉全网 + 本地过滤」，8 天要 270s。
                # 只接受「站名忽略大小写唯一命中且 moc=ITBBU」：ITBBU 才是 4G LTE BBU，
                # 同名 SDR/MRNC/MEC 是 2G/3G 网元，用它过滤 4G 会拿不到数据。
                used_name = True
                cur = conn.execute(
                    f"SELECT ne_id, nbi_id, name, moc FROM {NE_TABLE} "
                    f"WHERE LOWER(name) = LOWER(?) AND moc = 'ITBBU' LIMIT 2",
                    [name],
                )
                rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001 —— 网元表缺失/未同步不应让整个查询失败
        logger.warning("读取网元表失败 site=%s：%s", sid, exc)
        return None

    if not rows:
        # 网元表每日同步一次，新开站或同步滞后时会查不到 → 回退全网，功能不因此不可用
        logger.info("网元表无 nbi_id=%s / 唯一同名 ITBBU 的记录，回退全网查询", sid)
        return None
    if len(rows) > 1:
        if used_name:
            logger.warning("站点 %s 按名字命中多个 ITBBU 网元 %s，不做猜测，回退全网",
                           name, [r["ne_id"] for r in rows])
            return None
        logger.warning("nbi_id=%s 命中多个网元 %s，取第一个", sid,
                       [r["ne_id"] for r in rows])
    ne = rows[0]
    if used_name:
        logger.info("站点 %s 工参 enodeb_id=%s 与网元 nbi_id=%s 不同，按站名唯一命中后启用服务端过滤",
                    name, sid, ne.get("nbi_id"))
    if not ne.get("ne_id") or not ne.get("moc"):
        logger.warning("网元记录缺 ne_id/moc（%s），回退全网查询", ne)
        return None
    return ne


def apply_ne_filter(payload: Dict[str, Any], ne: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把 payload 从「全网」改成「单网元」。ne 为空时原样返回。

    就地修改并返回同一个 dict，便于调用方链式使用。
    """
    if not ne:
        return payload
    payload["filterlayer"] = "me"
    payload["uilocations"] = [{
        "haslogicmoc": False,
        "id": str(ne["ne_id"]),
        "moc": str(ne["moc"]),
        "model": NE_MODEL,
    }]
    return payload

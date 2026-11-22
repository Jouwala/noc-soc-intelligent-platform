# -*- coding: utf-8 -*-
"""GIS 站点查询的列名容错（2026-11-22 master_site_db 重建事故后的回归守卫）。

事故：`master_site_db` 被 CMDB 源表整表重建后 `site_type` 列消失，而
`/sites`、`/realtime/sites` 把它写死在 SELECT 里 → PG 报 UndefinedColumn，
两个端点整条 500，GIS 地图与实时告警地图全空。同一轮重建还把源表头
Supervisor 改名成 New Supervisor（列名 `new_supervisor`）。

这里锁定两条不变量：
1. `site_type` 缺列时 SELECT 退化为 NULL，而不是继续引用不存在的列；
2. 筛选列的候选列名按存在性解析，supervisor 能落到 new_supervisor。
"""
from __future__ import annotations

from backend.routers.gis import _resolve_filter_cols, _site_type_sel


def test_site_type_expr_uses_column_when_present():
    assert _site_type_sel({"site_cols": {"site_type"}}) == "site_type"


def test_site_type_expr_falls_back_to_null_when_column_missing():
    # 不允许返回 "site_type"：那会让整条查询 500。
    assert _site_type_sel({"site_cols": {"site_id", "site_name"}}) == "NULL AS site_type"


def test_supervisor_falls_back_to_new_supervisor():
    cols = {"site_id", "site_name", "new_supervisor"}
    resolved = {p: c for p, c, _ in _resolve_filter_cols(cols)}
    assert resolved.get("supervisor") == "new_supervisor"


def test_supervisor_prefers_legacy_column_when_both_exist():
    cols = {"site_id", "site_name", "supervisor", "new_supervisor"}
    resolved = {p: c for p, c, _ in _resolve_filter_cols(cols)}
    assert resolved.get("supervisor") == "supervisor"


def test_missing_filter_columns_are_skipped_not_emitted():
    cols = {"site_id", "site_name", "region", "network_type"}
    resolved = {p for p, _, _ in _resolve_filter_cols(cols)}
    # 存在的列照常下发；不存在的列不能出现在 SELECT/WHERE 中
    assert "network_type" in resolved
    assert "site_type" not in resolved
    assert "power_sources" not in resolved
    assert "supervisor" not in resolved

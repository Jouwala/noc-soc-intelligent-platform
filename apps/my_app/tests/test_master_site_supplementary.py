# -*- coding: utf-8 -*-
"""refresh_master_site 的补充列抢救逻辑（2026-11-22 事故后加固的单测）。

事故：`if_exists="replace"` 整表重建会把源文件里没有的补充列洗掉
（starlink_recently_migrated 曾整列丢失，NUR 站点维度查询报「字段不存在」）。
加固后有两条不变量：

1. 读旧表失败**不能**当作「首次导入」放行 —— 否则仍会 replace 并把补充列洗掉；
   只有「表不存在」这一种情况允许继续。
2. 补充列并入待写 DataFrame 一起 replace（一次写入），不再「先 replace 再回填」，
   避免回填失败留下「表已重建、列缺失」的中间态。
"""
from __future__ import annotations

import pandas as pd

from backend.scripts.refresh_master_site import (
    SUPPLEMENTARY_COLUMNS,
    _is_missing_table,
    apply_supplementary,
)


def test_missing_table_only_matches_first_import_errors():
    assert _is_missing_table(Exception('relation "master_site_db" does not exist'))
    assert _is_missing_table(Exception("no such table: master_site_db"))
    assert _is_missing_table(Exception("UndefinedTable: relation ..."))
    # 连接失败 / 超时 / 类型错误都不能被当成首次导入放行
    assert not _is_missing_table(Exception("connection timed out"))
    assert not _is_missing_table(Exception("could not connect to server"))
    assert not _is_missing_table(Exception("permission denied for table master_site_db"))


def test_apply_supplementary_keeps_existing_values_and_fills_default():
    df = pd.DataFrame({"site_id": [1, 2, 3]})
    kept = {"starlink_recently_migrated": pd.Series({"1": "Yes", "2": "No"})}

    out = apply_supplementary(df, kept)

    assert list(out["starlink_recently_migrated"]) == ["Yes", "No", "No"]


def test_apply_supplementary_always_keeps_columns_when_old_table_missing():
    """旧表不存在（首次导入）时也要带上补充列，避免随后 NUR 查询字段不存在。"""
    df = pd.DataFrame({"site_id": [10]})

    out = apply_supplementary(df, {})

    for col in SUPPLEMENTARY_COLUMNS:
        assert col in out.columns
    assert out["starlink_recently_migrated"].iloc[0] == "No"


def test_apply_supplementary_does_not_mutate_other_columns():
    df = pd.DataFrame({"site_id": [1], "site_name": ["S1"]})
    out = apply_supplementary(df, {})
    assert out["site_name"].iloc[0] == "S1"

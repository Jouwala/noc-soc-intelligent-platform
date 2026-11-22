#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 app.eflow_tt 提取非重复的 alarm_code / alarm_description。

输出：XLSX（两个 sheet：alarm_code_distinct 按 code 聚合视图、alarm_code_raw 原始 code+description 行）。

为什么要单独导出这一份：
    whatsapp-kb-bot 的 `knowledge/TT单-告警码字典.md` 由本文件产出的 xlsx 导出
    （见 apps/whatsapp-kb-bot/scripts/export-tt-knowledge.py 的 export_alarm_codes）。
    该 xlsx 同时被 whatsapp-kb-bot/src/myAppPaths.js 的 RULE_SOURCE_FILES 列为
    「规则源」之一 —— 文件不存在时 sync-kb.mjs 会直接判定「规则源文件缺失」并退出，
    三个 whatsapp_kb_sync_* 定时任务全部失败（2026-11-22 踩过：文件被误删后
    知识库同步链路静默停摆一整夜）。

    所以本脚本必须能随时重跑，且幂等（同样的库数据 → 同样的内容）。

用法：
    python data/export_eflow_tt_alarm_codes.py
"""
from __future__ import annotations

import os
import sys

import pandas as pd
from sqlalchemy import text

HERE = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.dirname(HERE)
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from backend.database import engine  # noqa: E402

OUT_XLSX = os.path.join(HERE, "eflow_tt_alarm_code_distinct.xlsx")

# 按告警码聚合：规范化 code（去掉 Excel 带出来的 .0 尾巴），同一 code 取最常见的描述
SQL_DISTINCT = """
WITH norm AS (
    SELECT
        CASE
            WHEN BTRIM(COALESCE(alarm_code, '')) ~ '^[0-9]+\\.0$'
                THEN regexp_replace(BTRIM(alarm_code), '\\.0$', '')
            WHEN COALESCE(BTRIM(alarm_code), '') = '' THEN '(空)'
            ELSE BTRIM(alarm_code)
        END                                              AS alarm_code_norm,
        NULLIF(BTRIM(alarm_description), '')             AS alarm_description,
        created_time
    FROM app.eflow_tt
)
SELECT
    alarm_code_norm                                                      AS alarm_code,
    count(*)                                                             AS tt_count,
    count(DISTINCT alarm_description)                                    AS description_variants,
    COALESCE(mode() WITHIN GROUP (ORDER BY alarm_description),
             '(无描述)')                                                 AS alarm_description,
    min(created_time)                                                    AS first_seen,
    max(created_time)                                                    AS last_seen
FROM norm
GROUP BY 1
ORDER BY tt_count DESC, alarm_code
"""

# 非数字告警码（派单规则里按名字匹配的那些）保留原始行，便于人工核对
SQL_RAW = """
SELECT tt_no, alarm_code, alarm_description
FROM app.eflow_tt
WHERE COALESCE(BTRIM(alarm_code), '') <> ''
  AND BTRIM(alarm_code) !~ '^[0-9]+(\\.0)?$'
ORDER BY alarm_code
"""


def main() -> int:
    with engine.connect() as conn:
        df_distinct = pd.read_sql_query(text(SQL_DISTINCT), conn)
        df_raw = pd.read_sql_query(text(SQL_RAW), conn)

    if df_distinct.empty:
        print("[FATAL] alarm_code_distinct 为空，拒绝覆盖（先确认 app.eflow_tt 有数据）")
        return 1

    for col in ("first_seen", "last_seen"):
        if col in df_distinct.columns:
            df_distinct[col] = pd.to_datetime(df_distinct[col]).dt.strftime("%Y-%m-%d %H:%M:%S")

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
        df_distinct.to_excel(writer, sheet_name="alarm_code_distinct", index=False)
        df_raw.to_excel(writer, sheet_name="alarm_code_raw", index=False)

    print(f"输出 {OUT_XLSX}")
    print(f"  alarm_code_distinct: {len(df_distinct)} 行")
    print(f"  alarm_code_raw:      {len(df_raw)} 行")
    if len(df_distinct):
        top = df_distinct.iloc[0]
        print(f"  最多: {top['alarm_code']} / {top['alarm_description']} / {top['tt_count']} 单")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""SQLite → PG 看板 SQL 改写的纯函数守卫（不连库、零第三方依赖）。

覆盖 2026-11-22 接入本机 PG 读通道时踩到的五类方言差异：
  1. `GLOB '*[0-9]*'` → PG 正则；
  2. `CAST(... AS REAL)` → DOUBLE PRECISION（REAL 是 4 字节，会丢精度）；
  3. `HAVING t >= ...` 用 SELECT 别名 —— PG 的 HAVING 不认别名；
  4. `WHERE t >= ...` 用 SELECT 别名 —— PG 的 WHERE 同样不认；
  5. `FROM (子查询)` 省略别名 —— PG 必须写；
  6. `date('now')` 必须限定 schema（pg_catalog 内建 date 会抢先命中）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.db.pg_query import (  # noqa: E402
    _fix_identifier_case,
    to_pg_sql,
)


def test_glob_to_regex():
    sql = ("SELECT 1 FROM t WHERE CASE WHEN TRIM(\"x\") GLOB '*[0-9]*' "
           "THEN CAST(TRIM(\"x\") AS REAL) END")
    out = to_pg_sql(sql)
    assert "GLOB" not in out.upper()
    assert "~ '[0-9]'" in out
    assert "DOUBLE PRECISION" in out


def test_having_alias_is_moved_out():
    sql = ("SELECT substr(\"End Time\",1,10) AS t, AVG(v) AS x FROM t "
           "GROUP BY t HAVING t >= date('now','-31 day') ORDER BY t")
    out = to_pg_sql(sql, "cn_kpi")
    assert " HAVING " not in out.upper()
    assert "AS _pg_having" in out
    assert "WHERE t >= cn_kpi.date('now','-31 day')" in out
    assert "ORDER BY t" in out


def test_where_alias_is_moved_out():
    sql = ("WITH sk AS (SELECT 1 AS d) "
           "SELECT sk.d AS t, 1 AS v FROM sk "
           "WHERE t >= date('now','-31 day') ORDER BY t")
    out = to_pg_sql(sql, "cn_kpi")
    assert "AS _pg_w" in out
    assert "WHERE t >=" in out


def test_subquery_gets_alias():
    out = to_pg_sql("SELECT * FROM (SELECT 1 AS t) GROUP BY t")
    assert "AS _pg_s1" in out


def test_date_is_schema_qualified():
    out = to_pg_sql("SELECT date('now'), date(d,'+1 day') FROM t", "ran_kpi")
    assert "ran_kpi.date('now')" in out
    assert "ran_kpi.date(d,'+1 day')" in out


def test_sum_bare_text_column_gets_safe_num():
    out = to_pg_sql('SELECT SUM("Col A") FROM t')
    assert 'SUM(safe_num("Col A"))' in out


def test_case_insensitive_identifier():
    cols = {"ne": "NE", "begin time": "Begin Time"}
    out = _fix_identifier_case('SELECT NE FROM t WHERE "begin time" > 1', cols)
    assert '"NE"' in out
    assert '"Begin Time"' in out


def test_exact_identifier_wins_over_lower_map():
    """同 schema 里 "SITE Name"（2G）与 "Site Name"（Eflow）并存，精确命中优先。"""
    cols = {"site name": "Site Name"}
    out = _fix_identifier_case('SELECT "SITE Name" FROM t', cols, {"SITE Name", "Site Name"})
    assert '"SITE Name"' in out


def test_max_time_column_is_not_safe_num():
    out = to_pg_sql('SELECT MAX("Update Time") FROM t')
    assert 'safe_num' not in out
    out2 = to_pg_sql('SELECT MAX("306004:TCH in service rate(%)") FROM t')
    assert 'safe_num' in out2


def test_text_numeric_comparison_and_multiplication():
    out = to_pg_sql(
        'SELECT 1 FROM t WHERE "306004:TCH in service rate(%)">=0.985 '
        'AND "306004:TCH in service rate(%)"*100 > 1')
    assert 'safe_num("306004:TCH in service rate(%)") >= 0.985' in out
    assert 'safe_num("306004:TCH in service rate(%)") * 100' in out

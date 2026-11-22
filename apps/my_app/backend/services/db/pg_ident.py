# -*- coding: utf-8 -*-
"""PG 标识符大小写适配（从 `pg_query.py` 拆出，控制单文件体积）。

SQLite 的标识符大小写不敏感、且允许省略子查询别名；PG 的带引号标识符严格
区分大小写，裸标识符一律折小写。原生看板 / CN 看板里两种写法混用，这里按
**SQL 实际引用的表**收集列名，引号标识符先精确命中、再按 lower 映射。
"""
from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Tuple


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


# 裸标识符不参与大小写修正的词（SQL 关键字 / 看板 SQL 里的固定别名）
_NOT_COLUMNS = {
    "select", "from", "where", "group", "by", "order", "having", "limit",
    "offset", "union", "all", "as", "and", "or", "not", "null", "is", "in",
    "case", "when", "then", "else", "end", "on", "left", "right", "inner",
    "outer", "join", "with", "recursive", "distinct", "asc", "desc", "between",
    "like", "glob", "cast", "real", "integer", "text", "numeric", "true",
    "false", "t", "v", "day", "series", "value", "time", "sum", "avg", "min",
    "max", "count", "round", "substr", "trim", "printf", "strftime", "date",
    "safe_num", "coalesce", "ifnull", "replace", "length", "lower", "upper",
    "datetime", "strftime", "printf",
}

_IDENT_CACHE: Dict[tuple, tuple] = {}


def _schema_tables(conn, schema: str) -> List[str]:
    key = (schema, "__tables__")
    now = time.time()
    hit = _IDENT_CACHE.get(key)
    if hit and now - hit[0] < 600:
        return hit[1]
    cur = conn.cursor()
    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema=%s",
        (schema,))
    names = [str(r[0]) for r in cur.fetchall()]
    cur.close()
    _IDENT_CACHE[key] = (now, names)
    return names


def _table_columns(conn, schema: str, table: str) -> List[str]:
    key = (schema, table)
    now = time.time()
    hit = _IDENT_CACHE.get(key)
    if hit and now - hit[0] < 600:
        return hit[1]
    cur = conn.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
        (schema, table))
    cols = [str(r[0]) for r in cur.fetchall()]
    cur.close()
    _IDENT_CACHE[key] = (now, cols)
    return cols


def _tables_in_sql(sql: str, known: List[str]) -> List[str]:
    refs = {m.lower() for m in re.findall(
        r'\b(?:FROM|JOIN)\s+"?([A-Za-z_][A-Za-z0-9_]*)"?', sql, re.I)}
    return [t for t in known if t.lower() in refs]


def _columns_for_sql(conn, schema: str, sql: str) -> Tuple[Dict[str, str], set]:
    """按 SQL 引用的表收集列名，返回 ({lower: 唯一真实名}, {真实名集合})。

    不能把整个 schema 的列混成一张表：`2G_Main_Kpi_Site_Daily` 是 `"SITE Name"`，
    `Eflow_TT_FB` 是 `"Site Name"`，全局 setdefault 会把前者改写成后者。
    """
    known = _schema_tables(conn, schema)
    used = _tables_in_sql(sql, known) or known
    exact: set = set()
    lower: Dict[str, Optional[str]] = {}
    for t in used:
        for c in _table_columns(conn, schema, t):
            exact.add(c)
            k = c.lower()
            if k in lower and lower[k] != c:
                lower[k] = None  # 同一 schema 内大小写不一致，交给调用方决定
            else:
                lower.setdefault(k, c)
    # 表名也加入（裸写 FROM Eflow_TT_FB 需要补引号）
    for t in used:
        lower.setdefault(t.lower(), t)
    return {k: v for k, v in lower.items() if v}, exact


def _fix_identifier_case(sql: str, columns: Dict[str, str],
                         exact: Optional[set] = None) -> str:
    """SQLite 标识符大小写不敏感，PG 的带引号标识符大小写敏感。

    引号标识符先看**精确命中**（同 schema 不同表可能有 `"SITE Name"` / `"Site Name"`
    两种写法）；没命中再按 lower 映射回该表真实列名。裸标识符一律补引号。
    """
    exact = exact or set(columns.values())
    out: List[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":  # 字符串字面量原样保留
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i:j + 1])
            i = j + 1
            continue
        if ch == '"':  # 带引号标识符
            j = sql.find('"', i + 1)
            if j < 0:
                out.append(sql[i:])
                break
            inner = sql[i + 1:j].replace('""', '"')
            from backend.dialect_helpers import pg_trunc_ident

            inner_t = pg_trunc_ident(inner)
            if inner_t in exact:
                out.append(_q(inner_t))
            else:
                real = columns.get(inner_t.lower())
                out.append(_q(real) if real else sql[i:j + 1])
            i = j + 1
            continue
        if ch.isalpha() or ch == "_":  # 裸标识符
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            word = sql[i:j]
            low = word.lower()
            real = columns.get(low)
            if real and low not in _NOT_COLUMNS:
                out.append(_q(real))
            else:
                out.append(word)
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)



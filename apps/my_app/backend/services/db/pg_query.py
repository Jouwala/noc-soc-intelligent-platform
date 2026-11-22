# -*- coding: utf-8 -*-
"""本机 PG 读通道：把原本走 .19 远端 SQLite 的看板 SQL 尽量原样跑在 PG 上。

配套 `pg_mirror.py`（把远端表增量镜像到 PG）。这一层只解决两件事：

1. **SQLite 方言 → PG**：CN/RAN 的看板 SQL 是照着 SQLite 写的（`date('now')`、
   `strftime`、`printf`、`GLOB`、`HAVING t >= ...` 用 SELECT 别名）。与其把
   几十条 SQL 各写一份 PG 版，不如在 PG 里装几个**同名兼容函数**，再做三处
   最小改写；SQL 本身仍是唯一真相源。
2. **PG 优先、远端回退**：PG 缺表 / 查询报错 / 返回 0 行时回落 `.19`，
   页面行为与改造前一致（AGENTS.md：不允许静默空白）。

开关：`PG_READ_ENABLED=0` 直接全部读远端（无需改代码重启即可回退）。
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from backend.services.db.pg_sql_compat import (  # noqa: F401 — 对外 re-export
    ensure_compat,
    ensure_schema_ready,
)

logger = logging.getLogger("pg_query")

# 远端库 → 本机 PG schema（与 pg_mirror.MIRROR_DBS 保持一致）
SCHEMA_BY_DB: Dict[str, str] = {
    "db_ps_cs_cn.sqlite3": "cn_kpi",
    "ran_kpi_daily.sqlite3": "ran_kpi",
}

READ_ENABLED = os.environ.get("PG_READ_ENABLED", "1").strip().lower() not in (
    "0", "false", "no", "off")

_QUERY_TIMEOUT_MS = int(os.environ.get("PG_READ_TIMEOUT_MS", "60000"))


class UnsupportedSql(RuntimeError):
    """该 SQL 还有未适配的 SQLite 方言，调用方应回退远端。"""


# ----------------------------------------------------------------------
# SQLite 方言改写
# ----------------------------------------------------------------------
_AggBare = re.compile(r"\b(SUM|AVG)\s*\(\s*\"([^\"]+)\"\s*\)", re.I)
_AggIdent = re.compile(r"\b(SUM|AVG)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", re.I)
_AggExpr = re.compile(r"\b(SUM|AVG)\s*\(([^()]*)\)", re.I)


def _wrap_agg_expr(m: "re.Match[str]") -> str:
    """SUM(a+b+c)：把表达式里的裸标识符逐个 safe_num（text 不能直接相加）。"""
    fn, inner = m.group(1).upper(), m.group(2)
    if "+" in inner:
        inner = re.sub(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\b",
            lambda x: f"safe_num({x.group(1)})", inner)
    return f"{fn}({inner})"
_CastReal = re.compile(r"\bAS\s+REAL\b", re.I)
_Gob = re.compile(
    r"(?P<lhs>(?:TRIM\s*\(\s*\"[^\"]+\"\s*\)|\([^()]*\)|\"[^\"]+\"|[\w.]+))"
    r"\s+GLOB\s+'\*\[0-9\]\*'", re.I)
_Having = re.compile(r"\bHAVING\b(?P<cond>.*?)(?:\bORDER\s+BY\b(?P<order>.*))?$",
                     re.I | re.S)

_ALIAS_STOP = {
    "where", "group", "having", "order", "limit", "union", "except", "intersect",
    "join", "left", "right", "inner", "outer", "cross", "on", "using", "as",
    "window", "offset", "fetch", "for",
}


def _find_paren(sql: str, start: int) -> int:
    """返回与 sql[start]=='(' 匹配的右括号下标；不匹配返回 -1。"""
    depth = 0
    in_s = False
    i = start
    while i < len(sql):
        ch = sql[i]
        if in_s:
            if ch == "'":
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    i += 2
                    continue
                in_s = False
        elif ch == "'":
            in_s = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _alias_subqueries(sql: str) -> str:
    """给 FROM (...) 子查询补别名（SQLite 允许省略，PG 必须写）。"""
    out = sql
    pos = 0
    counter = 0
    while True:
        m = re.search(r"\bFROM\s*\(", out[pos:], re.I)
        if not m:
            break
        paren = pos + m.end() - 1
        close = _find_paren(out, paren)
        if close < 0:
            break
        after = out[close + 1:]
        stripped = after.lstrip()
        token = re.match(r"([A-Za-z_][\w]*)?", stripped)
        nxt = (token.group(1) or "").lower() if token else ""
        # `AS x` / `"x"` 都算已有别名；不能再补一个
        has_alias = stripped.startswith('"') or nxt == "as" or (
            bool(nxt) and nxt not in _ALIAS_STOP)
        if not has_alias:
            counter += 1
            out = out[:close + 1] + f" AS _pg_s{counter}" + out[close + 1:]
            pos = close + len(f" AS _pg_s{counter}")
        else:
            pos = close + 1
    return out


def _rewrite_where_alias(sql: str) -> str:
    """把 `WHERE t >= ...`（t 是 SELECT 别名）挪到外层。

    SQLite 允许 WHERE 引用 SELECT 别名，PG 不允许（c2/c5 这类先聚合再按
    时间过滤的 SQL 会报「字段 t 不存在」）。只在该别名确实由 SELECT 定义、
    且 WHERE 条件只引用 t 时改写；其它查询原样返回。
    """
    if not re.search(r"\bAS\s+t\b", sql, re.I):
        return sql
    depth = 0
    in_s = False
    last_where = -1
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_s:
            if ch == "'":
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    i += 2
                    continue
                in_s = False
        elif ch == "'":
            in_s = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and sql[i:i + 5].upper() == "WHERE" and (
                i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] == "_")):
            last_where = i
        i += 1
    if last_where < 0:
        return sql
    rest = sql[last_where + 5:].strip()
    m = re.match(r"(?P<cond>.*?)(?:\bORDER\s+BY\b(?P<order>.*))?$", rest, re.I | re.S)
    if not m:
        return sql
    cond = m.group("cond").strip()
    order = (m.group("order") or "").strip()
    # 只处理「WHERE 就是最后一道按别名 t 的过滤」；带 GROUP BY / HAVING 的
    # 交给 _rewrite_having（否则会把整段 GROUP BY 塞进外层 WHERE）
    if re.search(r"\bGROUP\s+BY\b|\bHAVING\b", cond, re.I):
        return sql
    if not re.search(r"\bt\b", cond, re.I):
        return sql
    inner = sql[:last_where].rstrip()
    wrapped = f"SELECT * FROM ({inner}) AS _pg_w WHERE {cond}"
    if order:
        wrapped += f" ORDER BY {order}"
    return wrapped


def _rewrite_having(sql: str) -> str:
    """把 `... GROUP BY t HAVING t >= A ... ORDER BY t` 包一层。

    PG 的 HAVING 不认 SELECT 别名（SQLite 认），把 HAVING 条件挪到外层
    WHERE —— 这些条件全部作用在分组键 t 上，语义等价。
    """
    if not re.search(r"\bHAVING\b", sql, re.I):
        return sql
    m = _Having.search(sql)
    if not m:
        return sql
    inner = sql[:m.start()].rstrip()
    cond = m.group("cond").strip()
    order = (m.group("order") or "").strip()
    if not cond:
        return sql
    wrapped = f"SELECT * FROM ({inner}) AS _pg_having WHERE {cond}"
    if order:
        wrapped += f" ORDER BY {order}"
    return wrapped


def to_pg_sql(sql: str, schema: str = "") -> str:
    """SQLite → PG 最小改写；遇到未覆盖的方言抛 UnsupportedSql（⇒ 回退远端）。"""
    s = _CastReal.sub("AS DOUBLE PRECISION", sql)
    # date() 必须**限定 schema**：pg_catalog 永远排在 search_path 前面，
    # 不限定会命中内建 date(timestamp)（text 隐式转 timestamp），返回 date
    # 而不是我们要的 'YYYY-MM-DD' 文本，比较时直接报 text = date。
    if schema:
        s = re.sub(r"\bdate\s*\(", f"{schema}.date(", s, flags=re.I)
    # SUM(gsm+umts+eps)：算术表达式里的裸标识符是 text，PG 的 text+text 不存在
    s = _AggExpr.sub(_wrap_agg_expr, s)
    # text 数值列：SUM/AVG(裸列) → safe_num()，避免 PG 的 sum(text) 报错
    s = _AggBare.sub(lambda m: f"{m.group(1).upper()}(safe_num({_q(m.group(2))}))", s)
    # 别名也常是 text（子查询里 "列" AS v）：SUM(v) / AVG(v) 同样要过 safe_num。
    # safe_num 对 numeric / double / integer 有重载，命中数值别名时原样返回。
    s = _AggIdent.sub(lambda m: f"{m.group(1).upper()}(safe_num({_q(m.group(2))}))", s)
    # GLOB '*[0-9]*' → POSIX 正则（只这一种模式，别的留给回退）
    s = _Gob.sub(lambda m: f"({m.group('lhs')} ~ '[0-9]')", s)
    if re.search(r"\bGLOB\b", s, re.I):
        raise UnsupportedSql("存在未适配的 GLOB 模式")
    s = _rewrite_where_alias(s)
    s = _rewrite_having(s)
    s = _alias_subqueries(s)
    return s


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
}

_COLUMN_CACHE: Dict[str, Tuple[float, Dict[str, str]]] = {}


def _schema_columns(conn, schema: str) -> Dict[str, str]:
    """{lower(列名): 实际列名}；10 分钟缓存。"""
    now = time.time()
    hit = _COLUMN_CACHE.get(schema)
    if hit and now - hit[0] < 600:
        return hit[1]
    cur = conn.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema=%s",
        (schema,))
    mapping: Dict[str, str] = {}
    for (name,) in cur.fetchall():
        mapping.setdefault(str(name).lower(), str(name))
    cur.close()
    _COLUMN_CACHE[schema] = (now, mapping)
    return mapping


def _fix_identifier_case(sql: str, columns: Dict[str, str]) -> str:
    """SQLite 标识符大小写不敏感，PG 的带引号标识符大小写敏感。

    镜像建表时保留了源库大小写（`"NE"`），而看板 SQL 里既有 `"NE"` 也有裸
    `NE`。裸写会被 PG 折成小写 `ne` → 报「字段 ne 不存在」。这里把裸标识符
    按真实列名补引号，并把 `"ne"` 这类大小写不符的引号标识符改回真实列名。
    """
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
            # 超过 63 字节的列名在 PG 里被截断；information_schema 给的也是截断名，
            # 查询里的全名要先截断再查表（CN 的 "Byte ... per APN(Byte)" 70 字节）。
            from backend.dialect_helpers import pg_trunc_ident

            real = columns.get(pg_trunc_ident(inner).lower())
            out.append(_q(real) if real and real != inner else sql[i:j + 1])
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
                # 裸标识符在 PG 里一律折小写，哪怕大小写与真实列名相同也要补引号
                out.append(_q(real))
            else:
                out.append(word)
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ----------------------------------------------------------------------
# PG 兼容函数
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# 执行
# ----------------------------------------------------------------------
def _json_scalar(v: Any) -> Any:
    from decimal import Decimal

    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).decode("utf-8", "replace")
    return v


_COMPAT_READY: set = set()


def query_pg(sql: str, schema: str, limit: int = 5000,
             timeout: Optional[float] = None) -> Dict[str, Any]:
    """在指定 schema 上执行一条只读 SQL。返回结构与远端 API 同构。"""
    import psycopg2
    from backend.database import PG_DATABASE_URL

    if not PG_DATABASE_URL:
        raise RuntimeError("本机 PG 未配置 DATABASE_URL")
    # 与远端 query_remote 同一道只读守卫：本函数是公开入口，不能只依赖调用方
    if not re.match(r"^\s*(select|with)\b", sql, re.I):
        raise ValueError("仅允许 SELECT / WITH 只读查询")
    if schema not in _COMPAT_READY:
        ensure_schema_ready(schema)
        _COMPAT_READY.add(schema)
    pg_sql = to_pg_sql(sql, schema)
    stmt_ms = int((timeout or 0) * 1000) or _QUERY_TIMEOUT_MS
    t0 = time.time()
    conn = psycopg2.connect(
        PG_DATABASE_URL, connect_timeout=10,
        options=(f"-c search_path={schema},app,public "
                 f"-c statement_timeout={stmt_ms}"))
    try:
        conn.set_session(readonly=True, autocommit=True)
        pg_sql = _fix_identifier_case(pg_sql, _schema_columns(conn, schema))
        cur = conn.cursor()
        cur.execute(pg_sql)
        cols = [d[0] for d in (cur.description or [])]
        rows = [[_json_scalar(v) for v in r] for r in cur.fetchmany(limit)]
        return {
            "success": True,
            "columns": cols,
            "rows": rows,
            "count": len(rows),
            "truncated": len(rows) >= limit,
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
            "source": "pg",
        }
    finally:
        conn.close()


def primary_source_label() -> str:
    """页面 meta 里展示的主数据源标签（PG 优先时不再显示 .19 的地址）。"""
    from backend.database import DB_MODE

    if READ_ENABLED and DB_MODE == "pg":
        return "local PG"
    from backend.config.remote_host import get_api_base_url

    return get_api_base_url()


def query_data(sql: str, db: str = "", limit: int = 5000,
               timeout: Optional[float] = None, logger_=None) -> Dict[str, Any]:
    """本机 PG 优先、远端回退。

    - 非 PG 模式 / 没有镜像 schema → 直接远端；
    - PG 报错（缺表 / 未适配方言）或无数据 → 远端；
    - 远端也空 → 返回 PG 的空结果（形状一致）。
    """
    from backend.database import DB_MODE

    log = logger_ or logger
    schema = SCHEMA_BY_DB.get(db or "")
    if not READ_ENABLED or DB_MODE != "pg" or not schema:
        from backend.services.cn.cn_kpi_service import query_remote

        return query_remote(sql, db=db or "db_ps_cs_cn.sqlite3",
                            limit=limit, timeout=timeout)

    pg_err = ""
    pg_res: Optional[Dict[str, Any]] = None
    try:
        pg_res = query_pg(sql, schema, limit, timeout=timeout)
        if pg_res.get("rows"):
            return pg_res
    except Exception as e:  # noqa: BLE001 — 缺表/方言不支持都回退
        pg_err = str(e)[:200]
        log.debug("PG 读失败，回退远端（%s）：%s", schema, pg_err)

    # PG 空结果也回落一次：镜像可能还没追平 / 该表尚未镜像
    from backend.services.cn.cn_kpi_service import query_remote

    try:
        res = query_remote(sql, db=db or "db_ps_cs_cn.sqlite3",
                           limit=limit, timeout=timeout)
        if res.get("rows") or pg_res is None:
            res = dict(res)
            res["source"] = "remote"
            return res
        return pg_res or res
    except Exception:
        if pg_res is not None:
            return pg_res
        raise

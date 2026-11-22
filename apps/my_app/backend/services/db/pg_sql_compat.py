# -*- coding: utf-8 -*-
"""PG 侧 SQLite 兼容函数（date / strftime / printf / safe_num / round）。

与 `pg_query.py` 拆开只为控制单文件体积（AGENTS.md 三.2：新建文件 ≤500 行）。
函数建在**目标 schema**里，不建在 public：`query_pg` 的连接把目标 schema 放在
search_path 首位，`date(...)` 这类未限定调用才能稳定命中；`date` 还需调用方在
SQL 里显式限定 schema，因为 `pg_catalog` 永远排在 search_path 前面。
"""
from __future__ import annotations


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


_COMPAT_DDL = r"""
CREATE OR REPLACE FUNCTION {s}.date(p text) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT CASE
    WHEN lower(trim(p)) = 'now'
      THEN to_char((now() AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD')
    ELSE to_char(p::timestamp, 'YYYY-MM-DD')
  END
$$;

CREATE OR REPLACE FUNCTION {s}.date(p text, m text) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT to_char(
    (CASE WHEN lower(trim(p)) = 'now'
          THEN (now() AT TIME ZONE 'UTC')::timestamp
          ELSE p::timestamp END)
    + CASE WHEN m ~* 'month'
           THEN (regexp_replace(m, '[^0-9+-]', '', 'g'))::int * interval '1 month'
           ELSE (regexp_replace(m, '[^0-9+-]', '', 'g'))::int * interval '1 day'
      END,
    'YYYY-MM-DD')
$$;

CREATE OR REPLACE FUNCTION {s}.datetime(p text) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT to_char(
    CASE WHEN lower(trim(p)) = 'now'
         THEN (now() AT TIME ZONE 'UTC')
         ELSE p::timestamp END,
    'YYYY-MM-DD HH24:MI:SS')
$$;

CREATE OR REPLACE FUNCTION {s}.datetime(p text, m text) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT to_char(
    (CASE WHEN lower(trim(p)) = 'now'
          THEN (now() AT TIME ZONE 'UTC')
          ELSE p::timestamp END)
    + (regexp_replace(m, '[^0-9+-]', '', 'g'))::int * interval '1 day',
    'YYYY-MM-DD HH24:MI:SS')
$$;

CREATE OR REPLACE FUNCTION {s}.julianday(p text) RETURNS double precision
LANGUAGE sql STABLE AS $$
  SELECT CASE
    WHEN lower(trim(p)) = 'now' OR p ~ '^\s*[0-9]{4}-[0-9]{2}-[0-9]{2}'
    THEN extract(epoch from
      CASE WHEN lower(trim(p)) = 'now'
           THEN (now() AT TIME ZONE 'UTC')
           ELSE p::timestamp END) / 86400.0 + 2440587.5
    ELSE NULL
  END
$$;

CREATE OR REPLACE FUNCTION {s}.strftime(fmt text, t text) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT to_char(
    CASE WHEN lower(trim(t)) = 'now'
         THEN (now() AT TIME ZONE 'UTC')
         ELSE t::timestamp END,
    CASE WHEN fmt = '%Y-%m-%d %H:00' THEN 'YYYY-MM-DD HH24:00' ELSE fmt END)
$$;

CREATE OR REPLACE FUNCTION {s}.printf(fmt text, v numeric) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN fmt = '%02d'
              THEN lpad(trunc(v)::text, 2, '0')
              ELSE v::text END
$$;

CREATE OR REPLACE FUNCTION {s}.safe_num(x text) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN x ~ '^\s*-?[0-9]+(\.[0-9]+)?\s*$' THEN x::numeric ELSE NULL END
$$;

CREATE OR REPLACE FUNCTION {s}.safe_num(x numeric) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$ SELECT x $$;

CREATE OR REPLACE FUNCTION {s}.safe_num(x double precision) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$ SELECT x::numeric $$;

CREATE OR REPLACE FUNCTION {s}.safe_num(x integer) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$ SELECT x::numeric $$;

CREATE OR REPLACE FUNCTION {s}."round"(x double precision, n integer) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$
  SELECT round(x::numeric, n)
$$;

CREATE OR REPLACE FUNCTION {s}."round"(x text, n integer) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$
  SELECT round({s}.safe_num(x), n)
$$;
"""


def ensure_compat(cur, schema: str) -> None:
    cur.execute(_COMPAT_DDL.replace("{s}", _q(schema)))




def ensure_schema_ready(schema: str) -> None:
    """建兼容函数（幂等）。由镜像任务 / 首次读时调用。"""
    import psycopg2
    from backend.database import PG_DATABASE_URL

    if not PG_DATABASE_URL:
        return
    conn = psycopg2.connect(PG_DATABASE_URL, connect_timeout=10)
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS {_q(schema)}')
        ensure_compat(cur, schema)
    finally:
        conn.close()

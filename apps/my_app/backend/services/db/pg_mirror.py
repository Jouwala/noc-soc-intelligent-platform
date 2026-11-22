# -*- coding: utf-8 -*-
"""远端只读 SQLite（db_writer_api @ .19:8765）→ 本机 PG 的通用镜像。

为什么要有这个模块（2026-11-22 如意定）
----------------------------------------
my_app 的一批看板仍在**跨机**读 .19 的 SQLite（db_ps_cs_cn / ran_kpi_daily）。
2026-11-04 对 IP RAN 已经落过同一条原则：「数据是本机产生的，远端是给其他 BI
工具用的，看板不要跨机取数」；IP RAN 的 `mirror_rows_to_local` 只服务
`ipran_ume` 六张表、且按 ME 水位裁剪，不能直接套到 CN/RAN 这批表上。

本模块做的是**通用**的那一层：

    远端表清单（sqlite_master）
      → 读建表语句拿列名 / 主键
      → 本机 PG 建同名表（所有列 TEXT，与采集侧「全部 TEXT」一致）
      → 增量（按时间列 > 本机水位 − overlap）或整表重拉（EMS / 1month）
      → 按远端主键 upsert，幂等

调用方与开关
------------
任务 `pg_mirror_sync`（`backend/services/db/pg_mirror_task.py`）每 15 分钟跑一轮；
手工回填：

    python -m backend.services.db.pg_mirror --db db_ps_cs_cn.sqlite3 --full

回退：`PG_MIRROR_ENABLED=0` 关闭镜像任务；读侧本身还有「PG 缺表/空结果 → 远端」
的回退（见 `pg_query.query_data`），关掉镜像不会让页面空白。
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("pg_mirror")

# 远端库 → 本机 PG schema。新增库只在这里加一行 + 对应的读侧映射。
MIRROR_DBS: Dict[str, str] = {
    "db_ps_cs_cn.sqlite3": "cn_kpi",
    "ran_kpi_daily.sqlite3": "ran_kpi",
    # 原生看板（FineBI 重建）用的两个历史库；只镜像被看板 SQL 引用的表
    "db.sqlite3": "native_db",
    "fw_db.sqlite": "fw_native",
}

# 只镜像看板真正引用的表：db.sqlite3 里还有 2M 行级的 cell/hourly 表，整库镜像
# 既慢又没有消费者。清单来自 native_catalog 的 FROM 交集（2026-11-22 核定）。
MIRROR_TABLES: Dict[str, tuple] = {
    "db.sqlite3": (
        "2G_Main_Kpi_group_Daily",
        "3G_Main_Kpi_group_Daily",
        "4G_Main_Kpi_group_Daily",
        "2G_Main_Kpi_Site_Daily",
        "3G_Main_Kpi_Site_Daily",
        "4G_Main_Kpi_Site_Daily",
        "Eflow_WO_FB",
        "Eflow_TT_FB",
    ),
    "fw_db.sqlite": ("device_whole_throughput",),
}

# 无主键 + 有时间列的表：滚动窗口（天），只保留看板默认窗口（31 天）再多留余量，
# 避免每轮把 20 万行的 site_daily 整表拉一遍。
WINDOW_DAYS: Dict[str, int] = {
    "2G_Main_Kpi_Site_Daily": 120,
    "3G_Main_Kpi_Site_Daily": 120,
    "4G_Main_Kpi_Site_Daily": 120,
    "2G_Main_Kpi_group_Daily": 400,
    "3G_Main_Kpi_group_Daily": 400,
    "4G_Main_Kpi_group_Daily": 400,
    "Eflow_WO_FB": 180,
}
DEFAULT_WINDOW_DAYS = 120

# sqlite 内部表 / 元数据表不镜像
_SKIP_TABLES = {"sqlite_sequence", "sqlite_stat1", "_update_log"}

# 整表重拉的表：上游用 full_replace 重建（EMS 人工导出 / 1month 月表），
# 增量水位看不见「历史被改写」，只能定期整表拉。
_FULL_REPLACE_PREFIXES = ("1month_",)
_FULL_REPLACE_SUFFIXES = ("_EMS",)

# 时间列候选：按优先级取第一个真实存在的列
_TIME_COLS = ("Begin Time", "begin_time", "End Time", "end_time",
              "Start Time", "start_time", "time_full", "Created Time",
              "Time", "time")

# 每页行数（远端 /api/query 单请求上限按 5000 走）
PAGE = int(os.environ.get("PG_MIRROR_PAGE", "5000"))
# 增量重叠：远端 upsert 可能补写上一轮末尾几分钟，重叠一段避免缝隙
OVERLAP = timedelta(hours=float(os.environ.get("PG_MIRROR_OVERLAP_HOURS", "24")))
# 整表重拉的最小间隔（小时）。EMS 一天导一次，1month 一月一次，6h 足够。
FULL_REFRESH_HOURS = float(os.environ.get("PG_MIRROR_FULL_HOURS", "6"))
# 单表超过这个行数时拒绝整表重拉（防止把 500 万行表每轮全拉一遍）
FULL_MAX_ROWS = int(os.environ.get("PG_MIRROR_FULL_MAX_ROWS", "300000"))

META_SCHEMA = "mirror_meta"


# ----------------------------------------------------------------------
# 远端读取（复用 cn_kpi_service.query_remote；它已处理超时 / 只读守卫）
# ----------------------------------------------------------------------
def _query_remote(sql: str, db: str, limit: int = PAGE) -> Dict[str, Any]:
    from backend.services.cn import cn_kpi_service

    return cn_kpi_service.query_remote(sql, db=db, limit=limit)


def _q(name: str) -> str:
    """双引号标识符（列名 / 表名里可能有空格和大写）。"""
    return '"' + str(name).replace('"', '""') + '"'


def _ident(name: str) -> str:
    """PG 标识符按 63 字节截断。

    ⚠ 必须与 `information_schema.columns` 返回值同口径：PG 建列时会把超过
    63 字节的列名截断，之后 `ADD COLUMN` 用全名会再撞一次截断后的名字，
    报 `DuplicateColumn`（2026-11-22 实测：CN 流量列名 70 字节）。
    查询侧不用改：PG 解析 `"全名"` 时同样按 63 字节截断，能命中。
    """
    from backend.dialect_helpers import pg_trunc_ident

    return pg_trunc_ident(name)


def _remote_tables(db: str) -> List[str]:
    res = _query_remote(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name", db, 200)
    names = [str(r[0]) for r in (res.get("rows") or [])]
    return [n for n in names if n not in _SKIP_TABLES and not n.startswith("sqlite_")]


def _remote_schema(db: str, table: str) -> Tuple[List[str], List[str]]:
    """返回 (列名, 主键列)。列名取 `SELECT * LIMIT 0` 的真实顺序。"""
    res = _query_remote(f'SELECT * FROM {_q(table)} LIMIT 0', db, 1)
    cols = [str(c) for c in (res.get("columns") or [])]
    if not cols:
        raise RuntimeError(f"{db}:{table} 读不到列名")

    ddl = ""
    r2 = _query_remote(
        f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'", db, 1)
    rows = r2.get("rows") or []
    if rows and rows[0] and rows[0][0]:
        ddl = str(rows[0][0])
    pk: List[str] = []
    m = re.search(r"PRIMARY\s+KEY\s*\(([^)]*)\)", ddl, re.I | re.S)
    if m:
        for part in m.group(1).split(","):
            name = part.strip().strip('"').strip("'").strip()
            if name:
                pk.append(name)
    pk = [c for c in pk if c in cols]
    return cols, pk


def _remote_count(db: str, table: str) -> int:
    res = _query_remote(f'SELECT COUNT(*) AS n FROM {_q(table)}', db, 1)
    rows = res.get("rows") or []
    return int(rows[0][0] or 0) if rows else 0


def _fetch_page(db: str, table: str, where: str, offset: int) -> List[List[Any]]:
    sql = (f'SELECT * FROM {_q(table)}'
           + (f" WHERE {where}" if where else "")
           + f" LIMIT {PAGE} OFFSET {int(offset)}")
    res = _query_remote(sql, db, PAGE)
    if not res.get("success", True):
        raise RuntimeError(res.get("error") or "远端返回 success=false")
    return [list(r) for r in (res.get("rows") or [])]


# ----------------------------------------------------------------------
# PG 侧
# ----------------------------------------------------------------------
def _connect():
    import psycopg2
    from backend.database import PG_DATABASE_URL

    if not PG_DATABASE_URL:
        raise RuntimeError("本机 PG 未配置 DATABASE_URL（检查 backend/.env）")
    conn = psycopg2.connect(PG_DATABASE_URL, connect_timeout=10)
    conn.autocommit = False
    return conn


def _ensure_schema(cur, schema: str) -> None:
    cur.execute(f'CREATE SCHEMA IF NOT EXISTS {_q(schema)}')
    cur.execute(f'CREATE SCHEMA IF NOT EXISTS {_q(META_SCHEMA)}')
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {_q(META_SCHEMA)}.meta ("
        f"db TEXT NOT NULL, table_name TEXT NOT NULL, "
        f"last_full_at TIMESTAMPTZ, rows BIGINT, watermark TEXT, updated_at TIMESTAMPTZ, "
        f"PRIMARY KEY (db, table_name))")


def _ensure_table(cur, schema: str, table: str,
                  columns: Sequence[str], pk: Sequence[str]) -> None:
    pcols = [_ident(c) for c in columns]
    ppk = [_ident(c) for c in pk]
    decl = ", ".join(f"{_q(c)} TEXT" for c in pcols)
    cur.execute(f'CREATE TABLE IF NOT EXISTS {_q(schema)}.{_q(table)} ({decl})')
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s", (schema, table))
    existing = {r[0] for r in cur.fetchall()}
    for c in pcols:
        if c not in existing:
            cur.execute(f'ALTER TABLE {_q(schema)}.{_q(table)} ADD COLUMN {_q(c)} TEXT')
    if ppk:
        # 索引名用短哈希，避免 63 字节标识符截断后与其它表撞名
        h = hashlib.sha1(f"{schema}.{table}".encode("utf-8")).hexdigest()[:12]
        cur.execute(
            f'CREATE UNIQUE INDEX IF NOT EXISTS "ux_{h}" '
            f'ON {_q(schema)}.{_q(table)} '
            f'({", ".join(_q(c) for c in ppk)})')


def _pg_watermark(cur, schema: str, table: str, col: str) -> str:
    cur.execute(f'SELECT MAX({_q(_ident(col))})::text FROM {_q(schema)}.{_q(table)}')
    row = cur.fetchone()
    return str(row[0]) if row and row[0] else ""


def _pg_count(cur, schema: str, table: str) -> int:
    cur.execute(f'SELECT COUNT(*) FROM {_q(schema)}.{_q(table)}')
    return int((cur.fetchone() or [0])[0] or 0)


def _upsert(cur, schema: str, table: str, columns: Sequence[str],
            pk: Sequence[str], rows: Sequence[Sequence[Any]], batch: int = 1000) -> int:
    """按主键 upsert；所有值按 TEXT 写入（None 保持 NULL）。"""
    if not rows:
        return 0
    from psycopg2.extras import execute_values

    pcols_all = [_ident(c) for c in columns]
    ppk = [_ident(c) for c in pk]
    cols_sql = ", ".join(_q(c) for c in pcols_all)
    if pk:
        conflict = ", ".join(_q(c) for c in ppk)
        updates = [c for c in pcols_all if c not in set(ppk)]
        if updates:
            set_sql = ", ".join(f"{_q(c)}=EXCLUDED.{_q(c)}" for c in updates)
            action = f"DO UPDATE SET {set_sql}"
        else:
            action = "DO NOTHING"
        sql = (f'INSERT INTO {_q(schema)}.{_q(table)} ({cols_sql}) VALUES __V__ '
               f'ON CONFLICT ({conflict}) {action}')
    else:
        sql = f'INSERT INTO {_q(schema)}.{_q(table)} ({cols_sql}) VALUES __V__'

    # 列名里带 %（如 "Current usage ratio of UP's vCPU(%)"）：execute_values 走
    # psycopg2 的 % 格式化，裸 % 会被当成格式符报 "unsupported format character"。
    # 先把字面 % 翻倍，再把占位符换回 %s。
    sql = sql.replace("%", "%%").replace("__V__", "%s")

    total = 0
    for i in range(0, len(rows), batch):
        chunk = [tuple(None if v is None else str(v) for v in r) for r in rows[i:i + batch]]
        execute_values(cur, sql, chunk, page_size=len(chunk))
        total += len(chunk)
    return total


def _set_meta(cur, db: str, table: str, rows: int, watermark: str,
              full_done: bool = False) -> None:
    now = datetime.now(timezone.utc)
    if full_done:
        cur.execute(
            f"INSERT INTO {_q(META_SCHEMA)}.meta "
            f"(db, table_name, last_full_at, rows, watermark, updated_at) "
            f"VALUES (%s,%s,%s,%s,%s,%s) "
            f"ON CONFLICT (db, table_name) DO UPDATE SET "
            f"last_full_at=EXCLUDED.last_full_at, rows=EXCLUDED.rows, "
            f"watermark=EXCLUDED.watermark, updated_at=EXCLUDED.updated_at",
            (db, table, now, rows, watermark, now))
    else:
        cur.execute(
            f"INSERT INTO {_q(META_SCHEMA)}.meta "
            f"(db, table_name, rows, watermark, updated_at) "
            f"VALUES (%s,%s,%s,%s,%s) "
            f"ON CONFLICT (db, table_name) DO UPDATE SET "
            f"rows=EXCLUDED.rows, watermark=EXCLUDED.watermark, "
            f"updated_at=EXCLUDED.updated_at",
            (db, table, rows, watermark, now))


def _needs_full_refresh(cur, db: str, table: str) -> Tuple[bool, str]:
    cur.execute(
        f"SELECT last_full_at FROM {_q(META_SCHEMA)}.meta "
        f"WHERE db=%s AND table_name=%s", (db, table))
    row = cur.fetchone()
    if not row or not row[0]:
        return True, "无整表刷新记录"
    age = datetime.now(timezone.utc) - row[0]
    if age > timedelta(hours=FULL_REFRESH_HOURS):
        return True, f"距上次整表刷新 {age.total_seconds() / 3600:.1f}h"
    return False, ""


# ----------------------------------------------------------------------
# 单表镜像
# ----------------------------------------------------------------------
def _sync_window(cur, db: str, table: str, schema: str,
                 cols: Sequence[str], time_col: str) -> Dict[str, Any]:
    """无主键 + 有时间列：删掉窗口内的旧行再整段写回（窗口替换）。

    这些表（site_daily / group_daily / Eflow_WO_FB）没有唯一约束，增量 INSERT
    会重复；每轮整表重拉又要拉 20 万行。窗口替换只碰最近 N 天（默认 120），
    既幂等又能吸收上游对窗口内数值的修正。
    """
    t0 = time.time()
    watermark = _pg_watermark(cur, schema, table, time_col)
    lo = None
    if watermark:
        try:
            lo = datetime.strptime(watermark[:19], "%Y-%m-%d %H:%M:%S") - OVERLAP
        except ValueError:
            lo = None
    if lo is None:
        # 首次建镜像：整段历史都拉（原生看板默认窗口是「全历史」，只留 120 天
        # 会让页面突然少掉 2025 年的曲线）。之后只滚动最近 N 天。
        lo = datetime(1970, 1, 1, tzinfo=timezone.utc)
    lo_s = f"{lo:%Y-%m-%d %H:%M:%S}"
    where = f"{_q(time_col)} >= '{lo_s}'"

    rows: List[List[Any]] = []
    offset = 0
    page = _fetch_page(db, table, where, offset)
    rows.extend(page)
    offset += len(page)
    while len(page) == PAGE:
        page = _fetch_page(db, table, where, offset)
        rows.extend(page)
        offset += len(page)

    written = 0
    if rows:
        cur.execute(
            f'DELETE FROM {_q(schema)}.{_q(table)} '
            f'WHERE {_q(_ident(time_col))} >= %s', (lo_s,))
        written = _upsert(cur, schema, table, cols, [], rows)
    new_wm = _pg_watermark(cur, schema, table, time_col)
    _set_meta(cur, db, table, _pg_count(cur, schema, table), new_wm)
    return {"db": db, "table": table, "mode": "window", "rows": written,
            "watermark": new_wm, "where": where,
            "elapsed_ms": round((time.time() - t0) * 1000, 1)}


def sync_table(cur, db: str, table: str, schema: str, force_full: bool = False) -> Dict[str, Any]:
    t0 = time.time()
    cols, pk = _remote_schema(db, table)
    _ensure_table(cur, schema, table, cols, pk)

    time_col = next((c for c in _TIME_COLS if c in cols), "")
    if time_col and not pk:
        # 窗口替换要按时间列 DELETE：没有索引时 20 万行表一次删要 37s
        # （2026-11-22 实测 3G site daily），加普通索引后毫秒级。
        _h = hashlib.sha1(f"{schema}.{table}.{time_col}".encode("utf-8")).hexdigest()[:12]
        cur.execute(
            f'CREATE INDEX IF NOT EXISTS "ix_{_h}" ON {_q(schema)}.{_q(table)} '
            f'({_q(_ident(time_col))})')
    by_name = (table.startswith(_FULL_REPLACE_PREFIXES)
               or table.endswith(_FULL_REPLACE_SUFFIXES))
    # 原生看板的历史库：无主键 + 有时间列 -> 滚动窗口替换
    if db in ("db.sqlite3",) and not pk and time_col and not force_full:
        return _sync_window(cur, db, table, schema, cols, time_col)
    full = force_full or by_name or not pk or not time_col

    if full:
        need, why = _needs_full_refresh(cur, db, table)
        # 无主键的表**每轮整表重拉**：没有唯一约束就没有幂等 upsert，
        # 走增量会一行变两行（2026-11-22 实测 3G UME 表 20 行镜像成 40 行）。
        # 有主键的 EMS / 1month 表按 6h 间隔重拉，避免每 5 分钟全拉一遍。
        if pk and not force_full and not need:
            return {"db": db, "table": table, "mode": "skipped",
                    "rows": 0, "watermark": "",
                    "reason": f"{why or '未到整表刷新间隔'}",
                    "elapsed_ms": round((time.time() - t0) * 1000, 1)}
        n = _remote_count(db, table)
        if n > FULL_MAX_ROWS:
            raise RuntimeError(
                f"{db}:{table} 有 {n} 行，超过整表重拉上限 {FULL_MAX_ROWS}；"
                f"该表没有可用时间列/主键，需要单独设计增量策略")
        cur.execute(f'DELETE FROM {_q(schema)}.{_q(table)}')
        rows = _fetch_page(db, table, "", 0)
        written = _upsert(cur, schema, table, cols, pk, rows)
        offset = len(rows)
        while len(rows) == PAGE:
            rows = _fetch_page(db, table, "", offset)
            written += _upsert(cur, schema, table, cols, pk, rows)
            offset += len(rows)
        watermark = ""
        if time_col:
            cur.execute(f'SELECT MAX({_q(_ident(time_col))})::text '
                        f'FROM {_q(schema)}.{_q(table)}')
            watermark = str((cur.fetchone() or [""])[0] or "")
        _set_meta(cur, db, table, written, watermark, full_done=True)
        return {"db": db, "table": table, "mode": "full", "rows": written,
                "watermark": watermark, "reason": why,
                "elapsed_ms": round((time.time() - t0) * 1000, 1)}

    # ---- 增量 ----
    watermark = _pg_watermark(cur, schema, table, time_col)
    where = ""
    if watermark:
        try:
            lo = (datetime.strptime(watermark[:19], "%Y-%m-%d %H:%M:%S") - OVERLAP)
            where = f"{_q(time_col)} >= '{lo:%Y-%m-%d %H:%M:%S}'"
        except ValueError:
            # 时间格式不是标准 DATETIME（如只有日期）：退化为整表刷新
            cur.execute(f'DELETE FROM {_q(schema)}.{_q(table)}')
            where = ""

    written = 0
    offset = 0
    rows = _fetch_page(db, table, where, offset)
    written += _upsert(cur, schema, table, cols, pk, rows)
    offset += len(rows)
    while len(rows) == PAGE:
        rows = _fetch_page(db, table, where, offset)
        written += _upsert(cur, schema, table, cols, pk, rows)
        offset += len(rows)

    new_wm = _pg_watermark(cur, schema, table, time_col) if time_col else ""
    _set_meta(cur, db, table, _pg_count(cur, schema, table), new_wm)
    return {"db": db, "table": table, "mode": "incremental", "rows": written,
            "watermark": new_wm, "where": where,
            "elapsed_ms": round((time.time() - t0) * 1000, 1)}


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------
# 镜像进程间互斥（PG session 级 advisory lock）：daemon 与手工 CLI、或两个
# 后端实例同时跑时，同一张表两路 upsert 会死锁（2026-11-22 实测）。拿不到锁
# 就跳过本轮，下一轮再来。
MIRROR_LOCK_KEY = 0x5A4D4952  # "ZMIR"


def run_mirror(db: Optional[str] = None, full: bool = False,
               tables: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """镜像一个库（或全部）。返回每个表的统计与失败列表。"""
    dbs = [db] if db else list(MIRROR_DBS)
    results: List[Dict[str, Any]] = []
    failures: List[str] = []
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (MIRROR_LOCK_KEY,))
        if not cur.fetchone()[0]:
            logger.info("[PG-MIRROR] 已有另一轮镜像在跑，跳过本轮")
            return {"ok": True, "skipped": "another mirror is running",
                    "dbs": dbs, "tables": 0, "rows": 0, "failures": [],
                    "results": []}
        try:
            for one in dbs:
                schema = MIRROR_DBS[one]
                _ensure_schema(cur, schema)
                # DDL 必须单独提交：后面任一表失败会 rollback，把刚建的 schema 一起撤掉
                conn.commit()
                names = (list(tables) if tables is not None
                     else list(MIRROR_TABLES.get(one) or _remote_tables(one)))
                for table in names:
                    try:
                        st = sync_table(cur, one, table, schema, force_full=full)
                        conn.commit()
                        results.append(st)
                        logger.info("[PG-MIRROR] %s.%s %s rows=%s %sms",
                                    one, table, st["mode"], st["rows"], st["elapsed_ms"])
                    except Exception as e:  # noqa: BLE001 — 单表失败不阻断其余表
                        conn.rollback()
                        failures.append(f"{one}:{table}: {e}")
                        logger.warning("[PG-MIRROR] %s:%s 失败: %s", one, table, e)
        finally:
            try:
                conn.rollback()
                cur.execute("SELECT pg_advisory_unlock(%s)", (MIRROR_LOCK_KEY,))
            except Exception:  # noqa: BLE001 — 连接断开时锁会随 session 释放
                pass
    return {"ok": not failures, "dbs": dbs, "tables": len(results),
            "rows": sum(int(r.get("rows") or 0) for r in results),
            "failures": failures, "results": results}


def run_pg_mirror_sync(full_backfill: bool = False) -> Dict[str, Any]:
    """任务入口（手工 / 外部调度用；生产默认由 start_mirror_daemon 跑）。"""
    if os.environ.get("PG_MIRROR_ENABLED", "1").strip().lower() in ("0", "false", "no", "off"):
        return {"ok": True, "skipped": "PG_MIRROR_ENABLED=0", "tables": 0, "rows": 0}
    out = run_mirror(full=full_backfill)
    return out


if __name__ == "__main__":
    from backend.services.db.pg_mirror_cli import main as _cli_main

    sys.exit(_cli_main())

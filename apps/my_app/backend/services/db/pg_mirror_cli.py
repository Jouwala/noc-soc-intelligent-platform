# -*- coding: utf-8 -*-
"""`pg_mirror` 的 CLI（status / 手工回填），从主模块拆出控制体积。

用法：
    python -m backend.services.db.pg_mirror --status
    python -m backend.services.db.pg_mirror --db db_ps_cs_cn.sqlite3 --full
    python -m backend.services.db.pg_mirror --db db.sqlite3 --table Eflow_TT_FB
"""
from __future__ import annotations

import argparse
import logging
from typing import Any, Dict, List, Optional

from backend.services.db.pg_mirror import (
    META_SCHEMA,
    MIRROR_DBS,
    _connect,
    _q,
    run_mirror,
)


def status() -> Dict[str, Any]:
    """诊断：每个库在 PG 里的表数 / 行数 / 水位。"""
    out: Dict[str, Any] = {}
    with _connect() as conn:
        cur = conn.cursor()
        for db, schema in MIRROR_DBS.items():
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=%s ORDER BY 1", (schema,))
            tables = [r[0] for r in cur.fetchall()]
            cur.execute(
                f"SELECT db, table_name, rows, watermark, last_full_at "
                f"FROM {_q(META_SCHEMA)}.meta WHERE db=%s ORDER BY table_name", (db,))
            out[db] = {"schema": schema, "tables": tables, "meta": [
                dict(zip(("db", "table", "rows", "watermark", "last_full_at"), r))
                for r in cur.fetchall()]}
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="远端 SQLite -> 本机 PG 镜像")
    parser.add_argument("--db", default="", help=f"远端库名，缺省=全部 {list(MIRROR_DBS)}")
    parser.add_argument("--table", default="", help="只处理一张表")
    parser.add_argument("--full", action="store_true", help="强制整表重拉")
    parser.add_argument("--status", action="store_true", help="只看 PG 侧现状")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if args.status:
        print(status())
        return 0
    tables = [args.table] if args.table else None
    out = run_mirror(db=args.db or None, full=args.full, tables=tables)
    print({k: v for k, v in out.items() if k != "results"})
    return 0 if out["ok"] else 1

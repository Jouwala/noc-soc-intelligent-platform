# -*- coding: utf-8 -*-
"""把 master_site_db 被整表重建洗掉的项目自有列补回来（2026-11-22 GIS 空白事故）。

## 事故

2026-11-21 用 CMDB 源表（`ZTE OLR MS Master Site Database 20261120.xlsx`）整表
`replace` 重建 `master_site_db`。CMDB 源表里**没有**项目自己维护的列，于是：

- `site_type`（站点物理形态 Indoor / Outdoor）整列消失 →
  `backend/routers/gis.py` 的两条站点查询对 `site_type` 是**硬编码 SELECT**，
  PostgreSQL 直接报 `UndefinedColumn` → `/api/gis/sites` 与
  `/api/gis/realtime/sites` 一律 500 → GIS 地图页与实时告警地图**全空**
  （2026-11-22 用户反馈「页面没有告警数据了」）。
- `power_sources`（供电方式）消失 → GIS 高级筛选的「供电方式」下拉静默消失。
- `supervisor` 被源表头改名成 `new_supervisor`（属于改名，不是丢失；
  消费方按列名容错改造，不在本脚本处理范围）。

`refresh_master_site.py` 已有补充列抢救机制（`SUPPLEMENTARY_COLUMNS`），
但当时清单里只有 `starlink_recently_migrated`。本次同时把它们加进清单，
本脚本负责**一次性**把已经丢掉的数据补回来。

## 行为（只增不删，可重复执行）

1. 目标表缺列 → `ALTER TABLE ... ADD COLUMN ... TEXT`；
2. 从一份旧快照库（同结构的 SQLite `app.db`，需含 `master_site_db`）按
   `site_id` → 规范化 `site_name` 的顺序回填；
3. **只填 NULL / 空串**，绝不覆盖目标表现有的非空值；
4. 回填后打印每列的覆盖数；源库里没有值的站保持空，不臆造。

## 用法

    # 先体检（不写库）
    python -X utf8 backend/scripts/restore_master_site_columns.py \
        --source "D:/02_MS Intelligent O&M Platform/build/dbwork/rebuild/app.db" --dry-run

    # 执行
    python -X utf8 backend/scripts/restore_master_site_columns.py \
        --source "D:/02_MS Intelligent O&M Platform/build/dbwork/rebuild/app.db"

快照路径**不做默认值**：不同机器、不同批次的位置不一样，写死会在别的环境
静默读到错误数据。必须由执行人显式给出并核对 `_import_time`。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.database import DB_MODE, get_raw_connection
from backend.dialect_helpers import table_columns

TARGET_TABLE = "master_site_db"
# 需要恢复的项目自有列（CMDB 源表里没有，见模块头注释）。
# supervisor 不在其中：它在源表里叫 new_supervisor，属于改名而非丢失。
RESTORE_COLUMNS = ("site_type", "power_sources")
KEY_COLUMNS = ("site_id", "site_name")


def norm_name(value) -> str:
    """站点名归一化：NBSP → 空格、压缩空白、小写。"""
    s = str(value or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def norm_id(value) -> str:
    """site_id 归一化：快照里可能是 TEXT（如 '3448'），生产是 bigint。"""
    s = str(value or "").strip()
    try:
        return str(int(float(s)))
    except (TypeError, ValueError):
        return s


def load_snapshot(path: Path) -> tuple[dict, dict, int]:
    """读旧快照 → (by_id, by_name, 总行数)。只读打开，不改快照。"""
    if not path.is_file():
        raise SystemExit(f"[FATAL] 快照文件不存在: {path}")
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({TARGET_TABLE})")}
        if not cols:
            raise SystemExit(f"[FATAL] 快照里没有表 {TARGET_TABLE}: {path}")
        missing = [c for c in (*KEY_COLUMNS, *RESTORE_COLUMNS) if c not in cols]
        if missing:
            raise SystemExit(f"[FATAL] 快照缺列 {missing}，无法用于回填: {path}")
        rows = con.execute(
            f"SELECT site_id, site_name, {', '.join(RESTORE_COLUMNS)} FROM {TARGET_TABLE}"
        ).fetchall()
    finally:
        con.close()
    by_id, by_name = {}, {}
    for row in rows:
        by_id[norm_id(row[0])] = row
        named = norm_name(row[1])
        if named:
            by_name.setdefault(named, row)
    return by_id, by_name, len(rows)


def pick(row_id: str, row_name: str, by_id: dict, by_name: dict):
    """先按 site_id 命中，再用规范化站点名兜底。"""
    hit = by_id.get(row_id)
    if hit is None:
        hit = by_name.get(norm_name(row_name))
    return hit


def main() -> int:
    ap = argparse.ArgumentParser(
        description="给 master_site_db 补回被整表重建洗掉的项目自有列并从旧快照回填"
    )
    ap.add_argument("--source", required=True,
                    help="旧快照 app.db（SQLite，含 master_site_db 且带 site_type/power_sources）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = ap.parse_args()

    src = Path(args.source).expanduser().resolve()
    by_id, by_name, snap_rows = load_snapshot(src)
    print(f"快照: {src}\n快照 {TARGET_TABLE} 行数: {snap_rows}")

    with get_raw_connection() as conn:
        existing = set(table_columns(conn, TARGET_TABLE))
        if not existing:
            raise SystemExit(f"[FATAL] 目标库没有 {TARGET_TABLE}")
        target_rows = conn.execute(
            f"SELECT site_id, site_name" + "".join(
                f", {c}" if c in existing else f", NULL AS {c}" for c in RESTORE_COLUMNS
            ) + f" FROM {TARGET_TABLE}"
        ).fetchall()
        print(f"目标库 {TARGET_TABLE} 行数: {len(target_rows)}（DB_MODE={DB_MODE}）")

        to_add = [c for c in RESTORE_COLUMNS if c not in existing]
        print("缺失列: " + (", ".join(to_add) if to_add else "无"))

        # 每个待回填值：row 顺序与 RESTORE_COLUMNS 一致
        updates = []  # (column, site_id, value)
        for row in target_rows:
            sid, sname = row[0], row[1]
            snap = pick(norm_id(sid), sname, by_id, by_name)
            if snap is None:
                continue
            for idx, col in enumerate(RESTORE_COLUMNS, start=2):
                current = row[idx] if col in existing else None
                if current is not None and str(current).strip() != "":
                    continue  # 已有非空值，不覆盖
                value = snap[idx]
                if value is None or str(value).strip() == "":
                    continue
                updates.append((col, sid, str(value).strip()))

        for col in RESTORE_COLUMNS:
            n = sum(1 for u in updates if u[0] == col)
            print(f"  待回填 {col}: {n} 行")

        if args.dry_run:
            print("[DRY-RUN] 未写库。")
            return 0

        for col in to_add:
            conn.execute(f"ALTER TABLE {TARGET_TABLE} ADD COLUMN {col} TEXT")
            print(f"  已加列 {col} TEXT")
        conn.commit()

        for col, sid, value in updates:
            conn.execute(
                f"UPDATE {TARGET_TABLE} SET {col} = ? WHERE site_id = ?", (value, sid)
            )
        conn.commit()

        # 回填后复核：覆盖数 + 其余仍为空的站数
        for col in RESTORE_COLUMNS:
            total, filled = conn.execute(
                f"SELECT COUNT(*), COUNT(NULLIF(TRIM({col}), '')) FROM {TARGET_TABLE}"
            ).fetchone()
            print(f"  复核 {col}: {filled}/{total} 有值，{total - filled} 站仍为空")
        conn.close()

    print(f"{TARGET_TABLE} 列恢复完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

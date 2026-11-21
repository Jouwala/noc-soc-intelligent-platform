# -*- coding: utf-8 -*-
"""把 my_app/db 下最新的 ZTE OLR MS Master Site Database xlsx 导入 master_site_db。

历史问题（2026-11-21 修）：
1. 旧版把 Excel 文件名写死成无日期的 `ZTE OLR MS Master Site Database.xlsx`，
   而实际投放的都带日期后缀（...20261120.xlsx），直接跑必报「文件不存在」。
2. 旧版调用 load_excel_to_db 传全量 SHEET_TABLE_MAP，会把 13 张表全部 replace；
   且新文件主 sheet 名是 'Master Site Database  '（两个尾随空格），与映射表里的
   'Master Site Database '（一个）不匹配 → 反而建出错表 master_site_database，
   真正的 master_site_db 一动不动。
3. 生产 DB_MODE=pg，get_raw_connection() 返回 PG 连接；write_df 走 SQLAlchemy
   引擎按当前 schema 写，本脚本仍可用 —— 但只动 master_site_db 一张表。

用法：
    python -X utf8 backend/scripts/refresh_master_site.py            # 导入最新
    python -X utf8 backend/scripts/refresh_master_site.py --dry-run  # 只体检不写库
    python -X utf8 backend/scripts/refresh_master_site.py --file <绝对路径>
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.database import get_raw_connection
from backend.dialect_helpers import ensure_table_indexes, write_df
from backend.scripts.init_db import clean_column_name, normalize_admin_columns

DB_DIR = PROJECT_ROOT / "db"
FILE_GLOB = "ZTE OLR MS Master Site Database*.xlsx"
SHEET_HINT = "master site database"
TARGET_TABLE = "master_site_db"
# 关键列：缺任何一个就拒绝替换（防止用错文件把主数据表洗掉）
REQUIRED_COLUMNS = {"site_id", "site_name"}


def find_latest(explicit: str | None = None) -> Path:
    """显式文件优先；否则按修改时间取最新的 CMDB 主数据 xlsx（跳过 ~$ 临时文件）。"""
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.is_file():
            raise SystemExit(f"[FATAL] 指定的源文件不存在: {p}")
        return p
    cands = [p for p in DB_DIR.glob(FILE_GLOB)
             if not p.name.startswith("~$") and p.is_file()]
    if not cands:
        raise SystemExit(f"[FATAL] {DB_DIR} 下没有匹配 {FILE_GLOB} 的文件")
    cands.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    print("候选源文件（最新在前）:")
    for p in cands[:5]:
        print(f"  {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M}  {p.name}")
    return cands[0]


def pick_master_sheet(xls) -> str:
    """兼容 'Master Site Database ' / 'Master Site Database  '（尾随空格数量不同）。"""
    norm = lambda n: re.sub(r"\s+", " ", str(n)).strip().lower()
    hits = [n for n in xls.sheet_names if norm(n) == SHEET_HINT]
    if not hits:
        hits = [n for n in xls.sheet_names if SHEET_HINT in norm(n)]
    if not hits:
        raise SystemExit(f"[FATAL] 找不到主数据 sheet，现有 sheet: {xls.sheet_names}")
    return hits[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="刷新 PG/SQLite 的 master_site_db（只动这一张表）")
    ap.add_argument("--file", default=None, help="显式指定源 xlsx（默认取 db 目录最新一份）")
    ap.add_argument("--dry-run", action="store_true", help="只体检（文件/sheet/列/行数），不写库")
    args = ap.parse_args()

    import pandas as pd

    src = find_latest(args.file)
    print(f"\n源文件: {src}")
    xls = pd.ExcelFile(str(src), engine="openpyxl")
    sheet = pick_master_sheet(xls)
    print(f"源 sheet: {sheet!r}")

    df = pd.read_excel(xls, sheet_name=sheet, engine="openpyxl")
    xls.close()

    # 表头清洗与 init_db.load_excel_to_db 完全同口径
    df.columns = [clean_column_name(c) for c in df.columns]
    seen: dict[str, int] = {}
    new_cols = []
    for c in df.columns:
        if c in seen:
            seen[c] += 1
            new_cols.append(f"{c}_{seen[c]}")
        else:
            seen[c] = 0
            new_cols.append(c)
    df.columns = new_cols
    df = df.dropna(axis=1, how="all")

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing or len(df) < 100:
        print(f"[FATAL] 体检不通过：缺关键列 {sorted(missing)}，行数 {len(df)}（<100）")
        return 1

    fixed = normalize_admin_columns(df)
    for col, audit in fixed.items():
        print(f"  县名归一化 {col}: " + "; ".join(f"{a!r}->{b!r}×{n}" for a, b, n in audit))

    df["_source_file"] = src.name
    df["_source_sheet"] = sheet
    df["_import_time"] = pd.Timestamp.now().isoformat()

    print(f"体检通过: {len(df)} 行 / {len(df.columns)} 列")
    if args.dry_run:
        print("[DRY-RUN] 未写库。")
        return 0

    with get_raw_connection() as conn:
        write_df(df, TARGET_TABLE, if_exists="replace")
        idx = ensure_table_indexes(conn, TARGET_TABLE, df.columns)
        conn.commit()
        for name in idx:
            print(f"  索引: {name}")
    print(f"{TARGET_TABLE} 更新完成（{len(df)} 行，来源 {src.name}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

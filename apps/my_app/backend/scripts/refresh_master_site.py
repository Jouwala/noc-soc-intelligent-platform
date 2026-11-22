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

from backend.database import engine, get_raw_connection
from backend.dialect_helpers import ensure_table_indexes, write_df
from backend.scripts.init_db import clean_column_name, normalize_admin_columns

DB_DIR = PROJECT_ROOT / "db"
FILE_GLOB = "ZTE OLR MS Master Site Database*.xlsx"
SHEET_HINT = "master site database"
TARGET_TABLE = "master_site_db"
# 关键列：缺任何一个就拒绝替换（防止用错文件把主数据表洗掉）
REQUIRED_COLUMNS = {"site_id", "site_name"}

# ============ 补充列抢救 + 写入前体检（2026-11-22 事故后加固） ============
# 补充列：由项目自己维护、**不在 CMDB 源文件里**的业务列。`if_exists="replace"`
# 是整表重建，源文件没有的列会被无声洗掉。
# 事故（2026-11-21 10:51）：那次重建洗掉 starlink_recently_migrated（Yes=48/No=779），
# nur_calc 的 `SELECT ... starlink_recently_migrated FROM master_site_db` 直接报
# 「字段不存在」，NUR 看板站点维度全部失效；同一轮还把 Supervisor 列改名
# new_supervisor，打断 SITE DOWN 报表的三个映射，导致 11-22 TTs/MTTR 报表 0 行。
# 处理：重建前按 site_id 把补充列抢救出来、并进待写 DataFrame 一起 replace；
#       读不到旧表就中止（不再「读失败 → 空 {} → 照常 replace」）。
SUPPLEMENTARY_COLUMNS = ("starlink_recently_migrated",)
SUPPLEMENTARY_DEFAULTS = {"starlink_recently_migrated": "No"}
# 关键列非空率下限：低于阈值直接拒绝 replace（宁可失败也不静默洗掉主数据）
KEY_COVERAGE = {"site_name": 0.95, "site_caregory": 0.95}


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


def health_check(df) -> list[str]:
    """写入前体检：关键列覆盖率 / 主键唯一 / Supervisor 列存在性。返回问题清单。"""
    problems: list[str] = []
    for col, floor in KEY_COVERAGE.items():
        if col not in df.columns:
            problems.append(f"缺关键列 {col}")
            continue
        filled = df[col].notna() & (df[col].astype(str).str.strip() != "")
        rate = float(filled.sum()) / max(len(df), 1)
        if rate < floor:
            problems.append(f"{col} 非空率 {rate:.1%} < 下限 {floor:.0%}")
    if "site_id" in df.columns:
        dup = int(df["site_id"].duplicated().sum())
        if dup:
            problems.append(f"site_id 重复 {dup} 行（主数据必须唯一）")
    if not any("supervisor" in str(c).lower() for c in df.columns):
        problems.append("没有 supervisor/new_supervisor 列"
                        "（SITE DOWN 报表的 FME/Supervisor/Site Category 映射依赖它）")
    return problems


_MISSING_TABLE_HINTS = ("does not exist", "no such table", "undefinedtable")


def _is_missing_table(exc: Exception) -> bool:
    """区分「表还不存在（首次导入）」与「表存在但读不出来」——后者不能放行。"""
    msg = str(exc).lower()
    return any(h in msg for h in _MISSING_TABLE_HINTS)


def salvage_supplementary() -> dict:
    """重建前把补充列抢救出来：返回 {列名: Series(site_id -> 值)}。

    表不存在（首次导入）返回 {}；**其它任何读取失败都中止整个导入** ——
    replace 是整表重建，拿不到旧表就无法回填，继续跑等于把补充列静默洗掉
    （2026-11-21 事故：一次重建丢了 starlink_recently_migrated）。
    """
    import pandas as pd
    from sqlalchemy import text

    try:
        old = pd.read_sql_query(text(f'SELECT * FROM "{TARGET_TABLE}"'), engine)
    except Exception as exc:  # noqa: BLE001
        if _is_missing_table(exc):
            print(f"  [skip] 现有 {TARGET_TABLE} 不存在（首次导入），补充列用默认值")
            return {}
        raise SystemExit(
            f"[FATAL] 读取现有 {TARGET_TABLE} 失败，无法抢救补充列，拒绝继续 replace："
            f"{type(exc).__name__}: {exc}"
        ) from exc
    keep: dict = {}
    for col in SUPPLEMENTARY_COLUMNS:
        if col in old.columns:
            s = old.set_index(old["site_id"].astype(str))[col]
            keep[col] = s[~s.index.duplicated()]
            print(f"  抢救补充列 {col}: {int(keep[col].notna().sum())}/{len(keep[col])} 个非空值")
        else:
            print(f"  ⚠ 现有表已无补充列 {col}（上次重建丢失？需业务补数据）")
    return keep


def apply_supplementary(df, kept: dict):
    """把补充列并入待写 DataFrame，随 replace 一次写入（不再事后 ALTER/UPDATE）。

    为什么不先 replace 再回填：那是两个事务，回填失败会留下「表已重建、补充列缺失」
    的中间态 —— 2026-11-21 事故就是 supplement 列被 replace 洗掉后没人补回。
    并进 df 后只有一次整表写入：有旧值按 site_id 搬回，没有的用列级默认值。
    """
    for col in SUPPLEMENTARY_COLUMNS:
        default = SUPPLEMENTARY_DEFAULTS.get(col)
        series = kept.get(col)
        if series is not None:
            vals = df["site_id"].astype(str).map(series)
            restored = int(vals.notna().sum())
            df[col] = vals.where(vals.notna(), default)
            print(f"  补充列 {col}: 从旧表恢复 {restored} 行，"
                  f"其余 {len(df) - restored} 行用默认值 {default!r}")
        else:
            df[col] = default
            print(f"  补充列 {col}: 旧表里没有该列，全量用默认值 {default!r}"
                  f"（历史值可能已丢失，需业务补数据）")
    return df


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

    problems = health_check(df)
    if problems:
        print("[FATAL] 体检不通过，拒绝替换主数据（防止用错文件洗表）：")
        for p in problems:
            print(f"  - {p}")
        return 1

    fixed = normalize_admin_columns(df)
    for col, audit in fixed.items():
        print(f"  县名归一化 {col}: " + "; ".join(f"{a!r}->{b!r}×{n}" for a, b, n in audit))

    df["_source_file"] = src.name
    df["_source_sheet"] = sheet
    df["_import_time"] = pd.Timestamp.now().isoformat()

    print(f"体检通过: {len(df)} 行 / {len(df.columns)} 列")

    # replace 是整表重建，源文件没有的补充列必须先抢救并**并入 df**（见文件头
    # SUPPLEMENTARY_COLUMNS）：这样 rebuild 只有一次写入，不存在「已 replace、列未回填」。
    kept = salvage_supplementary()
    df = apply_supplementary(df, kept)

    if args.dry_run:
        print(f"[DRY-RUN] 未写库。补充列: {list(SUPPLEMENTARY_COLUMNS)}（有旧值则恢复，无则默认值）")
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

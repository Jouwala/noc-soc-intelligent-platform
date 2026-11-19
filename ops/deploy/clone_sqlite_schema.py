#!/usr/bin/env python
"""clone_sqlite_schema.py —— 只复制 SQLite 的「表结构」，不复制任何数据行
=====================================================================

为什么需要它（2026-11-19）：

    冒烟运行副本（``<项目>\\.smoke``）需要一个带完整 schema 的 ``app.db``，
    但它**不能**直接拿生产那份 3.2 GB 的库（体积、以及冒烟会往里写）。
    同时它又必须有自己的三张「本地权威表」数据，否则冒烟 §8.2 会按
    「三表为空」判失败（2026-11-11 事故形态）。

    所以拆成两步：

        1. 本脚本：从生产 ``app.db`` 复制 ``sqlite_master`` 里的 DDL
           （表 / 索引 / 视图 / 触发器），得到一个 0 行但结构完整的库；
        2. ``local_authority_tools.py migrate --source <生产库> --target <新库>``
           把 credentials / task_schedule_config / email_recipients 三表灌进去。

    这样「结构」来自代码对应的生产库，「本地权威数据」也来自生产库，
    但业务数据一行不搬，避免把生产数据带进冒烟副本。

安全约束：
    - 源库**只读打开**（``mode=ro``），绝不写、不加锁升级；
    - 不打印任何行内容，只打印对象数量；
    - 目标已存在且非空时默认拒绝（``-Force`` 才覆盖，覆盖前先删文件）。

用法：
    python ops\\deploy\\clone_sqlite_schema.py ^
        --source D:\\02_olbr_portal\\apps\\my_app\\db\\app.db ^
        --target D:\\02_olbr_portal\\.smoke\\apps\\my_app\\db\\app.db

退出码：0 成功；2 目标已存在且非空；3 参数/文件错误；4 执行失败。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Windows 控制台默认按系统代码页编码 stdout，中文会 UnicodeEncodeError。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # 老解释器/被重定向时忽略
    pass

# DDL 的依赖顺序：表 → 视图 → 索引 → 触发器。
_ORDER = {"table": 0, "view": 1, "index": 2, "trigger": 3}


def _objects(con: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """返回 (type, name, sql)，按依赖顺序；跳过 sqlite 内部对象。"""
    rows = con.execute(
        """
        SELECT type, name, sql
          FROM sqlite_master
         WHERE sql IS NOT NULL
           AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()
    return sorted(rows, key=lambda r: (_ORDER.get(r[0], 9), r[1]))


def _count(con: sqlite3.Connection) -> int:
    return int(con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="复制 SQLite 表结构（DDL），不复制数据")
    parser.add_argument("--source", required=True, help="源 app.db（只读）")
    parser.add_argument("--target", required=True, help="目标 app.db（将被创建）")
    parser.add_argument("--force", action="store_true", help="目标已存在且非空时先删除再建")
    args = parser.parse_args(argv)

    src = Path(args.source)
    dst = Path(args.target)
    if not src.is_file():
        print(f"[FAIL] 源库不存在：{src}")
        return 3
    if src.resolve() == dst.resolve():
        print("[FAIL] 源与目标是同一个文件，拒绝执行")
        return 3
    if dst.exists() and dst.stat().st_size > 0 and not args.force:
        print(f"[FAIL] 目标已存在且非空：{dst}（确需重建请加 --force）")
        return 2
    if dst.exists() and args.force:
        dst.unlink()

    dst.parent.mkdir(parents=True, exist_ok=True)

    uri = "file:%s?mode=ro" % str(src).replace("\\", "/")
    try:
        with sqlite3.connect(uri, uri=True, timeout=10) as s, sqlite3.connect(str(dst)) as t:
            objs = _objects(s)
            for kind, name, sql in objs:
                t.execute(sql)
            t.commit()
            print(f"[ OK ] 结构已复制：{len(objs)} 个对象，目标 { _count(t) } 张表 -> {dst}")
    except sqlite3.Error as exc:
        print(f"[FAIL] SQLite 操作失败：{exc}")
        return 4
    print("[HINT] 下一步：python ops\\deploy\\local_authority_tools.py migrate "
          "--source <源> --target <目标>（三张本地权威表）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

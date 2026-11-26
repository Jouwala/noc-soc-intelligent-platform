# -*- coding: utf-8 -*-
"""NGI 工参解析与 `SectorID` 补号规则的单测。

## 为什么要有这组用例

三件事都是「错了也不报错、只是数据静默变脏」的：

1. **表头位置**：工参 xlsx 前两行是中英文填写说明，第 3 行才是字段名。
   把第 1 行当表头 → 列名变成"字符串；必填"；把第 3 行当数据 → 字段名被当成
   一个小区写进库。两种都不抛异常。
2. **`SectorID` 补号**：空值（实测 7 054/8 227）必须按「同一站点 + 同一
   `Downlink Center Frequency`」分组、按 `Azimuth` 升序补 1,2,3,4…；
   若只按频点分组，编号会变成 1~2324；若把已有值也重编，会破坏现场自定义起编位置。
3. **DDL 与字段映射同步**：`ngi_store.DDL` 是手写 SQL，`LTE_FIELDS` / `NR_FIELDS`
   是手写映射；两边漂移时 PG 报的错要到任务运行才看得见。

不触网、不碰库：xlsx 在内存里现造，PG 只比对 DDL 文本与字段元组。

运行：
    cd D:/02_olbr_portal/apps/my_app
    python tests/test_ngi_param.py
"""
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.services.ngi_data import ngi_param, ngi_store  # noqa: E402


# ────────────────────────── 造一个「三行表头」的工参 xlsx ──────────────────────────
LTE_HEADER = ["RAT Type", "SubNetwork ID", "ME ID", "eNodeB Name", "eNodeB ID",
              "Cell Name", "Cell ID", "SectorID", "Azimuth",
              "Downlink Center Frequency"]


def _build_zip(rat: str, header: list, data: list) -> bytes:
    """按 NGI 模板版式造 ZIP：第 1 行中文说明 / 第 2 行英文说明 / 第 3 行字段名。"""
    import openpyxl
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "LTEProject" if rat == "LTE" else "NRProject"
    ws.append(["字符串；必填" for _ in header])
    ws.append(["String; required" for _ in header])
    ws.append(header)
    for row in data:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    name = "LTEProjectParam.xlsx" if rat == "LTE" else "NRProjectParam.xlsx"
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr(name, buf.getvalue())
    return zbuf.getvalue()


def _lte_row(cell_id, azimuth, sector="", site="100", freq="1815.1"):
    return ["LTEFDD", "2", "4791", "Site-A", site, f"Site-A-C{cell_id}",
            str(cell_id), sector, str(azimuth), freq]


# ────────────────────────── 表头位置 ──────────────────────────
def test_header_is_third_row_and_instructions_are_dropped():
    """第 3 行作表头、前两行丢弃；数据从第 4 行起，不会把字段名当小区。"""
    z = _build_zip("LTE", LTE_HEADER, [_lte_row(30, 0, "1"), _lte_row(31, 110, "2")])
    rows = ngi_param.read_sheet(z, "LTE")
    assert len(rows) == 2, f"应读到 2 行数据，实际 {len(rows)}"
    assert rows[0]["Cell Name"] == "Site-A-C30", rows[0]
    assert rows[0]["eNodeB ID"] == "100"
    # 说明行不会被当成数据：第一行数据的 RAT Type 必须是真实制式
    assert rows[0]["RAT Type"] == "LTEFDD"
    assert all(r["RAT Type"] != "字符串；必填" for r in rows)


def test_missing_required_column_fails_loudly():
    """模板少列时必须报错（而不是静默丢字段继续写库）。"""
    bad = [h for h in LTE_HEADER if h != "Azimuth"]
    z = _build_zip("LTE", bad, [_lte_row(30, 0, "1")])
    try:
        ngi_param.read_sheet(z, "LTE")
    except RuntimeError as e:
        assert "Azimuth" in str(e)
    else:
        raise AssertionError("缺列时 read_sheet 应该抛 RuntimeError")


# ────────────────────────── SectorID 补号 ──────────────────────────
def _rows(*specs):
    """specs: (site, freq, cell_id, azimuth, sector)"""
    out = []
    for site, freq, cid, az, sec in specs:
        out.append({"eNodeB ID": site, "Downlink Center Frequency": freq,
                    "Cell ID": str(cid), "Cell Name": f"C{cid}",
                    "Azimuth": str(az), "SectorID": sec})
    return out


def test_empty_sector_filled_by_azimuth_ascending():
    """同站同频三个小区，方位角 250/0/110 → 补号 2/1/3。"""
    rows = _rows(("100", "1815.1", 30, 250, ""),
                 ("100", "1815.1", 20, 0, ""),
                 ("100", "1815.1", 31, 110, ""))
    stats = ngi_param.fill_missing_sector_ids(rows)
    got = {r["Cell ID"]: r["SectorID"] for r in rows}
    assert got == {"20": "1", "31": "2", "30": "3"}, got
    assert stats["filled"] == 3 and stats["groups_touched"] == 1
    assert all(r["_sector_source"] == "derived" for r in rows)


def test_grouping_is_per_site_not_per_frequency():
    """不同站点用同一频点时各编各的（只按频点分组会编出 1~全网）。"""
    rows = _rows(("100", "1815.1", 1, 0, ""), ("100", "1815.1", 2, 120, ""),
                 ("200", "1815.1", 3, 240, ""))
    ngi_param.fill_missing_sector_ids(rows)
    assert [r["SectorID"] for r in rows] == ["1", "2", "1"]


def test_different_frequency_is_a_separate_group():
    """同站不同频点各自从 1 起编（多载波扇区编号互不干扰）。"""
    rows = _rows(("100", "1815.1", 1, 0, ""), ("100", "1815.1", 2, 120, ""),
                 ("100", "1832.2", 3, 60, ""))
    ngi_param.fill_missing_sector_ids(rows)
    got = {r["Cell ID"]: r["SectorID"] for r in rows}
    assert got == {"1": "1", "2": "2", "3": "1"}, got


def test_existing_sector_ids_are_never_rewritten():
    """已有值原样保留（现场存在自定义起编位置：实测 28 组编号是旋转过的）。"""
    rows = _rows(("100", "1815.1", 1, 160, "3"),
                 ("100", "1815.1", 2, 220, "1"),
                 ("100", "1815.1", 3, 300, ""))
    ngi_param.fill_missing_sector_ids(rows)
    assert [r["SectorID"] for r in rows] == ["3", "1", "2"]
    assert [r["_sector_source"] for r in rows] == ["file", "file", "derived"]


def test_derived_numbers_skip_numbers_already_taken():
    """两个物理站点共用 eNodeB ID 时（实测 4269/4428），补号必须跳过已占用的号。

    否则同站同频会出现两个 1 号、两个 3 号扇区（2026-11-24 首版实测 10 组撞号）。
    """
    rows = _rows(("4269", "1815.1", 1, 20, "1"),
                 ("4269", "1815.1", 2, 120, "2"),
                 ("4269", "1815.1", 3, 280, "3"),
                 ("4269", "1815.1", 4, 15, ""),
                 ("4269", "1815.1", 5, 45, ""),
                 ("4269", "1815.1", 6, 205, ""))
    stats = ngi_param.fill_missing_sector_ids(rows)
    derived = [r["SectorID"] for r in rows if r["_sector_source"] == "derived"]
    assert derived == ["4", "5", "6"], derived
    assert len(set(r["SectorID"] for r in rows)) == len(rows)
    assert stats["filled"] == 3 and stats["collisions"] == 0


def test_upstream_duplicate_sector_ids_are_counted_not_hidden():
    """上游自己就重复的 SectorID 计入 collisions（不静默当成正常）。"""
    rows = _rows(("100", "1815.1", 1, 0, "1"),
                 ("100", "1815.1", 2, 120, "1"),
                 ("100", "1815.1", 3, 240, ""))
    stats = ngi_param.fill_missing_sector_ids(rows)
    assert stats["collisions"] == 1, stats
    assert [r["SectorID"] for r in rows] == ["1", "1", "2"]


def test_missing_azimuth_goes_last_but_still_gets_a_number():
    """方位角缺失的小区排最后（不能让它排到 1 号扇区前面）。"""
    rows = _rows(("100", "1815.1", 1, 10, ""),
                 ("100", "1815.1", 2, "", ""),
                 ("100", "1815.1", 3, 5, ""))
    stats = ngi_param.fill_missing_sector_ids(rows)
    assert [r["SectorID"] for r in rows] == ["2", "3", "1"]
    assert stats["missing_azimuth"] == 1


def test_no_empty_sector_means_no_work():
    """全都有值时不动数据（避免无谓重写）。"""
    rows = _rows(("100", "1815.1", 1, 0, "1"), ("100", "1815.1", 2, 120, "2"))
    stats = ngi_param.fill_missing_sector_ids(rows)
    assert stats["filled"] == 0 and stats["groups_touched"] == 0
    assert [r["SectorID"] for r in rows] == ["1", "2"]


# ────────────────────────── DDL 与字段映射一致 ──────────────────────────
def test_ddl_contains_every_field_column():
    """DDL 必须覆盖 FIELDS 里的每一列（漏列 → 任务运行时才报错）。"""
    ddl = ngi_store.DDL
    for table, fields in ((ngi_store.LTE_TABLE, ngi_store.LTE_FIELDS),
                          (ngi_store.NR_TABLE, ngi_store.NR_FIELDS)):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl, table
        for col, _header, _kind in fields:
            assert col in ddl, f"{table} 的 DDL 缺列 {col}"
    for col, _kind in ngi_store.GRID_FIELDS:
        assert col in ddl, f"{ngi_store.GRID_TABLE} 的 DDL 缺列 {col}"
    assert "run_id" in ddl and "kpi" in ddl


def test_lte_and_nr_field_names_exist_in_header_constants():
    """字段映射的上游列名必须出现在工参模板的表头里（防止手写错别名）。"""
    lte_headers = set(LTE_HEADER)
    # 这里只校验必填子集；完整模板列太多，别把测试写成第二份真相源
    for col, header, _kind in ngi_store.LTE_FIELDS:
        if header in ("RAT Type", "eNodeB ID", "Cell Name", "Cell ID", "SectorID",
                      "Azimuth", "Downlink Center Frequency"):
            assert header in lte_headers, f"LTE 字段 {col} 的上游列名 {header!r} 不在模板表头里"


def test_ddl_column_definitions_are_comma_separated():
    """每行列定义必须有逗号（首版漏逗号 → PG 直接 `语法错误 在 "rat" 或附近`）。"""
    ddl = ngi_store.DDL
    for table in (ngi_store.LTE_TABLE, ngi_store.NR_TABLE,
                  ngi_store.GRID_TABLE, ngi_store.LOG_TABLE):
        start = ddl.index(f"CREATE TABLE IF NOT EXISTS {table}")
        block = ddl[start:ddl.index(");", start)]
        lines = [ln.strip() for ln in block.splitlines()[1:] if ln.strip()]
        # 最后一行是最后一条列定义（或 PRIMARY KEY），不需要逗号
        for ln in lines[:-1]:
            if ln.startswith("PRIMARY KEY"):
                continue
            assert ln.endswith(","), f"{table} 的列定义缺逗号：{ln!r}"


def _nr_rows(*specs):
    """specs: (gnb, freq, cid, azimuth) —— NR 工参没有 SectorID 列"""
    out = []
    for gnb, freq, cid, az in specs:
        out.append({"gNodeBID": gnb, "DLCenterFrequency": freq,
                    "CellID": str(cid), "CellName": f"N{cid}", "Azimuth": str(az)})
    return out


def test_nr_sector_ids_generated_from_scratch():
    """5G 工参没有 SectorID 列：按 gNodeBID + DLCenterFrequency 分组、方位角升序生成 1..N。"""
    rows = _nr_rows(("6008", "3450", 50, 0), ("6008", "3450", 51, 120),
                    ("6008", "3450", 52, 240))
    stats = ngi_param.fill_missing_sector_ids(rows, "NR")
    assert [r["SectorID"] for r in rows] == ["1", "2", "3"], [r["SectorID"] for r in rows]
    assert all(r["_sector_source"] == "derived" for r in rows)
    assert stats["filled"] == 3 and stats["groups_total"] == 1


def test_nr_sector_ids_group_by_gnb_and_frequency():
    """不同 gNodeB 各编各的；同站不同频点也从 1 起编。"""
    rows = _nr_rows(("6008", "3450", 1, 200), ("6008", "3450", 2, 20),
                    ("6008", "3500", 3, 100), ("6245", "3450", 4, 300))
    ngi_param.fill_missing_sector_ids(rows, "NR")
    got = {r["CellID"]: r["SectorID"] for r in rows}
    assert got == {"2": "1", "1": "2", "3": "1", "4": "1"}, got


def test_nr_field_map_carries_generated_sector_id():
    """NR 字段映射必须把生成的 SectorID 落成一列（否则库里没有这一列）。"""
    cols = [c for c, _h, _k in ngi_store.NR_FIELDS]
    assert "sector_id" in cols, cols
    header_of = {c: h for c, h, _k in ngi_store.NR_FIELDS}
    assert header_of["sector_id"] == "SectorID"
    # 老库升级路径：DDL 里必须有 ALTER，否则 CREATE TABLE IF NOT EXISTS 不会补列
    assert "ADD COLUMN IF NOT EXISTS sector_id" in ngi_store.DDL
    assert "ADD COLUMN IF NOT EXISTS sector_source" in ngi_store.DDL


CHECKS = [
    test_header_is_third_row_and_instructions_are_dropped,
    test_missing_required_column_fails_loudly,
    test_empty_sector_filled_by_azimuth_ascending,
    test_grouping_is_per_site_not_per_frequency,
    test_different_frequency_is_a_separate_group,
    test_existing_sector_ids_are_never_rewritten,
    test_derived_numbers_skip_numbers_already_taken,
    test_upstream_duplicate_sector_ids_are_counted_not_hidden,
    test_missing_azimuth_goes_last_but_still_gets_a_number,
    test_no_empty_sector_means_no_work,
    test_ddl_contains_every_field_column,
    test_ddl_column_definitions_are_comma_separated,
    test_nr_sector_ids_generated_from_scratch,
    test_nr_sector_ids_group_by_gnb_and_frequency,
    test_nr_field_map_carries_generated_sector_id,
    test_lte_and_nr_field_names_exist_in_header_constants,
]


if __name__ == "__main__":
    passed = failed = 0
    for fn in CHECKS:
        try:
            fn()
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[ERROR] {fn.__name__}: {type(e).__name__}: {e}")
        else:
            passed += 1
            print(f"[PASS] {fn.__name__}")
    print(f"\nPASS={passed} FAIL={failed}")
    sys.exit(1 if failed else 0)

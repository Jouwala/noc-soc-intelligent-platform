# -*- coding: utf-8 -*-
"""工参（ProjectParam）解析与 `SectorID` 补号。

## 文件版式（实测 2026-11-24）

`projectParamFiles` 返回 `ProjectData.zip`，内含两个工作簿：

| 文件 | sheet | 数据量 |
| --- | --- | --- |
| `LTEProjectParam.xlsx` | `LTEProject` | 8 227 个 LTE 小区 / 101 列 |
| `NRProjectParam.xlsx` | `NRProject` | 101 个 NR 小区 / 61 列 |

**表头是 3 行**（NGI 工参导入模板的固定版式）：

| 行 | 内容 | 处理 |
| --- | --- | --- |
| 1 | 中文填写说明（"字符串；必填"…） | **丢弃** |
| 2 | 英文填写说明（"String; required"…） | **丢弃** |
| 3 | 字段名（`RAT Type` / `Cell ID` / `Azimuth`…） | 作为表头 |
| 4 起 | 数据 | 入库 |

若把第 1 行当表头，列名会变成一堆"字符串；必填"；若把第 3 行当数据，
会把字段名当成一个小区写进库 —— 两种都会静默产出脏数据，故这里显式跳过前两行。

## `SectorID` 补号规则（2026-11-24 如意定）

LTE 工参里 `SectorID` 有大量空值（实测 8 227 行里 7 054 行为空），空值按
**同一站点（`eNodeB ID`）+ 同一 `Downlink Center Frequency`** 分组后，
按 `Azimuth` 升序依次补 1、2、3、4……

* 只填空值，**已有值原样保留**（实测已有值只取 1~4，且 355/383 个全有值分组
  与"按方位角升序编号"完全一致；剩下 28 组是现场自定义的起编位置，改了会破坏
  现场口径）；
* 分组必须带站点：只按频点分组时每组有 1 883~2 324 个小区（全网同频），
  编号会变成 1~2324，不可能是扇区号；
* 方位角缺失的行排在最后，按 `Cell ID` 排序兜底（实测当前数据无缺失）；
* **补出的号要跳过本组已被占用的号**（实测 2 个站点共用同一 `eNodeB ID`：
  4269 的 `Faith Comm` 与 `Redlight 3`、4428 的 `New Georgian Bassa Comm` 与
  `Wood Camp2`，同频下一共 15 个小区、其中 3 个已有 1/2/3 —— 若按"位置号直接写"，
  空值会补出重复的 1 和 3，扇区号在同组内不唯一）；
* 撞号数仍回传（`collisions`），但此时只会是"归档值本身已重复"的上游脏数据。
"""
from __future__ import annotations

import io
import logging
import zipfile
from typing import Any

logger = logging.getLogger("ngi_param")

# 制式 -> (ZIP 内文件名关键词, 兜底 sheet 名, 该表要求的必需列)
SHEETS = {
    "LTE": {
        "file_keyword": "LTEProjectParam",
        "sheet": "LTEProject",
        "required": ("RAT Type", "eNodeB ID", "Cell Name", "Cell ID", "Azimuth",
                     "Downlink Center Frequency", "SectorID"),
    },
    "NR": {
        "file_keyword": "NRProjectParam",
        "sheet": "NRProject",
        "required": ("RATType", "gNodeBID", "CellName", "CellID", "Azimuth"),
    },
}

# 表头所在行（1-based）。前两行是中英文填写说明。
HEADER_ROW = 3


def _s(v: Any) -> str | None:
    """文本列：去首尾空白，空串归一成 None。"""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _f(v: Any) -> float | None:
    """浮点列：容忍 '1 815.1'（上游偶有千分位空格）/ ``'None'`` / 空串。"""
    s = _s(v)
    if s is None:
        return None
    s = s.replace(" ", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _i(v: Any) -> int | None:
    """整数列。上游常给 '32.0'，先按浮点解析再取整；非数字返回 None。"""
    f = _f(v)
    return None if f is None else int(round(f))


def _blank(v: Any) -> bool:
    return v is None or str(v).strip() == ""


def open_workbook(zip_bytes: bytes, keyword: str) -> tuple[str, Any]:
    """在工参 ZIP 里找指定制式的工作簿，返回 (文件名, openpyxl 只读工作簿)。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise RuntimeError(f"工参导出不是合法 ZIP：{e}") from e
    names = [n for n in zf.namelist() if keyword.lower() in n.lower()]
    if not names:
        raise RuntimeError(
            f"工参 ZIP 里没有 {keyword} 工作簿（实际条目：{zf.namelist()}）；"
            "上游可能改了导出内容，拒绝落库以免清空已有工参"
        )
    import openpyxl  # 延迟导入：只有本任务需要

    raw = zf.read(names[0])
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    return names[0], wb


def read_sheet(zip_bytes: bytes, rat: str) -> list[dict]:
    """读某一制式的全部数据行（跳过前两行填写说明，第 3 行做表头）。"""
    spec = SHEETS[rat]
    fname, wb = open_workbook(zip_bytes, spec["file_keyword"])
    sheet_name = spec["sheet"] if spec["sheet"] in wb.sheetnames else wb.sheetnames[0]
    ws = wb[sheet_name]

    rows_iter = ws.iter_rows(min_row=HEADER_ROW, values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        raise RuntimeError(f"{fname} 第 {HEADER_ROW} 行应为表头，实际为空") from None
    header = [_s(h) or f"col{i + 1}" for i, h in enumerate(header)]

    missing = [c for c in spec["required"] if c not in header]
    if missing:
        raise RuntimeError(
            f"{fname} 缺少必需列 {missing}；现有列前 20 个：{header[:20]}。"
            "上游可能改了工参模板，拒绝落库"
        )

    out: list[dict] = []
    for raw in rows_iter:
        if raw is None or all(_blank(v) for v in raw):
            continue
        row = {col: raw[i] if i < len(raw) else None for i, col in enumerate(header)}
        # 上游可能把列往后挪（模板加列），这里只取我们认识的列，其余进 raw_json
        if _blank(row.get(spec["required"][0])):
            continue
        out.append(row)
    wb.close()

    if not out:
        raise RuntimeError(f"{fname}/{sheet_name} 没有任何数据行（表头之后为空）")
    return out


def fill_missing_sector_ids(rows: list[dict]) -> dict:
    """按「站点 + 下行中心频点」分组，按方位角升序给空的 `SectorID` 补 1、2、3…

    就地修改 `rows`，并写入 `_sector_source`（`file` / `derived`）。
    返回统计 `{filled, groups_touched, groups_total, collisions, missing_azimuth}`，
    其中 `collisions` 只统计**上游已有值自身重复**的个数（补号不会造出重复）。
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        site = _s(r.get("eNodeB ID")) or ""
        freq = _s(r.get("Downlink Center Frequency")) or ""
        groups.setdefault((site, freq), []).append(r)

    filled = touched = collisions = missing_az = 0
    for _key, items in groups.items():
        seen: set[int] = set()
        for r in items:
            if _blank(r.get("SectorID")):
                r["_sector_source"] = None
                continue
            r["_sector_source"] = "file"
            n = _i(r.get("SectorID"))
            if n is not None:
                if n in seen:
                    collisions += 1
                seen.add(n)
        if not any(_blank(r.get("SectorID")) for r in items):
            continue
        touched += 1

        def _sort_key(r: dict):
            az = _f(r.get("Azimuth"))
            cid = _i(r.get("Cell ID"))
            return (az is None, az if az is not None else 0.0,
                    cid is None, cid if cid is not None else 0, _s(r.get("Cell Name")) or "")

        used = set(seen)
        next_no = 1
        for r in sorted(items, key=_sort_key):
            if not _blank(r.get("SectorID")):
                continue
            # 跳过本组已被占用的号：保证同站同频下扇区号唯一
            while next_no in used:
                next_no += 1
            if _f(r.get("Azimuth")) is None:
                missing_az += 1
            r["SectorID"] = str(next_no)
            r["_sector_source"] = "derived"
            used.add(next_no)
            filled += 1
            next_no += 1

    stats = {
        "filled": filled,
        "groups_touched": touched,
        "groups_total": len(groups),
        "collisions": collisions,
        "missing_azimuth": missing_az,
    }
    if filled:
        logger.info("[NGI] SectorID 补号：%s", stats)
    else:
        logger.info("[NGI] SectorID 无空值，未补号")
    return stats


def normalize_uid(rat: str, row: dict) -> str:
    """小区唯一键：`LTE:<eNodeB ID>:<Cell ID>` / `NR:<gNodeBID>:<CellID>`。

    缺 ID 时退回用小区名（并记录到 raw_json），保证主键非空。
    """
    if rat == "LTE":
        site, cell = _s(row.get("eNodeB ID")), _s(row.get("Cell ID"))
        name = _s(row.get("Cell Name"))
    else:
        site, cell = _s(row.get("gNodeBID")), _s(row.get("CellID"))
        name = _s(row.get("CellName"))
    if site and cell:
        return f"{rat}:{site}:{cell}"
    return f"{rat}:name:{name or 'unknown'}"


def compact_name(rat: str, row: dict) -> str:
    """排障日志用的小区标识（不含全行）。"""
    if rat == "LTE":
        return f"{_s(row.get('eNodeB Name')) or '?'}/{_s(row.get('Cell Name')) or '?'}"
    return f"{_s(row.get('gNodeBName')) or '?'}/{_s(row.get('CellName')) or '?'}"


__all__ = [
    "SHEETS", "HEADER_ROW", "open_workbook", "read_sheet",
    "fill_missing_sector_ids", "normalize_uid", "compact_name",
]

# -*- coding: utf-8 -*-
"""覆盖评估网格 CSV 解析。

`downloadExportMapFile` 返回 `CSV.zip`（实测 11.4 MB），里面唯一一个 CSV
（如 `LTE_RSRP_Grid_Layer.csv`，33.3 MB / 178 370 行 / 13 列）：

| 列 | 含义 | 入库列 |
| --- | --- | --- |
| `earthid` | 地球分块号（实测只有 2932 / 2933） | `earthid` |
| `offsetx` / `offsety` | 块内格点偏移 | `offsetx` / `offsety` |
| `styleKey` | RSRP 区间序号（1~7，对应 legend 的 7 段） | `bin_index` |
| `eNodeBID_CellID` | 该格点覆盖的小区（多值分号分隔） | `cell_keys` |
| `PCI` | 同上，多值分号分隔 | `pcis` |
| `Top_Sample_Points` / `Total_Sample_Points` | 主服小区采样点 / 总采样点 | 同名 |
| `RSRP` | 格点 RSRP 均值（dBm） | `rsrp` |
| `GoodRatio` | 好点率，形如 `77.72(%)` | `good_ratio`（去掉 `(%)`） |
| `center_longitude` / `center_latitude` | 格点中心经纬度 | `center_lon` / `center_lat` |
| `Polygon` | 50 m 格点四边形（"lon lat lon lat …" 空格分隔） | `polygon` |

主键为 `(kpi, earthid, offsetx, offsety)`：实测 178 370 行内该组合唯一。
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile

logger = logging.getLogger("ngi_coverage")

EXPECTED_COLUMNS = (
    "earthid", "offsetx", "offsety", "styleKey", "eNodeBID_CellID", "PCI",
    "Top_Sample_Points", "Total_Sample_Points", "RSRP", "GoodRatio",
    "center_longitude", "center_latitude", "Polygon",
)


def _f(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _i(v):
    f = _f(v)
    return None if f is None else int(round(f))


def _ratio(v):
    """`77.72(%)` -> 77.72；空值 None。"""
    if v is None:
        return None
    s = str(v).strip().replace("(%)", "").replace("%", "").strip()
    return _f(s)


def _s(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def parse_zip(zip_bytes: bytes) -> tuple[str, list[dict]]:
    """解出 CSV 并解析为行字典，返回 `(csv 文件名, rows)`。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise RuntimeError(f"覆盖导出不是合法 ZIP：{e}") from e
    csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
    if not csv_names:
        raise RuntimeError(f"覆盖 ZIP 里没有 CSV（实际条目：{zf.namelist()}）")
    name = csv_names[0]
    raw = zf.read(name)

    text = None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise RuntimeError(f"{name} 无法按 utf-8/gbk 解码（{len(raw)} 字节）")

    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        raise RuntimeError(f"{name} 是空文件") from None
    missing = [c for c in EXPECTED_COLUMNS if c not in header]
    if missing:
        raise RuntimeError(
            f"{name} 缺少列 {missing}；实际列：{header}。上游可能改了导出格式，拒绝落库"
        )
    idx = {c: header.index(c) for c in EXPECTED_COLUMNS}

    rows: list[dict] = []
    for lineno, rec in enumerate(reader, start=2):
        if not rec or all(not str(x).strip() for x in rec):
            continue
        if len(rec) < len(header):
            logger.warning("[NGI] %s 第 %d 行字段数不足（%d < %d），跳过",
                           name, lineno, len(rec), len(header))
            continue
        rows.append({
            "earthid": _s(rec[idx["earthid"]]),
            "offsetx": _i(rec[idx["offsetx"]]),
            "offsety": _i(rec[idx["offsety"]]),
            "bin_index": _i(rec[idx["styleKey"]]),
            "cell_keys": _s(rec[idx["eNodeBID_CellID"]]),
            "pcis": _s(rec[idx["PCI"]]),
            "top_sample_points": _i(rec[idx["Top_Sample_Points"]]),
            "total_sample_points": _i(rec[idx["Total_Sample_Points"]]),
            "rsrp": _f(rec[idx["RSRP"]]),
            "good_ratio": _ratio(rec[idx["GoodRatio"]]),
            "center_lon": _f(rec[idx["center_longitude"]]),
            "center_lat": _f(rec[idx["center_latitude"]]),
            "polygon": _s(rec[idx["Polygon"]]),
        })

    if not rows:
        raise RuntimeError(f"{name} 只有表头没有数据行")
    return name, rows


__all__ = ["EXPECTED_COLUMNS", "parse_zip"]

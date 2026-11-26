# -*- coding: utf-8 -*-
"""位置页覆盖图层 `NGC1` 二进制格式与 ETag 协商的单测（不连库、不触网）。

守住两件「错了不报错、只是图画歪」的事：

1. **字节布局**：前端 `pages/location/coverageGrid.ts::parseGridBlob` 直接按偏移
   开 `Float32Array` 视图；这里多一个字节、少一个对齐，地图上就是一片乱点。
2. **ETag 协商**：同一轮同步内浏览器必须拿到 304，否则每次开图层都要重下 1~2 MB。

运行：
    cd D:/02_olbr_portal/apps/my_app
    python -m pytest tests/test_ngi_coverage_api.py
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.ngi_data import ngi_coverage_api as api  # noqa: E402


def test_pack_layout_matches_frontend_offsets():
    rows = [(-10.80123, 6.30001, 1), (-10.79999, 6.29950, 7), (-10.5, 6.1, None)]
    blob = api.pack_grid(rows)
    n = 3
    assert blob[:4] == b"NGC1"
    assert struct.unpack_from("<I", blob, 4)[0] == n
    assert len(blob) == 8 + 9 * n
    # 两段 float32 的起点必须 4 字节对齐，前端才能直接开 Float32Array 视图
    assert 8 % 4 == 0 and (8 + 4 * n) % 4 == 0
    lon = struct.unpack_from(f"<{n}f", blob, 8)
    lat = struct.unpack_from(f"<{n}f", blob, 8 + 4 * n)
    bins = list(blob[8 + 8 * n:])
    assert abs(lon[0] - -10.80123) < 1e-5 and abs(lat[1] - 6.29950) < 1e-5
    assert bins == [1, 7, 0]  # 空档位 → 0


def test_rows_without_coordinates_are_dropped():
    blob = api.pack_grid([(None, 6.3, 1), (-10.8, None, 2), (float("nan"), 6.3, 3), (-10.8, 6.3, 4)])
    lon, lat, bins = api.unpack_grid(blob)
    assert len(lon) == 1 and bins == [4]


def test_out_of_range_bin_becomes_zero():
    _lon, _lat, bins = api.unpack_grid(api.pack_grid([(-10.8, 6.3, 999), (-10.8, 6.3, -1), (-10.8, 6.3, "x")]))
    assert bins == [0, 0, 0]


def test_roundtrip_precision_is_well_below_grid_size():
    rows = [(-10.0 - i * 0.00045, 6.0 + i * 0.00045, (i % 7) + 1) for i in range(1000)]
    lon, lat, bins = api.unpack_grid(api.pack_grid(rows))
    worst = max(max(abs(a - r[0]), abs(b - r[1])) for a, b, r in zip(lon, lat, rows))
    assert worst < 2e-6  # ≈0.2 m，远小于 50 m 网格
    assert bins == [r[2] for r in rows]


def test_empty_grid_is_valid():
    blob = api.pack_grid([])
    assert blob == b"NGC1" + b"\x00\x00\x00\x00"
    assert api.unpack_grid(blob) == ([], [], [])


def test_etag_changes_with_run_and_count_and_matches_weak_forms():
    e1 = api.make_etag("LTE_RSRP", 12, 178370)
    assert e1 != api.make_etag("LTE_RSRP", 13, 178370)
    assert e1 != api.make_etag("LTE_RSRP", 12, 178371)
    assert api.etag_matches(e1, e1)
    assert api.etag_matches(e1.removeprefix("W/"), e1)  # 代理把 W/ 去掉也要认
    assert api.etag_matches(f'"other", {e1}', e1)
    assert not api.etag_matches(None, e1)
    assert not api.etag_matches('W/"ngc-LTE_RSRP-11-178370"', e1)


def test_etag_sanitizes_kpi():
    assert '"' not in api.make_etag('a"b', 1, 1)[3:-1]


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))

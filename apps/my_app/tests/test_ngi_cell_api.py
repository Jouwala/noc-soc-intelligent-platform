# -*- coding: utf-8 -*-
"""位置页「LTE 小区」图层 payload 的单测（不连库、不触网）。

前端 `pages/location/lteCells.ts::parseCellPayload` **按列名**取值，所以这里守住：
列名清单与前端一致、行与列等长、NaN/日期/Decimal 都能变成合法 JSON。

运行：
    cd D:/02_olbr_portal/apps/my_app
    python -m pytest tests/test_ngi_cell_api.py
"""
import gzip
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.ngi_data import ngi_cell_api as api  # noqa: E402
from backend.services.ngi_data import ngi_store  # noqa: E402

FRONTEND_COLUMNS = {
    "cell_uid", "enodeb_id", "enodeb_name", "cell_id", "cell_name", "sector_id", "pci", "tac",
    "dl_center_freq", "dl_earfcn", "band_indicator", "bandclass", "dl_system_bw", "azimuth",
    "height_m", "mech_downtilt", "elec_downtilt", "cell_lat", "cell_lon", "site_lat", "site_lon",
    "cover_type", "active", "vendor",
}


def test_columns_match_frontend_and_exist_in_table():
    assert set(api.COLUMNS) == FRONTEND_COLUMNS
    table_cols = {"cell_uid"} | {c for c, _h, _k in ngi_store.LTE_FIELDS}
    missing = [c for c in api.COLUMNS if c not in table_cols]
    assert not missing, f"COLUMNS 里有 LTE 工参表没有的列：{missing}"


def _identifiers(expr: str) -> set[str]:
    import re

    return {w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr)} - {"NULL", "COALESCE"}


def test_nr_select_covers_every_output_column_and_uses_real_nr_columns():
    # 两张表必须输出同一套列名：前端一份解包代码两层共用
    assert set(api.NR_SELECT) == set(api.COLUMNS)
    assert set(api.LTE_SELECT) == set(api.COLUMNS)
    nr_cols = {"cell_uid"} | {c for c, _h, _k in ngi_store.NR_FIELDS}
    bad = {out: expr for out, expr in api.NR_SELECT.items() if not _identifiers(expr) <= nr_cols}
    assert not bad, f"NR_SELECT 引用了 NR 工参表没有的列：{bad}"
    # 坐标与站点键不能是 NULL，否则 NR 图层一个扇区都画不出来
    for c in ("enodeb_id", "cell_lat", "cell_lon", "site_lat", "site_lon", "azimuth", "dl_center_freq"):
        assert api.NR_SELECT[c] != "NULL", c


def test_select_sql_aliases_in_column_order():
    sql = api.select_sql("NR")
    assert sql.startswith("SELECT cell_uid AS cell_uid, gnb_id AS enodeb_id,")
    assert " FROM app.ngi_nr_cell_param ORDER BY " in sql
    pos = [sql.index(f" AS {c}") for c in api.COLUMNS]
    assert pos == sorted(pos)
    assert "FROM app.ngi_lte_cell_param" in api.select_sql("LTE")


def _row(**kw):
    return tuple(kw.get(c) for c in api.COLUMNS)


def test_payload_is_columnar_and_json_safe():
    rows = [
        _row(cell_uid="1_1", cell_name="A", azimuth=30.0, dl_center_freq=Decimal("1815.0"),
             cell_lat=6.3, cell_lon=float("nan"), pci=101),
        _row(cell_uid="1_2", azimuth=None, cell_lat=6.31, cell_lon=-10.8),
    ]
    p = api.build_payload(rows, snapshot_date=date(2026, 11, 22), fetched_at=datetime(2026, 11, 22, 2, 5))
    assert p["available"] is True and p["count"] == 2
    assert p["columns"] == list(api.COLUMNS)
    assert all(len(r) == len(api.COLUMNS) for r in p["rows"])
    i = p["columns"].index
    assert p["rows"][0][i("dl_center_freq")] == 1815.0
    assert p["rows"][0][i("cell_lon")] is None  # NaN → None，否则 JSON 不合法
    assert p["snapshot_date"] == "2026-11-22"
    raw, gz = api.encode(p)
    assert json.loads(raw) == json.loads(gzip.decompress(gz))
    assert b"NaN" not in raw


def test_etag_changes_with_count_and_fetch_time():
    a = api.make_etag(8227, datetime(2026, 11, 22, 2, 5))
    assert a.startswith('W/"') and a.endswith('"')
    assert a != api.make_etag(8228, datetime(2026, 11, 22, 2, 5))
    assert a != api.make_etag(8227, datetime(2026, 11, 29, 2, 5))
    # LTE / NR 各自的 ETag 不能撞
    assert a != api.make_etag(8227, datetime(2026, 11, 22, 2, 5), "ngn")


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))

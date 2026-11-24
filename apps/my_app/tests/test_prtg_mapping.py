# -*- coding: utf-8 -*-
"""PRTG 传感器映射构建测试（Issue #226）。

全部离线：传感器/设备用手造的行（形态与 #225 实测的 table 接口一致），
拓扑站点用合成小集 + 真实 orangeTopologySites.json 各验一次。
不依赖真实 PRTG，不发网络请求。
"""
import ast
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database import Base
from backend.models.prtg import PrtgBlindspotDevice, PrtgSensorMapping
from backend.services.prtg.mapping import (
    UNKNOWN_SITE,
    build_blindspot_rows,
    build_mapping_rows,
    load_topology_sites,
    match_peer_to_site,
    parse_sensor_name,
)

SITES = [
    {"n": "Kakata City", "s": "MAR", "x": 1, "y": 2},
    {"n": "AML Tokadeh", "s": "NIM", "x": 3, "y": 4},
]


def test_parse_peer_pattern():
    assert parse_sensor_name("(10152) to CSQUARED") == ("10152", "CSQUARED")
    assert parse_sensor_name("(234) to Kakata City") == ("234", "Kakata City")


def test_parse_interface_token_without_peer():
    assert parse_sensor_name("B2B_CORE-SW G1/0/45") == ("G1/0/45", None)


def test_parse_unstructured_name_guesses_nothing():
    assert parse_sensor_name("AML _TOKADEH_PING") == (None, None)
    assert parse_sensor_name("") == (None, None)


def test_match_peer_normalizes_case_and_spaces():
    site = match_peer_to_site("KAKATA  CITY", SITES)
    assert site is not None and site["n"] == "Kakata City"
    loose = match_peer_to_site("AML_TOKADEH", SITES)
    assert loose is not None and loose["n"] == "AML Tokadeh"


def test_match_peer_external_network_is_unknown():
    # CSQUARED 是外部网络、不是拓扑站点：必须未命中，落 Unknown 显式标注。
    assert match_peer_to_site("CSQUARED", SITES) is None
    assert match_peer_to_site(None, SITES) is None


def test_real_topology_sites_load():
    path = (
        Path(__file__).resolve().parents[2]
        / "portal" / "src" / "data" / "orangeTopologySites.json"
    )
    sites = load_topology_sites(path)
    assert len(sites) == 553
    assert all("n" in s and "s" in s for s in sites)


def _sensor(objid, device, name, priority=None):
    row = {"objid": objid, "device": device, "sensor": name}
    if priority is not None:
        row["priority"] = priority
    return row


def test_build_rows_every_sensor_gets_a_row():
    sensors = [_sensor(2721, "PTP_SW", "(10152) to CSQUARED", "★★★★★")]
    sensors += [_sensor(3000 + i, "OLIB-NX-3172", f"({2000 + i}) to Kakata City") for i in range(24)]
    rows = build_mapping_rows(sensors, SITES)
    assert len(rows) == 25  # 一个传感器一行，不静默丢弃
    first = rows[0]
    assert first["site_id"] == UNKNOWN_SITE and first["match_status"] == "unknown"
    assert first["interface_no"] == "10152" and first["peer_name"] == "CSQUARED"
    assert first["criticality"] == 5
    matched = rows[1]
    assert matched["site_id"] == "Kakata City" and matched["region"] == "MAR"
    assert matched["match_status"] == "auto"
    # capacity 没有可靠来源前必须留空，不许编造分母
    assert all(r["capacity_mbps"] is None and r["capacity_source"] is None for r in rows)


def test_build_blindspot_rows_only_two_groups():
    devices = [
        {"device": "FW-01", "group": "Core Firewalls"},
        {"device": "FW-02", "group": "Core Firewalls"},
        {"device": "VSAT-A", "group": "VSAT Customers"},
        {"device": "PTP_SW", "group": "Core Switching"},
    ]
    rows = build_blindspot_rows(devices)
    assert len(rows) == 3
    assert all(r["monitored"] is False and r["reason"] for r in rows)


def test_models_create_and_multimapping_unique():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[PrtgSensorMapping.__table__, PrtgBlindspotDevice.__table__])
    with Session(engine) as session:
        session.add(PrtgSensorMapping(sensor_id=2721, device_name="PTP_SW",
                                      sensor_name="(10152) to CSQUARED", site_id=UNKNOWN_SITE))
        # 同一传感器服务第二个站点：多对多写第二行应允许
        session.add(PrtgSensorMapping(sensor_id=2721, device_name="PTP_SW",
                                      sensor_name="(10152) to CSQUARED", site_id="Kakata City"))
        session.commit()
        assert session.query(PrtgSensorMapping).count() == 2
        session.add(PrtgSensorMapping(sensor_id=2721, device_name="PTP_SW",
                                      sensor_name="(10152) to CSQUARED", site_id="Kakata City"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_migration_chains_after_0006():
    # 用 AST 静态读 revision 链，不 import 迁移模块（本地 alembic/ 目录会遮蔽 alembic 包）。
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0007_prtg_sensor_mapping.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in ("revision", "down_revision") and isinstance(node.value, ast.Constant):
                values[node.target.id] = node.value.value
    assert values["revision"] == "0007_prtg_sensor_mapping"
    assert values["down_revision"] == "0006_manual_stop_flag"


def test_seed_cli_from_recorded_files(tmp_path):
    # 种子生成 CLI 的离线模式：录制的 table 响应 → mapping_seed.json。
    import json as jsonlib

    from backend.services.prtg.build_mapping_seed import main as seed_main

    sensors_file = tmp_path / "sensors.json"
    devices_file = tmp_path / "devices.json"
    sites_file = tmp_path / "sites.json"
    out_file = tmp_path / "seed.json"
    sensors_file.write_text(jsonlib.dumps({"sensors": [
        {"objid": 2721, "device": "PTP_SW", "sensor": "(10152) to CSQUARED"},
        {"objid": 3001, "device": "OLIB-NX-3172", "sensor": "(7) to Kakata City"},
    ]}), encoding="utf-8")
    devices_file.write_text(jsonlib.dumps({"devices": [
        {"device": "FW-01", "group": "Core Firewalls"},
        {"device": "PTP_SW", "group": "Core Switching"},
    ]}), encoding="utf-8")
    sites_file.write_text(jsonlib.dumps(SITES), encoding="utf-8")

    assert seed_main(["--from-file", str(sensors_file), str(devices_file),
                      "--sites", str(sites_file), "--out", str(out_file)]) == 0
    payload = jsonlib.loads(out_file.read_text(encoding="utf-8"))
    assert payload["generated_from"] == "file"
    assert len(payload["mappings"]) == 2
    assert payload["mappings"][0]["site_id"] == UNKNOWN_SITE
    assert payload["mappings"][1]["site_id"] == "Kakata City"
    assert len(payload["blindspots"]) == 1
    assert payload["blindspots"][0]["device_name"] == "FW-01"


def test_models_package_registers_prtg_tables():
    """聚合注册：只 import backend.models 时两张 PRTG 表须在 Base.metadata 中。

    否则 sqlite 模式下 Base.metadata.create_all() 不会建这两张表，
    全新 sqlite 库缺表（Muse 1 对 PR #266 的审查意见，2026-11-24）。
    用子进程隔离：本模块顶部已直接 import backend.models.prtg，
    同进程内断言无法区分注册来源、守不住回归。
    """
    import subprocess
    import sys

    code = (
        "import backend.models\n"
        "from backend.database import Base\n"
        "assert 'prtg_sensor_mapping' in Base.metadata.tables\n"
        "assert 'prtg_blindspot_device' in Base.metadata.tables\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr


# ---------------- 种子灌库（#226 数据部分） ----------------

def test_apply_seed_inserts_mappings_and_blindspots(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import backend.models  # noqa: F401
    from backend.database import Base
    from backend.models.prtg import PrtgBlindspotDevice, PrtgSensorMapping
    from backend.services.prtg import apply_mapping_seed as loader

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(loader, "SessionLocal", Session)

    seed = {
        "mappings": [{
            "sensor_id": 2717, "device_name": "PTP_SW", "sensor_name": "(10115) to MATADI-SW",
            "interface_no": "10115", "peer_name": "MATADI-SW", "site_id": "Matadi",
            "region": "monrovia", "link_type": None, "capacity_mbps": None,
            "capacity_source": None, "criticality": None, "enabled": True,
            "match_status": "auto",
        }],
        "blindspots": [
            {"device_name": "ISP-FIREWALL", "group_name": "Core Firewalls", "region": None,
             "customer": None, "reason": "PRTG 未配置传感器（0 sensors）", "owner": None,
             "monitored": False},
        ],
    }
    stats = loader.apply_seed(seed)
    assert stats["mapping_inserted"] == 1 and stats["blindspot_inserted"] == 1

    # 人工确认后重跑：不得覆盖 verified 行
    with Session() as session:
        row = session.execute(select(PrtgSensorMapping)).scalar_one()
        row.match_status = "verified"
        session.commit()
    stats2 = loader.apply_seed(seed)
    assert stats2["mapping_skipped"] == 1 and stats2["blindspot_skipped"] == 1


def test_apply_seed_does_not_add_unknown_when_sensor_already_verified(tmp_path, monkeypatch):
    """人工把 verified 行改成真实站点后重跑种子：不能再补一条 Unknown 行。"""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import backend.models  # noqa: F401
    from backend.database import Base
    from backend.models.prtg import PrtgSensorMapping
    from backend.services.prtg import apply_mapping_seed as loader

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(loader, "SessionLocal", Session)

    row = {
        "sensor_id": 2717, "device_name": "PTP_SW", "sensor_name": "(10115) to MATADI-SW",
        "interface_no": "10115", "peer_name": "MATADI-SW", "site_id": "Unknown",
        "region": None, "link_type": None, "capacity_mbps": None,
        "capacity_source": None, "criticality": None, "enabled": True,
        "match_status": "unknown",
    }
    seed = {"mappings": [row], "blindspots": []}
    loader.apply_seed(seed)

    # 人工确认：把 site_id 改成真实站点
    with Session() as session:
        current = session.execute(select(PrtgSensorMapping)).scalar_one()
        current.site_id = "Matadi"
        current.match_status = "verified"
        session.commit()

    stats = loader.apply_seed(seed)
    assert stats["mapping_inserted"] == 0
    assert stats["mapping_skipped"] == 1
    with Session() as session:
        rows = session.execute(select(PrtgSensorMapping)).scalars().all()
    assert len(rows) == 1 and rows[0].site_id == "Matadi"

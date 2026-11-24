# -*- coding: utf-8 -*-
"""门户流量拓扑服务测试（Issue #230）。内存 SQLite，不联网。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401
from backend.database import Base
from backend.models.network_topology import NetworkLink, NetworkTopologyNode
from backend.models.prtg_collector import (
    NetworkLinkCapacity,
    PrtgChannelSample5m,
    PrtgSensor,
)
from backend.services.prtg import topology


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(topology, "SessionLocal", Session)
    return Session


def _seed(db):
    with db() as session:
        session.add(PrtgSensor(objid=2721, sensor_name="(10152) to CSQUARED", device_name="PTP_SW",
                               sensortype="SNMP Traffic 64bit", status="Up", active=True))
        session.add(PrtgSensor(objid=2565, sensor_name="(369098753) port-channel2",
                               device_name="OLIB-NX", sensortype="SNMP Traffic 64bit",
                               status="Unusual", active=True))
        session.add(NetworkLinkCapacity(sensor_objid=2721, capacity_mbps=1000.0,
                                        capacity_source="manual", updated_at=datetime.utcnow()))
        session.commit()


def _add_sample(db, sensor_id, channel_id, ts, value):
    with db() as session:
        session.add(PrtgChannelSample5m(
            sensor_objid=sensor_id, channel_objid=channel_id, bucket_time=ts,
            avg_value=value, samples=1, updated_at=ts,
        ))
        session.commit()


def test_ensure_topology_creates_nodes_and_links(db):
    _seed(db)
    stats = topology.ensure_topology()
    assert stats["links"] == 2
    with db() as session:
        nodes = session.execute(select(NetworkTopologyNode)).scalars().all()
        links = session.execute(select(NetworkLink)).scalars().all()
    names = {n.name for n in nodes}
    assert "PTP_SW" in names and "CSQUARED" in names and "OLIB-NX" in names
    assert all(link.source_node_id and link.target_node_id for link in links)
    # 幂等：再跑不新增
    assert topology.ensure_topology()["created_links"] == 0


def test_traffic_utilization_only_with_capacity(db):
    _seed(db)
    now = datetime.utcnow().replace(second=0, microsecond=0)
    _add_sample(db, 2721, 0, now, 300e6 / 8)  # In 300 Mbps
    _add_sample(db, 2721, 1, now, 500e6 / 8)  # Out 500 Mbps
    _add_sample(db, 2565, 0, now, 900e6 / 8)  # 无容量

    data = topology.traffic("24h")
    by_sensor = {l["sensor_id"]: l for l in data["links"]}
    assert by_sensor[2721]["utilization_pct"] == pytest.approx(50.0)
    assert by_sensor[2721]["data_quality"]["capacity_known"] is True
    assert by_sensor[2565]["utilization_pct"] is None  # 无容量绝不按 0 算
    assert by_sensor[2565]["data_quality"]["capacity_known"] is False
    assert data["kpi"]["capacity_known"] == 1
    assert data["kpi"]["capacity_total"] == 2
    assert data["kpi"]["unusual"] == 1


def test_layout_update_and_read(db):
    _seed(db)
    topology.ensure_topology()
    nodes = topology.layout()["nodes"]
    target = nodes[0]
    assert topology.update_layout([{"id": target["id"], "x": 123, "y": 456}])["updated"] == 1
    after = {n["id"]: n for n in topology.layout()["nodes"]}
    assert after[target["id"]]["x"] == pytest.approx(123.0)
    assert after[target["id"]]["y"] == pytest.approx(456.0)


def test_mapping_update_mirrors_capacity(db):
    _seed(db)
    topology.ensure_topology()
    link = next(l for l in topology.mappings()["links"] if l["sensor_id"] == 2721)
    assert topology.update_mappings([{
        "id": link["id"], "capacity_ab_mbps": 2000.0, "capacity_ba_mbps": 1000.0,
        "capacity_source": "manual", "link_type": "backbone",
        "mapping_confidence": "verified",
    }])["updated"] == 1
    updated = next(l for l in topology.mappings()["links"] if l["id"] == link["id"])
    assert updated["capacity_ab_mbps"] == 2000.0
    assert updated["link_type"] == "backbone"
    with db() as session:
        capacity = session.execute(
            select(NetworkLinkCapacity).where(NetworkLinkCapacity.sensor_objid == 2721)
        ).scalar_one()
    assert capacity.capacity_mbps == 2000.0


def test_history_returns_in_out_series(db):
    _seed(db)
    topology.ensure_topology()
    now = datetime.utcnow().replace(second=0, microsecond=0)
    _add_sample(db, 2721, 0, now - timedelta(minutes=5), 100e6 / 8)
    _add_sample(db, 2721, 0, now, 200e6 / 8)
    _add_sample(db, 2721, 1, now, 300e6 / 8)
    link = next(l for l in topology.mappings()["links"] if l["sensor_id"] == 2721)
    data = topology.history(link["id"], "24h")
    assert len(data["series"]["in"]) == 2
    assert len(data["series"]["out"]) == 1
    assert data["series"]["in"][-1]["mbps"] == pytest.approx(200.0)

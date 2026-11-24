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


def test_ping_sensor_is_not_a_traffic_link(db):
    with db() as session:
        session.add(PrtgSensor(objid=2724, sensor_name="AML _TOKADEH_PING", device_name="AML",
                               sensortype="Ping", status="Up", active=True))
        session.commit()
    topology.ensure_topology()
    links = topology.mappings()["links"]
    assert all(link["sensor_id"] != 2724 for link in links)
    assert len(links) == 0  # 只有 Ping 时没有链路，而不是 0.001 Mbps 假链路


def test_asymmetric_capacity_pairs_in_with_ba_and_out_with_ab(db):
    _seed(db)
    now = datetime.utcnow().replace(second=0, microsecond=0)
    with db() as session:
        link = session.execute(select(NetworkLink).where(NetworkLink.sensor_objid == 2565)).scalar_one_or_none()
    # 先 ensure 再补容量（避免 fixture 的 1000 对称值）
    topology.ensure_topology()
    with db() as session:
        capacity = NetworkLinkCapacity(sensor_objid=2565, capacity_mbps=2000.0, capacity_source="manual",
                                       updated_at=now)
        session.add(capacity)
        session.commit()
    with db() as session:
        l = session.execute(select(NetworkLink).where(NetworkLink.sensor_objid == 2565)).scalar_one()
        l.capacity_ab_mbps = 2000.0
        l.capacity_ba_mbps = 1000.0
        session.commit()
    _add_sample(db, 2565, 0, now, 1500e6 / 8)  # In = B→A，配 ba=1000 → 150%
    _add_sample(db, 2565, 1, now, 100e6 / 8)   # Out = A→B，配 ab=2000 → 5%
    data = topology.traffic("24h")
    row = next(l for l in data["links"] if l["sensor_id"] == 2565)
    assert row["utilization_pct"] == pytest.approx(150.0)


def test_paused_sensor_is_unmonitored_not_up(db):
    _seed(db)
    with db() as session:
        sensor = session.execute(select(PrtgSensor).where(PrtgSensor.objid == 2565)).scalar_one()
        sensor.status = "Paused"
        session.commit()
    topology.ensure_topology()
    data = topology.traffic("24h")
    row = next(l for l in data["links"] if l["sensor_id"] == 2565)
    assert row["status"] == "Unmonitored"
    assert data["kpi"]["unknown"] >= 1
    node = next(n for n in data["nodes"] if n["name"] == "OLIB-NX")
    assert node["status"] == "Unmonitored"


def test_invalid_layout_input_is_skipped(db):
    _seed(db)
    topology.ensure_topology()
    assert topology.update_layout([{"id": "abc"}, {"id": 1, "x": "abc"}])["updated"] == 0


def test_invalid_capacity_does_not_clear_existing_value(db):
    _seed(db)
    topology.ensure_topology()
    link = next(l for l in topology.mappings()["links"] if l["sensor_id"] == 2721)
    with pytest.raises(ValueError):
        topology.update_mappings([{"id": link["id"], "capacity_ab_mbps": "oops"}])
    with db() as session:
        capacity = session.execute(
            select(NetworkLinkCapacity).where(NetworkLinkCapacity.sensor_objid == 2721)
        ).scalar_one()
    assert capacity.capacity_mbps == 1000.0


def test_node_status_keeps_up_and_unusual(db):
    _seed(db)
    topology.ensure_topology()
    data = topology.traffic("24h")
    nodes = {n["name"]: n["status"] for n in data["nodes"]}
    assert nodes["PTP_SW"] == "Up"          # Up 链路节点保持 Up，不能变 Unmonitored
    assert nodes["OLIB-NX"] == "Unusual"    # Unusual 链路节点保持 Unusual


def test_backend_app_imports_with_topology_router():
    import backend.main  # noqa: F401  # 防止路由导入错误导致后端起不来


def test_invalid_layout_number_is_skipped(db):
    _seed(db)
    topology.ensure_topology()
    assert topology.update_layout([{"id": 1, "x": "abc", "y": 2}])["updated"] == 0


def test_manual_node_and_link_crud(db):
    _seed(db)
    topology.ensure_topology()
    # ensure 之后新增的传感器尚未自动建链路，用来测试「人工绑定」
    with db() as session:
        session.add(PrtgSensor(objid=3000, sensor_name="(200) to NEWPEER", device_name="PTP_SW",
                               sensortype="SNMP Traffic 64bit", status="Up", active=True))
        session.commit()
    node = topology.create_node("MANUAL-PEER", "peer", 400, 300)
    assert node["type"] == "peer"
    link = topology.create_link(3000, 1, node["id"], link_type="test",
                                capacity_ab_mbps=1000, capacity_ba_mbps=500)
    assert link["sensor_id"] == 3000
    # 绑定后 traffic() 不能再因唯一约束 500，且链路存在
    refreshed = topology.traffic("24h")
    assert any(l["id"] == link["id"] for l in refreshed["links"])
    # 人工节点必须扛过 ensure_topology 的清理
    assert any(n["id"] == node["id"] for n in refreshed["nodes"])
    # 已绑定传感器不能重复绑定
    with pytest.raises(ValueError):
        topology.create_link(3000, 1, node["id"])
    # 上下游确认：交换两端与分方向容量
    swapped = topology.swap_direction(link["id"])
    assert swapped["source_node"] == node["id"]
    with db() as session:
        row = session.get(NetworkLink, link["id"])
        assert row.capacity_ab_mbps == 500 and row.capacity_ba_mbps == 1000
    # 折线偏移与别名
    assert topology.set_bend(link["id"], 50)["bend_offset"] == 50.0
    assert topology.add_alias(node["id"], "MANUAL-PEER-ALIAS")["alias"] == "MANUAL-PEER-ALIAS"
    # 节点仍被引用时拒绝删除；删链路后可删节点
    with pytest.raises(ValueError):
        topology.delete_node(node["id"])
    topology.delete_link(link["id"])
    topology.delete_node(node["id"])


def test_create_link_upserts_existing_capacity_row(db):
    """SLA 已登记容量后再绑定链路：更新容量行，不能 UNIQUE 冲突。"""
    _seed(db)
    topology.ensure_topology()
    with db() as session:
        session.add(PrtgSensor(objid=3001, sensor_name="(300) to PEER3", device_name="PTP_SW",
                               sensortype="SNMP Traffic 64bit", status="Up", active=True))
        session.add(NetworkLinkCapacity(sensor_objid=3001, capacity_mbps=500.0,
                                        capacity_source="manual", updated_at=datetime.utcnow()))
        session.commit()
    node = topology.create_node("PEER3", "peer")
    link = topology.create_link(3001, 1, node["id"], capacity_ab_mbps=1000)
    assert link["sensor_id"] == 3001
    with db() as session:
        rows = session.execute(
            select(NetworkLinkCapacity).where(NetworkLinkCapacity.sensor_objid == 3001)
        ).scalars().all()
    assert len(rows) == 1 and rows[0].capacity_mbps == 1000.0


def test_auto_link_endpoints_follow_sensor_rename(db):
    _seed(db)
    topology.ensure_topology()
    with db() as session:
        sensor = session.execute(select(PrtgSensor).where(PrtgSensor.objid == 2565)).scalar_one()
        sensor.sensor_name = "(200) to NEWPEER"
        session.commit()
    topology.ensure_topology()
    data = topology.traffic("24h")
    link = next(l for l in data["links"] if l["sensor_id"] == 2565)
    assert link["target"] == "NEWPEER"
    assert any(n["name"] == "NEWPEER" for n in data["nodes"])


def test_swap_on_auto_link_persists_after_refresh(db):
    _seed(db)
    topology.ensure_topology()
    data = topology.traffic("24h")
    auto = next(l for l in data["links"] if l["sensor_id"] == 2721)
    before = (auto["source_node"], auto["target_node"])
    topology.swap_direction(auto["id"])
    refreshed = next(l for l in topology.traffic("24h")["links"] if l["sensor_id"] == 2721)
    assert (refreshed["source_node"], refreshed["target_node"]) == (before[1], before[0])

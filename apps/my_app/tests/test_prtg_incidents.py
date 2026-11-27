# -*- coding: utf-8 -*-
"""设备级事件合并离线测试（P0-3，2026-11-26）。

用例来自摸底真实时间线：
  - 2026-11-02 10:26:57–10:27:33 NEXUS 3172 的 16 个传感器约每 3 秒依次 Down；
  - 2026-11-25 18:36 前后 AML TOKADEH1：Ping ICMP 超时 + 3 条 SNMP 链路相继 Down。
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401
import backend.models.prtg_logs  # noqa: F401
from backend.database import Base
from backend.models.prtg_logs import PrtgIncident, PrtgLogMessage
from backend.services.prtg import incidents, store
from backend.services.prtg.incidents import LogEvent, build_incidents

SNMP = "SNMP Traffic 64bit"


def _nexus_events():
    start = datetime(2026, 11, 2, 10, 26, 57)
    events, i = [], 0
    for k in range(16):
        i += 1
        events.append(LogEvent(i, 2560 + k, f"s{k}", "NEXUS 3172", SNMP,
                               start + timedelta(seconds=3 * k) if k < 12 else start + timedelta(seconds=33 + k - 12),
                               "Down", "snmp_no_response"))
    for k in range(16):
        i += 1
        events.append(LogEvent(i, 2560 + k, f"s{k}", "NEXUS 3172", SNMP,
                               start + timedelta(minutes=5, seconds=2 * k), "Up", "value"))
    return events


def _aml_events():
    a = datetime(2026, 11, 25, 18, 36, 41)
    return [
        LogEvent(1, 2725, "LAN", "AML TOKADEH1", SNMP, a, "Warning", "snmp_no_response"),
        LogEvent(2, 2724, "PING", "AML TOKADEH1", "Ping", a + timedelta(seconds=4), "Warning", "icmp_timeout"),
        LogEvent(3, 2724, "PING", "AML TOKADEH1", "Ping", a + timedelta(seconds=9), "Down", "icmp_timeout"),
        LogEvent(4, 2791, "ISP", "AML TOKADEH1", SNMP, a + timedelta(seconds=15), "Down", "snmp_no_response"),
        LogEvent(5, 2790, "IPRAN", "AML TOKADEH1", SNMP, a + timedelta(seconds=20), "Down", "snmp_no_response"),
        LogEvent(6, 2725, "LAN", "AML TOKADEH1", SNMP, a + timedelta(seconds=25), "Down", "snmp_no_response"),
        LogEvent(7, 2724, "PING", "AML TOKADEH1", "Ping", a + timedelta(seconds=60), "Up", "value"),
        LogEvent(8, 2791, "ISP", "AML TOKADEH1", SNMP, a + timedelta(seconds=105), "Up", "value"),
        LogEvent(9, 2790, "IPRAN", "AML TOKADEH1", SNMP, a + timedelta(seconds=110), "Up", "value"),
        LogEvent(10, 2725, "LAN", "AML TOKADEH1", SNMP, a + timedelta(seconds=115), "Up", "value"),
    ]


def test_nexus_sixteen_downs_become_one_incident():
    result = build_incidents(_nexus_events())
    assert len(result) == 1
    inc = result[0]
    assert inc["sensor_count"] == 16
    assert inc["kind"] == incidents.KIND_SNMP_MGMT  # 设备上没有 Ping 传感器
    assert inc["evidence"]["has_ping_sensor"] is False
    assert inc["started_at"] == datetime(2026, 11, 2, 10, 26, 57)
    assert inc["ended_at"] == datetime(2026, 11, 2, 10, 32, 27)
    assert inc["duration_seconds"] == 330


def test_aml_ping_and_snmp_is_device_unreachable():
    result = build_incidents(_aml_events(), ping_devices={"AML TOKADEH1"})
    assert len(result) == 1
    inc = result[0]
    assert inc["kind"] == incidents.KIND_DEVICE_UNREACHABLE
    assert inc["sensor_count"] == 4
    assert inc["duration_seconds"] == 106
    assert inc["evidence"]["icmp_timeouts"] >= 2  # Down 前 5 秒的 Warning 也算佐证


def test_bursts_further_apart_than_window_split():
    a = datetime(2026, 11, 25, 18, 27, 56)
    events = _aml_events() + [
        LogEvent(20, 2791, "ISP", "AML TOKADEH1", SNMP, a, "Down", "snmp_no_response"),
        LogEvent(21, 2791, "ISP", "AML TOKADEH1", SNMP, a + timedelta(minutes=1), "Up", "value"),
    ]
    result = build_incidents(events, ping_devices={"AML TOKADEH1"})
    assert [r["started_at"] for r in result] == [a, datetime(2026, 11, 25, 18, 36, 50)]
    assert result[0]["kind"] == incidents.KIND_SINGLE_LINK


def test_open_incident_has_no_end():
    t = datetime(2026, 11, 26, 9, 0, 0)
    result = build_incidents([LogEvent(1, 9, "x", "D", SNMP, t, "Down", "snmp_no_response")])
    assert result[0]["ended_at"] is None and result[0]["duration_seconds"] is None


def test_flap_within_window_reopens_same_incident():
    t = datetime(2026, 11, 1)
    events = [
        LogEvent(1, 9, "a", "D", SNMP, t, "Down", "snmp_no_response"),
        LogEvent(2, 9, "a", "D", SNMP, t + timedelta(seconds=30), "Up", "value"),
        LogEvent(3, 9, "a", "D", SNMP, t + timedelta(seconds=90), "Down", "snmp_no_response"),
        LogEvent(4, 9, "a", "D", SNMP, t + timedelta(seconds=150), "Up", "value"),
    ]
    result = build_incidents(events)
    assert len(result) == 1
    assert result[0]["evidence"]["flaps"] == 1
    assert result[0]["duration_seconds"] == 150


# ---------------- 持久化（内存 SQLite） ----------------

@pytest.fixture()
def db_session(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(store, "SessionLocal", Session)
    return Session


def _store_events(Session, events):
    with Session() as session:
        for e in events:
            session.add(PrtgLogMessage(
                sensor_objid=e.sensor_objid, device_name=e.device_name, sensor_name=e.sensor_name,
                sensor_type=e.sensor_type, happened_at=e.happened_at, status=e.status,
                reason=e.reason or "other", message="", dedup_key=f"k{e.id}",
                collected_at=e.happened_at,
            ))
        session.commit()


def test_correlate_is_idempotent(db_session):
    _store_events(db_session, _aml_events())
    now = datetime(2026, 11, 26, 9, 0, 0)
    first = incidents.correlate(now)
    second = incidents.correlate(now)
    assert first["incidents"] == second["incidents"] == 1
    with db_session() as session:
        assert session.execute(select(func.count(PrtgIncident.id))).scalar() == 1
    items = incidents.query_incidents(now - timedelta(days=7))
    assert items[0]["kind"] == incidents.KIND_DEVICE_UNREACHABLE
    assert items[0]["is_open"] is False
    assert {s["sensor_id"] for s in items[0]["sensors"]} == {2724, 2725, 2790, 2791}
    assert incidents.query_incidents(now - timedelta(days=7), sensor_id=2790)
    assert incidents.query_incidents(now - timedelta(days=7), sensor_id=1) == []
    summary = incidents.summary(now)
    assert summary["total"] == 1 and summary["open"] == 0
    assert summary["by_kind"][incidents.KIND_DEVICE_UNREACHABLE] == 1


def test_reclassify_then_correlate_sets_device_unreachable(db_session):
    """旧 bug：ICMP/SNMP 行 message 只剩 # 代码、reason=other → kind 错成 snmp_mgmt；
    reclassify 后 correlate 幂等改写成 device_unreachable。"""
    from backend.services.prtg import logs

    a = datetime(2026, 11, 25, 18, 36, 41)
    # 与 _aml_events 同时间线，但 reason 全错成 other（模拟 message_raw 误入库）
    broken = [
        LogEvent(1, 2725, "LAN", "AML TOKADEH1", SNMP, a, "Warning", "other"),
        LogEvent(2, 2724, "PING", "AML TOKADEH1", "Ping", a + timedelta(seconds=4), "Warning", "other"),
        LogEvent(3, 2724, "PING", "AML TOKADEH1", "Ping", a + timedelta(seconds=9), "Down", "other"),
        LogEvent(4, 2791, "ISP", "AML TOKADEH1", SNMP, a + timedelta(seconds=15), "Down", "other"),
        LogEvent(5, 2790, "IPRAN", "AML TOKADEH1", SNMP, a + timedelta(seconds=20), "Down", "other"),
        LogEvent(6, 2725, "LAN", "AML TOKADEH1", SNMP, a + timedelta(seconds=25), "Down", "other"),
        LogEvent(7, 2724, "PING", "AML TOKADEH1", "Ping", a + timedelta(seconds=60), "Up", "value"),
        LogEvent(8, 2791, "ISP", "AML TOKADEH1", SNMP, a + timedelta(seconds=105), "Up", "value"),
        LogEvent(9, 2790, "IPRAN", "AML TOKADEH1", SNMP, a + timedelta(seconds=110), "Up", "value"),
        LogEvent(10, 2725, "LAN", "AML TOKADEH1", SNMP, a + timedelta(seconds=115), "Up", "value"),
    ]
    with db_session() as session:
        for e in broken:
            # Ping Down/Warning 存 #M11010，SNMP 存 #N-2003（与生产一致）
            if e.sensor_type == "Ping" and e.status in ("Down", "Warning"):
                msg = "#M11010"
            elif e.sensor_type == SNMP and e.status in ("Down", "Warning"):
                msg = "#N-2003"
            else:
                msg = "6 msec"
            session.add(PrtgLogMessage(
                sensor_objid=e.sensor_objid, device_name=e.device_name, sensor_name=e.sensor_name,
                sensor_type=e.sensor_type, happened_at=e.happened_at, status=e.status,
                reason=e.reason or "other", message=msg, dedup_key=f"k{e.id}",
                collected_at=e.happened_at,
            ))
        session.commit()

    now = datetime(2026, 11, 26, 9, 0, 0)
    before = incidents.correlate(now)
    assert before["incidents"] == 1
    items = incidents.query_incidents(now - timedelta(days=7))
    # 无 icmp_timeout 佐证时：有 Ping 传感器仍可能判 device_unreachable，或退化——以 reclassify 后为准
    logs.reclassify_legacy_codes(now)
    after = incidents.correlate(now)
    assert after["incidents"] == 1
    items = incidents.query_incidents(now - timedelta(days=7))
    assert items[0]["kind"] == incidents.KIND_DEVICE_UNREACHABLE
    assert items[0]["evidence"]["icmp_timeouts"] >= 2


def test_query_incidents_sensor_filter_applies_before_limit(db_session):
    """带 sensor_id 时 limit 必须作用在过滤之后（否则会被更新的无关事件挤掉）。"""
    import json

    base = datetime(2026, 11, 20, 12, 0, 0)
    with db_session() as session:
        # 1 条匹配事件（较旧）
        session.add(PrtgIncident(
            device_name="MATCH-DEV", device_objid=1,
            started_at=base, ended_at=base + timedelta(minutes=5),
            kind=incidents.KIND_SINGLE_LINK, sensor_count=1, duration_seconds=300,
            sensors_json=json.dumps([{"sensor_objid": 9999, "sensor_name": "x",
                                      "sensor_type": SNMP, "down_at": None, "up_at": None}]),
            evidence_json="{}", created_at=base, updated_at=base,
        ))
        # 5 条更新的无关事件（旧实现先 limit=2 再过滤 → 空结果）
        for i in range(5):
            t = base + timedelta(hours=i + 1)
            session.add(PrtgIncident(
                device_name=f"OTHER-{i}", device_objid=100 + i,
                started_at=t, ended_at=t + timedelta(minutes=1),
                kind=incidents.KIND_SINGLE_LINK, sensor_count=1, duration_seconds=60,
                sensors_json=json.dumps([{"sensor_objid": 1000 + i, "sensor_name": "o",
                                          "sensor_type": SNMP, "down_at": None, "up_at": None}]),
                evidence_json="{}", created_at=t, updated_at=t,
            ))
        session.commit()

    got = incidents.query_incidents(base - timedelta(days=1), sensor_id=9999, limit=2)
    assert len(got) == 1
    assert got[0]["device_name"] == "MATCH-DEV"
    assert {s["sensor_id"] for s in got[0]["sensors"]} == {9999}

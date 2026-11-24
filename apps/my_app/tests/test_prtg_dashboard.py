# -*- coding: utf-8 -*-
"""PRTG 看板只读查询测试（Issue #228 口径）。内存 SQLite，不联网。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401
from backend.database import Base
from backend.models.prtg_collector import PrtgChannelSample, PrtgSensor
from backend.services.prtg import dashboard, store


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(store, "SessionLocal", Session)
    monkeypatch.setattr(dashboard, "SessionLocal", Session)
    return Session


def _add_sensor(session, objid, sensor_type, name):
    session.add(PrtgSensor(
        objid=objid, sensor_name=name, sensortype=sensor_type, status="Up",
        status_raw=3, active=True,
    ))


def test_ping_sensor_metrics_are_null_not_zero(db):
    now = datetime.utcnow().replace(second=0, microsecond=0)
    with db() as session:
        _add_sensor(session, 2724, "Ping", "AML _TOKADEH_PING")
        session.add(PrtgChannelSample(
            sensor_objid=2724, channel_objid=0, sample_time=now,
            value_num=70.0, value_display="70 msec", unit="msec", collected_at=now,
        ))
        session.commit()

    row = dashboard.links("5m")["links"][0]
    assert row["is_traffic"] is False
    assert row["in_mbps"] is None and row["out_mbps"] is None and row["total_mbps"] is None
    assert row["errors_in"] is None and row["discards_in"] is None
    assert row["utilization_pct"] is None

    detail = dashboard.link_detail(2724, "5m")
    assert detail["is_traffic"] is False
    assert list(detail["series"].keys()) == ["0"]


def test_traffic_sensor_converts_byte_per_second_to_mbps(db):
    now = datetime.utcnow().replace(second=0, microsecond=0)
    with db() as session:
        _add_sensor(session, 2721, "SNMP Traffic 64bit", "(10152) to CSQUARED")
        for channel_id, value in ((-1, 2_000_000.0), (0, 1_250_000.0), (1, 750_000.0)):
            session.add(PrtgChannelSample(
                sensor_objid=2721, channel_objid=channel_id, sample_time=now,
                value_num=value, collected_at=now,
            ))
        session.commit()

    row = dashboard.links("5m")["links"][0]
    assert row["is_traffic"] is True
    assert row["in_mbps"] == pytest.approx(10.0)
    assert row["out_mbps"] == pytest.approx(6.0)
    assert row["total_mbps"] == pytest.approx(16.0)
    # 没有容量：利用率必须是 null（页面显示「待补全 / —」），不得按 0 算
    assert row["capacity_mbps"] is None and row["utilization_pct"] is None


def test_five_minute_window_is_five_minutes():
    assert dashboard.WINDOWS["5m"]["seconds"] == 300
    assert dashboard.WINDOW_LABELS["5m"] == "近 5 分钟"


def test_trend_series_aggregates_total_channel(db):
    now = datetime.utcnow().replace(second=0, microsecond=0)
    with db() as session:
        _add_sensor(session, 2721, "SNMP Traffic 64bit", "a")
        _add_sensor(session, 2722, "SNMP Traffic 64bit", "b")
        for sensor_id in (2721, 2722):
            session.add(PrtgChannelSample(
                sensor_objid=sensor_id, channel_objid=-1, sample_time=now,
                value_num=1_000_000.0, collected_at=now,
            ))
        session.commit()
    series = dashboard.trend_series("5m")
    assert len(series) == 1
    assert series[0]["total_mbps"] == pytest.approx(16.0)  # 两条各 8 Mbps


def test_overview_counts_only_warning_or_critical_composites(db):
    from backend.models.prtg_collector import NetworkAnomalyEvent

    now = datetime.utcnow().replace(second=0, microsecond=0)
    with db() as session:
        _add_sensor(session, 2721, "SNMP Traffic 64bit", "a")
        session.add(NetworkAnomalyEvent(
            sensor_objid=2721, rule_code="composite", severity="warning",
            message="warn", started_at=now, created_at=now,
        ))
        session.add(NetworkAnomalyEvent(
            sensor_objid=2721, rule_code="baseline_low_pending_review", severity="info",
            message="info", started_at=now, created_at=now,
        ))
        session.commit()
    assert dashboard.overview("24h")["kpi"]["anomaly_links"] == 1


def test_anomalies_lists_rule_label_and_open_state(db):
    from backend.models.prtg_collector import NetworkAnomalyEvent

    now = datetime.utcnow().replace(second=0, microsecond=0)
    with db() as session:
        _add_sensor(session, 2721, "SNMP Traffic 64bit", "a")
        session.add(NetworkAnomalyEvent(
            sensor_objid=2721, rule_code="zero_traffic", severity="critical",
            message="zero", evidence_json='{"buckets": 2}', started_at=now, created_at=now,
        ))
        session.commit()
    result = dashboard.anomalies("24h", open_only=True)
    assert result["count"] == 1
    item = result["anomalies"][0]
    assert item["rule_label"] == "流量归零" and item["is_open"] is True
    assert item["evidence"] == {"buckets": 2}

# -*- coding: utf-8 -*-
"""PRTG SLA 月报与容量利用率测试（Issue #231）。内存 SQLite，不联网。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401
from backend.database import Base
from backend.models.prtg_collector import (
    NetworkLinkCapacity,
    PrtgChannelSample5m,
    PrtgSensor,
    PrtgSensorEvent,
    PrtgSlaDaily,
)
from backend.services.prtg import sla, store


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(sla, "SessionLocal", Session)
    monkeypatch.setattr(store, "SessionLocal", Session)
    return Session


def _sensor(session, objid=2721, **kwargs):
    data = dict(objid=objid, sensor_name=f"s{objid}", sensortype="SNMP Traffic 64bit",
                status="Up", uptime_pct=99.9, downtime_minutes=100.0, active=True)
    data.update(kwargs)
    session.add(PrtgSensor(**data))


def test_parse_prtg_duration_minutes():
    from backend.services.prtg.parsing import parse_prtg_duration_minutes as f

    assert f("956 d") == 956 * 1440
    assert f("28 h 9 m") == 28 * 60 + 9
    assert f("958 d  [=100% coverage]") == 958 * 1440
    assert f("45 s") == 0.75
    assert f("") is None and f("n/a") is None


def test_snapshot_delta_and_month_report(db):
    day1 = datetime(2026, 11, 20, 0, 0)
    day2 = datetime(2026, 11, 21, 0, 0)
    with db() as session:
        _sensor(session, 2721, updown_total_minutes=1000.0)
        session.commit()
    sla.snapshot_day(now=day1)  # 首日：无前序，回退累计 uptime

    with db() as session:
        sensor = session.execute(__import__("sqlalchemy").select(PrtgSensor)).scalar_one()
        sensor.downtime_minutes = 160.0  # 一天停机 60 分钟
        sensor.updown_total_minutes = 2440.0  # 覆盖 +1440
        session.add(PrtgSensorEvent(sensor_objid=2721, event_type="down",
                                    happened_at=day2 + timedelta(hours=3),
                                    created_at=day2))
        session.commit()
    sla.snapshot_day(now=day2)

    with db() as session:
        rows = session.execute(__import__("sqlalchemy").select(PrtgSlaDaily)
                               .order_by(PrtgSlaDaily.day)).scalars().all()
    assert len(rows) == 2
    assert rows[0].downtime_delta_minutes is None
    assert rows[1].downtime_delta_minutes == 60.0
    assert rows[1].availability_pct == pytest.approx(100 - 60 / 1440 * 100, rel=1e-3)
    assert rows[1].down_count == 1

    report = sla.month_report("2026-11", now=day2 + timedelta(hours=1))
    assert report["summary"]["sla_days_recorded"] == 2
    row = report["links"][0]
    assert row["basis"] == "month_delta"
    assert row["downtime_hours_month"] == pytest.approx(1.0)
    assert row["down_count"] == 1


def test_snapshot_is_idempotent_for_same_day(db):
    day = datetime(2026, 11, 20, 0, 0)
    with db() as session:
        _sensor(session, 2721)
        session.commit()
    sla.snapshot_day(now=day)
    sla.snapshot_day(now=day + timedelta(hours=5))
    with db() as session:
        rows = session.execute(__import__("sqlalchemy").select(PrtgSlaDaily)).scalars().all()
    assert len(rows) == 1


def test_capacity_utilization_only_when_capacity_known(db):
    now = datetime(2026, 11, 24, 12, 0)
    with db() as session:
        _sensor(session, 2721)
        _sensor(session, 2722)
        session.add(NetworkLinkCapacity(sensor_objid=2721, capacity_mbps=1000.0,
                                        capacity_source="manual", updated_at=now))
        # 2721：In 300 Mbps、Out 500 Mbps → 50%
        session.add(PrtgChannelSample5m(sensor_objid=2721, channel_objid=0, bucket_time=now,
                                        avg_value=300e6 / 8, samples=1, updated_at=now))
        session.add(PrtgChannelSample5m(sensor_objid=2721, channel_objid=1, bucket_time=now,
                                        avg_value=500e6 / 8, samples=1, updated_at=now))
        # 2722 无容量
        session.add(PrtgChannelSample5m(sensor_objid=2722, channel_objid=0, bucket_time=now,
                                        avg_value=900e6 / 8, samples=1, updated_at=now))
        session.commit()

    rows = {r["sensor_id"]: r for r in sla.capacity_list(now=now)}
    assert rows[2721]["capacity_known"] is True
    assert rows[2721]["utilization_pct"] == pytest.approx(50.0)
    assert rows[2721]["level"] == "ok"
    assert rows[2722]["capacity_known"] is False
    assert rows[2722]["utilization_pct"] is None


def test_below_target_sorted_and_region_ranking(db):
    now = datetime(2026, 11, 24, 12, 0)
    with db() as session:
        _sensor(session, 2721, uptime_pct=99.99)
        _sensor(session, 2724, uptime_pct=88.0)
        session.add(PrtgSensorEvent(sensor_objid=2724, event_type="down",
                                    happened_at=now - timedelta(hours=2)))
        session.commit()
    sla.snapshot_day(now=now)
    report = sla.month_report("2026-11", now=now)
    assert report["links"][0]["sensor_id"] == 2724  # 可用率最低排最前
    assert report["below_target"][0]["sensor_id"] == 2724
    assert report["region_ranking"][0]["region"] == "Unknown"


def test_day_coverage_uses_updown_delta(db):
    """整天有覆盖：分母 = updowntotal 增量（1440），不是自然日兜底。"""
    day1 = datetime(2026, 11, 20, 0, 0)
    day2 = datetime(2026, 11, 21, 0, 0)
    with db() as session:
        _sensor(session, 2721, downtime_minutes=100.0, updown_total_minutes=1000.0)
        session.commit()
    sla.snapshot_day(now=day1)

    with db() as session:
        sensor = session.execute(__import__("sqlalchemy").select(PrtgSensor)).scalar_one()
        sensor.downtime_minutes = 160.0
        sensor.updown_total_minutes = 2440.0  # 覆盖 +1440
        session.commit()
    sla.snapshot_day(now=day2)

    from sqlalchemy import select
    with db() as session:
        row = session.execute(select(PrtgSlaDaily).where(PrtgSlaDaily.day == day2)).scalar_one()
    assert row.coverage_minutes == 1440.0
    assert row.availability_pct == pytest.approx(100 - 60 / 1440 * 100, rel=1e-4)


def test_partial_paused_day_is_undefined_not_full_day(db):
    """PRTG updowntotal 是天粒度：增量 0（半天/整天 Paused）时不能按 1440 当可用。"""
    day1 = datetime(2026, 11, 20, 0, 0)
    day2 = datetime(2026, 11, 21, 0, 0)
    with db() as session:
        _sensor(session, 2721, downtime_minutes=100.0, updown_total_minutes=1000.0)
        session.commit()
    sla.snapshot_day(now=day1)
    with db() as session:
        sensor = session.execute(__import__("sqlalchemy").select(PrtgSensor)).scalar_one()
        sensor.downtime_minutes = 160.0
        sensor.updown_total_minutes = 1000.0  # 没有覆盖增量
        session.commit()
    sla.snapshot_day(now=day2)

    from sqlalchemy import select
    with db() as session:
        row = session.execute(select(PrtgSlaDaily).where(PrtgSlaDaily.day == day2)).scalar_one()
    assert row.coverage_minutes is None
    assert row.availability_pct is None

    report = sla.month_report("2026-11", now=day2 + timedelta(hours=1))
    link = report["links"][0]
    assert link["basis"] == "undefined_coverage"
    assert link["availability_pct"] is None  # 不把累计 uptime 冒充当月值
    assert link["downtime_hours_month"] == pytest.approx(1.0)


def test_multi_day_gap_uses_real_coverage_not_1440(db):
    day1 = datetime(2026, 11, 17, 0, 0)
    day4 = datetime(2026, 11, 20, 0, 0)
    with db() as session:
        _sensor(session, 2721, downtime_minutes=100.0, updown_total_minutes=1000.0)
        session.commit()
    sla.snapshot_day(now=day1)
    with db() as session:
        sensor = session.execute(__import__("sqlalchemy").select(PrtgSensor)).scalar_one()
        sensor.downtime_minutes = 160.0
        sensor.updown_total_minutes = 5320.0
        session.commit()
    sla.snapshot_day(now=day4)
    from sqlalchemy import select
    with db() as session:
        row = session.execute(select(PrtgSlaDaily).where(PrtgSlaDaily.day == day4)).scalar_one()
    assert row.coverage_minutes == 4320.0
    assert row.availability_pct == pytest.approx(100 - 60 / 4320 * 100, rel=1e-4)


def test_month_report_counts_down_events_after_snapshot(db):
    """快照发生在当天 10:00，18:00 的 Down 事件也要计入月报。"""
    day = datetime(2026, 11, 24, 10, 0)
    with db() as session:
        _sensor(session, 2721)
        session.commit()
    sla.snapshot_day(now=day)
    with db() as session:
        session.add(PrtgSensorEvent(sensor_objid=2721, event_type="down",
                                    happened_at=datetime(2026, 11, 24, 18, 0),
                                    created_at=day))
        session.commit()
    report = sla.month_report("2026-11", now=datetime(2026, 11, 24, 23, 0))
    row = report["links"][0]
    assert row["down_count"] == 1


def test_mixed_covered_and_unknown_days_do_not_pollute_denominator(db):
    """混合月：可判定覆盖日的停机才进分母，无覆盖日的停机单列。"""
    d1 = datetime(2026, 11, 20, 0, 0)
    d2 = datetime(2026, 11, 21, 0, 0)
    d3 = datetime(2026, 11, 22, 0, 0)
    with db() as session:
        _sensor(session, 2721, downtime_minutes=100.0, updown_total_minutes=1000.0)
        session.commit()
    sla.snapshot_day(now=d1)
    with db() as session:
        sensor = session.execute(__import__("sqlalchemy").select(PrtgSensor)).scalar_one()
        sensor.downtime_minutes = 160.0
        sensor.updown_total_minutes = 2440.0  # 覆盖 +1440
        session.commit()
    sla.snapshot_day(now=d2)
    with db() as session:
        sensor = session.execute(__import__("sqlalchemy").select(PrtgSensor)).scalar_one()
        sensor.downtime_minutes = 220.0
        sensor.updown_total_minutes = 2440.0  # 无覆盖增量（部分 Paused）
        session.commit()
    sla.snapshot_day(now=d3)

    report = sla.month_report("2026-11", now=d3 + timedelta(hours=1))
    link = report["links"][0]
    assert link["basis"] == "month_delta"
    assert link["availability_pct"] == pytest.approx(100 - 60 / 1440 * 100, rel=1e-4)
    assert link["downtime_hours_month"] == pytest.approx(1.0)  # 只含可判定日
    assert link["downtime_hours_unknown_coverage"] == pytest.approx(1.0)
    assert link["unknown_coverage_days"] == 1

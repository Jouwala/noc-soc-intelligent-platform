# -*- coding: utf-8 -*-
"""repair_live_scale 计划逻辑单测（内存 SQLite，不写生产）。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401
from backend.database import Base
from backend.models.prtg_collector import PrtgChannelSample5m, PrtgHistoricalSample
from backend.services.prtg import repair_live_scale


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(repair_live_scale, "SessionLocal", Session)
    return Session


def test_plan_5m_repairs_uses_per_row_hist_factor(db):
    bucket = datetime(2026, 11, 24, 16, 40, 0)
    with db() as session:
        session.add(PrtgChannelSample5m(
            sensor_objid=2565, channel_objid=-1, bucket_time=bucket,
            avg_value=800_000_000.0, max_value=800_000_000.0,
            avg_volume=48_000_000_000.0, max_volume=48_000_000_000.0,
            samples=3, updated_at=bucket,
        ))
        for i in range(5):
            session.add(PrtgHistoricalSample(
                sensor_objid=2565, channel_objid=-1,
                sample_time=bucket + timedelta(minutes=i),
                value_num=80_000_000.0, volume_num=4_800_000_000.0,
                collected_at=bucket,
            ))
        session.commit()
        plan = repair_live_scale.plan_5m_repairs(session, until=datetime(2026, 11, 25))
    repairs = [r for r in plan if r.action == "repair"]
    assert len(repairs) == 1
    assert abs(repairs[0].factor - 0.1) < 0.01
    assert abs(repairs[0].new_value - 80_000_000.0) < 1.0

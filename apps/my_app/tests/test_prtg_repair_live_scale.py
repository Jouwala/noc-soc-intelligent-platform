# -*- coding: utf-8 -*-
"""repair_live_scale 计划逻辑单测（内存 SQLite，不写生产）。"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401
from backend.database import Base
from backend.models.prtg_collector import (
    NetworkAnomalyEvent,
    PrtgChannelSample5m,
    PrtgHistoricalSample,
)
from backend.services.prtg import anomaly, repair_live_scale


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


def test_close_after_reeval_tags_scale_repair_and_rollback(db, tmp_path, monkeypatch):
    """N2：close_after_reeval 写 closed_by=scale_repair；rollback 恢复 ended_at/evidence。"""
    monkeypatch.setattr(anomaly, "HYSTERESIS_OPEN_BUCKETS", 2)
    now = datetime(2026, 11, 24, 18, 0, 0)
    with db() as session:
        for d in range(1, 8):
            session.add(PrtgChannelSample5m(
                sensor_objid=2565, channel_objid=-1,
                bucket_time=now - timedelta(days=d),
                avg_value=80_000_000.0, samples=1, updated_at=now,
            ))
        # 当前已恢复到正常量级（模拟 apply 后）
        session.add(PrtgChannelSample5m(
            sensor_objid=2565, channel_objid=-1, bucket_time=now,
            avg_value=80_000_000.0, samples=1, updated_at=now,
        ))
        session.add(NetworkAnomalyEvent(
            sensor_objid=2565, rule_code=anomaly.RULE_ABOVE, severity="warning",
            message="stale above", evidence_json=json.dumps({"ratio": 10}),
            started_at=now - timedelta(hours=1), created_at=now - timedelta(hours=1),
        ))
        session.commit()
        snapshots = repair_live_scale.close_after_reeval(session, now)
        assert len(snapshots) == 1
        ev = session.execute(select(NetworkAnomalyEvent)).scalar_one()
        assert ev.ended_at == now
        evid = json.loads(ev.evidence_json)
        assert evid["closed_by"] == "scale_repair"
        assert evid["ratio"] == 10

        backup = Path(tmp_path)
        repair_live_scale.write_backup(backup, [], [], [], closeable_events=snapshots)

    result = repair_live_scale.rollback_from_backup(backup)
    assert result["restored_events"] == 1
    with db() as session:
        ev = session.execute(select(NetworkAnomalyEvent)).scalar_one()
        assert ev.ended_at is None
        assert json.loads(ev.evidence_json) == {"ratio": 10}

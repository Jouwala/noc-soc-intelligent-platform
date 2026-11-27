# -*- coding: utf-8 -*-
"""PRTG 异常规则与 Ping 状态机测试（Issue #229）。内存 SQLite，不联网。"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401
from backend.database import Base
from backend.models.prtg_collector import (
    NetworkAnomalyEvent,
    PrtgChannelSample5m,
    PrtgPingState,
    PrtgSensor,
)
from backend.services.prtg import anomaly, store


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(anomaly, "SessionLocal", Session)
    monkeypatch.setattr(store, "SessionLocal", Session)
    return Session


def _add_5m(session, sensor_id, channel_id, samples):
    for ts, value in samples:
        session.add(PrtgChannelSample5m(
            sensor_objid=sensor_id, channel_objid=channel_id, bucket_time=ts,
            avg_value=value, samples=1, updated_at=ts,
        ))


def _week_samples(base: datetime, per_day: float):
    """过去 7 天、同一分钟档一个值。"""
    return [(base - timedelta(days=d), per_day) for d in range(1, 8)]


def test_baseline_uses_same_slot_across_days():
    now = datetime(2026, 11, 24, 10, 0)
    samples = _week_samples(now, 1000.0) + [(now, 100.0)]
    baseline, days = anomaly.baseline_for(samples, now)
    assert baseline == 1000.0 and days == 7


def test_baseline_requires_three_days():
    now = datetime(2026, 11, 24, 10, 0)
    baseline, days = anomaly.baseline_for([(now - timedelta(days=1), 1000.0)], now)
    assert baseline is None and days == 1


def test_below_baseline_and_low_30min_and_zero():
    now = datetime(2026, 11, 24, 10, 0)
    history = _week_samples(now, 100_000.0)
    recent = [(now - timedelta(minutes=5 * i), 20_000.0) for i in range(6)]
    hits, state = anomaly.evaluate_traffic_sensor(
        sensor_id=1, status="Unusual", total_series=history + recent, error_series=[],
    )
    codes = {h.rule_code for h in hits}
    assert anomaly.RULE_BELOW in codes
    assert anomaly.RULE_LOW_30 in codes
    assert state.startswith("ok")

    zero = [(now - timedelta(minutes=5 * i), 0.0) for i in range(3)]
    hits_zero, _ = anomaly.evaluate_traffic_sensor(
        sensor_id=1, status="Up", total_series=history + zero, error_series=[],
    )
    assert anomaly.RULE_ZERO in {h.rule_code for h in hits_zero}


def test_above_baseline_and_errors_growth():
    now = datetime(2026, 11, 24, 10, 0)
    history = _week_samples(now, 100_000.0)
    # E：需连续 2 桶超基线
    high = [(now - timedelta(minutes=5), 300_000.0), (now, 300_000.0)]
    hits, _ = anomaly.evaluate_traffic_sensor(
        sensor_id=1, status="Up", total_series=history + high,
        error_series=[(now - timedelta(minutes=10), 1.0), (now - timedelta(minutes=5), 2.0), (now, 3.0)],
    )
    codes = {h.rule_code for h in hits}
    assert anomaly.RULE_ABOVE in codes and anomaly.RULE_ERRORS in codes


def test_above_single_bucket_spike_no_hit():
    now = datetime(2026, 11, 24, 10, 0)
    history = _week_samples(now, 100_000.0)
    hits, _ = anomaly.evaluate_traffic_sensor(
        sensor_id=1, status="Up",
        total_series=history + [(now, 300_000.0)], error_series=[],
    )
    assert anomaly.RULE_ABOVE not in {h.rule_code for h in hits}


def test_ping_state_machine_sequence():
    state, f, s = "UNKNOWN", 0, 0
    state, f, s = anomaly.ping_transition(state, f, s, success=True)
    assert (state, f, s) == ("OK", 0, 1)
    for _ in range(1):
        state, f, s = anomaly.ping_transition(state, f, s, success=False)
    assert state == "OK"  # 1 次失败不升级
    state, f, s = anomaly.ping_transition(state, f, s, success=False)
    assert state == "SUSPECT" and f == 2
    state, f, s = anomaly.ping_transition(state, f, s, success=False)
    assert state == "DOWN" and f == 3
    state, f, s = anomaly.ping_transition(state, f, s, success=True)
    assert state == "RECOVERING"
    state, f, s = anomaly.ping_transition(state, f, s, success=True)
    assert state == "OK"


def test_evaluate_all_dedupes_and_closes(db, monkeypatch):
    monkeypatch.setattr(anomaly, "HYSTERESIS_OPEN_BUCKETS", 1)
    monkeypatch.setattr(anomaly, "HYSTERESIS_CLOSE_BUCKETS", 1)
    now = datetime(2026, 11, 24, 10, 0)
    with db() as session:
        session.add(PrtgSensor(objid=2721, sensor_name="t", sensortype="SNMP Traffic 64bit",
                               status="Up", active=True))
        _add_5m(session, 2721, -1, _week_samples(now, 100_000.0) + [(now, 10.0)])
        session.commit()

    stats = anomaly.evaluate_all(now=now)
    assert stats["rule_hits"] >= 1 and stats["events_opened"] >= 1
    with db() as session:
        opened = session.execute(select(NetworkAnomalyEvent)).scalars().all()
        assert any(e.rule_code == "composite" for e in opened)
        assert all(e.ended_at is None for e in opened)
        before = len(opened)

    # 同条件再跑：不新增事件（去重）
    stats2 = anomaly.evaluate_all(now=now + timedelta(minutes=5))
    assert stats2["events_opened"] == 0

    # 恢复正常：未结束事件全部关闭
    with db() as session:
        session.add(PrtgChannelSample5m(
            sensor_objid=2721, channel_objid=-1, bucket_time=now + timedelta(minutes=5),
            avg_value=100_000.0, samples=1, updated_at=now,
        ))
        session.commit()
    anomaly.evaluate_all(now=now + timedelta(minutes=5))
    with db() as session:
        still_open = session.execute(
            select(NetworkAnomalyEvent).where(NetworkAnomalyEvent.ended_at.is_(None))
        ).scalars().all()
    assert still_open == []
    assert before >= 1


def test_ping_transition_writes_event_only_on_change(db):
    now = datetime(2026, 11, 24, 10, 0)
    with db() as session:
        session.add(PrtgSensor(objid=2724, sensor_name="ping", sensortype="Ping",
                               status="Up", active=True))
        _add_5m(session, 2724, 0, [(now, 70.0)])
        session.commit()

    anomaly.evaluate_all(now=now)  # UNKNOWN → OK，无异常事件
    with db() as session:
        assert session.execute(select(NetworkAnomalyEvent)).scalars().all() == []

    # 连续两次失败：第一次不升级，第二次 → SUSPECT（1 次转换，1 条事件）
    with db() as session:
        sensor = session.execute(select(PrtgSensor).where(PrtgSensor.objid == 2724)).scalar_one()
        sensor.status = "Unusual"
        session.commit()
    anomaly.evaluate_all(now=now + timedelta(minutes=5))
    anomaly.evaluate_all(now=now + timedelta(minutes=5))
    with db() as session:
        ping_events = session.execute(
            select(NetworkAnomalyEvent).where(NetworkAnomalyEvent.rule_code == "ping_state")
        ).scalars().all()
        row = session.execute(select(PrtgPingState)).scalar_one()
    assert len(ping_events) == 1 and row.state == "SUSPECT"
    assert ping_events[0].message.startswith("Ping 状态 OK → SUSPECT")
    # 转换事件是瞬时事实：立即关闭，不进入「未结束」列表
    assert ping_events[0].ended_at is not None
    with db() as session:
        open_ping = session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.rule_code == "ping_state")
            .where(NetworkAnomalyEvent.ended_at.is_(None))
        ).scalars().all()
        composite = session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.rule_code == "composite")
            .where(NetworkAnomalyEvent.ended_at.is_(None))
        ).scalar_one()
    assert open_ping == []
    assert composite.severity == "warning"

    # 第三次失败 → DOWN：composite 必须升级为 critical
    anomaly.evaluate_all(now=now + timedelta(minutes=15))
    with db() as session:
        row = session.execute(select(PrtgPingState)).scalar_one()
        composite = session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.rule_code == "composite")
            .where(NetworkAnomalyEvent.ended_at.is_(None))
        ).scalar_one()
        ping_events = session.execute(
            select(NetworkAnomalyEvent).where(NetworkAnomalyEvent.rule_code == "ping_state")
        ).scalars().all()
    assert row.state == "DOWN"
    assert composite.severity == "critical"
    assert all(e.ended_at is not None for e in ping_events)


def test_errors_rule_sums_four_channels_per_bucket(db):
    """生产接线：4 个通道必须先按桶求和，再判 3 桶严格上升（审核阻塞 1）。"""
    now = datetime(2026, 11, 24, 10, 0)
    with db() as session:
        session.add(PrtgSensor(objid=2719, sensor_name="t", sensortype="SNMP Traffic 64bit",
                               status="Up", active=True))
        for i in range(3):
            session.add(PrtgChannelSample5m(
                sensor_objid=2719, channel_objid=-1,
                bucket_time=now - timedelta(minutes=5 * (2 - i)),
                avg_value=100_000.0, samples=1, updated_at=now,
            ))
        for channel_id in (10, 11, 12, 13):
            for i, value in enumerate((1.0, 2.0, 3.0)):
                session.add(PrtgChannelSample5m(
                    sensor_objid=2719, channel_objid=channel_id,
                    bucket_time=now - timedelta(minutes=5 * (2 - i)),
                    avg_value=value, samples=1, updated_at=now,
                ))
        session.commit()
    anomaly.evaluate_all(now=now)
    with db() as session:
        rows = session.execute(
            select(NetworkAnomalyEvent).where(NetworkAnomalyEvent.rule_code == "errors_discards_growth")
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].evidence_json


def test_import_protect_suppresses_new_opens(db, monkeypatch):
    monkeypatch.setattr(anomaly, "HYSTERESIS_OPEN_BUCKETS", 1)
    monkeypatch.setattr(anomaly, "_import_protect_active", lambda now: True)
    now = datetime(2026, 11, 24, 10, 0)
    with db() as session:
        session.add(PrtgSensor(objid=2721, sensor_name="t", sensortype="SNMP Traffic 64bit",
                               status="Up", active=True))
        _add_5m(session, 2721, -1, _week_samples(now, 100_000.0) + [
            (now - timedelta(minutes=5), 300_000.0), (now, 300_000.0),
        ])
        session.commit()
    stats = anomaly.evaluate_all(now=now)
    assert stats["import_protect"] is True
    assert stats["events_opened"] == 0
    assert stats["open_suppressed"] >= 1


def test_batch_merge_same_run(db, monkeypatch):
    monkeypatch.setattr(anomaly, "HYSTERESIS_OPEN_BUCKETS", 1)
    monkeypatch.setattr(anomaly, "BATCH_MIN_MEMBERS", 5)
    monkeypatch.setattr(anomaly, "_import_protect_active", lambda now: False)
    now = datetime(2026, 11, 24, 16, 59, 34)
    with db() as session:
        for sid in range(100, 115):
            session.add(PrtgSensor(objid=sid, sensor_name=f"s{sid}",
                                   sensortype="SNMP Traffic 64bit", status="Up", active=True))
            _add_5m(session, sid, -1, _week_samples(now, 100_000.0) + [
                (now - timedelta(minutes=5), 300_000.0), (now, 300_000.0),
            ])
        session.commit()
    anomaly.evaluate_all(now=now)
    with db() as session:
        members = session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.rule_code == anomaly.RULE_ABOVE)
            .where(NetworkAnomalyEvent.ended_at.is_(None))
        ).scalars().all()
        batches = session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.rule_code == f"{anomaly.RULE_BATCH_PREFIX}{anomaly.RULE_ABOVE}")
        ).scalars().all()
    assert len(members) == 15
    assert len(batches) == 1
    evidence = json.loads(batches[0].evidence_json)
    assert evidence["member_count"] == 15


def _seed_above_sensors(session, sids, when, value=300_000.0):
    for sid in sids:
        existing = session.execute(
            select(PrtgSensor).where(PrtgSensor.objid == sid)
        ).scalar_one_or_none()
        if existing is None:
            session.add(PrtgSensor(objid=sid, sensor_name=f"s{sid}",
                                   sensortype="SNMP Traffic 64bit", status="Up", active=True))
        _add_5m(session, sid, -1, _week_samples(when, 100_000.0) + [
            (when - timedelta(minutes=5), value), (when, value),
        ])


def _set_latest(session, sids, when, value, baseline=100_000.0):
    """同一时刻槽写入基线周样本 + 当前值；先删同槽避免 UNIQUE 冲突。"""
    for sid in sids:
        slots = [when - timedelta(days=d) for d in range(0, 8)]
        session.execute(
            delete(PrtgChannelSample5m).where(
                PrtgChannelSample5m.sensor_objid == sid,
                PrtgChannelSample5m.channel_objid == -1,
                PrtgChannelSample5m.bucket_time.in_(slots),
            )
        )
        _add_5m(session, sid, -1, _week_samples(when, baseline) + [(when, value)])


def test_batch_closes_after_two_quiet_rounds(db, monkeypatch):
    """B1：风暴过后 batch 第 2 轮才关；成员行也正常关闭。"""
    monkeypatch.setattr(anomaly, "HYSTERESIS_OPEN_BUCKETS", 1)
    monkeypatch.setattr(anomaly, "HYSTERESIS_CLOSE_BUCKETS", 2)
    monkeypatch.setattr(anomaly, "BATCH_MIN_MEMBERS", 5)
    monkeypatch.setattr(anomaly, "_import_protect_active", lambda now: False)
    now = datetime(2026, 11, 24, 16, 0, 0)
    with db() as session:
        _seed_above_sensors(session, range(100, 105), now)
        session.commit()
    anomaly.evaluate_all(now=now)
    batch_code = f"{anomaly.RULE_BATCH_PREFIX}{anomaly.RULE_ABOVE}"
    with db() as session:
        assert session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.rule_code == batch_code)
            .where(NetworkAnomalyEvent.ended_at.is_(None))
        ).scalars().all()

    t1 = now + timedelta(days=1)
    with db() as session:
        _set_latest(session, range(100, 105), t1, 100_000.0)
        session.commit()
    anomaly.evaluate_all(now=t1)
    with db() as session:
        batch = session.execute(
            select(NetworkAnomalyEvent).where(NetworkAnomalyEvent.rule_code == batch_code)
        ).scalar_one()
        members_open = session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.rule_code == anomaly.RULE_ABOVE)
            .where(NetworkAnomalyEvent.ended_at.is_(None))
        ).scalars().all()
    assert batch.ended_at is None  # 第 1 轮未关
    assert members_open  # 成员也需 2 轮

    t2 = now + timedelta(days=2)
    with db() as session:
        _set_latest(session, range(100, 105), t2, 100_000.0)
        session.commit()
    anomaly.evaluate_all(now=t2)
    with db() as session:
        batch = session.execute(
            select(NetworkAnomalyEvent).where(NetworkAnomalyEvent.rule_code == batch_code)
        ).scalar_one()
        members_open = session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.rule_code == anomaly.RULE_ABOVE)
            .where(NetworkAnomalyEvent.ended_at.is_(None))
        ).scalars().all()
    assert batch.ended_at is not None
    assert members_open == []


def test_batch_miss_one_round_then_recover_keeps_open(db, monkeypatch):
    """B1：只低于 5 一轮又回升 → 不关闭。"""
    monkeypatch.setattr(anomaly, "HYSTERESIS_OPEN_BUCKETS", 1)
    monkeypatch.setattr(anomaly, "HYSTERESIS_CLOSE_BUCKETS", 2)
    monkeypatch.setattr(anomaly, "BATCH_MIN_MEMBERS", 5)
    monkeypatch.setattr(anomaly, "_import_protect_active", lambda now: False)
    now = datetime(2026, 11, 24, 16, 0, 0)
    with db() as session:
        _seed_above_sensors(session, range(200, 205), now)
        session.commit()
    anomaly.evaluate_all(now=now)
    batch_code = f"{anomaly.RULE_BATCH_PREFIX}{anomaly.RULE_ABOVE}"

    t1 = now + timedelta(days=1)
    with db() as session:
        for sid in range(200, 205):
            val = 300_000.0 if sid < 202 else 100_000.0
            _set_latest(session, [sid], t1, val)
        session.commit()
    anomaly.evaluate_all(now=t1)
    with db() as session:
        batch = session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.rule_code == batch_code)
            .where(NetworkAnomalyEvent.ended_at.is_(None))
        ).scalar_one()
    assert batch.ended_at is None

    t2 = now + timedelta(days=2)
    with db() as session:
        _set_latest(session, range(200, 205), t2, 300_000.0)
        session.commit()
    anomaly.evaluate_all(now=t2)
    with db() as session:
        batch = session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.rule_code == batch_code)
            .where(NetworkAnomalyEvent.ended_at.is_(None))
        ).scalar_one()
        assert json.loads(batch.evidence_json)["member_count"] == 5


def test_batch_reopens_after_close(db, monkeypatch):
    """B1：关闭后再风暴 → 新开一条 batch。"""
    monkeypatch.setattr(anomaly, "HYSTERESIS_OPEN_BUCKETS", 1)
    monkeypatch.setattr(anomaly, "HYSTERESIS_CLOSE_BUCKETS", 1)
    monkeypatch.setattr(anomaly, "BATCH_MIN_MEMBERS", 5)
    monkeypatch.setattr(anomaly, "_import_protect_active", lambda now: False)
    now = datetime(2026, 11, 24, 16, 0, 0)
    with db() as session:
        _seed_above_sensors(session, range(300, 305), now)
        session.commit()
    anomaly.evaluate_all(now=now)
    t1 = now + timedelta(days=1)
    with db() as session:
        _set_latest(session, range(300, 305), t1, 100_000.0)
        session.commit()
    anomaly.evaluate_all(now=t1)
    batch_code = f"{anomaly.RULE_BATCH_PREFIX}{anomaly.RULE_ABOVE}"
    with db() as session:
        closed = session.execute(
            select(NetworkAnomalyEvent).where(NetworkAnomalyEvent.rule_code == batch_code)
        ).scalars().all()
        assert len(closed) == 1 and closed[0].ended_at is not None

    t2 = now + timedelta(days=2)
    with db() as session:
        _set_latest(session, range(300, 305), t2, 300_000.0)
        session.commit()
    anomaly.evaluate_all(now=t2)
    with db() as session:
        batches = session.execute(
            select(NetworkAnomalyEvent).where(NetworkAnomalyEvent.rule_code == batch_code)
        ).scalars().all()
    assert len(batches) == 2
    assert sum(1 for b in batches if b.ended_at is None) == 1


def test_composite_list_evidence_shape_preserved_on_hysteresis_close(db, monkeypatch):
    """N1：composite evidence 原为 list，迟滞关闭后形状仍为 list。"""
    monkeypatch.setattr(anomaly, "HYSTERESIS_OPEN_BUCKETS", 1)
    monkeypatch.setattr(anomaly, "HYSTERESIS_CLOSE_BUCKETS", 2)
    monkeypatch.setattr(anomaly, "_import_protect_active", lambda now: False)
    now = datetime(2026, 11, 24, 10, 0)
    with db() as session:
        session.add(PrtgSensor(objid=4001, sensor_name="t", sensortype="SNMP Traffic 64bit",
                               status="Up", active=True))
        _add_5m(session, 4001, -1, _week_samples(now, 100_000.0) + [
            (now - timedelta(minutes=5), 300_000.0), (now, 300_000.0),
        ])
        session.commit()
    anomaly.evaluate_all(now=now)
    with db() as session:
        comp = session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.rule_code == anomaly.RULE_COMPOSITE)
            .where(NetworkAnomalyEvent.ended_at.is_(None))
        ).scalar_one()
        before = json.loads(comp.evidence_json)
        assert isinstance(before, list)

    t1 = now + timedelta(days=1)
    with db() as session:
        _set_latest(session, [4001], t1, 100_000.0)
        session.commit()
    anomaly.evaluate_all(now=t1)
    with db() as session:
        comp = session.execute(
            select(NetworkAnomalyEvent).where(NetworkAnomalyEvent.rule_code == anomaly.RULE_COMPOSITE)
        ).scalar_one()
        mid = json.loads(comp.evidence_json)
        assert isinstance(mid, list)
        assert comp.ended_at is None

    t2 = now + timedelta(days=2)
    with db() as session:
        _set_latest(session, [4001], t2, 100_000.0)
        session.commit()
    anomaly.evaluate_all(now=t2)
    with db() as session:
        comp = session.execute(
            select(NetworkAnomalyEvent).where(NetworkAnomalyEvent.rule_code == anomaly.RULE_COMPOSITE)
        ).scalar_one()
        after = json.loads(comp.evidence_json)
    assert comp.ended_at is not None
    assert isinstance(after, list)

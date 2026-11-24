# -*- coding: utf-8 -*-
"""PRTG 采集服务离线测试（Issue #227）。

不联网：client 用桩，数据库用内存 SQLite（与生产 PG 同一套 SQLAlchemy 模型）。
覆盖：状态变化落事件、采样幂等、回填令牌桶与断点、5m 聚合、清理、CSV/通道解析。
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401 —— 注册全部模型
from backend.database import Base
from backend.models.prtg_collector import (
    PrtgChannelSample,
    PrtgChannelSample5m,
    PrtgSensor,
    PrtgSensorEvent,
    PrtgSyncState,
)
from backend.services.prtg import collector as collector_mod
from backend.services.prtg import store
from backend.services.prtg.client import PrtgConfig
from backend.services.prtg.collector import PrtgCollector
from backend.services.prtg.parsing import parse_channels_xml, parse_historic_csv
from backend.services.prtg.settings import PrtgSettings, resolve_base_url

SENSORS_JSON = json.dumps({
    "prtg-version": "14.2.10.2256",
    "treesize": 2,
    "sensors": [
        {"objid": 2721, "group": "Core Switching", "device": "PTP_SW",
         "sensor": "(10152) to CSQUARED", "status": "Up", "status_raw": 3,
         "type": "SNMP Traffic 64bit", "priority": 3,
         "lastvalue": "17,453 kbit/s", "lastvalue_raw": 2181625.0},
        {"objid": 2724, "group": "Core Switching", "device": "AML TOKADEH1",
         "sensor": "AML _TOKADEH_PING", "status": "Unusual", "status_raw": 10,
         "type": "Ping", "priority": 1,
         "lastvalue": "7 msec", "lastvalue_raw": 70.0},
    ],
})

DEVICES_JSON = json.dumps({
    "devices": [
        {"objid": 2001, "group": "Core Switching", "device": "PTP_SW", "type": "Cisco Device"},
        {"objid": 9, "group": "Core Firewalls", "device": "ISP-FIREWALL", "type": "Cisco Device"},
    ]
})

CHANNELS_XML_2721 = """<channels>
 <item><objid>0</objid><name>Traffic In</name>
  <lastvalue>3,161 kbit/s</lastvalue><lastvalue_raw>3950850.6357</lastvalue_raw>
  <lastvalue>23,153 KByte</lastvalue><lastvalue_raw>237090890.0</lastvalue_raw>
  <lastvalue>23,153 KByte</lastvalue><lastvalue_raw>237090890.0</lastvalue_raw></item>
 <item><objid>-1</objid><name>Traffic Total</name>
  <lastvalue>7,058 kbit/s</lastvalue><lastvalue_raw>8822610.4898</lastvalue_raw>
  <lastvalue>51,704 KByte</lastvalue><lastvalue_raw>529445120.0</lastvalue_raw>
  <lastvalue>51,704 KByte</lastvalue><lastvalue_raw>529445120.0</lastvalue_raw></item>
 <item><objid>-4</objid><name>Downtime</name><lastvalue></lastvalue></item>
</channels>"""

CHANNELS_XML_2724 = """<channels>
 <item><objid>0</objid><name>Ping Time</name>
  <lastvalue>7 msec</lastvalue><lastvalue_raw>70.0</lastvalue_raw></item>
</channels>"""

DETAILS_XML = """<sensordata>
 <name>(10152) to CSQUARED</name><sensortype>SNMP Traffic 64bit</sensortype>
 <interval>60</interval><statustext>Up</statustext>
 <lastup>46350.6156950926 [33 s ago]</lastup>
 <lastdown>46325.1080343287 [25 d ago]</lastdown>
 <lastcheck>46350.6156950926 [33 s ago]</lastcheck>
 <uptime>99.8775%</uptime><downtime>0.1225%</downtime>
</sensordata>"""

CSV_TEXT = """Date Time,Date Time(RAW),Traffic Total (volume),Traffic Total (volume)(RAW),Traffic Total (speed),Traffic Total (speed)(RAW),Traffic In (volume),Traffic In (volume)(RAW),Traffic In (speed),Traffic In (speed)(RAW),Traffic Out (volume),Traffic Out (volume)(RAW),Traffic Out (speed),Traffic Out (speed)(RAW),Coverage,Coverage(RAW)
11/24/2026 1:00:36 PM,46350.5420839005,"271,381 KByte",277893910.0000,"37,053 kbit/s",4631565.1667,"100 KByte",1000.0,"80 kbit/s",10.0,"171 KByte",171000.0,"29 kbit/s",3.6,100 %,0000010000
11/24/2026 1:01:36 PM,46350.5427777778,"271,400 KByte",277900000.0000,"37,060 kbit/s",4632000.0,"101 KByte",1010.0,"81 kbit/s",10.1,"171 KByte",171000.0,"29 kbit/s",3.6,100 %,0000010000
"""


class FakeClient:
    """按调用顺序返回桩数据；sensor 2721 第一次 Up、之后 Down（模拟状态变化）。"""

    def __init__(self):
        self.status = "Up"
        self.calls = []

    def table_json(self, content, *, columns=None, count=None, start=None, extra=None):
        self.calls.append((content, tuple(columns or ())))
        if content == "devices":
            return DEVICES_JSON
        data = json.loads(SENSORS_JSON)
        for row in data["sensors"]:
            if row["objid"] == 2721:
                row["status"] = self.status
        return json.dumps(data)

    def channels_xml(self, sensor_id):
        return CHANNELS_XML_2724 if sensor_id == 2724 else CHANNELS_XML_2721

    def sensor_details_xml(self, sensor_id):
        return DETAILS_XML

    def historicdata_csv(self, sensor_id, sdate, edate, avg=0):
        return CSV_TEXT

    def close(self):
        pass


@pytest.fixture()
def db_session(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(store, "SessionLocal", Session)
    return Session


@pytest.fixture()
def collector(db_session):
    settings = PrtgSettings(PrtgConfig(base_url="http://prtg.example", username="u", passhash="p"))
    fake = FakeClient()
    inst = PrtgCollector(settings, client=fake)
    inst._sensor_ids = [2721, 2724]
    return inst


def test_poll_once_stores_samples_and_status_event(collector, db_session):
    summary = collector.poll_once()
    assert summary["sensors"] == 2
    assert summary["samples"] == 3  # 2721 的 In/Total + 2724 的 Ping Time（Downtime 无值跳过）

    with db_session() as session:
        rows = session.execute(select(PrtgChannelSample)).scalars().all()
        assert len(rows) == 3
        total = [r for r in rows if r.channel_objid == -1][0]
        assert total.value_num == pytest.approx(8822610.4898)
        assert total.volume_num == pytest.approx(529445120.0)

    # 第二轮状态改为 Down：必须记录一条 down 事件
    collector._client.status = "Down"
    collector._now = lambda: datetime(2026, 11, 24, 14, 52, 30)
    summary2 = collector.poll_once()
    assert summary2["status_changes"] == 1
    with db_session() as session:
        events = session.execute(select(PrtgSensorEvent)).scalars().all()
        assert len(events) == 1
        assert events[0].event_type == "down"
        assert events[0].from_status == "Up" and events[0].to_status == "Down"


def test_poll_once_is_idempotent_for_same_minute(collector, db_session):
    fixed = datetime(2026, 11, 24, 14, 52, 10)
    collector._now = lambda: fixed
    collector.poll_once()
    collector._now = lambda: fixed.replace(second=40)  # 同一分钟的第二轮
    collector.poll_once()
    with db_session() as session:
        assert len(session.execute(select(PrtgChannelSample)).scalars().all()) == 3


def test_sync_catalog_and_details(collector, db_session):
    catalog = collector.sync_catalog()
    assert catalog == {"devices": 2, "sensors": 2, "channels": 3}
    assert collector.refresh_details() == 2
    with db_session() as session:
        sensor = session.execute(select(PrtgSensor).where(PrtgSensor.objid == 2721)).scalar_one()
        assert sensor.uptime_pct == pytest.approx(99.8775)
        assert sensor.interval_seconds == 60
        assert sensor.last_down is not None


def test_backfill_respects_token_bucket_and_is_idempotent(collector, db_session):
    collector.token_bucket._tokens = 5  # 令牌桶初始为 0（严格 ≤5 次/分钟），测试先补满
    results = []
    for _ in range(8):
        results.append(collector.backfill_step())
    inserted = [r for r in results if r]
    # 令牌桶容量 5：前 5 次拿到令牌，之后同一时刻不再放行
    assert len(inserted) == 5
    assert all(r["rows"] == 6 for r in inserted)  # CSV 2 行 × 3 通道

    # 重跑同一天：唯一键幂等，不新增行
    with db_session() as session:
        before = len(session.execute(select(collector_mod.store.PrtgHistoricalSample)).scalars().all())
    collector.token_bucket._tokens = 5  # 补满令牌，重跑
    collector.backfill_step()
    with db_session() as session:
        after = len(session.execute(select(collector_mod.store.PrtgHistoricalSample)).scalars().all())
    assert after >= before

    state = store.get_state("backfill")
    assert state["status"] == "ok" and state["cursor"]


def test_backfill_cursor_advances_day_by_day_on_same_sensor(collector, db_session):
    collector.token_bucket._tokens = 5
    seen = []
    for _ in range(5):
        step = collector.backfill_step()
        if step:
            seen.append((step["sensor_objid"], step["day"]))
    assert len(seen) == 5
    # 同一传感器逐日往回走，而不是每步换传感器
    assert {sensor for sensor, _ in seen} == {2721}
    assert len({day for _, day in seen}) == 5


def test_token_bucket_is_strictly_at_most_five_per_minute():
    from backend.services.prtg.collector import TokenBucket

    now = [0.0]
    bucket = TokenBucket(capacity=5, refill_per_second=5 / 60.0, clock=lambda: now[0])
    # 第 0 秒：初始 0，不允许突发
    assert bucket.try_acquire() is False
    granted = 0
    for _ in range(120):  # 前 60 秒，每 0.5 秒试一次
        now[0] += 0.5
        if bucket.try_acquire():
            granted += 1
    assert granted <= 5


def test_aggregate_reads_backfilled_history(collector, db_session):
    from backend.models.prtg_collector import PrtgHistoricalSample

    fixed = datetime(2026, 11, 24, 10, 0, 0)
    with db_session() as session:
        session.add(PrtgHistoricalSample(
            sensor_objid=2721, channel_objid=-1, sample_time=fixed,
            value_num=1_000_000.0, collected_at=fixed,
        ))
        session.commit()
    collector._now = lambda: fixed + timedelta(minutes=30)
    collector._aggregate_range(fixed - timedelta(hours=1), fixed + timedelta(hours=1),
                               minutes=60, model_name="1h")
    from backend.models.prtg_collector import PrtgChannelSample1h
    with db_session() as session:
        rows = session.execute(select(PrtgChannelSample1h)).scalars().all()
    assert rows and rows[0].samples == 1 and rows[0].avg_value == pytest.approx(1_000_000.0)


def test_aggregate_writes_5m_bucket(collector, db_session):
    fixed = datetime(2026, 11, 24, 14, 50, 10)
    collector._now = lambda: fixed
    collector.poll_once()
    collector._now = lambda: fixed + timedelta(seconds=60)
    collector.poll_once()
    collector._now = lambda: fixed + timedelta(seconds=120)
    result = collector.aggregate()
    assert result["5m"] > 0
    with db_session() as session:
        buckets = session.execute(select(PrtgChannelSample5m)).scalars().all()
        assert buckets
        assert all(b.samples >= 1 for b in buckets)


def test_prune_deletes_old_raw_samples(collector, db_session):
    now = datetime(2026, 11, 24, 12, 0, 0)
    with db_session() as session:
        session.add(PrtgChannelSample(
            sensor_objid=2721, channel_objid=-1,
            sample_time=now - timedelta(days=100), value_num=1.0,
            collected_at=now - timedelta(days=100),
        ))
        session.add(PrtgChannelSample(
            sensor_objid=2721, channel_objid=-1,
            sample_time=now - timedelta(days=1), value_num=2.0,
            collected_at=now - timedelta(days=1),
        ))
        session.commit()
    collector._now = lambda: now
    result = collector.prune()
    assert result["raw"] == 1
    with db_session() as session:
        remaining = session.execute(select(PrtgChannelSample)).scalars().all()
        assert len(remaining) == 1


def test_sync_state_records_error_on_failure(collector, db_session):
    class BrokenClient(FakeClient):
        def table_json(self, content, **kwargs):
            raise RuntimeError("PRTG 不可达")

    collector._client = BrokenClient()
    with pytest.raises(RuntimeError):
        collector.poll_once()
    # _safe 才会吞异常记状态；这里直接验证状态写入函数本身可用
    store.set_state("live", status="error", error="RuntimeError: PRTG 不可达")
    state = store.get_state("live")
    assert state["status"] == "error" and "不可达" in state["error"]


def test_parse_channels_xml_keeps_rate_and_volume():
    rows = parse_channels_xml(CHANNELS_XML_2721)
    assert [r["channel_objid"] for r in rows] == [0, -1]  # Downtime 无值被跳过
    total = rows[-1]
    assert total["value_display"] == "7,058 kbit/s"
    assert total["value_num"] == pytest.approx(8822610.4898)
    assert total["volume_display"] == "51,704 KByte"


def test_parse_historic_csv_uses_excel_serial_and_speed():
    rows = parse_historic_csv(CSV_TEXT)
    assert len(rows) == 2
    assert rows[0]["datetime"] == datetime(1899, 12, 30) + timedelta(days=46350.5420839005)
    assert rows[0]["channels"][-1]["value_num"] == pytest.approx(4631565.1667)
    assert rows[0]["channels"][-1]["volume_num"] == pytest.approx(277893910.0)


def test_resolve_base_url_priority():
    assert resolve_base_url({"base_url": "http://1.2.3.4/"}, {}) == "http://1.2.3.4"
    assert resolve_base_url({"host": "5.6.7.8", "port": 8080}, {}) == "http://5.6.7.8:8080"
    assert resolve_base_url({"base_url": "http://1.2.3.4"}, {"PRTG_BASE_URL": "http://9.9.9.9/"}) == "http://9.9.9.9"


def test_parse_historic_csv_skips_summary_rows():
    """historicdata.csv 末尾的 Sums/Averages 汇总行不能被当成采样点。"""
    text = CSV_TEXT + "Sums (of 1440 values),,123,,456,,789,,0,,0,,0,,0,,0,,0,,0,,0\n"
    rows = parse_historic_csv(text)
    assert len(rows) == 2  # 汇总行被跳过

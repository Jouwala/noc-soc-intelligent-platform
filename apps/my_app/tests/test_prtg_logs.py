# -*- coding: utf-8 -*-
"""PRTG 日志采集与分类离线测试（P0-1 / P0-2，2026-11-26）。

不联网：client 用桩，数据库用内存 SQLite。消息原文全部取自 2026-11-26
只读摸底（TMP/prtg_survey/out/prtg_messages_20261126_0910.csv）的真实样例。
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401 —— 注册全部模型
import backend.models.prtg_logs  # noqa: F401
from backend.database import Base
from backend.models.prtg_logs import PrtgLogMessage
from backend.services.prtg import logs, store

EPOCH = datetime(1899, 12, 30)


def serial(dt: datetime) -> float:
    return (dt - EPOCH).total_seconds() / 86400.0


@pytest.mark.parametrize("message, reason", [
    ("Request timed out (ICMP error # 11010)", logs.REASON_ICMP_TIMEOUT),
    ("No response (check: firewalls, routing, snmp settings of device, IPs, SNMP version, "
     "community, passwords etc) (SNMP error # -2003)", logs.REASON_SNMP_NO_RESPONSE),
    ("1 day interval average of 213,373 kbit/s (Traffic Out) is unusually low for this weekday",
     logs.REASON_UNUSUAL_LOW),
    ("1 hour interval average of 752,308 kbit/s (Traffic In) is unusually high for this hour of the week",
     logs.REASON_UNUSUAL_HIGH),
    ("Paused by user", logs.REASON_PAUSED),
    ("6 msec", logs.REASON_VALUE),
    ("1,180 kbit/s", logs.REASON_VALUE),
    ("OK", logs.REASON_OTHER),
    ("", logs.REASON_OTHER),
])
def test_classify_message(message, reason):
    assert logs.classify_message(message) == reason


def test_parse_unusual_structured():
    got = logs.parse_unusual(
        "1 day interval average of 213,373 kbit/s (Traffic Out) is unusually low for this weekday")
    assert got == {
        "window": "1 day", "value": 213373.0, "unit": "kbit/s", "value_mbps": 213.373,
        "channel": "Traffic Out", "direction": "low", "period": "weekday",
    }
    hour = logs.parse_unusual(
        "1 hour interval average of 5 kbit/s (Traffic Out) is unusually low for this hour of the week")
    assert hour["window"] == "1 hour" and hour["period"] == "hour of the week"
    assert logs.parse_unusual("OK") is None


def test_parse_value_and_strip_html():
    assert logs.parse_value("1,180 kbit/s") == (1180.0, "kbit/s")
    assert logs.parse_value("6 msec") == (6.0, "msec")
    assert logs.parse_value("OK") == (None, None)
    assert logs.strip_html('<div class="status">OK<div class="moreicon"></div></div>') == "OK"


def test_normalize_row_time_and_dedup():
    dt = datetime(2026, 11, 2, 10, 27, 33)
    row = {
        "objid": 2574, "datetime_raw": serial(dt), "parent": "NEXUS", "type": "SNMP Traffic 64bit",
        "name": "PEER-LINK", "status": "Down", "status_raw": 5,
        "message": '<div class="status">No response (SNMP error # -2003)</div>',
    }
    a = logs.normalize_row(row, datetime(2026, 11, 26))
    b = logs.normalize_row(dict(row), datetime(2026, 11, 27))
    assert a["happened_at"] == dt  # 序列日浮点尾差四舍五入到秒
    assert a["reason"] == logs.REASON_SNMP_NO_RESPONSE
    assert a["message"] == "No response (SNMP error # -2003)"
    assert a["dedup_key"] == b["dedup_key"]  # 采集时间不同也是同一行
    assert logs.normalize_row({"objid": 1, "datetime_raw": ""}, datetime(2026, 11, 26)) is None


# ---------------- 采集（内存 SQLite） ----------------

@pytest.fixture()
def db_session(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(store, "SessionLocal", Session)
    return Session


def _msg(i: int, dt: datetime, status="Up", message="6 msec", objid=2724):
    return {"objid": objid, "datetime": dt.strftime("%m/%d/%Y %I:%M:%S %p"), "datetime_raw": serial(dt),
            "parent": "AML TOKADEH1", "type": "Ping", "name": "AML _TOKADEH_PING",
            "status": status, "status_raw": 3 if status == "Up" else 5, "message": f"{message} #{i}"}


class FakeMessagesClient:
    """newest-first 的日志表；按 start/count 切片，超出范围返回空数组（与 PRTG 14 实测一致）。"""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def table_json(self, content, *, columns=None, count=None, start=None, extra=None):
        assert content == "messages"
        start = start or 0
        self.calls.append(start)
        return json.dumps({"treesize": 1000000, "messages": self.rows[start:start + count]})


def _count(Session) -> int:
    with Session() as session:
        return session.execute(select(func.count(PrtgLogMessage.id))).scalar()


def test_first_sync_pulls_until_empty_page(db_session):
    base = datetime(2026, 11, 26, 8, 0, 0)
    rows = [_msg(i, base - timedelta(minutes=i)) for i in range(7)]  # 新→旧
    client = FakeMessagesClient(rows)
    summary = logs.sync_messages(client, base, page_size=3)
    assert summary["inserted"] == 7
    assert summary["stop"] == "last_page"  # 第 3 页只有 1 行
    assert client.calls == [0, 3, 6]
    assert _count(db_session) == 7
    assert store.get_state("messages")["status"] == "ok"


def test_incremental_sync_stops_at_cursor_and_is_idempotent(db_session):
    base = datetime(2026, 11, 26, 8, 0, 0)
    rows = [_msg(i, base - timedelta(minutes=30 * i)) for i in range(9)]
    logs.sync_messages(FakeMessagesClient(rows), base, page_size=3)
    assert _count(db_session) == 9

    newer = [_msg(100 + i, base + timedelta(minutes=5 - i)) for i in range(2)]
    client = FakeMessagesClient(newer + rows)
    summary = logs.sync_messages(client, base + timedelta(minutes=6), page_size=3)
    assert summary["inserted"] == 2  # 重叠区的旧行按 dedup_key 跳过
    assert summary["stop"] == "cursor"
    # 第一页最老一行（= 已入库最新）还在 10 分钟重叠区内，翻第二页；第二页最老一行早于重叠区，停
    assert client.calls == [0, 3]
    assert _count(db_session) == 11


def test_max_pages_cap(db_session):
    base = datetime(2026, 11, 26, 8, 0, 0)
    rows = [_msg(i, base - timedelta(minutes=i)) for i in range(50)]
    client = FakeMessagesClient(rows)
    summary = logs.sync_messages(client, base, page_size=3, max_pages=4)
    assert summary["stop"] == "max_pages"
    assert client.calls == [0, 3, 6, 9]
    assert summary["inserted"] == 12


def test_prune_respects_retention(db_session):
    now = datetime(2026, 11, 26)
    rows = [_msg(1, now - timedelta(days=800)), _msg(2, now - timedelta(days=10))]
    logs.sync_messages(FakeMessagesClient(rows), now, page_size=10)
    assert logs.prune(now, environ={logs.ENV_RETENTION_DAYS: "730"}) == 1
    assert _count(db_session) == 1


def test_compute_sensor_stats_mttr_and_counts():
    now = datetime(2026, 11, 26, 12, 0, 0)
    events = [
        (1, now - timedelta(hours=30), "Down", logs.REASON_SNMP_NO_RESPONSE),
        (1, now - timedelta(hours=29), "Up", logs.REASON_VALUE),            # 60 分钟
        (1, now - timedelta(hours=2), "Down", logs.REASON_ICMP_TIMEOUT),
        (1, now - timedelta(hours=1, minutes=50), "Warning", logs.REASON_ICMP_TIMEOUT),
        (1, now - timedelta(hours=1, minutes=40), "Up", logs.REASON_VALUE),  # 20 分钟
        (2, now - timedelta(hours=1), "Down", logs.REASON_SNMP_NO_RESPONSE),  # 未恢复
    ]
    stats = logs.compute_sensor_stats(events, now)
    assert stats[1] == {"down_count_24h": 1, "icmp_timeouts_24h": 2, "mttr_minutes_7d": 40.0,
                        "recoveries_7d": 2}
    assert stats[2]["mttr_minutes_7d"] is None  # 没有完整 Down→Up 对：null，不是 0


def test_sensor_stats_none_before_first_sync(db_session):
    assert logs.sensor_stats(datetime(2026, 11, 26)) is None
    assert logs.top_noisy_device(datetime(2026, 11, 26)) is None


# ---------------- 看板字段（links / overview） ----------------

@pytest.fixture()
def dash_session(db_session, monkeypatch):
    from backend.services.prtg import dashboard

    monkeypatch.setattr(dashboard, "SessionLocal", db_session)
    return db_session


def test_links_fields_null_before_sync_then_counted(dash_session, monkeypatch):
    from backend.models.prtg_collector import PrtgSensor
    from backend.services.prtg import dashboard

    now = datetime(2026, 11, 26, 9, 0, 0)
    monkeypatch.setattr(dashboard, "sqlite_now", lambda: now)
    with dash_session() as session:
        session.add(PrtgSensor(
            objid=2724, sensor_name="AML _TOKADEH_PING", sensortype="Ping", status="Unusual",
            status_raw=10, active=True,
            last_message="1 hour interval average of 5 kbit/s (Traffic Out) is unusually low for this hour of the week",
        ))
        session.commit()

    row = dashboard.links("24h")["links"][0]
    assert row["down_count_24h"] is None and row["icmp_timeouts_24h"] is None  # 采集没跑过：null，不是 0
    assert row["unusual_reason"]["direction"] == "low"
    assert dashboard.overview("24h")["incidents_7d"] is None

    rows = [
        _msg(1, now - timedelta(hours=2), status="Down", message="Request timed out (ICMP error # 11010)"),
        _msg(2, now - timedelta(hours=1), status="Up", message="6 msec"),
    ]
    rows.reverse()  # PRTG 新→旧
    logs.sync_messages(FakeMessagesClient(rows), now, page_size=10)
    row = dashboard.links("24h")["links"][0]
    assert row["down_count_24h"] == 1
    assert row["icmp_timeouts_24h"] == 1
    assert row["mttr_minutes_7d"] == 60.0
    noisy = dashboard.overview("24h")["top_noisy_device"]
    assert noisy["device_name"] == "AML TOKADEH1" and noisy["share_pct"] == 100.0

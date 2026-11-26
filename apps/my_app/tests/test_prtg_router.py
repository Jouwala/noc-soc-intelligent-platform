# -*- coding: utf-8 -*-
"""PRTG 路由层离线测试（P0 / #302 审核：logs / incidents / collector status）。

不挂鉴权中间件：只挂 ``routers.prtg.router``，用回环 TestClient；
库用内存 SQLite，与 test_prtg_logs / test_prtg_incidents 同一套 fixture 思路。
"""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401
import backend.models.prtg_logs  # noqa: F401
from backend.database import Base
from backend.models.prtg_logs import PrtgIncident, PrtgLogMessage
from backend.routers import prtg as prtg_router
from backend.services.prtg import dashboard, store

NOW = datetime(2026, 11, 26, 12, 0, 0)


@pytest.fixture()
def db_session(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(store, "SessionLocal", Session)
    monkeypatch.setattr(dashboard, "SessionLocal", Session)
    monkeypatch.setattr(dashboard, "sqlite_now", lambda: NOW)
    return Session


@pytest.fixture()
def client(db_session):
    app = FastAPI()
    app.include_router(prtg_router.router)
    # 回环 client：与工程守卫约定一致（即使本 app 未挂鉴权中间件）
    return TestClient(app, client=("127.0.0.1", 12345))


def test_logs_invalid_reason_400(client):
    resp = client.get("/api/prtg/logs", params={"reason": "not-a-reason"})
    assert resp.status_code == 400
    assert "reason" in resp.json()["detail"]


def test_logs_empty_before_sync(client):
    resp = client.get("/api/prtg/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_data"] is False
    assert body["logs"] == []
    assert body["count"] == 0


def test_logs_filter_order_and_limit(client, db_session):
    base = NOW - timedelta(hours=3)
    with db_session() as session:
        for i, (sid, reason, msg) in enumerate([
            (100, "icmp_timeout", "Request timed out"),
            (100, "value", "6 msec"),
            (200, "icmp_timeout", "Request timed out"),
            (100, "snmp_no_response", "No response"),
        ]):
            t = base + timedelta(minutes=i)
            session.add(PrtgLogMessage(
                sensor_objid=sid, device_name="D", sensor_name="s", sensor_type="Ping",
                happened_at=t, status="Down", reason=reason, message=msg,
                dedup_key=f"k{i}", collected_at=NOW,
            ))
        session.commit()
    store.set_state("messages", status="ok", last_run_at=NOW, last_success_at=NOW)

    resp = client.get("/api/prtg/logs", params={"sensor_id": 100, "reason": "icmp_timeout", "limit": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_data"] is True
    assert body["count"] == 1
    assert body["logs"][0]["sensor_id"] == 100
    assert body["logs"][0]["reason"] == "icmp_timeout"

    resp2 = client.get("/api/prtg/logs", params={"sensor_id": 100, "limit": 2})
    assert resp2.status_code == 200
    rows = resp2.json()["logs"]
    assert len(rows) == 2
    # 新→旧
    assert rows[0]["happened_at"] >= rows[1]["happened_at"]


def test_incidents_bad_window_422(client):
    resp = client.get("/api/prtg/incidents", params={"window": "bad"})
    assert resp.status_code == 422


def test_incidents_sensor_filter(client, db_session):
    import json

    t0 = NOW - timedelta(days=1)
    with db_session() as session:
        session.add(PrtgIncident(
            device_name="DEV-A", started_at=t0, ended_at=t0 + timedelta(minutes=2),
            kind="single_link", sensor_count=1, duration_seconds=120,
            sensors_json=json.dumps([{"sensor_objid": 4242, "sensor_name": "x",
                                      "sensor_type": "Ping", "down_at": None, "up_at": None}]),
            evidence_json="{}", created_at=t0, updated_at=t0,
        ))
        session.add(PrtgIncident(
            device_name="DEV-B", started_at=t0 + timedelta(hours=1),
            ended_at=t0 + timedelta(hours=1, minutes=1),
            kind="single_link", sensor_count=1, duration_seconds=60,
            sensors_json=json.dumps([{"sensor_objid": 1111, "sensor_name": "y",
                                      "sensor_type": "Ping", "down_at": None, "up_at": None}]),
            evidence_json="{}", created_at=t0, updated_at=t0,
        ))
        session.commit()
    store.set_state("incidents", status="ok", last_run_at=NOW, last_success_at=NOW)

    resp = client.get("/api/prtg/incidents", params={"window": "7d", "sensor_id": 4242})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_data"] is True
    assert body["count"] == 1
    assert body["incidents"][0]["device_name"] == "DEV-A"
    assert {s["sensor_id"] for s in body["incidents"][0]["sensors"]} == {4242}


def test_collector_status_includes_messages_and_incidents(client):
    store.set_state("messages", status="ok", last_run_at=NOW, last_success_at=NOW, detail="n=1")
    store.set_state("incidents", status="ok", last_run_at=NOW, last_success_at=NOW, detail="n=1")
    resp = client.get("/api/prtg/collector/status")
    assert resp.status_code == 200
    keys = {row["state_key"] for row in resp.json()["collector"]}
    assert "messages" in keys and "incidents" in keys
    by_key = {row["state_key"]: row for row in resp.json()["collector"]}
    assert by_key["messages"]["status"] == "ok"
    assert by_key["incidents"]["status"] == "ok"

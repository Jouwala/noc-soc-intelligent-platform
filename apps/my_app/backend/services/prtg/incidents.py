# -*- coding: utf-8 -*-
"""设备级事件合并（P0-3，2026-11-26）。

问题：一台设备出事，PRTG 会让它下面的每个传感器各自 Down 一次。摸底实测
2026-11-02 10:26:57–10:27:33，NEXUS 3172 的 16 个传感器约每 3 秒依次 Down；
按传感器计会被当成 16 条链路故障。这里把它们合并成**一条设备事件**。

规则（纯函数 ``build_incidents``，单测覆盖）：
  1. 只看 ``prtg_log_messages``（秒级精度；状态快照只到分钟，排不出先后）；
  2. 同一设备上，新的 Down 满足任一条件就并入当前事件：
     a) 当前事件还有传感器处于 Down；
     b) 距当前事件最后一次 Down 不超过 ``MERGE_WINDOW_SECONDS``（120 秒，实测 33 秒）；
     否则另起一个事件；
  3. 事件内全部传感器都出现 Up 时事件结束（``ended_at`` = 最后一个 Up）；
     结束后 120 秒内同设备又 Down，视为同一事件重开（抖动），``flaps`` +1；
  4. 类型 ``kind``：
       device_unreachable  事件窗口内同设备有 ICMP 超时（Ping 传感器 Down/Warning），
                           或涉及的传感器本身就是 Ping —— 设备 / 上游不可达；
       single_link         只涉及 1 个传感器且没有 ICMP 佐证；
       snmp_mgmt           多个传感器同时 SNMP 无响应且没有 ICMP 佐证（设备无 Ping 传感器时
                           分不清是管理面还是整机问题，``evidence.has_ping_sensor`` 写明）。
  5. 幂等：按 ``(device_name, started_at)`` upsert，重算不产生重复事件。

日志里没有设备 objid（只有 ``parent`` 设备名），所以事件以设备名为键，
``device_objid`` 从 ``prtg_devices`` 按名补，补不到为 null。

与 ``anomaly.py`` 的规则事件互不替代：看板上「异常链路数」口径不变，
「设备事件」是新增的独立指标，两者不对账。
"""
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import func, or_, select

from backend.models.prtg_collector import PrtgDevice, PrtgSensor
from backend.models.prtg_logs import PrtgIncident, PrtgLogMessage
from backend.services.prtg import store

MERGE_WINDOW_SECONDS = 120
LOAD_LOOKBACK_DAYS = 35  # 读日志的回看窗口
UPSERT_LOOKBACK_DAYS = 30  # 只 upsert 开始时间在这之后的事件（窗口边缘的事件历史不全，不覆盖已存结果）
INCIDENTS_INTERVAL_SECONDS = 300

KIND_DEVICE_UNREACHABLE = "device_unreachable"
KIND_SNMP_MGMT = "snmp_mgmt"
KIND_SINGLE_LINK = "single_link"
KINDS = (KIND_DEVICE_UNREACHABLE, KIND_SNMP_MGMT, KIND_SINGLE_LINK)

REASON_ICMP_TIMEOUT = "icmp_timeout"  # 与 logs.REASON_ICMP_TIMEOUT 一致（避免循环依赖不 import）


@dataclass(frozen=True)
class LogEvent:
    id: int | None
    sensor_objid: int | None
    sensor_name: str | None
    device_name: str | None
    sensor_type: str | None
    happened_at: datetime
    status: str | None
    reason: str | None


def _is_ping(sensor_type: str | None) -> bool:
    return (sensor_type or "").strip().lower() == "ping"


def _is_icmp(event: LogEvent) -> bool:
    return event.reason == REASON_ICMP_TIMEOUT


def _new_incident(device: str, event: LogEvent) -> dict[str, Any]:
    return {
        "device_name": device,
        "started_at": event.happened_at,
        "ended_at": None,
        "last_down_at": event.happened_at,
        "down": set(),
        "sensors": {},
        "log_ids": [],
        "icmp": 0,
        "flaps": 0,
    }


def _finish(cur: dict[str, Any], window: int, ping_devices: set[str]) -> dict[str, Any]:
    sensors = sorted(cur["sensors"].values(), key=lambda s: (s["down_at"], s["sensor_objid"]))
    count = len(sensors)
    any_ping = any(_is_ping(s["sensor_type"]) for s in sensors)
    has_ping_sensor = any_ping or cur["device_name"] in ping_devices
    if cur["icmp"] > 0 or any_ping:
        kind = KIND_DEVICE_UNREACHABLE
    elif count == 1:
        kind = KIND_SINGLE_LINK
    else:
        kind = KIND_SNMP_MGMT
    ended = cur["ended_at"]
    return {
        "device_name": cur["device_name"],
        "started_at": cur["started_at"],
        "ended_at": ended,
        "kind": kind,
        "sensor_count": count,
        "duration_seconds": int((ended - cur["started_at"]).total_seconds()) if ended else None,
        "sensors": [
            {
                "sensor_objid": s["sensor_objid"],
                "sensor_name": s["sensor_name"],
                "sensor_type": s["sensor_type"],
                "down_at": s["down_at"],
                "up_at": s["up_at"],
            }
            for s in sensors
        ],
        "evidence": {
            "log_ids": cur["log_ids"][:100],
            "log_count": len(cur["log_ids"]),
            "icmp_timeouts": cur["icmp"],
            "has_ping_sensor": has_ping_sensor,
            "merge_window_seconds": window,
            "flaps": cur["flaps"],
        },
    }


def build_incidents(events: Iterable[LogEvent], *, merge_window_seconds: int = MERGE_WINDOW_SECONDS,
                    ping_devices: Iterable[str] = ()) -> list[dict[str, Any]]:
    """纯函数：日志事件 → 设备事件列表（按设备、时间升序）。规则见模块 docstring。"""
    ping_set = set(ping_devices)
    window = timedelta(seconds=merge_window_seconds)
    by_device: dict[str, list[LogEvent]] = defaultdict(list)
    for event in events:
        if event.device_name:
            by_device[event.device_name].append(event)

    results: list[dict[str, Any]] = []
    for device in sorted(by_device):
        items = sorted(by_device[device], key=lambda e: (e.happened_at, e.id or 0))
        cur: dict[str, Any] | None = None
        last_icmp_at: datetime | None = None
        for event in items:
            status = (event.status or "").strip().lower()
            t = event.happened_at
            if status == "down" and event.sensor_objid is not None:
                joinable = cur is not None and (bool(cur["down"]) or t - cur["last_down_at"] <= window)
                if not joinable:
                    if cur is not None:
                        results.append(_finish(cur, merge_window_seconds, ping_set))
                    cur = _new_incident(device, event)
                    # 事件开始前 120 秒内的 ICMP 超时（Warning 先于 Down）也算佐证
                    if last_icmp_at is not None and t - last_icmp_at <= window:
                        cur["icmp"] += 1
                elif cur["ended_at"] is not None:
                    cur["ended_at"] = None  # 结束后 120 秒内又 Down：同一事件重开
                    cur["flaps"] += 1
                sid = event.sensor_objid
                slot = cur["sensors"].get(sid)
                if slot is None:
                    cur["sensors"][sid] = {
                        "sensor_objid": sid,
                        "sensor_name": event.sensor_name,
                        "sensor_type": event.sensor_type,
                        "down_at": t,
                        "up_at": None,
                    }
                elif sid not in cur["down"]:
                    slot["up_at"] = None  # 同一事件内恢复后再次 Down，保留首次 down_at
                cur["down"].add(sid)
                cur["last_down_at"] = t
                if _is_icmp(event):
                    cur["icmp"] += 1
                if event.id is not None:
                    cur["log_ids"].append(event.id)
                continue

            if _is_icmp(event):
                last_icmp_at = t
            if cur is None:
                continue
            in_scope = bool(cur["down"]) or t - cur["last_down_at"] <= window
            if status == "up" and event.sensor_objid in cur["down"]:
                cur["down"].discard(event.sensor_objid)
                cur["sensors"][event.sensor_objid]["up_at"] = t
                if not cur["down"]:
                    cur["ended_at"] = t
                if event.id is not None:
                    cur["log_ids"].append(event.id)
            elif _is_icmp(event) and in_scope:
                cur["icmp"] += 1
                if event.id is not None:
                    cur["log_ids"].append(event.id)
        if cur is not None:
            results.append(_finish(cur, merge_window_seconds, ping_set))
    return results


# ---------------- 持久化 ----------------

def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    raise TypeError(type(value).__name__)


def _load_events(since: datetime) -> list[LogEvent]:
    with store.SessionLocal() as session:
        rows = session.execute(
            select(PrtgLogMessage)
            .where(PrtgLogMessage.happened_at >= since)
            .where(or_(
                PrtgLogMessage.status.in_(("Down", "Up", "down", "up")),
                PrtgLogMessage.reason == REASON_ICMP_TIMEOUT,
            ))
            .order_by(PrtgLogMessage.happened_at, PrtgLogMessage.id)
        ).scalars().all()
    return [
        LogEvent(r.id, r.sensor_objid, r.sensor_name, r.device_name, r.sensor_type,
                 r.happened_at, r.status, r.reason)
        for r in rows
    ]


def _ping_devices() -> set[str]:
    with store.SessionLocal() as session:
        rows = session.execute(
            select(PrtgSensor.device_name).where(func.lower(PrtgSensor.sensortype) == "ping")
        ).all()
    return {name for (name,) in rows if name}


def _device_ids() -> dict[str, int]:
    with store.SessionLocal() as session:
        rows = session.execute(select(PrtgDevice.device_name, PrtgDevice.objid)).all()
    return {name: int(objid) for name, objid in rows if name}


def correlate(now: datetime, *, merge_window_seconds: int = MERGE_WINDOW_SECONDS) -> dict[str, Any]:
    """从日志重算近 30 天的设备事件并幂等 upsert。"""
    events = _load_events(now - timedelta(days=LOAD_LOOKBACK_DAYS))
    incidents = build_incidents(events, merge_window_seconds=merge_window_seconds,
                                ping_devices=_ping_devices())
    upsert_since = now - timedelta(days=UPSERT_LOOKBACK_DAYS)
    device_ids = _device_ids()
    rows = []
    for inc in incidents:
        if inc["started_at"] < upsert_since:
            continue
        rows.append({
            "device_name": inc["device_name"],
            "device_objid": device_ids.get(inc["device_name"]),
            "started_at": inc["started_at"],
            "ended_at": inc["ended_at"],
            "kind": inc["kind"],
            "sensor_count": inc["sensor_count"],
            "duration_seconds": inc["duration_seconds"],
            "sensors_json": json.dumps(inc["sensors"], ensure_ascii=False, default=_json_default),
            "evidence_json": json.dumps(inc["evidence"], ensure_ascii=False, default=_json_default),
            "created_at": now,
            "updated_at": now,
        })
    with store.SessionLocal() as session:
        store.upsert(
            session, PrtgIncident, rows, ["device_name", "started_at"],
            ["device_objid", "ended_at", "kind", "sensor_count", "duration_seconds",
             "sensors_json", "evidence_json", "updated_at"],
        )
        session.commit()
    open_count = sum(1 for r in rows if r["ended_at"] is None)
    summary = {"events": len(events), "incidents": len(rows), "open": open_count}
    store.set_state(
        "incidents", status="ok", last_run_at=now, last_success_at=now, error="",
        detail=" ".join(f"{k}={v}" for k, v in summary.items()),
    )
    return summary


# ---------------- 查询 ----------------

def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value is not None else None


def incident_to_dict(row: PrtgIncident) -> dict[str, Any]:
    sensors = json.loads(row.sensors_json) if row.sensors_json else []
    return {
        "id": row.id,
        "device_name": row.device_name,
        "device_id": row.device_objid,
        "started_at": _iso(row.started_at),
        "ended_at": _iso(row.ended_at),
        "is_open": row.ended_at is None,
        "kind": row.kind,
        "sensor_count": row.sensor_count,
        "duration_seconds": row.duration_seconds,
        "sensors": [
            {
                "sensor_id": s.get("sensor_objid"),
                "sensor_name": s.get("sensor_name"),
                "sensor_type": s.get("sensor_type"),
                "down_at": s.get("down_at"),
                "up_at": s.get("up_at"),
            }
            for s in sensors
        ],
        "evidence": json.loads(row.evidence_json) if row.evidence_json else None,
    }


def query_incidents(since: datetime, *, device_name: str | None = None,
                    sensor_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
    stmt = (
        select(PrtgIncident)
        .where(PrtgIncident.started_at >= since)
        .order_by(PrtgIncident.started_at.desc())
    )
    if device_name:
        stmt = stmt.where(PrtgIncident.device_name == device_name)
    with store.SessionLocal() as session:
        rows = session.execute(stmt.limit(max(1, min(int(limit), 1000)))).scalars().all()
    items = [incident_to_dict(r) for r in rows]
    if sensor_id is not None:
        items = [i for i in items if any(s["sensor_id"] == sensor_id for s in i["sensors"])]
    return items


def summary(now: datetime, days: int = 7) -> dict[str, Any] | None:
    """看板 overview 用：近 N 天事件数、未结束数、按类型计数；从未合并过时返回 None。"""
    if store.latest_success("incidents") is None:
        return None
    since = now - timedelta(days=days)
    with store.SessionLocal() as session:
        rows = session.execute(
            select(PrtgIncident.kind, PrtgIncident.ended_at).where(PrtgIncident.started_at >= since)
        ).all()
    by_kind = {k: 0 for k in KINDS}
    open_count = 0
    for kind, ended_at in rows:
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if ended_at is None:
            open_count += 1
    return {"days": days, "total": len(rows), "open": open_count, "by_kind": by_kind}

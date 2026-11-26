# -*- coding: utf-8 -*-
"""PRTG 采集持久化（Issue #227）。

写入统一走 SQLAlchemy + 方言感知 upsert（SQLite / PostgreSQL 同一份代码）：
  - 目录类（设备/传感器/通道）：按自然键 upsert，保留 first_seen_at；
  - 采样类（60 秒原始 / 回填 / 5m / 1h）：唯一键 ``sensor_objid + channel_objid +
    sample_time`` 幂等，重复投递不产生第二行（#227 验收「回填幂等」）；
  - 同步状态：每类作业一行，记录 last_success_at 供接口算数据新鲜度。

时间一律 naive UTC（PRTG 服务器时区已实测为 UTC）。
"""
from datetime import datetime
from typing import Any, Iterable, Sequence

from sqlalchemy import delete, select

from backend.database import SessionLocal
from backend.models.prtg_collector import (
    PrtgChannelSample,
    PrtgChannelSample1h,
    PrtgChannelSample5m,
    PrtgDevice,
    PrtgHistoricalSample,
    PrtgSensor,
    PrtgSensorChannel,
    PrtgSensorEvent,
    PrtgSensorStatus,
    PrtgSyncState,
)


def _insert(session, model):
    """按方言返回 insert 构造器（PG / SQLite 的 ON CONFLICT 语法同源）。"""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert(model)
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    return sqlite_insert(model)


def upsert(
    session,
    model,
    rows: Sequence[dict[str, Any]],
    conflict_cols: Sequence[str],
    update_cols: Sequence[str] | None = None,
) -> int:
    """批量 upsert；update_cols 为空表示冲突时什么都不改（只插新行）。"""
    if not rows:
        return 0
    ins = _insert(session, model)
    stmt = ins.values(list(rows))
    if update_cols:
        stmt = stmt.on_conflict_do_update(
            index_elements=list(conflict_cols),
            set_={col: getattr(stmt.excluded, col) for col in update_cols},
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=list(conflict_cols))
    session.execute(stmt)
    return len(rows)


# ---------------- 目录 ----------------

def upsert_devices(devices: Iterable[dict[str, Any]], now: datetime) -> int:
    rows = [
        {
            "objid": int(d["objid"]),
            "group_name": d.get("group"),
            "device_name": d.get("device") or "",
            "device_type": d.get("type"),
            "active": True,
            "first_seen_at": now,
            "last_seen_at": now,
        }
        for d in devices
    ]
    with SessionLocal() as session:
        count = upsert(
            session, PrtgDevice, rows, ["objid"],
            ["group_name", "device_name", "device_type", "active", "last_seen_at"],
        )
        session.commit()
    return count


def upsert_sensors(sensors: Iterable[dict[str, Any]], now: datetime) -> int:
    """目录 + 状态 upsert。行里带 message 列（实时轮询）时一并刷新 last_message（P0-2）；
    不带时（目录同步用的列集合没有 message）不碰这两列，避免把原因清空。"""
    # 延迟 import：logs 依赖 store，顶层 import 会循环
    from backend.services.prtg.logs import strip_html

    rows = []
    with_message = False
    for s in sensors:
        row = {
            "objid": int(s["objid"]),
            "group_name": s.get("group"),
            "device_name": s.get("device"),
            "sensor_name": s.get("sensor") or "",
            "sensortype": s.get("type"),
            "status": s.get("status"),
            "status_raw": _to_int(s.get("status_raw")),
            "priority": _to_int(s.get("priority")),
            "last_value": s.get("lastvalue"),
            "last_value_raw": _to_float(s.get("lastvalue_raw")),
            "active": True,
            "first_seen_at": now,
            "last_seen_at": now,
            "updated_at": now,
        }
        if "message" in s or "message_raw" in s:
            with_message = True
            row["last_message"] = strip_html(s.get("message_raw") or s.get("message")) or None
            row["last_message_at"] = now
        rows.append(row)
    update_cols = [
        "group_name", "device_name", "sensor_name", "sensortype",
        "status", "status_raw", "priority", "last_value",
        "last_value_raw", "active", "last_seen_at", "updated_at",
    ]
    if with_message:
        update_cols += ["last_message", "last_message_at"]
        for row in rows:  # 批量 insert 要求每行同一组列
            row.setdefault("last_message", None)
            row.setdefault("last_message_at", now)
    with SessionLocal() as session:
        count = upsert(session, PrtgSensor, rows, ["objid"], update_cols)
        session.commit()
    return count


def upsert_channels(sensor_objid: int, channels: Iterable[dict[str, Any]], now: datetime) -> int:
    rows = []
    for ch in channels:
        channel_id = _to_int(ch.get("objid"))
        if channel_id is None:
            continue
        rows.append(
            {
                "sensor_objid": sensor_objid,
                "channel_objid": channel_id,
                "name": ch.get("name") or str(channel_id),
                "unit": ch.get("unit"),
                "value_kind": ch.get("value_kind"),
                "updated_at": now,
            }
        )
    with SessionLocal() as session:
        count = upsert(
            session, PrtgSensorChannel, rows,
            ["sensor_objid", "channel_objid"],
            ["name", "unit", "value_kind", "updated_at"],
        )
        session.commit()
    return count


# ---------------- 采样 ----------------

def insert_samples(sensor_objid: int, sample_time: datetime,
                   channels: Iterable[dict[str, Any]], collected_at: datetime) -> int:
    """写入一分钟的通道采样；唯一键冲突忽略（同一分钟重复轮询不重复入库）。"""
    rows = []
    for ch in channels:
        rows.append(
            {
                "sensor_objid": sensor_objid,
                "channel_objid": int(ch["channel_objid"]),
                "sample_time": sample_time,
                "value_num": _to_float(ch.get("value_num")),
                "value_display": ch.get("value_display"),
                "volume_num": _to_float(ch.get("volume_num")),
                "volume_display": ch.get("volume_display"),
                "unit": ch.get("unit"),
                "collected_at": collected_at,
            }
        )
    with SessionLocal() as session:
        count = upsert(
            session, PrtgChannelSample, rows,
            ["sensor_objid", "channel_objid", "sample_time"],
        )
        session.commit()
    return count


def insert_historical(rows: Iterable[dict[str, Any]], collected_at: datetime) -> int:
    payload = []
    for r in rows:
        payload.append(
            {
                "sensor_objid": int(r["sensor_objid"]),
                "channel_objid": int(r["channel_objid"]),
                "sample_time": r["sample_time"],
                "value_num": _to_float(r.get("value_num")),
                "value_display": r.get("value_display"),
                "volume_num": _to_float(r.get("volume_num")),
                "volume_display": r.get("volume_display"),
                "source": r.get("source") or "historic",
                "collected_at": collected_at,
            }
        )
    with SessionLocal() as session:
        count = upsert(
            session, PrtgHistoricalSample, payload,
            ["sensor_objid", "channel_objid", "sample_time"],
        )
        session.commit()
    return count


def insert_statuses(rows: Iterable[dict[str, Any]], collected_at: datetime) -> int:
    payload = [
        {
            "sensor_objid": int(r["sensor_objid"]),
            "status": r["status"],
            "status_raw": _to_int(r.get("status_raw")),
            "sampled_at": r["sampled_at"],
            "collected_at": collected_at,
        }
        for r in rows
    ]
    with SessionLocal() as session:
        count = upsert(
            session, PrtgSensorStatus, payload, ["sensor_objid", "sampled_at"],
        )
        session.commit()
    return count


def insert_event(sensor_objid: int, event_type: str, from_status: str | None,
                 to_status: str | None, happened_at: datetime, message: str | None = None) -> None:
    with SessionLocal() as session:
        session.add(PrtgSensorEvent(
            sensor_objid=sensor_objid,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            message=message,
            happened_at=happened_at,
            created_at=datetime.utcnow(),
        ))
        session.commit()


def upsert_aggregates(model, rows: Sequence[dict[str, Any]]) -> int:
    with SessionLocal() as session:
        count = upsert(
            session, model, list(rows),
            ["sensor_objid", "channel_objid", "bucket_time"],
            ["avg_value", "max_value", "avg_volume", "max_volume", "samples", "updated_at"],
        )
        session.commit()
    return count


# ---------------- 同步状态 ----------------

def get_state(state_key: str) -> dict[str, Any] | None:
    with SessionLocal() as session:
        row = session.execute(
            select(PrtgSyncState).where(PrtgSyncState.state_key == state_key)
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "state_key": row.state_key,
            "status": row.status,
            "last_run_at": row.last_run_at,
            "last_success_at": row.last_success_at,
            "cursor": row.cursor,
            "detail": row.detail,
            "error": row.error,
        }


def set_state(state_key: str, *, status: str | None = None,
              last_run_at: datetime | None = None,
              last_success_at: datetime | None = None,
              cursor: str | None = None, detail: str | None = None,
              error: str | None = None) -> None:
    now = datetime.utcnow()
    with SessionLocal() as session:
        row = session.execute(
            select(PrtgSyncState).where(PrtgSyncState.state_key == state_key)
        ).scalar_one_or_none()
        if row is None:
            row = PrtgSyncState(state_key=state_key)
            session.add(row)
        if status is not None:
            row.status = status
        if last_run_at is not None:
            row.last_run_at = last_run_at
        if last_success_at is not None:
            row.last_success_at = last_success_at
        if cursor is not None:
            row.cursor = cursor
        if detail is not None:
            row.detail = detail
        if error is not None:
            row.error = error
        row.updated_at = now
        session.commit()


def current_sensor_statuses() -> dict[int, str | None]:
    with SessionLocal() as session:
        rows = session.execute(select(PrtgSensor.objid, PrtgSensor.status)).all()
    return {int(objid): status for objid, status in rows}


def latest_success(state_key: str) -> datetime | None:
    state = get_state(state_key)
    return state["last_success_at"] if state else None


# ---------------- 采集辅助查询 ----------------

def sensor_objids() -> list[int]:
    with SessionLocal() as session:
        return [int(row[0]) for row in session.execute(select(PrtgSensor.objid)).all()]


def update_sensor_details(sensor_objid: int, fields: dict[str, Any]) -> None:
    """更新传感器详情字段（只更新传入的列，None 不覆盖已有值）。"""
    if not fields:
        return
    payload = {k: v for k, v in fields.items() if v is not None}
    if not payload:
        return
    with SessionLocal() as session:
        session.execute(
            PrtgSensor.__table__.update()
            .where(PrtgSensor.objid == sensor_objid)
            .values(**payload)
        )
        session.commit()


def fetch_raw_samples(since: datetime, until: datetime):
    """取时间窗内的 60 秒原始采样（实时 + 历史回填，按 (sensor, channel, time) 去重）。

    2026-11-24 审核修复：只读 prtg_channel_samples 会让回填的 90 天数据永远
    进不了 5m/1h 聚合，7d/30d 视图看不到历史。这里 UNION 上
    prtg_historical_samples，同一时间点两边都有时只保留一条，避免聚合时
    样本数翻倍。
    """
    live = (
        select(
            PrtgChannelSample.sensor_objid,
            PrtgChannelSample.channel_objid,
            PrtgChannelSample.sample_time,
            PrtgChannelSample.value_num,
            PrtgChannelSample.volume_num,
        )
        .where(PrtgChannelSample.sample_time >= since)
        .where(PrtgChannelSample.sample_time < until)
    )
    hist = (
        select(
            PrtgHistoricalSample.sensor_objid,
            PrtgHistoricalSample.channel_objid,
            PrtgHistoricalSample.sample_time,
            PrtgHistoricalSample.value_num,
            PrtgHistoricalSample.volume_num,
        )
        .where(PrtgHistoricalSample.sample_time >= since)
        .where(PrtgHistoricalSample.sample_time < until)
    )
    deduped: dict[tuple[int, int, datetime], tuple[float | None, float | None]] = {}
    with SessionLocal() as session:
        for sensor_objid, channel_objid, sample_time, value_num, volume_num in session.execute(
            live.union_all(hist)
        ).all():
            deduped[(int(sensor_objid), int(channel_objid), sample_time)] = (value_num, volume_num)
    return [
        (sensor_objid, channel_objid, sample_time, values[0], values[1])
        for (sensor_objid, channel_objid, sample_time), values in deduped.items()
    ]


# ---------------- 清理 ----------------

_SAMPLE_MODELS = {
    "raw": PrtgChannelSample,
    "historic": PrtgHistoricalSample,
}
_BUCKET_MODELS = {
    "5m": PrtgChannelSample5m,
    "1h": PrtgChannelSample1h,
}


def delete_samples_before(kind: str, cutoff: datetime) -> int:
    """删除早于 cutoff 的原始采样（kind: raw / historic）。"""
    model = _SAMPLE_MODELS[kind]
    with SessionLocal() as session:
        result = session.execute(delete(model).where(model.sample_time < cutoff))
        session.commit()
        return result.rowcount or 0


def delete_bucket_before(kind: str, cutoff: datetime) -> int:
    """删除早于 cutoff 的聚合桶（kind: 5m / 1h）。"""
    model = _BUCKET_MODELS[kind]
    with SessionLocal() as session:
        result = session.execute(delete(model).where(model.bucket_time < cutoff))
        session.commit()
        return result.rowcount or 0


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

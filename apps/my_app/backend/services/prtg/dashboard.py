# -*- coding: utf-8 -*-
"""PRTG 看板只读查询（Issue #227 / #228）。

口径（与 #225 字典一致，前端只展示、不重算）：
  - 流量速率取通道 **速率**（value_num，原始 byte/s），Mbps = byte/s × 8 ÷ 1e6；
  - Errors / Discards / Unicast 是**区间速率**（count/s），不做差分；
  - 利用率只在有 capacity_mbps 时算，且按 max(In/Cin, Out/Cout)，
    绝不用 Total/容量（会翻倍）；无容量返回 null，界面显示「待补全」；
  - 数据新鲜度来自 prtg_sync_state.live.last_success_at，过期时界面必须
    标注，不得把旧值当实时值。
"""
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from backend.database import SessionLocal
from backend.models.prtg import PrtgSensorMapping
from backend.models.prtg_collector import (
    NetworkAnomalyEvent,
    NetworkLinkCapacity,
    PrtgChannelSample,
    PrtgChannelSample1h,
    PrtgChannelSample5m,
    PrtgSensor,
    PrtgSensorEvent,
    PrtgSyncState,
)

WINDOWS = {
    # #228 的「5 分钟」是**近 5 分钟**（不是近 1 小时）：60 秒原始 + 5 个点。
    "5m": {"seconds": 300, "table": "raw", "bucket_minutes": 1},
    "24h": {"seconds": 86400, "table": "5m", "bucket_minutes": 5},
    "7d": {"seconds": 7 * 86400, "table": "1h", "bucket_minutes": 60},
    "30d": {"seconds": 30 * 86400, "table": "1h", "bucket_minutes": 60},
}
DEFAULT_WINDOW = "24h"
WINDOW_LABELS = {"5m": "近 5 分钟", "24h": "近 24 小时", "7d": "近 7 天", "30d": "近 30 天"}

CH_TOTAL, CH_IN, CH_OUT = -1, 0, 1


def is_traffic_sensor(sensor_type: str | None) -> bool:
    """Ping 等非 Traffic 传感器不能用 Traffic In/Out 通道语义解释。

    2026-11-24 审核实测：Ping 传感器的 channel 0 是 "Ping Time"（msec），
    当成 Traffic In 会被换算成 0.001 Mbps 显示 0.00，违反「取不到显示 —」。
    """
    return "traffic" in (sensor_type or "").lower()
CH_UNICAST_IN, CH_UNICAST_OUT = 2, 3
CH_ERRORS_IN, CH_ERRORS_OUT = 10, 11
CH_DISCARDS_IN, CH_DISCARDS_OUT = 12, 13

# 表格展示的通道（顺序固定，前端按 id 取）
LINK_CHANNELS = (
    CH_TOTAL, CH_IN, CH_OUT, CH_UNICAST_IN, CH_UNICAST_OUT,
    CH_ERRORS_IN, CH_ERRORS_OUT, CH_DISCARDS_IN, CH_DISCARDS_OUT,
)


def normalize_window(window: str | None) -> str:
    return window if window in WINDOWS else DEFAULT_WINDOW


def byte_per_second_to_mbps(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value * 8.0 / 1_000_000.0, 3)


def sqlite_now() -> datetime:
    return datetime.utcnow()


def _window_config(window: str) -> dict[str, Any]:
    return WINDOWS[normalize_window(window)]


def _ago(now: datetime, window: str) -> datetime:
    return now - timedelta(seconds=_window_config(window)["seconds"])


def _sample_model(table: str):
    if table == "raw":
        return PrtgChannelSample
    if table == "5m":
        return PrtgChannelSample5m
    return PrtgChannelSample1h


def _time_col(model):
    return model.bucket_time if hasattr(model, "bucket_time") else model.sample_time


def _value_col(model):
    return model.avg_value if hasattr(model, "avg_value") else model.value_num


def _sensor_rows() -> list[PrtgSensor]:
    with SessionLocal() as session:
        return list(session.execute(
            select(PrtgSensor).where(PrtgSensor.active.is_(True)).order_by(PrtgSensor.objid)
        ).scalars())


def _mapping_map() -> dict[int, PrtgSensorMapping]:
    with SessionLocal() as session:
        rows = session.execute(select(PrtgSensorMapping)).scalars().all()
    result: dict[int, PrtgSensorMapping] = {}
    for row in rows:
        # 一个传感器可对应多站：概览取第一行（verified 优先），详情接口返回全部
        current = result.get(row.sensor_id)
        if current is None or (row.match_status == "verified" and current.match_status != "verified"):
            result[row.sensor_id] = row
    return result


def _capacity_map() -> dict[int, NetworkLinkCapacity]:
    with SessionLocal() as session:
        rows = session.execute(select(NetworkLinkCapacity)).scalars().all()
    return {row.sensor_objid: row for row in rows}


def _live_state() -> dict[str, Any] | None:
    with SessionLocal() as session:
        row = session.execute(
            select(PrtgSyncState).where(PrtgSyncState.state_key == "live")
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "status": row.status,
            "last_run_at": _iso(row.last_run_at),
            "last_success_at": _iso(row.last_success_at),
            "detail": row.detail,
            "error": row.error,
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value is not None else None


def latest_samples(sensor_ids: list[int] | None = None,
                   lookback_minutes: int = 15) -> dict[tuple[int, int], dict[str, Any]]:
    """取每传感器每通道的最近一条采样（默认回看 15 分钟）。"""
    since = sqlite_now() - timedelta(minutes=lookback_minutes)
    stmt = (
        select(
            PrtgChannelSample.sensor_objid,
            PrtgChannelSample.channel_objid,
            PrtgChannelSample.sample_time,
            PrtgChannelSample.value_num,
            PrtgChannelSample.value_display,
            PrtgChannelSample.volume_num,
        )
        .where(PrtgChannelSample.sample_time >= since)
        .order_by(PrtgChannelSample.sample_time)
    )
    if sensor_ids:
        stmt = stmt.where(PrtgChannelSample.sensor_objid.in_(sensor_ids))
    with SessionLocal() as session:
        rows = session.execute(stmt).all()
    latest: dict[tuple[int, int], dict[str, Any]] = {}
    for sensor_objid, channel_objid, sample_time, value_num, value_display, volume_num in rows:
        latest[(sensor_objid, channel_objid)] = {
            "sample_time": sample_time,
            "value_num": value_num,
            "value_display": value_display,
            "volume_num": volume_num,
        }
    return latest


def trend_series(window: str = DEFAULT_WINDOW, sensor_ids: list[int] | None = None) -> list[dict[str, Any]]:
    """全网速率趋势：每个时间桶对 25 条链路的 Total 速率求和（Mbps）。"""
    window = normalize_window(window)
    cfg = _window_config(window)
    model = _sample_model(cfg["table"])
    now = sqlite_now()
    since = _ago(now, window)
    time_col = _time_col(model)
    value_col = _value_col(model)
    stmt = (
        select(model.sensor_objid, time_col, value_col)
        .where(time_col >= since)
        .where(model.channel_objid == CH_TOTAL)
        .order_by(time_col)
    )
    if sensor_ids:
        stmt = stmt.where(model.sensor_objid.in_(sensor_ids))
    with SessionLocal() as session:
        rows = session.execute(stmt).all()
    buckets: dict[datetime, float] = {}
    for _sensor_objid, bucket_time, value_num in rows:
        if value_num is None:
            continue
        buckets[bucket_time] = buckets.get(bucket_time, 0.0) + value_num
    return [
        {"t": _iso(t), "total_mbps": byte_per_second_to_mbps(total)}
        for t, total in sorted(buckets.items())
    ]


def link_series(sensor_objid: int, window: str = DEFAULT_WINDOW,
                channel_ids: tuple[int, ...] = (CH_TOTAL, CH_IN, CH_OUT)) -> dict[str, list[dict[str, Any]]]:
    """单链路多通道时序（详情抽屉用）。"""
    window = normalize_window(window)
    cfg = _window_config(window)
    model = _sample_model(cfg["table"])
    time_col = _time_col(model)
    value_col = _value_col(model)
    since = _ago(sqlite_now(), window)
    with SessionLocal() as session:
        rows = session.execute(
            select(model.channel_objid, time_col, value_col)
            .where(model.sensor_objid == sensor_objid)
            .where(time_col >= since)
            .where(model.channel_objid.in_(channel_ids))
            .order_by(time_col)
        ).all()
    series: dict[str, list[dict[str, Any]]] = {str(cid): [] for cid in channel_ids}
    for channel_objid, bucket_time, value_num in rows:
        if value_num is None:
            continue
        series[str(channel_objid)].append({"t": _iso(bucket_time), "value": value_num})
    return series


def overview(window: str = DEFAULT_WINDOW) -> dict[str, Any]:
    """KPI 卡：状态计数、平均可用率、异常链路数、数据新鲜度、趋势。"""
    from backend.services.prtg import incidents, logs

    window = normalize_window(window)
    sensors = _sensor_rows()
    now = sqlite_now()
    counts = {"up": 0, "unusual": 0, "down": 0, "unknown": 0}
    uptimes = []
    for sensor in sensors:
        status = (sensor.status or "Unknown").strip().lower()
        if status in counts:
            counts[status] += 1
        else:
            counts["unknown"] += 1
        if sensor.uptime_pct is not None:
            uptimes.append(sensor.uptime_pct)

    with SessionLocal() as session:
        open_anomalies = session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.ended_at.is_(None))
            .where(NetworkAnomalyEvent.rule_code == "composite")
            .where(NetworkAnomalyEvent.severity.in_(("warning", "critical")))
        ).scalars().all()
        pending_review = session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.ended_at.is_(None))
            .where(NetworkAnomalyEvent.rule_code == "baseline_low_pending_review")
        ).scalars().all()
        anomaly_link_ids = {row.sensor_objid for row in open_anomalies}
        pending_ids = {row.sensor_objid for row in pending_review}

    state = _live_state()
    last_success = state["last_success_at"] if state else None
    age_seconds = None
    if last_success:
        try:
            parsed = datetime.fromisoformat(last_success.replace("Z", ""))
            age_seconds = max(0, int((now - parsed).total_seconds()))
        except ValueError:
            age_seconds = None
    return {
        "generated_at": _iso(now),
        "window": window,
        "window_label": WINDOW_LABELS[window],
        "collector": state,
        "data_age_seconds": age_seconds,
        "kpi": {
            "total": len(sensors),
            "up": counts["up"],
            "unusual": counts["unusual"],
            "down": counts["down"],
            "unknown": counts["unknown"],
            "avg_uptime_pct": round(sum(uptimes) / len(uptimes), 4) if uptimes else None,
            # warning/critical open composite 按传感器去重；不含 pending_review / batch_*
            "anomaly_links": len(anomaly_link_ids),
            "anomaly_pending_review_links": len(pending_ids),
            "uptime_samples": len(uptimes),
        },
        "trend": trend_series(window),
        # P0：设备事件 7 天汇总与日志最多的设备；日志采集 / 合并从未成功时为 null（不是 0）。
        # 与 kpi.anomaly_links 是不同口径（事件按设备合并、异常按传感器去重），不对账。
        "incidents_7d": incidents.summary(now, 7),
        "top_noisy_device": logs.top_noisy_device(now, 7),
    }


def links(window: str = DEFAULT_WINDOW) -> dict[str, Any]:
    """25 条链路总览：最新值 + 可用率 + 容量/利用率 + 数据质量。"""
    from backend.services.prtg import logs

    window = normalize_window(window)
    sensors = _sensor_rows()
    mappings = _mapping_map()
    capacities = _capacity_map()
    latest = latest_samples([s.objid for s in sensors])
    now = sqlite_now()
    log_stats = logs.sensor_stats(now)  # None = 日志采集还没成功跑过
    rows = []
    for sensor in sensors:
        mapping = mappings.get(sensor.objid)
        capacity = capacities.get(sensor.objid)
        stat = (log_stats or {}).get(sensor.objid)
        def sample(channel_id: int) -> dict[str, Any] | None:
            return latest.get((sensor.objid, channel_id))

        traffic = is_traffic_sensor(sensor.sensortype)
        in_mbps = byte_per_second_to_mbps(_value(sample(CH_IN))) if traffic else None
        out_mbps = byte_per_second_to_mbps(_value(sample(CH_OUT))) if traffic else None
        total_mbps = byte_per_second_to_mbps(_value(sample(CH_TOTAL))) if traffic else None
        capacity_mbps = capacity.capacity_mbps if capacity else None
        utilization = None
        if (
            traffic and capacity_mbps and capacity_mbps > 0
            and (in_mbps is not None or out_mbps is not None)
        ):
            utilization = round(max((in_mbps or 0) / capacity_mbps, (out_mbps or 0) / capacity_mbps) * 100, 2)
        sample_time = None
        if sample(CH_TOTAL):
            sample_time = sample(CH_TOTAL)["sample_time"]
        rows.append({
            "sensor_id": sensor.objid,
            "sensor_name": sensor.sensor_name,
            "device_name": sensor.device_name,
            "group_name": sensor.group_name,
            "sensor_type": sensor.sensortype,
            "status": sensor.status,
            "status_raw": sensor.status_raw,
            "site_id": mapping.site_id if mapping else "Unknown",
            "region": mapping.region if mapping else None,
            "match_status": mapping.match_status if mapping else "unknown",
            "in_mbps": in_mbps,
            "out_mbps": out_mbps,
            "total_mbps": total_mbps,
            "unicast_in": _value(sample(CH_UNICAST_IN)) if traffic else None,
            "unicast_out": _value(sample(CH_UNICAST_OUT)) if traffic else None,
            "errors_in": _value(sample(CH_ERRORS_IN)) if traffic else None,
            "errors_out": _value(sample(CH_ERRORS_OUT)) if traffic else None,
            "discards_in": _value(sample(CH_DISCARDS_IN)) if traffic else None,
            "discards_out": _value(sample(CH_DISCARDS_OUT)) if traffic else None,
            "uptime_pct": sensor.uptime_pct,
            "downtime_pct": sensor.downtime_pct,
            "last_down": _iso(sensor.last_down),
            "capacity_mbps": capacity_mbps,
            "capacity_source": capacity.capacity_source if capacity else None,
            "utilization_pct": utilization,
            "is_traffic": traffic,
            "sample_time": _iso(sample_time),
            # P0-2：PRTG 当前状态说明；Unusual 时解析出结构化原因（窗口 / 通道 / Mbps / 偏高偏低）
            "last_message": sensor.last_message,
            "unusual_reason": logs.parse_unusual(sensor.last_message),
            # P0-1：日志派生指标。日志采集从未成功 → null；采过但该传感器 7 天内无日志 → 计数 0、MTTR null
            "down_count_24h": None if log_stats is None else (stat["down_count_24h"] if stat else 0),
            "icmp_timeouts_24h": None if log_stats is None else (stat["icmp_timeouts_24h"] if stat else 0),
            "mttr_minutes_7d": stat["mttr_minutes_7d"] if stat else None,
            "data_quality": {
                "traffic_available": total_mbps is not None,
                "capacity_known": bool(capacity_mbps),
                "sample_age_seconds": int((now - sample_time).total_seconds()) if sample_time else None,
            },
        })
    return {
        "generated_at": _iso(now),
        "window": window,
        "window_label": WINDOW_LABELS[window],
        "links": rows,
    }


def link_detail(sensor_objid: int, window: str = DEFAULT_WINDOW) -> dict[str, Any] | None:
    """单链路详情：基础信息 + 全部通道时序 + 最近状态事件 + 全部站映射。"""
    window = normalize_window(window)
    with SessionLocal() as session:
        sensor = session.execute(
            select(PrtgSensor).where(PrtgSensor.objid == sensor_objid)
        ).scalar_one_or_none()
        if sensor is None:
            return None
        mappings = session.execute(
            select(PrtgSensorMapping).where(PrtgSensorMapping.sensor_id == sensor_objid)
        ).scalars().all()
        capacity = session.execute(
            select(NetworkLinkCapacity).where(NetworkLinkCapacity.sensor_objid == sensor_objid)
        ).scalar_one_or_none()
        events = session.execute(
            select(PrtgSensorEvent)
            .where(PrtgSensorEvent.sensor_objid == sensor_objid)
            .order_by(PrtgSensorEvent.happened_at.desc())
            .limit(20)
        ).scalars().all()
        anomalies = session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.sensor_objid == sensor_objid)
            .order_by(NetworkAnomalyEvent.started_at.desc())
            .limit(20)
        ).scalars().all()
    return {
        "sensor_id": sensor.objid,
        "sensor_name": sensor.sensor_name,
        "device_name": sensor.device_name,
        "group_name": sensor.group_name,
        "sensor_type": sensor.sensortype,
        "status": sensor.status,
        "status_raw": sensor.status_raw,
        "interval_seconds": sensor.interval_seconds,
        "uptime_pct": sensor.uptime_pct,
        "downtime_pct": sensor.downtime_pct,
        "last_up": _iso(sensor.last_up),
        "last_down": _iso(sensor.last_down),
        "last_check": _iso(sensor.last_check),
        "is_traffic": is_traffic_sensor(sensor.sensortype),
        "capacity_mbps": capacity.capacity_mbps if capacity else None,
        "capacity_source": capacity.capacity_source if capacity else None,
        "last_message": sensor.last_message,
        "last_message_at": _iso(sensor.last_message_at),
        "unusual_reason": _parse_unusual(sensor.last_message),
        "mappings": [
            {"site_id": row.site_id, "region": row.region, "match_status": row.match_status}
            for row in mappings
        ],
        "anomalies": [
            {
                "rule_code": row.rule_code,
                "severity": row.severity,
                "message": row.message,
                "evidence": json.loads(row.evidence_json) if row.evidence_json else None,
                "started_at": _iso(row.started_at),
                "ended_at": _iso(row.ended_at),
            }
            for row in anomalies
        ],
        "events": [
            {
                "event_type": row.event_type,
                "from_status": row.from_status,
                "to_status": row.to_status,
                "message": row.message,
                "happened_at": _iso(row.happened_at),
            }
            for row in events
        ],
        "series": link_series(
            sensor_objid,
            window,
            (CH_TOTAL, CH_IN, CH_OUT, CH_UNICAST_IN, CH_UNICAST_OUT,
             CH_ERRORS_IN, CH_ERRORS_OUT, CH_DISCARDS_IN, CH_DISCARDS_OUT)
            if is_traffic_sensor(sensor.sensortype) else (0,),
        ),
        "window": window,
        "window_label": WINDOW_LABELS[window],
    }


def _value(sample: dict[str, Any] | None) -> float | None:
    return sample["value_num"] if sample else None


def anomalies(window: str = DEFAULT_WINDOW, *, open_only: bool = True,
              limit: int = 200) -> dict[str, Any]:
    """异常事件列表（#229）：默认只返回未结束事件，附传感器名与规则标签。"""
    window = normalize_window(window)
    now = sqlite_now()
    since = _ago(now, window)
    stmt = (
        select(NetworkAnomalyEvent, PrtgSensor.sensor_name, PrtgSensor.device_name,
               PrtgSensor.sensortype)
        .join(PrtgSensor, PrtgSensor.objid == NetworkAnomalyEvent.sensor_objid)
        .where(NetworkAnomalyEvent.started_at >= since)
        .order_by(NetworkAnomalyEvent.started_at.desc())
        .limit(limit)
    )
    if open_only:
        stmt = stmt.where(NetworkAnomalyEvent.ended_at.is_(None))
    with SessionLocal() as session:
        rows = session.execute(stmt).all()
    labels = {
        "below_baseline_30": "低于 7 天同期基线 30%",
        "zero_traffic": "流量归零",
        "above_baseline_200": "超过基线 200%",
        "errors_discards_growth": "Errors/Discards 持续增长",
        "low_traffic_30min": "连续 30 分钟低流量",
        "ping_state": "Ping 状态机转换",
        "composite": "综合异常",
    }
    items = []
    for event, sensor_name, device_name, sensor_type in rows:
        items.append({
            "id": event.id,
            "sensor_id": event.sensor_objid,
            "sensor_name": sensor_name,
            "device_name": device_name,
            "sensor_type": sensor_type,
            "rule_code": event.rule_code,
            "rule_label": labels.get(event.rule_code, event.rule_code),
            "severity": event.severity,
            "message": event.message,
            "evidence": json.loads(event.evidence_json) if event.evidence_json else None,
            "started_at": _iso(event.started_at),
            "ended_at": _iso(event.ended_at),
            "is_open": event.ended_at is None,
        })
    return {"generated_at": _iso(now), "window": window, "open_only": open_only,
            "count": len(items), "anomalies": items}


def _parse_unusual(message: str | None) -> dict[str, Any] | None:
    from backend.services.prtg import logs

    return logs.parse_unusual(message)

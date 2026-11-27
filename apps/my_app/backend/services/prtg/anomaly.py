# -*- coding: utf-8 -*-
"""PRTG 流量异常规则 + Ping 抖动状态机（Issue #229）。

原则
----
- **不依赖 PRTG 的 Unusual 判定**：门户自建规则，阈值先历史回测再上生产。
- **不写 realtime_alarm / history_alarm**：规则命中写 `network_anomaly_events`，
  同一 (传感器, 规则) 只维护一条未结束事件（去重）；多规则命中再合并成一条
  `composite` 综合事件（事件适配层，供告警收件箱消费）。
- Errors / Discards / Unicast 已是 PRTG 的**区间速率**，不再差分（#225 实测）。

规则（#229 验收 5 条）
---------------------
  1. below_baseline_30  Warning    5 分钟流量 < 7 天同期基线 30%
  2. zero_traffic       Critical   连续 2 个 5 分钟桶流量为 0 且传感器未 Down
  3. above_baseline_200 Warning    5 分钟流量 > 基线 200%
  4. errors_discards_growth Warning 近 3 个 5 分钟桶 Errors+Discards 速率持续上升
  5. low_traffic_30min  Investigate 近 30 分钟均值 < 基线 30%

基线：同一「当天分钟档」在过去 7 天（不含今天）的 5 分钟值中位数；样本 < 3 天
时不下结论（返回空并记 insufficient_baseline），避免上线初期误报。

Ping 状态机
-----------
UNKNOWN → OK → SUSPECT（2 次连续失败）→ DOWN（第 3 次）→ RECOVERING（1 次
成功）→ OK（连续 2 次成功）；失败 = PRTG 状态非 Up 或无 RTT。只有**状态转换**
才写事件，AML TOKADEH1 抖动不再每跳一条。
"""
import json
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select

from backend.database import SessionLocal
from backend.models.prtg_collector import (
    NetworkAnomalyEvent,
    PrtgChannelSample5m,
    PrtgPingState,
    PrtgSensor,
)

logger = logging.getLogger(__name__)

RULE_BELOW = "below_baseline_30"
RULE_ZERO = "zero_traffic"
RULE_ABOVE = "above_baseline_200"
RULE_ERRORS = "errors_discards_growth"
RULE_LOW_30 = "low_traffic_30min"
RULE_PING = "ping_state"
RULE_COMPOSITE = "composite"
RULE_LOW_BASELINE = "baseline_low_pending_review"

RULE_LABELS = {
    RULE_BELOW: "低于 7 天同期基线 30%",
    RULE_ZERO: "流量归零",
    RULE_ABOVE: "超过基线 200%",
    RULE_ERRORS: "Errors/Discards 持续增长",
    RULE_LOW_30: "连续 30 分钟低流量",
    RULE_PING: "Ping 状态机转换",
    RULE_COMPOSITE: "综合异常",
    RULE_LOW_BASELINE: "基线偏低（待核查）",
}

RULE_SEVERITY = {
    RULE_BELOW: "warning",
    RULE_ZERO: "critical",
    RULE_ABOVE: "warning",
    RULE_ERRORS: "warning",
    RULE_LOW_30: "info",
    RULE_PING: "warning",
    RULE_COMPOSITE: "warning",
    RULE_LOW_BASELINE: "info",
}

BASELINE_DAYS = 7
MIN_BASELINE_SAMPLES = 3
ZERO_CONSECUTIVE_BUCKETS = 2
LOW_30_BUCKETS = 6  # 6 × 5 分钟 = 30 分钟
ERROR_GROWTH_BUCKETS = 3

PING_SUSPECT_AFTER = 2
PING_DOWN_AFTER = 3
PING_RECOVER_AFTER = 2

EVENT_ERRORS = (10, 11, 12, 13)

# 7 天 p95 低于 10 kbit/s（1250 byte/s）视为「空闲/基线偏低」链路：
# 只打 info「待核查」，不把流量归零/超基线转成 warning/critical。
# 依据：2026-11-24 实测 25 条中有 7 条 p95=0（OLIB 未使用端口/防火墙备份链路），
# 它们正是 PRTG 里标 Unusual 的那批；#229 明确要求先观察、不直接转高危。
IDLE_P95_BYTES = 1250.0

# C：history_import 后保护期（秒）；期内只更新证据、不开新告警
IMPORT_PROTECT_SECONDS = 600
# E：开/关迟滞（连续 5m 桶数），可配置
HYSTERESIS_OPEN_BUCKETS = 2
HYSTERESIS_CLOSE_BUCKETS = 2
# D：同轮同规则跨传感器批事件
RULE_BATCH_PREFIX = "batch_"
BATCH_SENSOR_OBJID = 0  # 批事件锚点（无实体传感器）
BATCH_MIN_MEMBERS = 5


@dataclass
class Hit:
    rule_code: str
    severity: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return float(ordered[index])


def _minute_of_day(value: datetime) -> int:
    return value.hour * 60 + value.minute


def baseline_for(samples: list[tuple[datetime, float]], bucket_time: datetime) -> tuple[float | None, int]:
    """同一分钟档、过去 BASELINE_DAYS 天（不含今天）的中位数与样本天数。"""
    target = _minute_of_day(bucket_time)
    today = bucket_time.date()
    values: list[float] = []
    days: set = set()
    for ts, value in samples:
        if value is None or ts.date() == today:
            continue
        if abs(_minute_of_day(ts) - target) > 5:  # ±5 分钟档，容忍采集抖动
            continue
        if (today - ts.date()).days > BASELINE_DAYS:
            continue
        values.append(float(value))
        days.add(ts.date())
    if len(days) < MIN_BASELINE_SAMPLES:
        return None, len(days)
    return _median(values), len(days)


def evaluate_traffic_sensor(
    *,
    sensor_id: int,
    status: str | None,
    total_series: list[tuple[datetime, float]],
    error_series: list[tuple[datetime, float]],
) -> tuple[list[Hit], str]:
    """对单个流量传感器跑 5 条规则；返回 (hits, baseline_state)。"""
    if not total_series:
        return [], "no_data"
    total_series = sorted(total_series)
    p95 = _percentile([v for _, v in total_series if v is not None], 0.95)
    latest_time, latest_value = total_series[-1]
    baseline, baseline_days = baseline_for(total_series, latest_time)
    hits: list[Hit] = []

    # 空闲/基线偏低链路：只提示「待核查」，不参与告警分级（#229）
    if p95 is not None and p95 < IDLE_P95_BYTES:
        return [Hit(
            RULE_LOW_BASELINE, "info",
            f"7 天 p95 仅 {p95:.0f} byte/s（约 {p95 * 8 / 1000:.1f} kbit/s），疑似空闲/未使用链路，基线偏低待核查",
            {"p95": p95, "status": status},
        )], "idle"

    if baseline is None or baseline <= 0:
        baseline_state = f"insufficient_baseline({baseline_days}d)"
    else:
        baseline_state = f"ok({baseline_days}d)"
        # E：above/below 需连续 HYSTERESIS_OPEN_BUCKETS 个桶都越线才命中
        if _sustained_threshold(total_series, below=True, factor=0.3,
                                buckets=HYSTERESIS_OPEN_BUCKETS):
            hits.append(Hit(
                RULE_BELOW, "warning",
                f"5 分钟流量 {latest_value:.0f} byte/s，低于 7 天同期基线 {baseline:.0f} 的 30%"
                f"（连续 {HYSTERESIS_OPEN_BUCKETS} 桶）",
                {"current": latest_value, "baseline": baseline,
                 "ratio": round(latest_value / baseline, 4),
                 "hysteresis_open": HYSTERESIS_OPEN_BUCKETS},
            ))
        if _sustained_threshold(total_series, below=False, factor=2.0,
                                buckets=HYSTERESIS_OPEN_BUCKETS):
            hits.append(Hit(
                RULE_ABOVE, "warning",
                f"5 分钟流量 {latest_value:.0f} byte/s，超过 7 天同期基线 {baseline:.0f} 的 200%"
                f"（连续 {HYSTERESIS_OPEN_BUCKETS} 桶）",
                {"current": latest_value, "baseline": baseline,
                 "ratio": round(latest_value / baseline, 4),
                 "hysteresis_open": HYSTERESIS_OPEN_BUCKETS},
            ))
        if len(total_series) >= LOW_30_BUCKETS:
            recent = [v for _, v in total_series[-LOW_30_BUCKETS:] if v is not None]
            if len(recent) == LOW_30_BUCKETS and sum(recent) / len(recent) < baseline * 0.3:
                hits.append(Hit(
                    RULE_LOW_30, "info",
                    "连续 30 分钟平均流量低于 7 天同期基线 30%",
                    {"avg_30m": sum(recent) / len(recent), "baseline": baseline},
                ))

    # 流量归零：近 2 个桶都是 0，且传感器不是 Down（Down 是另一类事件）
    zero_values = [v for _, v in total_series[-ZERO_CONSECUTIVE_BUCKETS:]]
    if (
        len(zero_values) == ZERO_CONSECUTIVE_BUCKETS
        and all(v == 0 for v in zero_values)
        and (status or "").lower() != "down"
        and baseline is not None and baseline > 0
    ):
        hits.append(Hit(
            RULE_ZERO, "critical",
            f"连续 {ZERO_CONSECUTIVE_BUCKETS * 5} 分钟流量为 0（传感器状态 {status or 'Unknown'}）",
            {"buckets": ZERO_CONSECUTIVE_BUCKETS, "status": status},
        ))

    # Errors/Discards 持续增长：近 3 个桶的合计速率严格上升且末值 > 0
    error_series = sorted(error_series)
    if len(error_series) >= ERROR_GROWTH_BUCKETS:
        recent = [v for _, v in error_series[-ERROR_GROWTH_BUCKETS:]]
        if all(a < b for a, b in zip(recent, recent[1:])) and recent[-1] > 0:
            hits.append(Hit(
                RULE_ERRORS, "warning",
                "Errors/Discards 近 15 分钟持续增长",
                {"recent": recent},
            ))

    return hits, baseline_state


def _sustained_threshold(
    total_series: list[tuple[datetime, float]],
    *,
    below: bool,
    factor: float,
    buckets: int,
) -> bool:
    """最近 ``buckets`` 个桶是否都相对各自同期基线越线。"""
    if buckets <= 0 or len(total_series) < buckets:
        return False
    for ts, value in total_series[-buckets:]:
        if value is None:
            return False
        base, _days = baseline_for(total_series, ts)
        if base is None or base <= 0:
            return False
        if below:
            if not (value < base * factor):
                return False
        else:
            if not (value > base * factor):
                return False
    return True


def _load_evidence(row: NetworkAnomalyEvent) -> dict[str, Any]:
    try:
        data = json.loads(row.evidence_json or "{}")
        return data if isinstance(data, dict) else {"_list": data}
    except json.JSONDecodeError:
        return {}


def _upsert_rule_events(session, sensor_id: int, hits: list[Hit], now: datetime,
                        *, allow_close: bool = True,
                        allow_open: bool = True) -> tuple[int, int]:
    """按 (sensor, rule) 维护未结束事件；返回 (opened, closed)。"""
    open_rows = {
        row.rule_code: row
        for row in session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.sensor_objid == sensor_id)
            .where(NetworkAnomalyEvent.ended_at.is_(None))
        ).scalars()
    }
    hit_codes = {hit.rule_code for hit in hits}
    opened = closed = 0
    for hit in hits:
        row = open_rows.get(hit.rule_code)
        evid = dict(hit.evidence)
        evid["miss_streak"] = 0
        if row is None:
            if not allow_open:
                continue
            session.add(NetworkAnomalyEvent(
                sensor_objid=sensor_id,
                rule_code=hit.rule_code,
                severity=hit.severity,
                message=hit.message,
                evidence_json=json.dumps(evid, ensure_ascii=False),
                started_at=now,
                created_at=now,
            ))
            opened += 1
        else:
            row.message = hit.message
            row.evidence_json = json.dumps(evid, ensure_ascii=False)
    for code, row in open_rows.items():
        if code in hit_codes or code == RULE_COMPOSITE or not allow_close:
            continue
        if code.startswith(RULE_BATCH_PREFIX):
            continue
        evid = _load_evidence(row)
        miss = int(evid.get("miss_streak") or 0) + 1
        evid["miss_streak"] = miss
        if miss < HYSTERESIS_CLOSE_BUCKETS:
            row.evidence_json = json.dumps(evid, ensure_ascii=False)
            continue
        row.ended_at = now
        evid["closed_hysteresis"] = HYSTERESIS_CLOSE_BUCKETS
        row.evidence_json = json.dumps(evid, ensure_ascii=False)
        closed += 1

    # 综合事件：把同一传感器的多条证据合成一条，交给事件适配层/收件箱
    composite = open_rows.get(RULE_COMPOSITE)
    if hits:
        payload = [
            {"rule": h.rule_code, "label": RULE_LABELS.get(h.rule_code, h.rule_code),
             "severity": h.severity, "message": h.message, "evidence": h.evidence}
            for h in hits
        ]
        evidence = json.dumps(payload, ensure_ascii=False)
        if composite is None:
            if allow_open:
                session.add(NetworkAnomalyEvent(
                    sensor_objid=sensor_id, rule_code=RULE_COMPOSITE,
                    severity=_worst_severity(hits), message=f"{len(hits)} 条规则命中：" + "、".join(
                        RULE_LABELS.get(h.rule_code, h.rule_code) for h in hits),
                    evidence_json=evidence, started_at=now, created_at=now,
                ))
                opened += 1
        else:
            composite.message = f"{len(hits)} 条规则命中：" + "、".join(
                RULE_LABELS.get(h.rule_code, h.rule_code) for h in hits)
            composite.evidence_json = evidence
            composite.severity = _worst_severity(hits)
    elif composite is not None and allow_close:
        evid = _load_evidence(composite)
        miss = int(evid.get("miss_streak") or 0) + 1
        evid["miss_streak"] = miss
        if miss >= HYSTERESIS_CLOSE_BUCKETS:
            composite.ended_at = now
            composite.evidence_json = json.dumps(evid, ensure_ascii=False)
            closed += 1
        else:
            composite.evidence_json = json.dumps(evid, ensure_ascii=False)
    return opened, closed


def _worst_severity(hits: list[Hit]) -> str:
    order = {"info": 0, "warning": 1, "critical": 2}
    return max((h.severity for h in hits), key=lambda s: order.get(s, 0), default="warning")


# ---------------- Ping 状态机 ----------------

def ping_transition(state: str, failures: int, successes: int, success: bool) -> tuple[str, int, int]:
    """纯函数：给定当前状态与本次成败，返回 (新状态, 连续失败, 连续成功)。"""
    if success:
        successes += 1
        failures = 0
        if state in ("UNKNOWN", "OK"):
            return "OK", failures, successes
        if state == "SUSPECT":
            return "RECOVERING", failures, successes
        if state == "DOWN":
            return "RECOVERING", failures, successes
        if state == "RECOVERING" and successes >= PING_RECOVER_AFTER:
            return "OK", failures, successes
        return state, failures, successes
    failures += 1
    successes = 0
    if state in ("UNKNOWN", "OK", "RECOVERING"):
        if failures >= PING_DOWN_AFTER:
            return "DOWN", failures, successes
        if failures >= PING_SUSPECT_AFTER:
            return "SUSPECT", failures, successes
        return "SUSPECT" if state == "RECOVERING" else state, failures, successes
    if state == "SUSPECT" and failures >= PING_DOWN_AFTER:
        return "DOWN", failures, successes
    return state, failures, successes


def _update_ping_state(session, sensor: PrtgSensor, now: datetime) -> dict[str, Any] | None:
    row = session.execute(
        select(PrtgPingState).where(PrtgPingState.sensor_objid == sensor.objid)
    ).scalar_one_or_none()
    if row is None:
        row = PrtgPingState(sensor_objid=sensor.objid, state="UNKNOWN",
                            consecutive_failures=0, consecutive_successes=0)
        session.add(row)
        session.flush()
    status = (sensor.status or "Unknown").strip()
    rtt = _latest_channel_value(session, sensor.objid, 0)
    success = status.lower() == "up" and rtt is not None
    previous = row.state
    new_state, failures, successes = ping_transition(
        row.state, row.consecutive_failures or 0, row.consecutive_successes or 0, success,
    )
    row.state = new_state
    row.consecutive_failures = failures
    row.consecutive_successes = successes
    row.last_status = status
    row.last_rtt_ms = rtt
    row.updated_at = now
    if new_state != previous and not (previous == "UNKNOWN" and new_state == "OK"):
        row.last_transition_at = now
        severity = "critical" if new_state == "DOWN" else "warning"
        session.add(NetworkAnomalyEvent(
            sensor_objid=sensor.objid, rule_code=RULE_PING, severity=severity,
            message=f"Ping 状态 {previous} → {new_state}（status={status}, rtt={rtt}）",
            evidence_json=json.dumps({
                "from": previous, "to": new_state, "status": status,
                "rtt_ms": rtt, "failures": failures, "successes": successes,
            }, ensure_ascii=False),
            # 转换是**瞬时事实**，立即关闭，避免一次抖动留下 4 条永久未结束事件；
            # 持续性的 SUSPECT/DOWN 由 composite 事件承载。
            started_at=now, ended_at=now, created_at=now,
        ))
        logger.info("PRTG Ping %s 状态转换：%s → %s（status=%s）",
                    sensor.objid, previous, new_state, status)
        return {"sensor_objid": sensor.objid, "from": previous, "to": new_state}
    return None


# ---------------- 总入口 ----------------

def _import_protect_active(now: datetime) -> bool:
    from backend.services.prtg import store

    state = store.get_state("history_import") or {}
    detail = state.get("detail") or ""
    protect_until = None
    if detail.strip().startswith("{"):
        try:
            protect_until = json.loads(detail).get("protect_until")
        except json.JSONDecodeError:
            protect_until = None
    if protect_until:
        try:
            return now < datetime.fromisoformat(str(protect_until).replace("Z", ""))
        except ValueError:
            return False
    last_ok = state.get("last_success_at")
    if not last_ok:
        return False
    try:
        ts = datetime.fromisoformat(str(last_ok).replace("Z", ""))
    except ValueError:
        return False
    return (now - ts).total_seconds() < IMPORT_PROTECT_SECONDS


def _merge_batches(session, now: datetime) -> int:
    """同轮同规则、成员 ≥ BATCH_MIN_MEMBERS → 一条 batch_* 事件（保留成员行）。"""
    opened = session.execute(
        select(NetworkAnomalyEvent)
        .where(NetworkAnomalyEvent.started_at == now)
        .where(NetworkAnomalyEvent.ended_at.is_(None))
        .where(~NetworkAnomalyEvent.rule_code.startswith(RULE_BATCH_PREFIX))
        .where(NetworkAnomalyEvent.rule_code != RULE_COMPOSITE)
    ).scalars().all()
    by_rule: dict[str, list[NetworkAnomalyEvent]] = {}
    for ev in opened:
        by_rule.setdefault(ev.rule_code, []).append(ev)
    created = 0
    for rule, members in by_rule.items():
        if len(members) < BATCH_MIN_MEMBERS:
            continue
        batch_code = f"{RULE_BATCH_PREFIX}{rule}"
        existing = session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.sensor_objid == BATCH_SENSOR_OBJID)
            .where(NetworkAnomalyEvent.rule_code == batch_code)
            .where(NetworkAnomalyEvent.ended_at.is_(None))
        ).scalar_one_or_none()
        payload = {
            "members": [
                {"sensor_objid": m.sensor_objid, "event_id": m.id,
                 "message": m.message, "severity": m.severity}
                for m in members
            ],
            "member_count": len(members),
            "run_at": now.isoformat(),
        }
        msg = f"同轮批量 {rule}：{len(members)} 条链路"
        if existing is None:
            session.add(NetworkAnomalyEvent(
                sensor_objid=BATCH_SENSOR_OBJID, rule_code=batch_code,
                severity=_worst_severity([Hit(rule, m.severity or "warning", "") for m in members]),
                message=msg,
                evidence_json=json.dumps(payload, ensure_ascii=False),
                started_at=now, created_at=now,
            ))
            created += 1
        else:
            existing.message = msg
            existing.evidence_json = json.dumps(payload, ensure_ascii=False)
    return created


def evaluate_all(now: datetime | None = None,
                 session_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    """跑一轮全部规则（建议 5 分钟一次，与 5m 桶对齐）。"""
    now = now or datetime.utcnow()
    factory = session_factory or SessionLocal
    since = now - timedelta(days=BASELINE_DAYS + 2)
    from backend.services.prtg import store

    protect = _import_protect_active(now)
    prev_detail = {}
    try:
        prev_raw = (store.get_state("anomaly") or {}).get("detail") or ""
        if prev_raw.strip().startswith("{"):
            prev_detail = json.loads(prev_raw)
    except json.JSONDecodeError:
        prev_detail = {}
    prev_states = prev_detail.get("baseline_by_sensor") or {}

    stats = {"sensors": 0, "traffic_sensors": 0, "rule_hits": 0,
             "events_opened": 0, "events_closed": 0, "ping_transitions": 0,
             "insufficient_baseline": 0, "import_protect": protect,
             "batches_opened": 0, "open_suppressed": 0}
    baseline_by_sensor: dict[str, str] = {}
    with factory() as session:
        sensors = list(session.execute(
            select(PrtgSensor).where(PrtgSensor.active.is_(True))
        ).scalars())
        stats["sensors"] = len(sensors)
        samples = session.execute(
            select(PrtgChannelSample5m.sensor_objid, PrtgChannelSample5m.channel_objid,
                   PrtgChannelSample5m.bucket_time, PrtgChannelSample5m.avg_value)
            .where(PrtgChannelSample5m.bucket_time >= since)
            .where(PrtgChannelSample5m.bucket_time <= now)
        ).all()

        by_sensor: dict[int, dict[int, list[tuple[datetime, float]]]] = {}
        for sensor_objid, channel_objid, bucket_time, value in samples:
            if value is None:
                continue
            by_sensor.setdefault(int(sensor_objid), {}).setdefault(int(channel_objid), []).append(
                (bucket_time, float(value))
            )

        for sensor in sensors:
            channels = by_sensor.get(sensor.objid, {})
            if _is_traffic(sensor.sensortype):
                stats["traffic_sensors"] += 1
                total = channels.get(-1, [])
                bucket_errors: dict[datetime, float] = {}
                for channel_id in EVENT_ERRORS:
                    for ts, value in channels.get(channel_id, []):
                        bucket_errors[ts] = bucket_errors.get(ts, 0.0) + value
                errors: list[tuple[datetime, float]] = sorted(bucket_errors.items())
                if not errors:
                    errors = [(t, 0.0) for t, _ in total[-ERROR_GROWTH_BUCKETS:]]
                hits, baseline_state = evaluate_traffic_sensor(
                    sensor_id=sensor.objid, status=sensor.status,
                    total_series=total, error_series=errors,
                )
                baseline_by_sensor[str(sensor.objid)] = baseline_state
                if baseline_state.startswith("insufficient"):
                    stats["insufficient_baseline"] += 1
                stats["rule_hits"] += len(hits)
                prev = str(prev_states.get(str(sensor.objid)) or "")
                first_ok = prev.startswith("insufficient") and baseline_state.startswith("ok")
                allow_open = (not protect) and (not first_ok)
                if not allow_open and hits:
                    stats["open_suppressed"] += 1
                opened, closed = _upsert_rule_events(
                    session, sensor.objid, hits, now,
                    allow_close=baseline_state.startswith(("ok", "idle")),
                    allow_open=allow_open,
                )
                stats["events_opened"] += opened
                stats["events_closed"] += closed
            else:
                transition = _update_ping_state(session, sensor, now)
                if transition:
                    stats["ping_transitions"] += 1
                ping_row = session.execute(
                    select(PrtgPingState).where(PrtgPingState.sensor_objid == sensor.objid)
                ).scalar_one_or_none()
                _sync_ping_composite(session, sensor.objid, ping_row, now)
        stats["batches_opened"] = _merge_batches(session, now)
        session.commit()

    stats["baseline_by_sensor"] = baseline_by_sensor
    store.set_state("anomaly", status="ok", last_run_at=now, last_success_at=now,
                    error="", detail=json.dumps(stats, ensure_ascii=False))
    return stats


def _sync_ping_composite(session, sensor_id: int, ping_row: PrtgPingState | None, now: datetime) -> None:
    open_row = session.execute(
        select(NetworkAnomalyEvent)
        .where(NetworkAnomalyEvent.sensor_objid == sensor_id)
        .where(NetworkAnomalyEvent.rule_code == RULE_COMPOSITE)
        .where(NetworkAnomalyEvent.ended_at.is_(None))
    ).scalar_one_or_none()
    abnormal = ping_row is not None and ping_row.state in ("SUSPECT", "DOWN", "RECOVERING")
    if abnormal and open_row is None:
        session.add(NetworkAnomalyEvent(
            sensor_objid=sensor_id, rule_code=RULE_COMPOSITE,
            severity="critical" if ping_row.state == "DOWN" else "warning",
            message=f"Ping 抖动状态机：{ping_row.state}（连续失败 {ping_row.consecutive_failures} 次）",
            evidence_json=json.dumps({"state": ping_row.state,
                                      "failures": ping_row.consecutive_failures,
                                      "rtt_ms": ping_row.last_rtt_ms}, ensure_ascii=False),
            started_at=now, created_at=now,
        ))
    elif abnormal and open_row is not None:
        open_row.severity = "critical" if ping_row.state == "DOWN" else "warning"
        open_row.message = f"Ping 抖动状态机：{ping_row.state}（连续失败 {ping_row.consecutive_failures} 次）"
        open_row.evidence_json = json.dumps({
            "state": ping_row.state,
            "failures": ping_row.consecutive_failures,
            "rtt_ms": ping_row.last_rtt_ms,
        }, ensure_ascii=False)
    elif not abnormal and open_row is not None:
        open_row.ended_at = now


def _is_traffic(sensor_type: str | None) -> bool:
    return "traffic" in (sensor_type or "").lower()


def _latest_channel_value(session, sensor_id: int, channel_id: int) -> float | None:
    row = session.execute(
        select(PrtgChannelSample5m.avg_value)
        .where(PrtgChannelSample5m.sensor_objid == sensor_id)
        .where(PrtgChannelSample5m.channel_objid == channel_id)
        .order_by(PrtgChannelSample5m.bucket_time.desc())
        .limit(1)
    ).scalar_one_or_none()
    return float(row) if row is not None else None

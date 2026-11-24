# -*- coding: utf-8 -*-
"""PRTG SLA 月报与容量利用率（Issue #231）。

口径（书面结论）
----------------
1. **分母是 PRTG 的监控覆盖时间**：PRTG 的 Uptime/Downtime 以传感器实际被
   监控的时长为分母（`updowntotal`，Paused/维护窗口不计入）。本平台直接取
   PRTG 的累计 Uptime%/Downtime 与 `downtimetime`（累计停机分钟），**不另算
   维护窗口** —— 因此维护窗口天然不计入停机，与 PRTG 页面口径一致。
2. **月故障时长 = 相邻日快照的累计停机分钟差分**：PRTG 不提供逐日停机历史，
   所以每天快照一次累计 `downtimetime`，月内差分求和即为当月故障时长；
   首日无前序快照时该日 delta 为空（不猜）。
3. **覆盖率不足要显式标注**：若 `updowntotal` 在两次快照间的增量明显小于
   自然时长（例如传感器被 Paused），当天 coverage_minutes 会小于 1440，
   报表单独给出 coverage 字段，不把「未监控」当成「可用」。
4. **利用率只在有容量时算**：`network_link_capacity.capacity_mbps` 为空
   （PRTG 不提供容量）时所有利用率字段返回 null，界面显示「待补全」；
   绝不按 0 或猜测值算百分比。有容量时按 `max(In/Cin, Out/Cout)`，
   给出 24h/7d 峰值与 30d P95；阈值 70 关注 / 80 Warning / 90 Critical。

用法（MSNOC1）：
    python -m backend.services.prtg.sla --snapshot
    python -m backend.services.prtg.sla --month 2026-11
    python -m backend.services.prtg.sla --set-capacity 2721:10000:manual
"""
import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import func, select

from backend.database import SessionLocal
from backend.models.prtg import PrtgSensorMapping
from backend.models.prtg_collector import (
    NetworkLinkCapacity,
    PrtgChannelSample5m,
    PrtgSensor,
    PrtgSensorEvent,
    PrtgSlaDaily,
)
from backend.services.prtg import store

DEFAULT_TARGET = 99.5
DOWN_EVENT = "down"


def _day_start(value: datetime) -> datetime:
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _month_bounds(month: str | None, now: datetime) -> tuple[datetime, datetime, str]:
    if month:
        start = datetime.strptime(month + "-01", "%Y-%m-%d")
    else:
        start = datetime(now.year, now.month, 1)
    if start.month == 12:
        end = datetime(start.year + 1, 1, 1)
    else:
        end = datetime(start.year, start.month + 1, 1)
    return start, end, start.strftime("%Y-%m")


def snapshot_day(now: datetime | None = None) -> dict[str, Any]:
    """每日快照：累计停机分钟差分 + 当天可用率。可重复跑（同一天覆盖更新）。"""
    now = now or datetime.utcnow()
    day = _day_start(now)
    stats = {"sensors": 0, "written": 0, "reset_skipped": 0, "no_baseline": 0, "no_coverage": 0}
    with SessionLocal() as session:
        sensors = list(session.execute(
            select(PrtgSensor).where(PrtgSensor.active.is_(True))
        ).scalars())
        stats["sensors"] = len(sensors)
        for sensor in sensors:
            previous = session.execute(
                select(PrtgSlaDaily)
                .where(PrtgSlaDaily.sensor_objid == sensor.objid)
                .where(PrtgSlaDaily.day < day)
                .order_by(PrtgSlaDaily.day.desc())
                .limit(1)
            ).scalar_one_or_none()
            current = session.execute(
                select(PrtgSlaDaily)
                .where(PrtgSlaDaily.sensor_objid == sensor.objid)
                .where(PrtgSlaDaily.day == day)
            ).scalar_one_or_none()
            if current is None:
                current = PrtgSlaDaily(sensor_objid=sensor.objid, day=day)
                session.add(current)

            delta = None
            coverage = None
            if (
                previous is not None
                and previous.downtime_minutes_cum is not None
                and sensor.downtime_minutes is not None
            ):
                diff = sensor.downtime_minutes - previous.downtime_minutes_cum
                if diff < 0:
                    stats["reset_skipped"] += 1  # 传感器重建/计数器重置：不猜
                else:
                    delta = diff
                    # 分母 = 实际监控覆盖时间增量（PRTG updowntotal），不是自然日间隔：
                    # Paused/维护窗口期间 updowntotal 不增长，不能当成可用时间。
                    # 跨多天补跑也用真实增量，不截断到 1440。
                    if (
                        previous.updown_total_minutes_cum is not None
                        and sensor.updown_total_minutes is not None
                    ):
                        coverage_delta = sensor.updown_total_minutes - previous.updown_total_minutes_cum
                        if coverage_delta > 0:
                            coverage = coverage_delta
                        else:
                            # PRTG 的 updowntotal 是**天粒度**（"958 d"），增量 0 可能是
                            # 整段 Paused（无覆盖）也可能是半天 Paused（无法精确判定）。
                            # 两者都不能按自然日 1440 当可用时间，故 coverage=None，
                            # 该日可用率标 undefined，不猜。
                            stats["no_coverage"] += 1
                    # 老快照没有 updowntotal（0012 之前的行）：不按自然日兜底，
                    # 同样标 undefined（宁可缺一天，也不把 Paused 当可用）。

            down_count = session.execute(
                select(PrtgSensorEvent)
                .where(PrtgSensorEvent.sensor_objid == sensor.objid)
                .where(PrtgSensorEvent.event_type == DOWN_EVENT)
                .where(PrtgSensorEvent.happened_at >= day)
                .where(PrtgSensorEvent.happened_at < day + timedelta(days=1))
            ).all()

            availability = None
            if delta is not None:
                # 有停机增量时只在覆盖可判定时算当天可用率；覆盖未知就留空，
                # 不能回退累计值（否则等于把累计可用率当成当天）。
                if coverage:
                    availability = round(max(0.0, 100.0 - delta / coverage * 100.0), 4)
            elif sensor.uptime_pct is not None:
                availability = round(sensor.uptime_pct, 4)
                stats["no_baseline"] += 1

            current.uptime_pct = sensor.uptime_pct
            current.downtime_pct = sensor.downtime_pct
            current.downtime_minutes_cum = sensor.downtime_minutes
            current.updown_total_minutes_cum = sensor.updown_total_minutes
            current.downtime_delta_minutes = delta
            current.coverage_minutes = coverage
            current.availability_pct = availability
            current.down_count = len(down_count)
            if current.created_at is None:
                current.created_at = now
            stats["written"] += 1
        session.commit()

    store.set_state("sla", status="ok", last_run_at=now, last_success_at=now, error="",
                    detail=json.dumps(stats, ensure_ascii=False))
    return stats


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))])


def _capacity_rows(session, now: datetime) -> list[dict[str, Any]]:
    capacities = {
        row.sensor_objid: row
        for row in session.execute(select(NetworkLinkCapacity)).scalars()
    }
    sensors = list(session.execute(
        select(PrtgSensor).where(PrtgSensor.active.is_(True)).order_by(PrtgSensor.objid)
    ).scalars())
    rows = session.execute(
        select(
            PrtgChannelSample5m.sensor_objid,
            PrtgChannelSample5m.channel_objid,
            PrtgChannelSample5m.bucket_time,
            PrtgChannelSample5m.avg_value,
        )
        .where(PrtgChannelSample5m.channel_objid.in_((0, 1)))
        .where(PrtgChannelSample5m.bucket_time >= now - timedelta(days=30))
    ).all()
    series: dict[tuple[int, int], list[tuple[datetime, float]]] = {}
    for sensor_objid, channel_objid, bucket_time, value in rows:
        if value is None:
            continue
        series.setdefault((int(sensor_objid), int(channel_objid)), []).append(
            (bucket_time, float(value))
        )
    result = []
    for sensor in sensors:
        sensor_id = sensor.objid
        capacity = capacities.get(sensor_id)
        capacity_mbps = capacity.capacity_mbps if capacity else None
        entry: dict[str, Any] = {
            "sensor_id": sensor_id,
            "capacity_mbps": capacity_mbps,
            "sensor_name": sensor.sensor_name,
            "device_name": sensor.device_name,
            "capacity_source": capacity.capacity_source if capacity else None,
            "note": capacity.note if capacity else None,
            "capacity_known": bool(capacity_mbps and capacity_mbps > 0),
            "peak_24h_mbps": None,
            "peak_7d_mbps": None,
            "p95_30d_mbps": None,
            "utilization_pct": None,
            "level": "unknown",
        }
        if not entry["capacity_known"]:
            result.append(entry)
            continue
        for channel_id, prefix in ((0, "in"), (1, "out")):
            values = [v for _, v in series.get((sensor_id, channel_id), [])]
            if not values:
                continue
            in_24h = [v for t, v in series.get((sensor_id, channel_id), []) if t >= now - timedelta(hours=24)]
            in_7d = [v for t, v in series.get((sensor_id, channel_id), []) if t >= now - timedelta(days=7)]
            entry[f"peak_24h_{prefix}_mbps"] = round(max(in_24h) * 8 / 1e6, 3) if in_24h else None
            entry[f"peak_7d_{prefix}_mbps"] = round(max(in_7d) * 8 / 1e6, 3) if in_7d else None
            entry[f"p95_30d_{prefix}_mbps"] = (
                round(_percentile(values, 0.95) * 8 / 1e6, 3) if values else None
            )
        peaks = [
            entry.get("peak_24h_in_mbps"), entry.get("peak_24h_out_mbps"),
            entry.get("peak_7d_in_mbps"), entry.get("peak_7d_out_mbps"),
        ]
        peak = max((v for v in peaks if v is not None), default=None)
        if peak is not None:
            utilization = round(peak / capacity_mbps * 100, 2)
            entry["utilization_pct"] = utilization
            if utilization >= 90:
                entry["level"] = "critical"
            elif utilization >= 80:
                entry["level"] = "warning"
            elif utilization >= 70:
                entry["level"] = "attention"
            else:
                entry["level"] = "ok"
        entry["peak_mbps"] = peak
        result.append(entry)
    return result


def month_report(month: str | None = None, target: float = DEFAULT_TARGET,
                 now: datetime | None = None) -> dict[str, Any]:
    """月度 SLA + 区域排名 + 低于目标清单 + 容量利用率。"""
    now = now or datetime.utcnow()
    start, end, label = _month_bounds(month, now)
    with SessionLocal() as session:
        sensors = list(session.execute(
            select(PrtgSensor).where(PrtgSensor.active.is_(True)).order_by(PrtgSensor.objid)
        ).scalars())
        mappings: dict[int, PrtgSensorMapping] = {}
        for row in session.execute(select(PrtgSensorMapping)).scalars():
            current = mappings.get(row.sensor_id)
            if current is None or (row.match_status == "verified" and current.match_status != "verified"):
                mappings[row.sensor_id] = row
        sla_rows = session.execute(
            select(PrtgSlaDaily)
            .where(PrtgSlaDaily.day >= start)
            .where(PrtgSlaDaily.day < end)
        ).scalars().all()
        # 月内 Down 次数直接按事件表统计（日快照只统计到快照时刻，会丢晚间事件）
        month_down_rows = session.execute(
            select(PrtgSensorEvent.sensor_objid, func.count())
            .where(PrtgSensorEvent.event_type == DOWN_EVENT)
            .where(PrtgSensorEvent.happened_at >= start)
            .where(PrtgSensorEvent.happened_at < end)
            .group_by(PrtgSensorEvent.sensor_objid)
        ).all()
        month_down = {int(sensor_id): int(count) for sensor_id, count in month_down_rows}
        capacity_rows = _capacity_rows(session, now)

    by_sensor: dict[int, list[PrtgSlaDaily]] = {}
    for row in sla_rows:
        by_sensor.setdefault(row.sensor_objid, []).append(row)

    links = []
    for sensor in sensors:
        rows = sorted(by_sensor.get(sensor.objid, []), key=lambda r: r.day)
        deltas = [r.downtime_delta_minutes for r in rows if r.downtime_delta_minutes is not None]
        coverages = [r.coverage_minutes for r in rows if r.coverage_minutes is not None]
        month_downtime = sum(deltas) if deltas else None
        month_coverage = sum(coverages) if coverages else None
        if month_downtime is not None:
            if month_coverage:
                month_availability = round(
                    max(0.0, 100.0 - month_downtime / month_coverage * 100.0), 4
                )
                basis = "month_delta"
            else:
                # 有停机增量但覆盖无法判定：宁可显示「—」，也不把累计值冒充当月值
                month_availability = None
                basis = "undefined_coverage"
        else:
            month_availability = round(sensor.uptime_pct, 4) if sensor.uptime_pct is not None else None
            basis = "all_time_fallback"
        down_count = month_down.get(sensor.objid, 0)
        mapping = mappings.get(sensor.objid)
        links.append({
            "sensor_id": sensor.objid,
            "sensor_name": sensor.sensor_name,
            "device_name": sensor.device_name,
            "status": sensor.status,
            "site_id": mapping.site_id if mapping else "Unknown",
            "region": mapping.region if mapping else None,
            "availability_pct": month_availability,
            "uptime_pct_all_time": round(sensor.uptime_pct, 4) if sensor.uptime_pct is not None else None,
            "downtime_hours_month": round(month_downtime / 60.0, 2) if month_downtime is not None else None,
            "coverage_hours_month": round(month_coverage / 60.0, 1) if month_coverage else None,
            "down_count": down_count,
            "last_down": sensor.last_down.isoformat() + "Z" if sensor.last_down else None,
            "uptime_days": round(sensor.uptime_minutes / 1440.0, 1) if sensor.uptime_minutes else None,
            "target_pct": target,
            "below_target": month_availability is not None and month_availability < target,
            "basis": basis,
        })

    links.sort(key=lambda r: (r["availability_pct"] is None, r["availability_pct"] or 999))
    below = [r for r in links if r["below_target"]]

    regions: dict[str, list[float]] = {}
    for row in links:
        if row["availability_pct"] is None:
            continue
        regions.setdefault(row["region"] or "Unknown", []).append(row["availability_pct"])
    region_ranking = sorted(
        (
            {
                "region": region,
                "links": len(values),
                "avg_availability_pct": round(statistics.mean(values), 4),
                "below_target": sum(1 for v in values if v < target),
            }
            for region, values in regions.items()
        ),
        key=lambda r: r["avg_availability_pct"],
    )

    known = [row for row in capacity_rows if row["capacity_known"]]
    return {
        "generated_at": now.isoformat() + "Z",
        "month": label,
        "month_start": start.isoformat() + "Z",
        "month_end": end.isoformat() + "Z",
        "target_pct": target,
        "method": {
            "denominator": "PRTG 监控覆盖时间（updowntotal 的相邻快照增量，天粒度），Paused/维护窗口不计入；覆盖增量无法判定时该日可用率置空（basis=undefined_coverage），不按自然日也不回退累计值",
            "month_downtime": "相邻日快照累计 downtimetime 的差分求和；首日无基线不猜",
            "fallback": "当月无快照时 availability 回退到 PRTG 累计 uptime_pct（basis=all_time_fallback）",
            "down_count": "月内 Down 次数直接按 prtg_sensor_events 统计，不受日快照时刻截断",
            "capacity": "利用率只在 network_link_capacity.capacity_mbps > 0 时计算；否则 null/待补全",
            "utilization_formula": "max(In/Cin, Out/Cout) 的 24h/7d 峰值；阈值 70/80/90",
        },
        "summary": {
            "links": len(links),
            "below_target": len(below),
            "avg_availability_pct": round(
                statistics.mean([r["availability_pct"] for r in links if r["availability_pct"] is not None]), 4
            ) if any(r["availability_pct"] is not None for r in links) else None,
            "total_downtime_hours_month": round(
                sum(r["downtime_hours_month"] for r in links if r["downtime_hours_month"] is not None), 2
            ) if any(r["downtime_hours_month"] is not None for r in links) else None,
            "capacity_known": len(known),
            "capacity_total": len(capacity_rows),
            "sla_days_recorded": len({r.day.date() for r in sla_rows}),
        },
        "links": links,
        "below_target": below,
        "region_ranking": region_ranking,
        "capacity": capacity_rows,
    }


def capacity_list(now: datetime | None = None) -> list[dict[str, Any]]:
    """容量登记 + 利用率列表（无容量的链路也返回，utilization 为 null）。"""
    with SessionLocal() as session:
        return _capacity_rows(session, now or datetime.utcnow())


def set_capacity(sensor_id: int, capacity_mbps: float, source: str = "manual",
                 note: str | None = None) -> dict[str, Any]:
    """人工登记容量（PRTG 不提供），幂等 upsert。"""
    now = datetime.utcnow()
    with SessionLocal() as session:
        row = session.execute(
            select(NetworkLinkCapacity).where(NetworkLinkCapacity.sensor_objid == sensor_id)
        ).scalar_one_or_none()
        if row is None:
            row = NetworkLinkCapacity(sensor_objid=sensor_id)
            session.add(row)
        row.capacity_mbps = float(capacity_mbps)
        row.capacity_source = source
        row.note = note
        row.updated_at = now
        session.commit()
    return {"sensor_id": sensor_id, "capacity_mbps": capacity_mbps, "capacity_source": source}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PRTG SLA 快照 / 月报 / 容量登记（#231）")
    parser.add_argument("--snapshot", action="store_true", help="写当天 SLA 快照")
    parser.add_argument("--month", default=None, help="YYYY-MM，默认当月")
    parser.add_argument("--target", type=float, default=DEFAULT_TARGET)
    parser.add_argument("--set-capacity", default=None,
                        help="SENSOR:MBPS[:SOURCE]，人工登记链路容量")
    parser.add_argument("--out", default=None, help="月报 JSON 输出路径（默认 stdout）")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.snapshot:
        print(json.dumps(snapshot_day(), ensure_ascii=False))
        return 0
    if args.set_capacity:
        parts = args.set_capacity.split(":")
        sensor_id, mbps = int(parts[0]), float(parts[1])
        source = parts[2] if len(parts) > 2 else "manual"
        print(json.dumps(set_capacity(sensor_id, mbps, source), ensure_ascii=False))
        return 0
    report = month_report(args.month, args.target)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"月报已写 {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""PRTG 日 / 周 / 月报（Issue #232）。

三档报表全部复用看板同一套只读聚合，保证「报表数字 = 看板数字」：
  - 日报：传感器状态计数、异常链路、全网峰值/均值、容量待补全数；
  - 周报：可用率排名（前/后 N）、区域质量、异常规则命中统计、扩容关注；
  - 月报：直接复用 #231 的 `sla.month_report()`（可用率/故障时长/Down/容量），
    再补异常与扩容建议。

用法（MSNOC1）：
    python -m backend.services.prtg.reports --kind daily
    python -m backend.services.prtg.reports --kind weekly --out docs/workspace/prtg-reports
    python -m backend.services.prtg.reports --kind monthly --month 2026-11
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select

from backend.database import SessionLocal
from backend.models.prtg_collector import NetworkAnomalyEvent, PrtgSlaDaily
from backend.services.prtg import anomaly, dashboard, sla

DEFAULT_OUT = Path(__file__).resolve().parents[5] / "docs" / "workspace" / "prtg-reports"
SOURCE = "my_app /api/prtg（PRTG 采集服务，60 秒轮询）"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _peak_and_avg(window: str = "24h") -> tuple[float | None, float | None]:
    points = [p["total_mbps"] for p in dashboard.trend_series(window) if p.get("total_mbps") is not None]
    if not points:
        return None, None
    return round(max(points), 3), round(sum(points) / len(points), 3)


def _open_anomalies(window: str = "24h") -> list[dict[str, Any]]:
    return dashboard.anomalies(window, open_only=True)["anomalies"]


def _anomaly_counts_since(since: datetime) -> dict[str, int]:
    with SessionLocal() as session:
        rows = session.execute(
            select(NetworkAnomalyEvent.rule_code)
            .where(NetworkAnomalyEvent.started_at >= since)
        ).scalars().all()
    counts: dict[str, int] = {}
    for code in rows:
        counts[code] = counts.get(code, 0) + 1
    return counts


def _capacity_summary() -> dict[str, Any]:
    rows = sla.capacity_list()
    known = [r for r in rows if r["capacity_known"]]
    return {
        "total": len(rows),
        "known": len(known),
        "missing": len(rows) - len(known),
        "attention": [r for r in known if (r.get("utilization_pct") or 0) >= 70],
    }


def daily_report(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.utcnow()
    overview = dashboard.overview("24h")
    peak, avg = _peak_and_avg("24h")
    anomalies = _open_anomalies("24h")
    capacity = _capacity_summary()
    return {
        "kind": "daily",
        "generated_at": _iso(now),
        "source": SOURCE,
        "window": "近 24 小时",
        "kpi": overview["kpi"],
        "data_age_seconds": overview["data_age_seconds"],
        "traffic": {"peak_mbps": peak, "avg_mbps": avg},
        "anomalies": {
            "open_links": overview["kpi"].get("anomaly_links"),
            "items": [
                {
                    "sensor_id": a["sensor_id"],
                    "device_name": a["device_name"],
                    "sensor_name": a["sensor_name"],
                    "rule": a["rule_code"],
                    "rule_label": a["rule_label"],
                    "severity": a["severity"],
                    "started_at": a["started_at"],
                }
                for a in anomalies
                if a["severity"] in ("warning", "critical") and a["rule_code"] != "composite"
            ],
        },
        "capacity": capacity,
    }


def _week_start(now: datetime) -> datetime:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday())


def _sla_rows(start: datetime, end: datetime) -> list[PrtgSlaDaily]:
    with SessionLocal() as session:
        return list(session.execute(
            select(PrtgSlaDaily)
            .where(PrtgSlaDaily.day >= start)
            .where(PrtgSlaDaily.day < end)
        ).scalars())


def weekly_report(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.utcnow()
    start = _week_start(now)
    end = start + timedelta(days=7)
    rows = _sla_rows(start, min(end, now + timedelta(days=1)))
    by_sensor: dict[int, list[PrtgSlaDaily]] = {}
    for row in rows:
        by_sensor.setdefault(row.sensor_objid, []).append(row)

    ranking = []
    for link in dashboard.links("24h")["links"]:
        sensor_id = link["sensor_id"]
        items = by_sensor.get(sensor_id, [])
        covered = [(r.downtime_delta_minutes, r.coverage_minutes) for r in items
                   if r.downtime_delta_minutes is not None and r.coverage_minutes]
        unknown = [r.downtime_delta_minutes for r in items
                   if r.downtime_delta_minutes is not None and not r.coverage_minutes]
        if covered:
            downtime = sum(d for d, _ in covered)
            coverage = sum(c for _, c in covered)
            availability = round(max(0.0, 100 - downtime / coverage * 100), 4)
            basis = "week_delta"
            covered_hours = round(downtime / 60.0, 2)
        elif unknown:
            availability = None  # 有停机但覆盖不可判定：不拿累计值冒充本周
            basis = "undefined_coverage"
            covered_hours = None
        else:
            availability = link.get("uptime_pct")  # 本周无快照：回退累计并标注
            basis = "all_time_fallback"
            covered_hours = None
        ranking.append({
            "sensor_id": sensor_id,
            "device_name": link.get("device_name"),
            "sensor_name": link.get("sensor_name"),
            "region": link.get("region"),
            "availability_pct": availability,
            "basis": basis,
            "downtime_hours_week": (
                round(((downtime if covered else 0) + sum(unknown)) / 60.0, 2)
                if covered or unknown else None
            ),
            "downtime_hours_covered": covered_hours,
            "downtime_hours_unknown_coverage": (
                round(sum(unknown) / 60.0, 2) if unknown else None
            ),
            "coverage_hours_week": round(coverage / 60.0, 1) if covered else None,
        })
    # 0% 可用率是最差，不能用 `or 999` 被排到最后
    ranking.sort(key=lambda r: (r["availability_pct"] is None,
                                r["availability_pct"] if r["availability_pct"] is not None else 999))

    regions: dict[str, dict[str, Any]] = {}
    for row in ranking:
        slot = regions.setdefault(row["region"] or "Unknown", {
            "values": [], "week_delta": 0, "fallback": 0, "undefined": 0,
        })
        if row["basis"] == "week_delta":
            slot["week_delta"] += 1
        elif row["basis"] == "all_time_fallback":
            slot["fallback"] += 1
        else:
            slot["undefined"] += 1
        if row["availability_pct"] is not None:
            slot["values"].append(row["availability_pct"])

    region_quality = sorted(
        ({
            "region": region,
            "links": slot["week_delta"] + slot["fallback"] + slot["undefined"],
            "avg_availability_pct": (
                round(sum(slot["values"]) / len(slot["values"]), 4) if slot["values"] else None
            ),
            "week_delta_links": slot["week_delta"],
            "fallback_links": slot["fallback"],
            "undefined_links": slot["undefined"],
        } for region, slot in regions.items()),
        key=lambda r: (r["avg_availability_pct"] is None,
                       r["avg_availability_pct"] if r["avg_availability_pct"] is not None else 999),
    )
    return {
        "kind": "weekly",
        "generated_at": _iso(now),
        "source": SOURCE,
        "week_start": start.date().isoformat(),
        "week_end": (start + timedelta(days=6)).date().isoformat(),
        "as_of": _iso(now),
        "ranking": ranking,
        "bottom5": ranking[:5],
        "region_quality": region_quality,
        "anomalies": _anomaly_counts_since(start),
        "capacity": _capacity_summary(),
    }


def monthly_report(month: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.utcnow()
    base = sla.month_report(month, now=now)
    start, end, label = sla._month_bounds(month, now)
    capacity = _capacity_summary()
    return {
        "kind": "monthly",
        "generated_at": _iso(now),
        "source": SOURCE,
        "month": label,
        "sla": base,
        "anomalies": _anomaly_counts_since(start),
        "capacity": capacity,
        "expansion_advice": _expansion_advice({
            "capacity": capacity,
            "below_target": base.get("below_target", []),
            "target_pct": base.get("target_pct"),
        }),
    }


def _expansion_advice(report: dict[str, Any]) -> list[str]:
    advice: list[str] = []
    capacity = report.get("capacity", {})
    if capacity.get("missing"):
        advice.append(
            f"{capacity['missing']} 条链路未登记容量（network_link_capacity 为空），"
            "无法计算利用率；请用 `python -m backend.services.prtg.sla --set-capacity "
            "SENSOR:MBPS:manual` 补齐后再评估扩容。"
        )
    for row in capacity.get("attention", [])[:5]:
        advice.append(
            f"{row.get('device_name') or row['sensor_id']} / {row.get('sensor_name') or ''} "
            f"利用率 {row.get('utilization_pct')}%，已进入关注阈值（70%）。"
        )
    below = report.get("below_target", [])
    if below:
        advice.append(
            f"可用率低于目标 {report.get('target_pct')}% 的链路 {len(below)} 条，"
            "优先核查 Last Down 与维护窗口。"
        )
    return advice


def _fmt(value: Any, suffix: str = "") -> str:
    return "—" if value is None else f"{value}{suffix}"


def render_markdown(report: dict[str, Any]) -> str:
    kind = report["kind"]
    lines = [f"# PRTG {dict(daily='日报', weekly='周报', monthly='月报')[kind]}（{report.get('generated_at')}）", ""]
    lines.append(f"> 来源：{report['source']}")
    if kind == "daily":
        kpi = report["kpi"]
        lines += [
            f"> 窗口：{report['window']}；数据新鲜度：{report.get('data_age_seconds')} 秒",
            "",
            "## KPI",
            f"- 传感器：{kpi.get('total')}（Up {kpi.get('up')} / Unusual {kpi.get('unusual')} / Down {kpi.get('down')}）",
            f"- 平均可用率：{_fmt(kpi.get('avg_uptime_pct'), '%')}",
            f"- 异常链路：{_fmt(kpi.get('anomaly_links'))}",
            f"- 全网峰值 / 均值：{_fmt(report['traffic']['peak_mbps'])} / {_fmt(report['traffic']['avg_mbps'])} Mbps",
            f"- 容量已登记：{report['capacity']['known']} / {report['capacity']['total']}（缺失 {report['capacity']['missing']}）",
            "",
            "## 异常明细（warning/critical）",
        ]
        for item in report["anomalies"]["items"][:15]:
            lines.append(f"- {item['device_name']} / {item['sensor_name']}：{item['rule_label']}（{item['severity']}，自 {item['started_at']}）")
        if not report["anomalies"]["items"]:
            lines.append("- 无")
    elif kind == "weekly":
        lines += [
            f"> 周期：{report['week_start']} ~ {report['week_end']}（截至 {report.get('as_of')}）",
            "",
            "## 可用率排名（末 5）",
            "| 设备 / 传感器 | 区域 | 可用率 | 本周停机(h) | 口径 |",
            "| --- | --- | ---: | ---: | --- |",
        ]
        for row in report["bottom5"]:
            lines.append(
                f"| {row['device_name'] or ''} / {row['sensor_name'] or ''} | {row['region'] or 'Unknown'} "
                f"| {_fmt(row['availability_pct'], '%')} | {_fmt(row.get('downtime_hours_week'))} | {row['basis']} |"
            )
        lines += ["", "## 区域质量", "| 区域 | 链路数 | 平均可用率 | 口径 |", "| --- | ---: | ---: | --- |"]
        for row in report["region_quality"]:
            basis = f"周差分 {row['week_delta_links']} / 回退 {row['fallback_links']} / 不可判定 {row['undefined_links']}"
            lines.append(f"| {row['region']} | {row['links']} | {_fmt(row['avg_availability_pct'], '%')} | {basis} |")
        lines += ["", "## 异常规则命中（本周）"]
        for code, count in sorted(report["anomalies"].items(), key=lambda kv: -kv[1]):
            lines.append(f"- {code}：{count}")
        if not report["anomalies"]:
            lines.append("- 无")
        lines += ["", f"## 容量\n- 已登记 {report['capacity']['known']} / {report['capacity']['total']}（缺失 {report['capacity']['missing']}）"]
    else:
        base = report["sla"]
        summary = base["summary"]
        lines += [
            f"> 月份：{report['month']}；目标可用率：{base['target_pct']}%",
            "",
            "## 月度 SLA",
            f"- 平均可用率：{_fmt(summary.get('avg_availability_pct'), '%')}",
            f"- 低于目标：{_fmt(summary.get('below_target'))} 条",
            f"- 当月总停机：{_fmt(summary.get('total_downtime_hours_month'))} 小时",
            f"- Down 次数：{sum(r.get('down_count') or 0 for r in base['links'])}",
            "",
            "## 低于目标清单",
            "| 设备 / 传感器 | 可用率 | 当月停机(h) | Down | 口径 |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
        for row in base["below_target"][:15]:
            lines.append(
                f"| {row['device_name'] or ''} / {row['sensor_name'] or ''} "
                f"| {_fmt(row['availability_pct'], '%')} | {_fmt(row['downtime_hours_month'])} "
                f"| {_fmt(row['down_count'])} | {row['basis']} |"
            )
        lines += ["", "## 异常规则命中（本月）"]
        for code, count in sorted(report["anomalies"].items(), key=lambda kv: -kv[1]):
            lines.append(f"- {code}：{count}")
        lines += ["", "## 扩容建议"]
        for advice in report["expansion_advice"]:
            lines.append(f"- {advice}")
    lines += ["", "---", f"数据来源：{report['source']}；生成时间：{report['generated_at']}"]
    return "\n".join(lines)


def build_report(kind: str, month: str | None = None) -> dict[str, Any]:
    if kind == "daily":
        return daily_report()
    if kind == "weekly":
        return weekly_report()
    if kind == "monthly":
        return monthly_report(month)
    raise ValueError(f"未知报表类型：{kind}")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PRTG 日/周/月报（#232）")
    parser.add_argument("--kind", choices=("daily", "weekly", "monthly"), default="daily")
    parser.add_argument("--month", default=None)
    parser.add_argument("--out", default=None, help="输出目录（默认 docs/workspace/prtg-reports）")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = build_report(args.kind, args.month)
    out_dir = Path(args.out) if args.out else DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d")
    md_path = out_dir / f"prtg-{args.kind}-{stamp}.md"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    (out_dir / f"prtg-{args.kind}-{stamp}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"{args.kind} 报表已生成：{md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

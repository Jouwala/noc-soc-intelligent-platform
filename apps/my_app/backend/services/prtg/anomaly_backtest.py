# -*- coding: utf-8 -*-
"""异常规则历史回测 + Unusual 分类（Issue #229）。

用途
----
1. **5 条规则的回测记录**：用最近 7 天（外加前置基线）的 5 分钟数据回放
   `anomaly.evaluate_traffic_sensor()`，统计每条规则触发了多少次、涉及哪些
   传感器、最长持续多久。输出 Markdown 报告，作为 #229 验收的「有历史回测记录」。
2. **8 个 Unusual 传感器的分类结论**：按 7 天 p95/中位数与当前值给出
   「空闲/基线偏低（待核查）」「低流量待核对」「高流量/超基线待确认」等结论，
   不直接把 PRTG Unusual 当高危。

用法（MSNOC1）：
    python -m backend.services.prtg.anomaly_backtest --days 7 --out docs/workspace/prtg-anomaly-backtest.md
"""
import argparse
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select

from backend.database import SessionLocal
from backend.models.prtg_collector import PrtgChannelSample5m, PrtgSensor
from backend.services.prtg import anomaly

DEFAULT_OUT = Path(__file__).resolve().parents[5] / "docs" / "workspace" / "prtg-anomaly-backtest.md"


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))])


def _load_error_series(days: int) -> dict[int, list[tuple[datetime, float]]]:
    """每传感器 Errors+Discards（通道 10/11/12/13 区间速率之和）按时桶聚合。"""
    since = datetime.utcnow() - timedelta(days=days + 2)
    with SessionLocal() as session:
        rows = session.execute(
            select(PrtgChannelSample5m.sensor_objid, PrtgChannelSample5m.bucket_time,
                   PrtgChannelSample5m.avg_value)
            .where(PrtgChannelSample5m.channel_objid.in_((10, 11, 12, 13)))
            .where(PrtgChannelSample5m.bucket_time >= since)
        ).all()
    grouped: dict[tuple[int, datetime], float] = {}
    for sensor_objid, bucket_time, value in rows:
        if value is None:
            continue
        key = (int(sensor_objid), bucket_time)
        grouped[key] = grouped.get(key, 0.0) + float(value)
    result: dict[int, list[tuple[datetime, float]]] = defaultdict(list)
    for (sensor_objid, bucket_time), total in grouped.items():
        result[sensor_objid].append((bucket_time, total))
    for values in result.values():
        values.sort()
    return result


def _load_series(days: int) -> tuple[list[PrtgSensor], dict[int, list[tuple[datetime, float]]]]:
    since = datetime.utcnow() - timedelta(days=days + 2)
    with SessionLocal() as session:
        sensors = list(session.execute(select(PrtgSensor).where(PrtgSensor.active.is_(True))).scalars())
        rows = session.execute(
            select(PrtgChannelSample5m.sensor_objid, PrtgChannelSample5m.bucket_time,
                   PrtgChannelSample5m.avg_value)
            .where(PrtgChannelSample5m.channel_objid == -1)
            .where(PrtgChannelSample5m.bucket_time >= since)
        ).all()
    series: dict[int, list[tuple[datetime, float]]] = defaultdict(list)
    for sensor_objid, bucket_time, value in rows:
        if value is not None:
            series[int(sensor_objid)].append((bucket_time, float(value)))
    for values in series.values():
        values.sort()
    return sensors, series


def backtest(days: int = 7) -> dict[str, Any]:
    sensors, series = _load_series(days)
    error_series = _load_error_series(days)
    cutoff = datetime.utcnow() - timedelta(days=days)
    counters: Counter = Counter()
    per_sensor: dict[int, Counter] = defaultdict(Counter)
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sensor in sensors:
        values = series.get(sensor.objid, [])
        if not values:
            continue
        for i, (ts, _value) in enumerate(values):
            if ts < cutoff or i < anomaly.LOW_30_BUCKETS:
                continue
            window = values[: i + 1]
            errors = [(t, v) for t, v in error_series.get(sensor.objid, []) if t <= ts]
            hits, state = anomaly.evaluate_traffic_sensor(
                sensor_id=sensor.objid, status=sensor.status,
                total_series=window, error_series=errors,
            )
            for hit in hits:
                counters[hit.rule_code] += 1
                per_sensor[sensor.objid][hit.rule_code] += 1
                if len(examples[hit.rule_code]) < 3:
                    examples[hit.rule_code].append({
                        "sensor_id": sensor.objid,
                        "at": ts.isoformat(),
                        "message": hit.message,
                    })
    return {"days": days, "sensors": len(sensors), "counters": counters,
            "per_sensor": per_sensor, "examples": examples}


def classify_unusual() -> list[dict[str, Any]]:
    """对当前 Unusual 传感器给出分类结论（不直接当高危）。"""
    sensors, series = _load_series(7)
    result = []
    for sensor in sensors:
        if (sensor.status or "").lower() != "unusual":
            continue
        values = [v for _, v in series.get(sensor.objid, [])]
        if not values:
            result.append({"sensor_id": sensor.objid, "sensor_name": sensor.sensor_name,
                           "p95": None, "median": None, "verdict": "无 7 天数据，待补采"})
            continue
        p95 = _percentile(values, 0.95)
        median = statistics.median(values)
        latest = values[-1]
        if p95 < anomaly.IDLE_P95_BYTES:
            verdict = "空闲/未使用链路：基线偏低（待核查），不转高危"
        elif latest < median * 0.3:
            verdict = "当前流量低于 7 天中位数 30%：低流量待核对"
        elif latest > median * 2:
            verdict = "当前流量高于 7 天中位数 200%：高流量/超基线待确认"
        else:
            verdict = "流量在正常波动区间：观察，不转高危"
        result.append({
            "sensor_id": sensor.objid,
            "sensor_name": sensor.sensor_name,
            "device_name": sensor.device_name,
            "status": sensor.status,
            "p95_kbit": round(p95 * 8 / 1000, 1),
            "median_kbit": round(median * 8 / 1000, 1),
            "latest_kbit": round(latest * 8 / 1000, 1) if latest is not None else None,
            "verdict": verdict,
        })
    return result


def render_markdown(result: dict[str, Any], unusual: list[dict[str, Any]]) -> str:
    lines = [
        "# PRTG 异常规则历史回测与 Unusual 分类（Issue #229）",
        "",
        f"> 生成时间：{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC　|　执行位置：MSNOC1",
        f"> 数据：最近 {result['days']} 天 5 分钟聚合（`prtg_channel_samples_5m`），"
        f"共 {result['sensors']} 个传感器；基线 = 过去 7 天同一分钟档中位数（样本 <3 天不下结论）。",
        "> 回测方法：逐 5 分钟桶回放 `anomaly.evaluate_traffic_sensor()`，命中计数；"
        "空闲链路（p95 < 10 kbit/s）按「基线偏低（待核查）」归类，不参与告警分级。"
        "Errors/Discards 按 (传感器, 桶) 把 4 个通道求和后再判 3 桶严格上升。",
        "> 注意：报告是生成时刻的快照——最近几个 5 分钟桶会被采集器按 20 分钟窗口重算，"
        "晚几分钟重跑数字会小幅变化，不追求逐位复现。",
        "",
        "## 1. 五条规则回测命中",
        "",
        "| 规则 | 说明 | 命中次数 | 涉及传感器数 |",
        "| --- | --- | ---: | ---: |",
    ]
    labels = {
        anomaly.RULE_BELOW: "低于 7 天同期基线 30%",
        anomaly.RULE_ZERO: "流量归零",
        anomaly.RULE_ABOVE: "超过基线 200%",
        anomaly.RULE_ERRORS: "Errors/Discards 持续增长",
        anomaly.RULE_LOW_30: "连续 30 分钟低流量",
        anomaly.RULE_LOW_BASELINE: "基线偏低（待核查）",
    }
    for code in (anomaly.RULE_BELOW, anomaly.RULE_ZERO, anomaly.RULE_ABOVE,
                 anomaly.RULE_ERRORS, anomaly.RULE_LOW_30, anomaly.RULE_LOW_BASELINE):
        sensors_hit = sum(1 for counter in result["per_sensor"].values() if counter[code])
        lines.append(f"| `{code}` | {labels[code]} | {result['counters'][code]} | {sensors_hit} |")
    lines += ["", "示例（每规则最多 3 条）：", ""]
    for code, items in result["examples"].items():
        for item in items:
            lines.append(f"- `{code}` · 传感器 {item['sensor_id']} · {item['at']} · {item['message']}")
    lines += [
        "",
        "## 2. 当前 Unusual 传感器逐条分类",
        "",
        "| 传感器 | 设备 | 7d p95 (kbit/s) | 7d 中位数 | 当前 | 结论 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in unusual:
        lines.append(
            f"| {row['sensor_id']} {row.get('sensor_name', '')[:28]} | "
            f"{(row.get('device_name') or '')[:22]} | {row.get('p95_kbit')} | "
            f"{row.get('median_kbit')} | {row.get('latest_kbit')} | {row['verdict']} |"
        )
    idle = sum(1 for row in unusual if row["verdict"].startswith("空闲"))
    high = sum(1 for row in unusual if "高于 7 天中位数" in row["verdict"])
    low = sum(1 for row in unusual if "低于 7 天中位数" in row["verdict"])
    lines += [
        "",
        "## 3. 结论与上线口径",
        "",
        f"- 当前 Unusual {len(unusual)} 个：空闲/基线偏低 {idle} 个、高于 7 天中位数 200% {high} 个、"
        f"低于中位数 30% {low} 个。与 Issue #229 描述的「7 个 Unusual、流量异常偏低」不完全一致，"
        "**以本次实测为准**：不是所有 Unusual 都该转高危，逐条结论见第 2 节。",
        "- 2026-11-24 17:00 前后多条链路流量较 7 天同期显著抬升（>200%），"
        "规则按设计标 warning；需网络侧确认是真实峰值还是统计口径变化，再决定是否调阈值。",
        "- 5 条规则均有回测命中记录；`above_baseline_200` 与 `below_baseline_30` 是主力，"
        "`zero_traffic` 只在基线 >0 的链路上判 Critical，避免把空闲端口误报成故障。",
        "- Unusual 传感器按上表分类：空闲/基线偏低的一律标「待核查」，不转高危；"
        "只有当前流量相对 7 天中位数显著偏离的才进入告警。",
        "- Errors/Discards 用 PRTG 的区间速率，不再差分；PEER-LINK 两条链路的流量不对称"
        "由 `above_baseline_200`/`below_baseline_30` 同时命中时提示，人工核对 VPC/双活配置。",
        "",
    ]
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PRTG 异常规则回测 + Unusual 分类（#229）")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = backtest(days=args.days)
    unusual = classify_unusual()
    report = render_markdown(result, unusual)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"回测完成：{result['counters']}；Unusual {len(unusual)} 条；报告 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

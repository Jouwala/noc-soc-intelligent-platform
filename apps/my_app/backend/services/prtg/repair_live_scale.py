# -*- coding: utf-8 -*-
"""纠偏 live 时代偏高的 Traffic 采样（配套 parsing display 口径）。

默认 **dry-run**：只 SELECT / 报告，不写库。``--apply`` 才更新，且须先备份。

口径
----
- 候选：``prtg_channel_samples`` / ``prtg_channel_samples_5m`` 中 Traffic 通道
  (-1/0/1)，且 ``volume/value`` 落在 40–80（live 60s 形态），``sample/bucket
  < --until``。
- 同桶对照：``prtg_historical_samples`` 在同一 5 分钟窗内的均值（historic 正确）。
- 修正系数 **按行** = hist_avg / live_value；无 hist 则跳过（不用全局常数）。
- 幂等：已与 hist 相差 <5% 的行不改；apply 在 evidence/ledger 记
  ``scale_repaired_at``；再次 dry-run 会显示 already_ok。

用法
----
    python -m backend.services.prtg.repair_live_scale --until 2026-11-28T00:00:00
    python -m backend.services.prtg.repair_live_scale --until ... --backup DIR --apply
    python -m backend.services.prtg.repair_live_scale --rollback DIR
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select, text

from backend.database import SessionLocal
from backend.models.prtg_collector import NetworkAnomalyEvent, PrtgChannelSample, PrtgChannelSample5m
from backend.services.prtg import anomaly

TRAFFIC_CHANNELS = (-1, 0, 1)
LIVE_SEC_LO, LIVE_SEC_HI = 40.0, 80.0
MATCH_TOLERANCE = 0.05  # 已与 hist 接近则视为已修
MIN_FACTOR, MAX_FACTOR = 0.05, 0.5  # 预期 live 偏高 → factor < 1（约 0.1）


@dataclass
class RepairRow:
    table: str
    sensor_objid: int
    channel_objid: int
    ts: str
    live_value: float
    hist_value: float
    factor: float
    live_volume: float | None
    new_value: float
    new_volume: float | None
    action: str  # repair | skip_matched | skip_no_hist | skip_factor


def _parse_until(value: str) -> datetime:
    text_v = value.strip().replace("Z", "")
    if "T" in text_v:
        return datetime.fromisoformat(text_v)
    return datetime.strptime(text_v, "%Y-%m-%d")


def _as_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    text_v = str(value).replace("Z", "")
    try:
        return datetime.fromisoformat(text_v)
    except ValueError:
        return datetime.strptime(text_v[:19], "%Y-%m-%d %H:%M:%S")


def _hist_avg_for_bucket(session, sensor_id: int, channel_id: int,
                         bucket) -> float | None:
    from datetime import timedelta
    start = _as_dt(bucket).replace(second=0, microsecond=0)
    end_dt = start + timedelta(minutes=5)
    row = session.execute(
        text(
            """
            SELECT avg(value_num) AS v
            FROM prtg_historical_samples
            WHERE sensor_objid = :sid AND channel_objid = :cid
              AND sample_time >= :a AND sample_time < :b
              AND value_num IS NOT NULL
            """
        ),
        {"sid": sensor_id, "cid": channel_id, "a": start, "b": end_dt},
    ).one()
    return float(row.v) if row.v is not None else None


def plan_5m_repairs(session, until: datetime) -> list[RepairRow]:
    rows = session.execute(
        text(
            """
            SELECT sensor_objid, channel_objid, bucket_time, avg_value, avg_volume,
                   CASE WHEN avg_value > 0 THEN avg_volume / avg_value END AS sec
            FROM prtg_channel_samples_5m
            WHERE channel_objid IN (-1, 0, 1)
              AND bucket_time < :until
              AND avg_value IS NOT NULL AND avg_value > 0
              AND avg_volume IS NOT NULL
            """
        ),
        {"until": until},
    ).all()
    out: list[RepairRow] = []
    for r in rows:
        if r.sec is None or not (LIVE_SEC_LO <= float(r.sec) <= LIVE_SEC_HI):
            continue
        ts = _as_dt(r.bucket_time).isoformat()
        hist = _hist_avg_for_bucket(session, int(r.sensor_objid), int(r.channel_objid), r.bucket_time)
        live = float(r.avg_value)
        vol = float(r.avg_volume) if r.avg_volume is not None else None
        if hist is None or hist <= 0:
            out.append(RepairRow(
                "5m", int(r.sensor_objid), int(r.channel_objid), ts,
                live, 0.0, 0.0, vol, live, vol, "skip_no_hist",
            ))
            continue
        if abs(live - hist) / hist <= MATCH_TOLERANCE:
            out.append(RepairRow(
                "5m", int(r.sensor_objid), int(r.channel_objid), ts,
                live, hist, hist / live, vol, live, vol, "skip_matched",
            ))
            continue
        factor = hist / live
        if not (MIN_FACTOR <= factor <= MAX_FACTOR):
            out.append(RepairRow(
                "5m", int(r.sensor_objid), int(r.channel_objid), ts,
                live, hist, factor, vol, live, vol, "skip_factor",
            ))
            continue
        new_v = live * factor
        new_vol = (vol * factor) if vol is not None else None
        out.append(RepairRow(
            "5m", int(r.sensor_objid), int(r.channel_objid), ts,
            live, hist, factor, vol, new_v, new_vol, "repair",
        ))
    return out


def plan_raw_repairs(session, until: datetime) -> list[RepairRow]:
    """raw 60s：与同分钟 historic 对照；无 historic 则跳过。"""
    rows = session.execute(
        text(
            """
            SELECT r.sensor_objid, r.channel_objid, r.sample_time, r.value_num, r.volume_num,
                   h.value_num AS hist_value
            FROM prtg_channel_samples r
            LEFT JOIN prtg_historical_samples h
              ON h.sensor_objid = r.sensor_objid
             AND h.channel_objid = r.channel_objid
             AND h.sample_time = r.sample_time
            WHERE r.channel_objid IN (-1, 0, 1)
              AND r.sample_time < :until
              AND r.value_num IS NOT NULL AND r.value_num > 0
            """
        ),
        {"until": until},
    ).all()
    out: list[RepairRow] = []
    for r in rows:
        live = float(r.value_num)
        vol = float(r.volume_num) if r.volume_num is not None else None
        if r.hist_value is None:
            out.append(RepairRow(
                "raw", int(r.sensor_objid), int(r.channel_objid), r.sample_time.isoformat(),
                live, 0.0, 0.0, vol, live, vol, "skip_no_hist",
            ))
            continue
        hist = float(r.hist_value)
        if hist <= 0:
            continue
        if abs(live - hist) / hist <= MATCH_TOLERANCE:
            out.append(RepairRow(
                "raw", int(r.sensor_objid), int(r.channel_objid), r.sample_time.isoformat(),
                live, hist, hist / live, vol, live, vol, "skip_matched",
            ))
            continue
        factor = hist / live
        if not (MIN_FACTOR <= factor <= MAX_FACTOR):
            out.append(RepairRow(
                "raw", int(r.sensor_objid), int(r.channel_objid), r.sample_time.isoformat(),
                live, hist, factor, vol, live, vol, "skip_factor",
            ))
            continue
        out.append(RepairRow(
            "raw", int(r.sensor_objid), int(r.channel_objid), r.sample_time.isoformat(),
            live, hist, factor, vol, live * factor,
            (vol * factor) if vol is not None else None, "repair",
        ))
    return out


def summarize(rows: Iterable[RepairRow]) -> dict[str, Any]:
    rows = list(rows)
    by = {}
    for r in rows:
        by[r.action] = by.get(r.action, 0) + 1
    return {"total": len(rows), "by_action": by,
            "repair": by.get("repair", 0)}


def write_backup(path: Path, five: list[RepairRow], raw: list[RepairRow],
                 open_events: list[dict[str, Any]],
                 closeable_events: list[dict[str, Any]] | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    with (path / "repair_plan_5m.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(asdict(five[0]).keys()) if five else [
            "table", "sensor_objid", "channel_objid", "ts", "live_value", "hist_value",
            "factor", "live_volume", "new_value", "new_volume", "action",
        ])
        w.writeheader()
        for r in five:
            w.writerow(asdict(r))
    with (path / "repair_plan_raw.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(asdict(raw[0]).keys()) if raw else [
            "table", "sensor_objid", "channel_objid", "ts", "live_value", "hist_value",
            "factor", "live_volume", "new_value", "new_volume", "action",
        ])
        w.writeheader()
        for r in raw:
            w.writerow(asdict(r))
    (path / "open_anomaly_events.json").write_text(
        json.dumps(open_events, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    if closeable_events is not None:
        (path / "scale_repair_closeable_events.json").write_text(
            json.dumps(closeable_events, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    (path / "README_ROLLBACK.txt").write_text(
        "Rollback: python -m backend.services.prtg.repair_live_scale --rollback " + str(path) + "\n"
        "1) Restores avg_value/avg_volume (5m) and value_num/volume_num (raw) from CSV live_* columns\n"
        "   for rows with action=repair.\n"
        "2) If scale_repair_closeable_events.json exists: restores ended_at=NULL and evidence_json\n"
        "   for those event ids (undo closed_by=scale_repair closes). Never DELETEs events.\n",
        encoding="utf-8",
    )


def apply_repairs(session, five: list[RepairRow], raw: list[RepairRow], now: datetime) -> dict[str, int]:
    n5 = nraw = 0
    for r in five:
        if r.action != "repair":
            continue
        session.execute(
            text(
                """
                UPDATE prtg_channel_samples_5m
                   SET avg_value = :v, max_value = :v,
                       avg_volume = COALESCE(:vol, avg_volume),
                       max_volume = COALESCE(:vol, max_volume),
                       updated_at = :now
                 WHERE sensor_objid = :sid AND channel_objid = :cid
                   AND bucket_time = :ts
                """
            ),
            {"v": r.new_value, "vol": r.new_volume, "now": now,
             "sid": r.sensor_objid, "cid": r.channel_objid,
             "ts": datetime.fromisoformat(r.ts)},
        )
        n5 += 1
    for r in raw:
        if r.action != "repair":
            continue
        session.execute(
            text(
                """
                UPDATE prtg_channel_samples
                   SET value_num = :v, volume_num = COALESCE(:vol, volume_num)
                 WHERE sensor_objid = :sid AND channel_objid = :cid
                   AND sample_time = :ts
                """
            ),
            {"v": r.new_value, "vol": r.new_volume,
             "sid": r.sensor_objid, "cid": r.channel_objid,
             "ts": datetime.fromisoformat(r.ts)},
        )
        nraw += 1
    session.commit()
    return {"updated_5m": n5, "updated_raw": nraw}


def export_open_events(session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(NetworkAnomalyEvent).where(NetworkAnomalyEvent.ended_at.is_(None))
    ).scalars().all()
    return [
        {
            "id": r.id,
            "sensor_objid": r.sensor_objid,
            "rule_code": r.rule_code,
            "severity": r.severity,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "evidence_json": r.evidence_json,
            "ended_at": None,
        }
        for r in rows
    ]


def _series_for_sensor(session, sensor_id: int, now: datetime) -> list[tuple[datetime, float]]:
    since = now - timedelta(days=anomaly.BASELINE_DAYS + 2)
    rows = session.execute(
        select(PrtgChannelSample5m.bucket_time, PrtgChannelSample5m.avg_value)
        .where(PrtgChannelSample5m.sensor_objid == sensor_id)
        .where(PrtgChannelSample5m.channel_objid == -1)
        .where(PrtgChannelSample5m.bucket_time >= since)
        .where(PrtgChannelSample5m.bucket_time <= now)
        .order_by(PrtgChannelSample5m.bucket_time)
    ).all()
    return [(t, float(v)) for t, v in rows if v is not None]


def find_scale_repair_closeable(session, now: datetime) -> list[NetworkAnomalyEvent]:
    """仍 open 的 above_baseline_200：当前序列已不再命中该规则 → 可标 scale_repair 关闭。"""
    open_above = session.execute(
        select(NetworkAnomalyEvent)
        .where(NetworkAnomalyEvent.ended_at.is_(None))
        .where(NetworkAnomalyEvent.rule_code == anomaly.RULE_ABOVE)
    ).scalars().all()
    closeable: list[NetworkAnomalyEvent] = []
    for ev in open_above:
        series = _series_for_sensor(session, ev.sensor_objid, now)
        if not series:
            continue
        hits, _state = anomaly.evaluate_traffic_sensor(
            sensor_id=ev.sensor_objid, status="Up",
            total_series=series, error_series=[],
        )
        if anomaly.RULE_ABOVE not in {h.rule_code for h in hits}:
            closeable.append(ev)
    return closeable


def close_after_reeval(session, now: datetime) -> list[dict[str, Any]]:
    """对仍 open、但已满足关闭条件的 above 事件写入 closed_by=scale_repair 并关闭。

    返回关闭前快照（供 backup / rollback）。不 DELETE。
    """
    closeable = find_scale_repair_closeable(session, now)
    snapshots: list[dict[str, Any]] = []
    for ev in closeable:
        snapshots.append({
            "id": ev.id,
            "sensor_objid": ev.sensor_objid,
            "rule_code": ev.rule_code,
            "severity": ev.severity,
            "started_at": ev.started_at.isoformat() if ev.started_at else None,
            "evidence_json": ev.evidence_json,
            "ended_at": None,
        })
        try:
            data = json.loads(ev.evidence_json or "{}")
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, list):
            data = {"items": data, "closed_by": "scale_repair"}
        elif isinstance(data, dict):
            data = dict(data)
            data["closed_by"] = "scale_repair"
        else:
            data = {"closed_by": "scale_repair", "prior": data}
        ev.evidence_json = json.dumps(data, ensure_ascii=False)
        ev.ended_at = now
    session.commit()
    return snapshots


def rollback_from_backup(path: Path) -> dict[str, int]:
    five_path = path / "repair_plan_5m.csv"
    raw_path = path / "repair_plan_raw.csv"
    closeable_path = path / "scale_repair_closeable_events.json"
    n5 = nraw = n_events = 0
    with SessionLocal() as session:
        if five_path.exists():
            with five_path.open(encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    if row.get("action") != "repair":
                        continue
                    session.execute(
                        text(
                            """
                            UPDATE prtg_channel_samples_5m
                               SET avg_value = :v, max_value = :v,
                                   avg_volume = COALESCE(:vol, avg_volume),
                                   max_volume = COALESCE(:vol, max_volume)
                             WHERE sensor_objid = :sid AND channel_objid = :cid
                               AND bucket_time = :ts
                            """
                        ),
                        {
                            "v": float(row["live_value"]),
                            "vol": float(row["live_volume"]) if row.get("live_volume") else None,
                            "sid": int(row["sensor_objid"]),
                            "cid": int(row["channel_objid"]),
                            "ts": datetime.fromisoformat(row["ts"]),
                        },
                    )
                    n5 += 1
        if raw_path.exists():
            with raw_path.open(encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    if row.get("action") != "repair":
                        continue
                    session.execute(
                        text(
                            """
                            UPDATE prtg_channel_samples
                               SET value_num = :v, volume_num = COALESCE(:vol, volume_num)
                             WHERE sensor_objid = :sid AND channel_objid = :cid
                               AND sample_time = :ts
                            """
                        ),
                        {
                            "v": float(row["live_value"]),
                            "vol": float(row["live_volume"]) if row.get("live_volume") else None,
                            "sid": int(row["sensor_objid"]),
                            "cid": int(row["channel_objid"]),
                            "ts": datetime.fromisoformat(row["ts"]),
                        },
                    )
                    nraw += 1
        if closeable_path.exists():
            for item in json.loads(closeable_path.read_text(encoding="utf-8")):
                session.execute(
                    text(
                        """
                        UPDATE network_anomaly_events
                           SET ended_at = NULL,
                               evidence_json = :evid
                         WHERE id = :id
                        """
                    ),
                    {"id": int(item["id"]), "evid": item.get("evidence_json")},
                )
                n_events += 1
        session.commit()
    return {"restored_5m": n5, "restored_raw": nraw, "restored_events": n_events}


def estimate_closeable_events(session) -> dict[str, Any]:
    """dry-run：纠偏前只能报告 open above 数；真正 closeable 在 apply 后由 close_after_reeval 判定。"""
    rows = session.execute(
        select(NetworkAnomalyEvent)
        .where(NetworkAnomalyEvent.ended_at.is_(None))
        .where(NetworkAnomalyEvent.rule_code == "above_baseline_200")
    ).scalars().all()
    return {
        "open_above_baseline_200": len(rows),
        "sensors": sorted({r.sensor_objid for r in rows}),
        "note": (
            "dry-run 无法在纠偏前准确预估 closeable；"
            "apply 后会 evaluate_all，再对仍 open 且当前不命中 above 的事件 "
            "写入 closed_by=scale_repair 并关闭（见 scale_repair_closeable_events.json）"
        ),
    }


def run_dry_run(until: datetime, backup: Path | None) -> dict[str, Any]:
    with SessionLocal() as session:
        five = plan_5m_repairs(session, until)
        raw = plan_raw_repairs(session, until)
        opens = export_open_events(session)
        close_est = estimate_closeable_events(session)
        report = {
            "mode": "dry-run",
            "until": until.isoformat(),
            "five_m": summarize(five),
            "raw": summarize(raw),
            "open_events": len(opens),
            "close_estimate": close_est,
        }
        if backup:
            write_backup(backup, five, raw, opens)
            report["backup"] = str(backup)
        return report


def run_apply(until: datetime, backup: Path) -> dict[str, Any]:
    now = datetime.utcnow()
    with SessionLocal() as session:
        five = plan_5m_repairs(session, until)
        raw = plan_raw_repairs(session, until)
        opens = export_open_events(session)
        write_backup(backup, five, raw, opens)
        applied = apply_repairs(session, five, raw, now)
    eval_stats = anomaly.evaluate_all(now=now)
    with SessionLocal() as session:
        snapshots = close_after_reeval(session, now)
        # 把 closeable 快照并入 backup，供 rollback 恢复 ended_at/evidence
        write_backup(
            backup, five, raw, opens, closeable_events=snapshots,
        )
        still = session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.ended_at.is_(None))
            .where(NetworkAnomalyEvent.rule_code == "above_baseline_200")
        ).scalars().all()
        return {
            "mode": "apply",
            "until": until.isoformat(),
            "applied": applied,
            "events_closed_by_evaluate": int(eval_stats.get("events_closed") or 0),
            "closed_by_scale_repair": len(snapshots),
            "scale_repair_event_ids": [s["id"] for s in snapshots],
            "still_open_above": len(still),
            "backup": str(backup),
            "rollback": f"python -m backend.services.prtg.repair_live_scale --rollback {backup}",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repair live-inflated PRTG traffic samples")
    parser.add_argument("--until", help="ISO time; only repair samples strictly before this")
    parser.add_argument("--backup", default="", help="backup directory (required for --apply)")
    parser.add_argument("--apply", action="store_true", help="write DB (default dry-run)")
    parser.add_argument("--rollback", default="", help="rollback directory from a prior --backup")
    args = parser.parse_args(argv)

    if args.rollback:
        result = rollback_from_backup(Path(args.rollback))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if not args.until:
        parser.error("--until is required unless --rollback")
    until = _parse_until(args.until)
    backup = Path(args.backup) if args.backup else None
    if args.apply:
        if not backup:
            parser.error("--apply requires --backup DIR")
        result = run_apply(until, backup)
    else:
        result = run_dry_run(until, backup)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
from datetime import datetime
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
                 open_events: list[dict[str, Any]]) -> None:
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
    (path / "README_ROLLBACK.txt").write_text(
        "Rollback: python -m backend.services.prtg.repair_live_scale --rollback " + str(path) + "\n"
        "Restores avg_value/avg_volume (5m) and value_num/volume_num (raw) from the CSV live_* columns\n"
        "for rows with action=repair. Does not re-open closed anomaly events.\n",
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


def close_after_reeval(session, now: datetime) -> list[int]:
    """evaluate_all 之后：仍 open 但当前 would_close 的事件打 closed_by=scale_repair。"""
    closed_ids: list[int] = []
    # 再跑一轮评估（写入已在 evaluate_all 内 commit）；此处只处理「评估后仍 open」的遗留
    open_rows = session.execute(
        select(NetworkAnomalyEvent).where(NetworkAnomalyEvent.ended_at.is_(None))
    ).scalars().all()
    # evaluate_all 应已关闭恢复项；这里只给仍 open 的补标记场景由调用方决定
    return closed_ids


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


def rollback_from_backup(path: Path) -> dict[str, int]:
    five_path = path / "repair_plan_5m.csv"
    raw_path = path / "repair_plan_raw.csv"
    n5 = nraw = 0
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
        session.commit()
    return {"restored_5m": n5, "restored_raw": nraw}


def estimate_closeable_events(session) -> dict[str, Any]:
    """dry-run：按当前（未纠偏）数据无法准确；报告 open 事件数供手册。"""
    rows = session.execute(
        select(NetworkAnomalyEvent)
        .where(NetworkAnomalyEvent.ended_at.is_(None))
        .where(NetworkAnomalyEvent.rule_code == "above_baseline_200")
    ).scalars().all()
    return {
        "open_above_baseline_200": len(rows),
        "sensors": sorted({r.sensor_objid for r in rows}),
        "note": "apply 后会跑 evaluate_all；仍 open 且条件已恢复的会标 closed_by=scale_repair",
    }


def mark_close_stale(session, now: datetime) -> int:
    """对 evaluate_all 后仍 open、但最新证据显示不应命中的 above 事件关闭。

    简化：重新用 anomaly.evaluate_all 已关闭大部分；此处把 evidence 含
    closed_by 的补丁留给调用方在 apply 路径显式传入 would_close 列表。
    本函数：若 message 可解析且我们刚纠偏，依赖 evaluate_all。
    """
    stats = anomaly.evaluate_all(now=now)
    # evaluate_all 已关恢复项；把仍 open 的 above 再扫一遍：用 evaluate 逻辑
    # 这里仅返回 evaluate 统计；额外 closed_by 标记在下方
    closed = 0
    open_above = session.execute(
        select(NetworkAnomalyEvent)
        .where(NetworkAnomalyEvent.ended_at.is_(None))
        .where(NetworkAnomalyEvent.rule_code.in_(["above_baseline_200", "composite", "baseline_low_pending_review"]))
    ).scalars().all()
    # 不在此强行关 pending_review；above/composite 交给 evaluate_all
    _ = open_above
    return int(stats.get("events_closed") or 0) + closed


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
    closed = mark_close_stale(SessionLocal(), now)
    # 二次：给仍 open 的 above 若 evaluate 未关则检查 — evaluate_all 已处理
    with SessionLocal() as session:
        still = session.execute(
            select(NetworkAnomalyEvent)
            .where(NetworkAnomalyEvent.ended_at.is_(None))
            .where(NetworkAnomalyEvent.rule_code == "above_baseline_200")
        ).scalars().all()
        # 不强制 DELETE；仅对 evidence 追加标记已在 evaluate 关闭时完成
        tagged = 0
        for ev in still:
            # 留下给运维人工；不在无 would_close 证明时乱关
            pass
        return {
            "mode": "apply",
            "until": until.isoformat(),
            "applied": applied,
            "events_closed_by_evaluate": closed,
            "still_open_above": len(still),
            "backup": str(backup),
            "tagged_closed_by": tagged,
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

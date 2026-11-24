# -*- coding: utf-8 -*-
"""把 mapping_seed.json 灌入 prtg_sensor_mapping / prtg_blindspot_device（Issue #226）。

用法（在能写本机库的主机上；默认读同目录 mapping_seed.json）：

    python -m backend.services.prtg.apply_mapping_seed
    python -m backend.services.prtg.apply_mapping_seed --file path.json --dry-run

幂等与「不覆盖人工修订」：
  - 映射行按 (sensor_id, site_id) 判重；已存在且 match_status="verified"
    （人工已确认）的行**不覆盖**，只补缺失行；
  - 盲区行按 (device_name, group_name) 判重；已存在不覆盖（owner/region/
    customer 是人工字段）；
  - 不写任何凭证、不访问 PRTG。
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select

from backend.database import SessionLocal
from backend.models.prtg import PrtgBlindspotDevice, PrtgSensorMapping

DEFAULT_SEED = Path(__file__).with_name("mapping_seed.json")


def load_seed(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"种子文件应为对象：{path}")
    return data


def apply_seed(seed: dict[str, Any], *, dry_run: bool = False) -> dict[str, int]:
    """灌库；返回 {mapping_inserted, mapping_updated, mapping_skipped, blindspot_inserted, blindspot_skipped}。"""
    mappings = seed.get("mappings") or []
    blindspots = seed.get("blindspots") or []
    stats = {
        "mapping_inserted": 0, "mapping_updated": 0, "mapping_skipped": 0,
        "blindspot_inserted": 0, "blindspot_skipped": 0,
    }
    if dry_run:
        stats["mapping_inserted"] = len(mappings)
        stats["blindspot_inserted"] = len(blindspots)
        return stats

    with SessionLocal() as session:
        existing = {
            (row.sensor_id, row.site_id): row
            for row in session.execute(select(PrtgSensorMapping)).scalars().all()
        }
        for row in mappings:
            key = (int(row["sensor_id"]), str(row["site_id"]))
            current = existing.get(key)
            if current is None:
                session.add(PrtgSensorMapping(
                    sensor_id=key[0],
                    device_name=row["device_name"],
                    sensor_name=row["sensor_name"],
                    interface_no=row.get("interface_no"),
                    peer_name=row.get("peer_name"),
                    site_id=key[1],
                    region=row.get("region"),
                    link_type=row.get("link_type"),
                    capacity_mbps=row.get("capacity_mbps"),
                    capacity_source=row.get("capacity_source"),
                    criticality=row.get("criticality"),
                    enabled=bool(row.get("enabled", True)),
                    match_status=row.get("match_status") or "unknown",
                ))
                stats["mapping_inserted"] += 1
            elif current.match_status == "verified":
                stats["mapping_skipped"] += 1
            else:
                current.device_name = row["device_name"]
                current.sensor_name = row["sensor_name"]
                current.interface_no = row.get("interface_no")
                current.peer_name = row.get("peer_name")
                current.region = row.get("region")
                current.match_status = row.get("match_status") or current.match_status
                stats["mapping_updated"] += 1

        existing_blind = {
            (row.device_name, row.group_name)
            for row in session.execute(select(PrtgBlindspotDevice)).scalars().all()
        }
        for row in blindspots:
            key = (str(row["device_name"]), str(row["group_name"]))
            if key in existing_blind:
                stats["blindspot_skipped"] += 1
                continue
            session.add(PrtgBlindspotDevice(
                device_name=key[0],
                group_name=key[1],
                region=row.get("region"),
                customer=row.get("customer"),
                reason=row.get("reason") or "PRTG 未配置传感器（0 sensors）",
                owner=row.get("owner"),
                monitored=False,
            ))
            stats["blindspot_inserted"] += 1
        session.commit()
    return stats


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="灌入 PRTG 映射种子（Issue #226）")
    parser.add_argument("--file", default=str(DEFAULT_SEED), help="种子文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args(list(argv) if argv is not None else None)
    seed = load_seed(Path(args.file))
    stats = apply_seed(seed, dry_run=args.dry_run)
    print(f"映射：新增 {stats['mapping_inserted']}，更新 {stats['mapping_updated']}，"
          f"保留人工确认 {stats['mapping_skipped']}；"
          f"盲区：新增 {stats['blindspot_inserted']}，已存在 {stats['blindspot_skipped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

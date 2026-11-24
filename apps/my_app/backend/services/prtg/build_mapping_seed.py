# -*- coding: utf-8 -*-
"""生成 prtg_sensor_mapping / prtg_blindspot_device 种子数据（Issue #226）。

用法（在能访问 PRTG 内网的主机上，如 MSNOC1；VM 访问不到 PRTG 内网）：

    # 直连 PRTG（凭据只从环境变量读：PRTG_BASE_URL / PRTG_USERNAME / PRTG_PASSHASH）
    python -m backend.services.prtg.build_mapping_seed

    # 离线：用录制的 table.json 响应文件（{"sensors": [...]} / {"devices": [...]}）
    python -m backend.services.prtg.build_mapping_seed --from-file sensors.json devices.json

输出 mapping_seed.json（与本文件同目录，可用 --out 改）：
{"mappings": [...], "blindspots": [...]}。生成后须人工确认
match_status=auto 的行、补 capacity_mbps / link_type / 盲区 owner，
再写入数据库（本脚本不写库、不打印任何凭据）。
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any

from backend.services.prtg.mapping import (
    build_blindspot_rows,
    build_mapping_rows,
    load_topology_sites,
)

_SENSOR_COLUMNS = ["objid", "group", "device", "sensor", "status", "type"]
_DEVICE_COLUMNS = ["objid", "group", "device"]


def _default_sites_path() -> Path:
    # 本文件在 apps/my_app/backend/services/prtg/，仓库根为上溯 5 层。
    return Path(__file__).resolve().parents[5] / "apps" / "portal" / "src" / "data" / "orangeTopologySites.json"


def _rows_from_table_json(text: str, key: str) -> list[dict[str, Any]]:
    payload = json.loads(text)
    rows = payload.get(key)
    if not isinstance(rows, list):
        raise ValueError(f"table 响应缺少 {key} 数组")
    return rows


def fetch_live() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """直连 PRTG 拉 sensors / devices（显式 columns，含 objid）。"""
    from backend.services.prtg.client import PrtgClient, PrtgConfig

    with PrtgClient(PrtgConfig.from_env()) as client:
        sensors = _rows_from_table_json(
            client.table_json("sensors", columns=_SENSOR_COLUMNS, count=100), "sensors"
        )
        devices = _rows_from_table_json(
            client.table_json("devices", columns=_DEVICE_COLUMNS, count=200), "devices"
        )
    return sensors, devices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 PRTG 映射种子数据（Issue #226）")
    parser.add_argument("--from-file", nargs=2, metavar=("SENSORS_JSON", "DEVICES_JSON"),
                        help="离线模式：录制的 sensors / devices table 响应文件")
    parser.add_argument("--sites", default=str(_default_sites_path()),
                        help="orangeTopologySites.json 路径")
    parser.add_argument("--out", default=str(Path(__file__).with_name("mapping_seed.json")),
                        help="输出路径")
    args = parser.parse_args(argv)

    if args.from_file:
        sensors = _rows_from_table_json(Path(args.from_file[0]).read_text(encoding="utf-8"), "sensors")
        devices = _rows_from_table_json(Path(args.from_file[1]).read_text(encoding="utf-8"), "devices")
        source = "file"
    else:
        sensors, devices = fetch_live()
        source = "live"

    sites = load_topology_sites(args.sites)
    mappings = build_mapping_rows(sensors, sites)
    blindspots = build_blindspot_rows(devices)

    payload = {"generated_from": source, "mappings": mappings, "blindspots": blindspots}
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    unmatched = [r for r in mappings if r["match_status"] == "unknown"]
    print(f"传感器 {len(mappings)} 行：自动匹配 {len(mappings) - len(unmatched)}，未匹配 {len(unmatched)}")
    for row in unmatched:
        print(f"  Unknown: sensor_id={row['sensor_id']} device={row['device_name']} name={row['sensor_name']}")
    print(f"盲区设备 {len(blindspots)} 台（Core Firewalls + VSAT Customers）")
    print(f"已写出：{args.out}（capacity_mbps / link_type / owner 待人工补，未写库）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

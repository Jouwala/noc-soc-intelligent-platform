# -*- coding: utf-8 -*-
"""PRTG 传感器 → 拓扑站点映射构建（Issue #226）。纯函数，不发网络请求。

口径（来自 #225 字典与字段对照表，均已实测）：
  - 传感器名解析：`^\\((\\d+)\\) to (.+)$` → (interface_no, peer_name)，
    如 "(10152) to CSQUARED" → ("10152", "CSQUARED")；无编号名（如
    "B2B_CORE-SW G1/0/45"）只尝试提取 Gx/y/z 形态接口号，peer 不猜。
  - peer 与拓扑站点匹配：站名来自 orangeTopologySites.json 的 n 字段，
    region 取 s 字段。归一化与门户 topologyData.ts 的 norm 一致
    （小写 + 去空白），另加一档宽松归一（再去 _ 与 -）兜底；
    两档都未命中 → site_id 显式 "Unknown"，不静默丢弃。
  - capacity_mbps 不在本模块产生：PRTG 无此数据，来源只允许
    snmp_ifspeed / manual，落定前留空（消费方须显式提示「待补全」）。
  - 盲区设备：分组为 Core Firewalls / VSAT Customers 的设备（PRTG 里
    0 传感器），monitored=False，原因/责任方字段随行输出待人工补。
"""
import json
import re
from pathlib import Path
from typing import Any, Iterable

UNKNOWN_SITE = "Unknown"

BLINDSPOT_GROUPS = ("Core Firewalls", "VSAT Customers")
BLINDSPOT_DEFAULT_REASON = "PRTG 未配置传感器（0 sensors）"

_PEER_PATTERN = re.compile(r"^\((\d+)\)\s+to\s+(.+)$")
_INTERFACE_TOKEN = re.compile(r"\b([A-Za-z]+\d+(?:/\d+)+)\b")


def parse_sensor_name(name: str) -> tuple[str | None, str | None]:
    """传感器名 → (interface_no, peer_name)。解析不出返回 (None, None)。"""
    text = (name or "").strip()
    match = _PEER_PATTERN.match(text)
    if match:
        return match.group(1), match.group(2).strip()
    token = _INTERFACE_TOKEN.search(text)
    if token:
        return token.group(1), None
    return None, None


def _norm(text: str) -> str:
    """与门户 topologyData.ts 的 norm 一致：小写 + 去全部空白。"""
    return re.sub(r"\s+", "", (text or "").lower())


def _norm_loose(text: str) -> str:
    """宽松归一：在 _norm 基础上再去掉下划线与连字符。"""
    return re.sub(r"[_\-]+", "", _norm(text))


def load_topology_sites(path: str | Path) -> list[dict[str, Any]]:
    """读 orangeTopologySites.json，返回 [{"n": 站名, "s": 区域代码, ...}]。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"拓扑站点文件应为数组：{path}")
    return data


def match_peer_to_site(
    peer_name: str | None, sites: Iterable[dict[str, Any]]
) -> dict[str, Any] | None:
    """peer 名 → 拓扑站点（先严格归一、后宽松归一）；未命中返回 None。"""
    if not peer_name:
        return None
    site_list = list(sites)
    for normalize in (_norm, _norm_loose):
        target = normalize(peer_name)
        for site in site_list:
            if normalize(str(site.get("n", ""))) == target:
                return site
    return None


def build_mapping_rows(
    sensors: Iterable[dict[str, Any]], sites: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """传感器清单（table 接口行：objid/device/sensor[/priority]）→ 映射行。

    每个传感器恰好一行（首轮自动匹配是单站点；多对多的额外行由人工
    确认时补）。未匹配行 site_id="Unknown"、match_status="unknown"。
    priority（★ 星数）若存在则转 criticality 整数（1–5），不存在留空。
    """
    site_list = list(sites)
    rows: list[dict[str, Any]] = []
    for sensor in sensors:
        sensor_name = str(sensor.get("sensor", ""))
        interface_no, peer_name = parse_sensor_name(sensor_name)
        site = match_peer_to_site(peer_name, site_list)
        priority = sensor.get("priority")
        criticality = None
        if isinstance(priority, str):
            stars = priority.count("★")
            criticality = stars if 1 <= stars <= 5 else None
        elif isinstance(priority, int) and 1 <= priority <= 5:
            criticality = priority
        rows.append(
            {
                "sensor_id": int(sensor["objid"]),
                "device_name": str(sensor.get("device", "")),
                "sensor_name": sensor_name,
                "interface_no": interface_no,
                "peer_name": peer_name,
                "site_id": str(site["n"]) if site else UNKNOWN_SITE,
                "region": str(site["s"]) if site and site.get("s") else None,
                "link_type": None,
                "capacity_mbps": None,
                "capacity_source": None,
                "criticality": criticality,
                "enabled": True,
                "match_status": "auto" if site else "unknown",
            }
        )
    return rows


def build_blindspot_rows(devices: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """设备清单（table 接口行：device/group）→ 盲区行（仅两组 0 传感器设备）。"""
    rows: list[dict[str, Any]] = []
    for device in devices:
        group = str(device.get("group", ""))
        if group not in BLINDSPOT_GROUPS:
            continue
        rows.append(
            {
                "device_name": str(device.get("device", "")),
                "group_name": group,
                "region": None,
                "customer": None,
                "reason": BLINDSPOT_DEFAULT_REASON,
                "owner": None,
                "monitored": False,
            }
        )
    return rows

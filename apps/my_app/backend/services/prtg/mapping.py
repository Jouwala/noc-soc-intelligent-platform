# -*- coding: utf-8 -*-
"""PRTG 传感器 → 拓扑站点映射构建（Issue #226）。纯函数，不发网络请求。

口径（来自 #225 字典与字段对照表，均已实测）：
  - 传感器名解析：`^\\((\\d+)\\) to (.+)$` → (interface_no, peer_name)，
    如 "(10152) to CSQUARED" → ("10152", "CSQUARED")；无编号名（如
    "B2B_CORE-SW G1/0/45"）只尝试提取 Gx/y/z 形态接口号，peer 不猜。
  - peer 与拓扑站点匹配：站名来自 orangeTopologySites.json 的 n 字段，
    region 取 s 字段。归一化去全部非字母数字（括号/空白/下划线/连字符），
    并对 peer 去掉尾部设备类型后缀（-SW/-RTR/-FW…）再比一次；两轮都
    未命中 → site_id 显式 "Unknown"，不静默丢弃，也不按相似度猜站点。
    实测：553 个拓扑站是客户站点分布，PRTG 25 个传感器多为核心/ISP 链路，
    只有 MATADI-SW → Matadi 这类能自动命中，其余等人工确认。
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

# 盲区责任方（#226 验收「各有原因与责任方」）：按设备组先落到对口团队，
# 属**暂定责任方**，人工确认后可在库里改；apply_mapping_seed 不覆盖已有值。
BLINDSPOT_OWNER = {
    "Core Firewalls": "传输与 IP 网管部",
    "VSAT Customers": "MS 项目组",
}

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


_DEVICE_SUFFIX = re.compile(r"[-_ ](sw|rtr|router|switch|fw|firewall|gw|core)$", re.IGNORECASE)


def _norm(text: str) -> str:
    """站名/对端名归一：小写 + 去全部非字母数字（括号、空白、下划线、连字符都不参与）。"""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _peer_variants(peer_name: str) -> list[str]:
    """对端名的候选写法：原名 + 去掉尾部设备类型后缀（如 MATADI-SW → MATADI）。"""
    variants = [peer_name]
    stripped = _DEVICE_SUFFIX.sub("", peer_name.strip())
    if stripped != peer_name:
        variants.append(stripped)
    return variants


def load_topology_sites(path: str | Path) -> list[dict[str, Any]]:
    """读 orangeTopologySites.json，返回 [{"n": 站名, "s": 区域代码, ...}]。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"拓扑站点文件应为数组：{path}")
    return data


def match_peer_to_site(
    peer_name: str | None, sites: Iterable[dict[str, Any]]
) -> dict[str, Any] | None:
    """peer 名 → 拓扑站点；未命中返回 None。

    2026-11-24 实测口径修正：拓扑 553 个站是**客户站点分布**，PRTG 25 个传感器
    多为核心/ISP 链路（CSQUARED、BICS_VOICE、PEER-LINK 等），两边命名体系不同，
    自动匹配只能命中少量（如 MATADI-SW → Matadi）。命中不了的显式 Unknown，
    等人工确认；**不得**按相似度猜站点。
    """
    if not peer_name:
        return None
    site_list = list(sites)
    site_index = {_norm(str(site.get("n", ""))): site for site in site_list}
    for candidate in _peer_variants(peer_name):
        site = site_index.get(_norm(candidate))
        if site is not None:
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
        device_name = str(device.get("device", ""))
        rows.append(
            {
                "device_name": device_name,
                "group_name": group,
                "region": None,
                # VSAT 设备名就是客户名（Supreme / Lee Group / BHP-…），直接落到 customer；
                # 防火墙组没有客户概念，留空。
                "customer": device_name if group == "VSAT Customers" else None,
                "reason": BLINDSPOT_DEFAULT_REASON,
                "owner": BLINDSPOT_OWNER.get(group),
                "monitored": False,
            }
        )
    return rows

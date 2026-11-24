# -*- coding: utf-8 -*-
"""门户流量拓扑服务（Issue #230）。

建模口径
--------
- **Sensor ≠ Interface ≠ Link ≠ Edge**：一条 `network_links` 绑定一个 PRTG
  传感器；两端节点由设备名 + 传感器名里的对端（`(10152) to CSQUARED`）
  解析得到；前端一条链路只画一条边。
- 节点坐标是 **0–1000 相对坐标**（画布），不是经纬度；人工拖动后
  `pinned=true` 并落 `network_topology_nodes`。
- 容量不是 PRTG 数据：只从 `network_link_capacity` 取；缺失时利用率返回
  `null`，前端「待补全」，**绝不按 0 或猜测值算百分比**。
- 利用率 `U = max(In/Cin, Out/Cout)`，分方向容量；>100% 标 OVER CAPACITY。
- 数据质量随每条链路返回：`capacity_known / traffic_available /
  sample_age_seconds / mapping_confidence`，PRTG 不可达时页面显示最后数据并标过期。

自动生成是幂等的：重复调用只补新节点/链路，保留人工坐标、容量与置信度。
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import func, select

from backend.database import SessionLocal
from backend.models.network_topology import (
    NetworkLink,
    NetworkLinkLayout,
    NetworkNodeAlias,
    NetworkTopologyNode,
)
from backend.models.prtg import PrtgSensorMapping
from backend.models.prtg_collector import (
    NetworkLinkCapacity,
    PrtgChannelSample,
    PrtgChannelSample5m,
    PrtgSensor,
)
from backend.services.prtg.dashboard import is_traffic_sensor
from backend.services.prtg.mapping import parse_sensor_name

logger = logging.getLogger(__name__)

NODE_TYPES = ("device", "peer", "customer", "unknown")
PERIODS = {
    "live": {"minutes": 15, "table": "raw"},
    "1h": {"minutes": 60, "table": "5m"},
    "24h": {"minutes": 1440, "table": "5m"},
    "7d": {"minutes": 10080, "table": "5m"},
}
# 未监控 ≠ 正常：Paused/Unknown 不预热成 Up，空节点也不默认 Up。
STATUS_RANK = {"Up": 0, "Unusual": 1, "Unknown": 2, "Unmonitored": 2, "Down": 3}
STATUS_MAP = {"up": "Up", "unusual": "Unusual", "warning": "Unusual",
              "down": "Down", "paused": "Unmonitored", "unknown": "Unmonitored"}


def _norm(text: str | None) -> str:
    import re

    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _status_of(raw: str | None) -> str:
    return STATUS_MAP.get((raw or "").strip().lower(), "Unmonitored")


def _worst(statuses: Iterable[str | None]) -> str:
    """节点状态取链路里最差的一档；没有任何链路才回退「未监控」。"""
    worst: str | None = None
    for status in statuses:
        mapped = _status_of(status)
        if worst is None or STATUS_RANK.get(mapped, 2) > STATUS_RANK.get(worst, 2):
            worst = mapped
    return worst or "Unmonitored"


def _place_nodes(nodes: list[NetworkTopologyNode]) -> None:
    """给新节点分配圆形坐标（device 内圈、peer 中圈、unknown 外圈）。"""
    groups = {"device": [], "peer": [], "customer": [], "unknown": []}
    for node in nodes:
        groups.setdefault(node.node_type, groups["unknown"]).append(node)
    # 前端画布 1000×620（ZoomStage baseW/baseH），坐标必须落在视口内；
    # 之前的 500 圆心 + 470 半径会把 y>620 的节点裁掉。
    radii = {"device": 150, "peer": 270, "customer": 210}
    default_radius = 290
    import math

    for node_type, items in groups.items():
        radius = radii.get(node_type, default_radius)
        for index, node in enumerate(items):
            if node.x is not None and node.y is not None:
                continue
            count = max(1, len(items))
            angle = 2 * math.pi * index / count + (0.2 if node_type == "peer" else 0)
            node.x = round(500 + radius * math.cos(angle), 1)
            node.y = round(310 + radius * math.sin(angle), 1)


def ensure_topology(now: datetime | None = None) -> dict[str, int]:
    """从 PRTG 传感器幂等生成节点/链路；保留人工坐标与映射。"""
    now = now or datetime.utcnow()
    with SessionLocal() as session:
        # Ping 的 channel 0 是 Ping Time（msec），当成 Traffic 会算出 0.001 Mbps 假链路
        sensors = [
            s for s in session.execute(
                select(PrtgSensor).where(PrtgSensor.active.is_(True)).order_by(PrtgSensor.objid)
            ).scalars()
            if is_traffic_sensor(s.sensortype)
        ]
        nodes = {n.node_key: n for n in session.execute(select(NetworkTopologyNode)).scalars()}
        links = {l.link_key: l for l in session.execute(select(NetworkLink)).scalars()}
        links_by_sensor = {l.sensor_objid: l for l in links.values()}
        capacities = {
            c.sensor_objid: c for c in session.execute(select(NetworkLinkCapacity)).scalars()
        }
        mappings = {}
        for row in session.execute(select(PrtgSensorMapping)).scalars():
            current = mappings.get(row.sensor_id)
            if current is None or (row.match_status == "verified" and current.match_status != "verified"):
                mappings[row.sensor_id] = row

        created_nodes: list[NetworkTopologyNode] = []
        created_links = 0

        def node_for(key: str, name: str, node_type: str, device_objid: int | None = None) -> NetworkTopologyNode:
            node = nodes.get(key)
            if node is None:
                node = NetworkTopologyNode(node_key=key, name=name, node_type=node_type,
                                           device_objid=device_objid, updated_at=now)
                session.add(node)
                session.flush()
                nodes[key] = node
                created_nodes.append(node)
            elif device_objid and node.device_objid is None:
                node.device_objid = device_objid
            return node

        for sensor in sensors:
            device_key = f"dev:{_norm(sensor.device_name)}"
            source = node_for(device_key, sensor.device_name or f"device-{sensor.objid}",
                              "device", sensor.device_objid)
            interface_no, peer_name = parse_sensor_name(sensor.sensor_name or "")
            mapping = mappings.get(sensor.objid)
            if peer_name:
                peer_key = f"peer:{_norm(peer_name)}"
                target = node_for(peer_key, peer_name, "peer")
            elif mapping and mapping.site_id and mapping.site_id != "Unknown":
                target = node_for(f"customer:{_norm(mapping.site_id)}", mapping.site_id, "customer")
            else:
                target = node_for(f"unknown:{sensor.objid}",
                                  sensor.sensor_name or f"sensor-{sensor.objid}", "unknown")

            link_key = f"sensor:{sensor.objid}"
            link = links.get(link_key) or links_by_sensor.get(sensor.objid)
            confidence = "verified" if mapping and mapping.match_status == "verified" else (
                "candidate" if mapping and mapping.match_status == "auto" else "discovered"
            )
            capacity = capacities.get(sensor.objid)
            if link is None:
                link = NetworkLink(
                    link_key=link_key, sensor_objid=sensor.objid,
                    source_node_id=source.id, target_node_id=target.id,
                    capacity_ab_mbps=capacity.capacity_mbps if capacity else None,
                    capacity_ba_mbps=capacity.capacity_mbps if capacity else None,
                    capacity_source=capacity.capacity_source if capacity else None,
                    mapping_confidence=confidence, link_type=None, updated_at=now,
                )
                session.add(link)
                created_links += 1
            else:
                # 人工绑定（link_key=manual:）冻结两端；自动发现链路继续跟随 PRTG 名称刷新。
                if not link.link_key.startswith("manual:"):
                    link.source_node_id = source.id
                    link.target_node_id = target.id
                if link.capacity_ab_mbps is None and capacity and capacity.capacity_mbps:
                    link.capacity_ab_mbps = capacity.capacity_mbps
                    link.capacity_ba_mbps = capacity.capacity_mbps
                    link.capacity_source = capacity.capacity_source
                if link.mapping_confidence == "discovered" and confidence != "discovered":
                    link.mapping_confidence = confidence
        # 清理历史脏数据：非流量链路（如旧的 Ping 链路）与失去链路的孤儿节点；
        # 未固定的旧坐标如果落在 1000×620 画布之外，重新自动布局（人工 pinned 不动）。
        traffic_ids = {s.objid for s in sensors}
        for stale in list(session.execute(select(NetworkLink)).scalars()):
            if stale.sensor_objid not in traffic_ids:
                session.delete(stale)
        session.flush()
        used_nodes = set()
        for link_row in session.execute(select(NetworkLink)).scalars():
            used_nodes.update((link_row.source_node_id, link_row.target_node_id))
        for node in list(session.execute(select(NetworkTopologyNode)).scalars()):
            if node.id not in used_nodes and node.node_type != "device" and node.meta != "manual":
                session.delete(node)
            elif not node.pinned and (
                node.x is None or node.y is None or not 0 <= node.x <= 1000 or not 0 <= node.y <= 620
            ):
                node.x = None
                node.y = None
        session.flush()
        _place_nodes(list(session.execute(select(NetworkTopologyNode)).scalars()))
        session.commit()
        total_links = session.execute(select(func.count()).select_from(NetworkLink)).scalar() or 0
        total_nodes = session.execute(select(func.count()).select_from(NetworkTopologyNode)).scalar() or 0
        return {"nodes": total_nodes, "links": total_links,
                "created_nodes": len(created_nodes), "created_links": created_links}


def _samples(session, sensor_ids: list[int], period: str) -> dict[tuple[int, int], dict[str, Any]]:
    """取每传感器 In/Out 的最新值或窗口均值（Mbps）。"""
    cfg = PERIODS.get(period) or PERIODS["24h"]
    since = datetime.utcnow() - timedelta(minutes=cfg["minutes"])
    model = PrtgChannelSample if cfg["table"] == "raw" else PrtgChannelSample5m
    time_col = model.sample_time if cfg["table"] == "raw" else model.bucket_time
    value_col = model.value_num if cfg["table"] == "raw" else model.avg_value
    rows = session.execute(
        select(model.sensor_objid, model.channel_objid, time_col, value_col)
        .where(model.sensor_objid.in_(sensor_ids))
        .where(model.channel_objid.in_((0, 1)))
        .where(time_col >= since)
    ).all()
    grouped: dict[tuple[int, int], list[tuple[datetime, float]]] = {}
    for sensor_objid, channel_objid, ts, value in rows:
        if value is None:
            continue
        grouped.setdefault((int(sensor_objid), int(channel_objid)), []).append((ts, float(value)))
    result: dict[tuple[int, int], dict[str, Any]] = {}
    for key, values in grouped.items():
        values.sort()
        if cfg["table"] == "raw":
            ts, value = values[-1]
            result[key] = {"mbps": round(value * 8 / 1e6, 3), "sample_time": ts}
        else:
            avg = sum(v for _, v in values) / len(values)
            result[key] = {"mbps": round(avg * 8 / 1e6, 3), "sample_time": values[-1][0]}
    return result


def _utilization(link: NetworkLink, in_mbps: float | None, out_mbps: float | None) -> float | None:
    # source_node=A（传感器所在设备），target=B。PRTG Traffic In 是进入 A，即 B→A；
    # Traffic Out 是 A→B。因此 In 配 capacity_ba、Out 配 capacity_ab。
    values = []
    if link.capacity_ba_mbps and in_mbps is not None:
        values.append(in_mbps / link.capacity_ba_mbps)
    if link.capacity_ab_mbps and out_mbps is not None:
        values.append(out_mbps / link.capacity_ab_mbps)
    if not values:
        return None
    return round(max(values) * 100, 2)


def traffic(period: str = "live") -> dict[str, Any]:
    """拓扑数据：节点 + 链路 + KPI + 数据质量（供门户 Traffic Topology 视图）。"""
    ensure_topology()
    period = period if period in PERIODS else "live"
    now = datetime.utcnow()
    with SessionLocal() as session:
        nodes = list(session.execute(select(NetworkTopologyNode)).scalars())
        links = list(session.execute(select(NetworkLink)).scalars())
        sensors = {
            s.objid: s for s in session.execute(select(PrtgSensor)).scalars()
        }
        layouts = {l.link_id: l for l in session.execute(select(NetworkLinkLayout)).scalars()}
        samples = _samples(session, list(sensors.keys()), period)

    node_by_id = {node.id: node for node in nodes}
    link_payload = []
    node_status: dict[int, list[str | None]] = {}
    for link in links:
        sensor = sensors.get(link.sensor_objid)
        in_sample = samples.get((link.sensor_objid, 0))
        out_sample = samples.get((link.sensor_objid, 1))
        in_mbps = in_sample["mbps"] if in_sample else None
        out_mbps = out_sample["mbps"] if out_sample else None
        utilization = _utilization(link, in_mbps, out_mbps)
        sample_time = max(
            (s["sample_time"] for s in (in_sample, out_sample) if s), default=None
        )
        age = int((now - sample_time).total_seconds()) if sample_time else None
        source = node_by_id.get(link.source_node_id)
        target = node_by_id.get(link.target_node_id)
        status = _status_of(sensor.status if sensor else None)
        node_status.setdefault(link.source_node_id, []).append(status)
        node_status.setdefault(link.target_node_id, []).append(status)
        link_payload.append({
            "id": link.id,
            "key": link.link_key,
            "sensor_id": link.sensor_objid,
            "source_node": link.source_node_id,
            "target_node": link.target_node_id,
            "source": source.name if source else None,
            "target": target.name if target else None,
            "status": status,
            "in_mbps": in_mbps,
            "out_mbps": out_mbps,
            "capacity_ab_mbps": link.capacity_ab_mbps,
            "capacity_ba_mbps": link.capacity_ba_mbps,
            "utilization_pct": utilization,
            "mapping_confidence": link.mapping_confidence,
            "link_type": link.link_type,
            "bend_offset": layouts.get(link.id).bend_offset if layouts.get(link.id) else None,
            "data_quality": {
                "capacity_known": bool(link.capacity_ab_mbps or link.capacity_ba_mbps),
                "traffic_available": in_mbps is not None or out_mbps is not None,
                "sample_age_seconds": age,
                "mapping_confidence": link.mapping_confidence,
            },
        })

    node_payload = []
    for node in nodes:
        node_payload.append({
            "id": node.id,
            "key": node.node_key,
            "name": node.name,
            "type": node.node_type if node.node_type in NODE_TYPES else "unknown",
            "x": node.x,
            "y": node.y,
            "pinned": node.pinned,
            "status": _worst(node_status.get(node.id, [])),
        })

    up = sum(1 for l in link_payload if l["status"] == "Up")
    unusual = sum(1 for l in link_payload if l["status"] == "Unusual")
    down = sum(1 for l in link_payload if l["status"] == "Down")
    unknown = sum(1 for l in link_payload if l["status"] in ("Unknown", "Unmonitored"))
    over = sum(1 for l in link_payload if (l["utilization_pct"] or 0) > 100)
    ages = [l["data_quality"]["sample_age_seconds"] for l in link_payload
            if l["data_quality"]["sample_age_seconds"] is not None]
    return {
        "generated_at": now.isoformat() + "Z",
        "period": period,
        "source": "PRTG",
        "data_age_seconds": max(ages) if ages else None,
        "kpi": {
            "nodes": len(node_payload),
            "links": len(link_payload),
            "up": up,
            "unusual": unusual,
            "down": down,
            "unknown": unknown,
            "over_capacity": over,
            "capacity_known": sum(1 for l in link_payload if l["data_quality"]["capacity_known"]),
            "capacity_total": len(link_payload),
        },
        "nodes": node_payload,
        "links": link_payload,
    }


def layout() -> dict[str, Any]:
    ensure_topology()
    with SessionLocal() as session:
        nodes = list(session.execute(select(NetworkTopologyNode)).scalars())
    return {"nodes": [{"id": n.id, "x": n.x, "y": n.y, "pinned": n.pinned} for n in nodes]}


def update_layout(items: Iterable[dict[str, Any]], now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.utcnow()
    updated = 0
    with SessionLocal() as session:
        nodes = {n.id: n for n in session.execute(select(NetworkTopologyNode)).scalars()}
        for item in items:
            try:
                node = nodes.get(int(item.get("id") or 0))
                x, y = item.get("x"), item.get("y")
                if node is None or x is None or y is None:
                    continue
                x, y = float(x), float(y)
            except (TypeError, ValueError):
                continue
            node.x = max(0.0, min(1000.0, x))
            node.y = max(0.0, min(620.0, y))
            node.pinned = bool(item.get("pinned", True))
            node.updated_at = now
            updated += 1
        session.commit()
    return {"updated": updated}


def mappings() -> dict[str, Any]:
    ensure_topology()
    with SessionLocal() as session:
        links = list(session.execute(select(NetworkLink)).scalars())
        sensors = {s.objid: s for s in session.execute(select(PrtgSensor)).scalars()}
    return {"links": [{
        "id": link.id,
        "sensor_id": link.sensor_objid,
        "sensor_name": sensors[link.sensor_objid].sensor_name if link.sensor_objid in sensors else None,
        "device_name": sensors[link.sensor_objid].device_name if link.sensor_objid in sensors else None,
        "capacity_ab_mbps": link.capacity_ab_mbps,
        "capacity_ba_mbps": link.capacity_ba_mbps,
        "capacity_source": link.capacity_source,
        "link_type": link.link_type,
        "mapping_confidence": link.mapping_confidence,
    } for link in links]}


def _capacity_value(value: Any) -> float | None:
    """容量只接受正有限值；0/负数归一为「未登记」；非法值抛 ValueError（不落库）。"""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"容量不是数字：{value!r}") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"容量不是有限数值：{value!r}")
    return number if number > 0 else None


def update_mappings(items: Iterable[dict[str, Any]], now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.utcnow()
    # 先全部校验，任何一项非法就抛错，**不写库**（避免把非法容量清成 None 再镜像到 SLA）。
    validated: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("links 每一项必须是对象")
        try:
            link_id = int(item.get("id") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"link id 非法：{item.get('id')!r}") from exc
        entry: dict[str, Any] = {"id": link_id}
        if "capacity_ab_mbps" in item:
            entry["capacity_ab_mbps"] = _capacity_value(item.get("capacity_ab_mbps"))
        if "capacity_ba_mbps" in item:
            entry["capacity_ba_mbps"] = _capacity_value(item.get("capacity_ba_mbps"))
        if "capacity_source" in item:
            entry["capacity_source"] = item.get("capacity_source")
        if "link_type" in item:
            entry["link_type"] = item.get("link_type")
        if "mapping_confidence" in item:
            confidence = item.get("mapping_confidence")
            if confidence not in ("verified", "candidate", "discovered"):
                raise ValueError(f"mapping_confidence 非法：{confidence!r}")
            entry["mapping_confidence"] = confidence
        validated.append(entry)

    updated = 0
    with SessionLocal() as session:
        links = {link.id: link for link in session.execute(select(NetworkLink)).scalars()}
        capacities = {c.sensor_objid: c for c in session.execute(select(NetworkLinkCapacity)).scalars()}
        for entry in validated:
            link = links.get(entry["id"])
            if link is None:
                continue
            if "capacity_ab_mbps" in entry:
                link.capacity_ab_mbps = entry["capacity_ab_mbps"]
            if "capacity_ba_mbps" in entry:
                link.capacity_ba_mbps = entry["capacity_ba_mbps"]
            if "capacity_source" in entry:
                link.capacity_source = entry["capacity_source"]
            if "link_type" in entry:
                link.link_type = entry["link_type"]
            if "mapping_confidence" in entry:
                link.mapping_confidence = entry["mapping_confidence"]
            link.updated_at = now
            # SLA 仍是单容量口径（Phase 1），镜像 AB 方向；BA 只存在于拓扑表。
            if "capacity_ab_mbps" in entry:
                capacity = capacities.get(link.sensor_objid)
                if capacity is None:
                    capacity = NetworkLinkCapacity(sensor_objid=link.sensor_objid)
                    session.add(capacity)
                    capacities[link.sensor_objid] = capacity
                capacity.capacity_mbps = link.capacity_ab_mbps
                if "capacity_source" in entry:
                    capacity.capacity_source = link.capacity_source
                capacity.updated_at = now
            updated += 1
        session.commit()
    return {"updated": updated}


def create_node(name: str, node_type: str = "unknown", x: float | None = None,
                y: float | None = None, now: datetime | None = None) -> dict[str, Any]:
    """人工新增节点（Phase 2）。节点 key 用 manual:<uuid>，不覆盖自动发现结果。"""
    import uuid

    if not (name or "").strip():
        raise ValueError("节点名不能为空")
    if node_type not in NODE_TYPES:
        raise ValueError(f"节点类型非法：{node_type!r}")
    now = now or datetime.utcnow()
    with SessionLocal() as session:
        node = NetworkTopologyNode(
            node_key=f"manual:{uuid.uuid4().hex}", name=name.strip(), node_type=node_type,
            meta="manual",
            x=max(0.0, min(1000.0, float(x))) if x is not None else 500.0,
            y=max(0.0, min(620.0, float(y))) if y is not None else 310.0,
            pinned=True, updated_at=now,
        )
        session.add(node)
        session.commit()
        session.refresh(node)
        return {"id": node.id, "name": node.name, "type": node.node_type,
                "x": node.x, "y": node.y}


def delete_node(node_id: int, now: datetime | None = None) -> dict[str, Any]:
    """删除人工节点；仍被链路引用的节点拒绝删除（先改链路或删链路）。"""
    with SessionLocal() as session:
        node = session.execute(
            select(NetworkTopologyNode).where(NetworkTopologyNode.id == node_id)
        ).scalar_one_or_none()
        if node is None:
            raise LookupError("节点不存在")
        used = session.execute(
            select(NetworkLink.id)
            .where((NetworkLink.source_node_id == node_id) | (NetworkLink.target_node_id == node_id))
            .limit(1)
        ).first()
        if used:
            raise ValueError("节点仍被链路引用，先删除或改绑链路")
        for alias in session.execute(
            select(NetworkNodeAlias).where(NetworkNodeAlias.node_id == node_id)
        ).scalars():
            session.delete(alias)
        session.delete(node)
        session.commit()
    return {"deleted": node_id}


def create_link(sensor_objid: int, source_node_id: int, target_node_id: int,
                link_type: str | None = None, capacity_ab_mbps: Any = None,
                capacity_ba_mbps: Any = None, capacity_source: str = "manual",
                now: datetime | None = None) -> dict[str, Any]:
    """把未绑定的 Traffic 传感器绑成一条链路（Phase 2 的「绑定传感器」）。"""
    now = now or datetime.utcnow()
    ab = _capacity_value(capacity_ab_mbps)
    ba = _capacity_value(capacity_ba_mbps)
    with SessionLocal() as session:
        sensor = session.execute(
            select(PrtgSensor).where(PrtgSensor.objid == sensor_objid)
        ).scalar_one_or_none()
        if sensor is None:
            raise LookupError("传感器不存在")
        if not is_traffic_sensor(sensor.sensortype):
            raise ValueError("只能绑定 Traffic 传感器（Ping 等非流量传感器不建链路）")
        if not sensor.active:
            raise ValueError("传感器已停用，不能绑定链路")
        if source_node_id == target_node_id:
            raise ValueError("起点和终点不能是同一个节点")
        existing = session.execute(
            select(NetworkLink).where(NetworkLink.sensor_objid == sensor_objid)
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError("该传感器已绑定链路，请直接编辑现有链路")
        for node_id in (source_node_id, target_node_id):
            if session.get(NetworkTopologyNode, node_id) is None:
                raise LookupError(f"节点不存在：{node_id}")
        link = NetworkLink(
            link_key=f"manual:{sensor_objid}", sensor_objid=sensor_objid,
            source_node_id=source_node_id, target_node_id=target_node_id,
            link_type=link_type, capacity_ab_mbps=ab, capacity_ba_mbps=ba,
            capacity_source=capacity_source if (ab or ba) else None,
            mapping_confidence="candidate", updated_at=now,
        )
        session.add(link)
        session.flush()
        if ab is not None:
            capacity_row = session.execute(
                select(NetworkLinkCapacity).where(NetworkLinkCapacity.sensor_objid == sensor_objid)
            ).scalar_one_or_none()
            if capacity_row is None:
                session.add(NetworkLinkCapacity(sensor_objid=sensor_objid, capacity_mbps=ab,
                                                capacity_source=capacity_source, updated_at=now))
            else:
                capacity_row.capacity_mbps = ab
                capacity_row.capacity_source = capacity_source
                capacity_row.updated_at = now
        session.add(NetworkLinkLayout(link_id=link.id, bend_offset=0.0, updated_at=now))
        session.commit()
        session.refresh(link)
        return {"id": link.id, "sensor_id": sensor_objid,
                "source_node": source_node_id, "target_node": target_node_id}


def delete_link(link_id: int, now: datetime | None = None) -> dict[str, Any]:
    with SessionLocal() as session:
        link = session.get(NetworkLink, link_id)
        if link is None:
            raise LookupError("链路不存在")
        layout = session.execute(
            select(NetworkLinkLayout).where(NetworkLinkLayout.link_id == link_id)
        ).scalar_one_or_none()
        if layout is not None:
            session.delete(layout)
        session.delete(link)
        session.commit()
    return {"deleted": link_id}


def swap_direction(link_id: int, now: datetime | None = None) -> dict[str, Any]:
    """确认上下游：交换 A/B 两端与分方向容量（In/Out 配对语义随之翻转）。"""
    now = now or datetime.utcnow()
    with SessionLocal() as session:
        link = session.get(NetworkLink, link_id)
        if link is None:
            raise LookupError("链路不存在")
        link.source_node_id, link.target_node_id = link.target_node_id, link.source_node_id
        link.capacity_ab_mbps, link.capacity_ba_mbps = link.capacity_ba_mbps, link.capacity_ab_mbps
        link.updated_at = now
        capacity = session.execute(
            select(NetworkLinkCapacity).where(NetworkLinkCapacity.sensor_objid == link.sensor_objid)
        ).scalar_one_or_none()
        if capacity is not None:
            capacity.capacity_mbps = link.capacity_ab_mbps
            capacity.updated_at = now
        session.commit()
        return {"id": link_id, "source_node": link.source_node_id,
                "target_node": link.target_node_id}


def set_bend(link_id: int, bend_offset: float, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.utcnow()
    with SessionLocal() as session:
        if session.get(NetworkLink, link_id) is None:
            raise LookupError("链路不存在")
        layout = session.execute(
            select(NetworkLinkLayout).where(NetworkLinkLayout.link_id == link_id)
        ).scalar_one_or_none()
        if layout is None:
            layout = NetworkLinkLayout(link_id=link_id)
            session.add(layout)
        layout.bend_offset = max(-300.0, min(300.0, float(bend_offset)))
        layout.updated_at = now
        session.commit()
        return {"link_id": link_id, "bend_offset": layout.bend_offset}


def add_alias(node_id: int, alias: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.utcnow()
    alias = (alias or "").strip()
    if not alias:
        raise ValueError("别名不能为空")
    with SessionLocal() as session:
        if session.get(NetworkTopologyNode, node_id) is None:
            raise LookupError("节点不存在")
        existing = session.execute(
            select(NetworkNodeAlias).where(NetworkNodeAlias.alias == alias)
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError("别名已存在")
        row = NetworkNodeAlias(node_id=node_id, alias=alias, created_at=now)
        session.add(row)
        session.commit()
        session.refresh(row)
        return {"id": row.id, "node_id": node_id, "alias": alias}


def sensor_options() -> dict[str, Any]:
    """Phase 2 绑定用：列出 Traffic 传感器及是否已绑定链路。"""
    with SessionLocal() as session:
        sensors = [
            s for s in session.execute(select(PrtgSensor).order_by(PrtgSensor.objid)).scalars()
            if is_traffic_sensor(s.sensortype) and s.active
        ]
        bound = {l.sensor_objid for l in session.execute(select(NetworkLink)).scalars()}
    return {"sensors": [{
        "objid": s.objid, "name": s.sensor_name, "device": s.device_name,
        "bound": s.objid in bound,
    } for s in sensors]}


def history(link_id: int, period: str = "24h") -> dict[str, Any] | None:
    period = period if period in PERIODS else "24h"
    with SessionLocal() as session:
        link = session.execute(select(NetworkLink).where(NetworkLink.id == link_id)).scalar_one_or_none()
        if link is None:
            return None
        sensor_id = link.sensor_objid
    if period == "live":
        since = datetime.utcnow() - timedelta(minutes=15)
        with SessionLocal() as session:
            rows = session.execute(
                select(PrtgChannelSample.channel_objid, PrtgChannelSample.sample_time,
                       PrtgChannelSample.value_num)
                .where(PrtgChannelSample.sensor_objid == sensor_id)
                .where(PrtgChannelSample.channel_objid.in_((0, 1)))
                .where(PrtgChannelSample.sample_time >= since)
                .order_by(PrtgChannelSample.sample_time)
            ).all()
    else:
        since = datetime.utcnow() - timedelta(minutes=PERIODS[period]["minutes"])
        with SessionLocal() as session:
            rows = session.execute(
                select(PrtgChannelSample5m.channel_objid, PrtgChannelSample5m.bucket_time,
                       PrtgChannelSample5m.avg_value)
                .where(PrtgChannelSample5m.sensor_objid == sensor_id)
                .where(PrtgChannelSample5m.channel_objid.in_((0, 1)))
                .where(PrtgChannelSample5m.bucket_time >= since)
                .order_by(PrtgChannelSample5m.bucket_time)
            ).all()
    series: dict[int, list[dict[str, Any]]] = {0: [], 1: []}
    for channel_objid, ts, value in rows:
        if value is None:
            continue
        series[int(channel_objid)].append({"t": ts.isoformat() + "Z",
                                           "mbps": round(float(value) * 8 / 1e6, 3)})
    return {"link_id": link_id, "sensor_id": sensor_id, "period": period,
            "series": {"in": series[0], "out": series[1]}}


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

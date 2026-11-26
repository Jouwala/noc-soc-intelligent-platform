# -*- coding: utf-8 -*-
"""门户流量拓扑模型（Issue #230）。

Sensor ≠ Interface ≠ Link ≠ Edge：一条 `network_links` 绑定一个 PRTG 传感器
（传感器本身可能是一个 interface/channel 视图），两端节点来自设备与接口名
解析出的对端；节点坐标是 0–1000 相对坐标（画布，不是经纬度）。

容量不是 PRTG 提供的：`capacity_ab_mbps` / `capacity_ba_mbps` 只从
`network_link_capacity`（人工登记）或后续 SNMP 写入；为空时利用率必须为
null，前端显示「待补全」，**不得**按 0 或猜测值计算。
"""
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, Text

from backend.database import Base


class NetworkTopologyNode(Base):
    __tablename__ = "network_topology_nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    node_key = Column(Text, nullable=False, unique=True)
    name = Column(Text, nullable=False)
    node_type = Column(Text, nullable=False, default="unknown")  # device/peer/customer/unknown
    device_objid = Column(Integer)  # PRTG 设备 objid（device 类型才有）
    x = Column(Float)  # 0–1000 相对坐标
    y = Column(Float)
    pinned = Column(Boolean, nullable=False, default=False)  # 人工拖动后固定
    meta = Column(Text)
    updated_at = Column(DateTime)


class NetworkLink(Base):
    __tablename__ = "network_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    link_key = Column(Text, nullable=False, unique=True)
    sensor_objid = Column(Integer, nullable=False, unique=True)
    source_node_id = Column(Integer, nullable=False)
    target_node_id = Column(Integer, nullable=False)
    link_type = Column(Text)  # backbone/access/peer/isp...
    capacity_ab_mbps = Column(Float)  # A→B 方向容量（利用率分母，按方向取）
    capacity_ba_mbps = Column(Float)
    capacity_source = Column(Text)  # manual / snmp_ifspeed
    mapping_confidence = Column(Text, nullable=False, default="discovered")
    updated_at = Column(DateTime)


class NetworkNodeAlias(Base):
    __tablename__ = "network_node_aliases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    node_id = Column(Integer, nullable=False)
    alias = Column(Text, nullable=False, unique=True)
    created_at = Column(DateTime)


class NetworkLinkLayout(Base):
    __tablename__ = "network_link_layouts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    link_id = Column(Integer, nullable=False, unique=True)
    bend_offset = Column(Float)
    updated_at = Column(DateTime)

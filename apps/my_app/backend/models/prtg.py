"""PRTG 传感器映射与盲区设备表模型（app.db，Issue #226）

prtg_sensor_mapping：PRTG 传感器与拓扑站点的人工可维护映射。
PRTG 侧没有 site_id / region / capacity，全部靠这张表补齐（字段对照见
docs/workspace/prtg-channel-dictionary.md 与 #225 语义字典）：

- 一个传感器可对应多个站点（一行一个 (sensor_id, site_id) 对，支持多对多：
  一个核心接口服务多个站点时写多行）；
- peer_name 由传感器名解析（"^\\((\\d+)\\) to (.+)$"），与拓扑
  orangeTopologySites.json 的站名（n 字段）首轮自动匹配，region 取拓扑
  的 s 字段；未匹配显式写 site_id="Unknown"，不静默丢弃；
- capacity_mbps 是利用率分母，PRTG 没有：来源只允许 snmp_ifspeed
  （SNMP ifSpeed/ifHighSpeed）或 manual（设备配置人工维护），落定前
  留空（None）——消费方对空值必须显式提示「待补全」、不得算利用率；
- match_status：auto（自动匹配待确认）/ verified（人工已确认）/
  unknown（未匹配）。

prtg_blindspot_device：PRTG 里 0 传感器的设备盲区清单（Core Firewalls
2 台 + VSAT Customers 23 台），作扩容跟踪；monitored 恒为 False 直到
真正配置传感器并由人工改判。
"""
from sqlalchemy import Boolean, Column, Float, Integer, Text, UniqueConstraint

from backend.database import Base


class PrtgSensorMapping(Base):
    __tablename__ = "prtg_sensor_mapping"
    __table_args__ = (
        UniqueConstraint("sensor_id", "site_id", name="uq_prtg_mapping_sensor_site"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sensor_id = Column(Integer, nullable=False)  # PRTG objid
    device_name = Column(Text, nullable=False)  # PRTG 设备名，如 PTP_SW
    sensor_name = Column(Text, nullable=False)  # PRTG 传感器名原文
    interface_no = Column(Text)  # 传感器名解析出的接口号，如 10152 / G1/0/45
    peer_name = Column(Text)  # 传感器名解析出的对端名，如 CSQUARED
    site_id = Column(Text, nullable=False, default="Unknown")  # 拓扑站名；未匹配显式 Unknown
    region = Column(Text)  # 拓扑 s 字段（区域代码）；未匹配为空
    link_type = Column(Text)  # 链路类型（人工确认：backbone/access/peer…），首轮可空
    capacity_mbps = Column(Float)  # 利用率分母；缺失为空，消费方须显式提示待补全
    capacity_source = Column(Text)  # snmp_ifspeed / manual；未落定为空
    criticality = Column(Integer)  # 1–5（PRTG priority 星数转换）；缺失为空
    enabled = Column(Boolean, nullable=False, default=True)
    match_status = Column(Text, nullable=False, default="unknown")  # auto/verified/unknown


class PrtgBlindspotDevice(Base):
    __tablename__ = "prtg_blindspot_device"
    __table_args__ = (
        UniqueConstraint("device_name", "group_name", name="uq_prtg_blindspot_device_group"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_name = Column(Text, nullable=False)
    group_name = Column(Text, nullable=False)  # Core Firewalls / VSAT Customers
    region = Column(Text)  # 维护区域（人工补）
    customer = Column(Text)  # 客户名（VSAT 行）；防火墙行为空
    reason = Column(Text, nullable=False, default="PRTG 未配置传感器（0 sensors）")
    owner = Column(Text)  # 责任方（人工补）
    monitored = Column(Boolean, nullable=False, default=False)

# -*- coding: utf-8 -*-
"""PRTG 日志与设备级事件表模型（P0，2026-11-26；迁移 0014_prtg_logs_incidents）。

- prtg_log_messages：PRTG 日志原文 + 分类（PRTG 自身只留约 30 天，这里长期保留，
  默认 730 天，见 services/prtg/logs.py 的 PRTG_LOG_RETENTION_DAYS）；
- prtg_incidents：同一设备多个传感器在 120 秒内相继 Down 合并成的设备事件
  （services/prtg/incidents.py）。

时间一律 naive UTC（与 prtg_collector.py 同口径），接口层补 "Z"。
"""
from sqlalchemy import Column, DateTime, Float, Index, Integer, Text, UniqueConstraint

from backend.database import Base


class PrtgLogMessage(Base):
    """PRTG 日志一行（table.json?content=messages），dedup_key 幂等。"""

    __tablename__ = "prtg_log_messages"
    __table_args__ = (
        Index("ix_prtg_log_sensor_time", "sensor_objid", "happened_at"),
        Index("ix_prtg_log_time", "happened_at"),
        Index("ix_prtg_log_device_time", "device_name", "happened_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sensor_objid = Column(Integer)  # 日志行 objid（实测为传感器 id；系统级日志可能不是）
    device_name = Column(Text)  # parent
    sensor_name = Column(Text)  # name
    sensor_type = Column(Text)  # type：Ping / SNMP Traffic 64bit
    happened_at = Column(DateTime, nullable=False)  # datetime_raw 换算，naive UTC，秒级
    datetime_raw = Column(Float)  # PRTG 原始 Excel 序列日（去重与对数用）
    status = Column(Text)  # Up / Warning / Down / Unusual / Paused …
    status_raw = Column(Integer)
    message = Column(Text)  # 去 HTML 的原文
    reason = Column(Text, nullable=False, default="other")  # 见 logs.classify_message
    value_num = Column(Float)  # reason=value 时解析出的数值（单位见 value_unit）
    value_unit = Column(Text)  # msec / kbit/s / % …
    dedup_key = Column(Text, nullable=False, unique=True)
    collected_at = Column(DateTime, nullable=False)


class PrtgIncident(Base):
    """设备级事件：同设备 120 秒内相继 Down 的传感器合并为一条。"""

    __tablename__ = "prtg_incidents"
    __table_args__ = (
        UniqueConstraint("device_name", "started_at", name="uq_prtg_incident_device_start"),
        Index("ix_prtg_incident_start", "started_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_name = Column(Text, nullable=False)
    device_objid = Column(Integer)  # 按设备名从 prtg_devices 补，补不到为 null
    started_at = Column(DateTime, nullable=False)  # 首个 Down
    ended_at = Column(DateTime)  # 最后一个 Up；null = 未结束
    kind = Column(Text, nullable=False)  # device_unreachable / snmp_mgmt / single_link
    sensor_count = Column(Integer, nullable=False, default=0)
    duration_seconds = Column(Integer)  # 结束后才有值
    sensors_json = Column(Text)  # [{sensor_objid, sensor_name, sensor_type, down_at, up_at}]
    evidence_json = Column(Text)  # {log_ids, log_count, icmp_timeouts, has_ping_sensor, merge_window_seconds, flaps}
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

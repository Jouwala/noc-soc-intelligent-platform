# -*- coding: utf-8 -*-
"""PRTG 采集与服务表模型（Issue #227）。

独立建表，不塞现有告警表（realtime_alarm / history_alarm 不动）：
PRTG 的传感器、通道、采样、状态事件、同步状态各一张表，保留 PRTG 原始
objid 与 source_system=PRTG，便于与上游对数。

时间口径（2026-11-24 MSNOC1 实测）：PRTG 服务器时区为 UTC（lastcheck 的
Excel 序列日换算与本机 UTC 只差轮询时延），所以 sample_time / collected_at
一律存 **naive UTC**，接口层再补 "Z"。historicdata 的 datetime_raw 同为
UTC 序列日；不要再按服务器本地时区偏移。

保留策略（由采集服务的聚合/清理任务执行）：
  - prtg_channel_samples        60 秒原始，90 天
  - prtg_channel_samples_5m     5 分钟聚合，1 年
  - prtg_channel_samples_1h     小时聚合，2–3 年
  - prtg_sensor_status/events   SLA 与事件，长期
"""
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)

from backend.database import Base


class PrtgDevice(Base):
    """PRTG 设备目录（每小时同步一次）。"""

    __tablename__ = "prtg_devices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    objid = Column(Integer, nullable=False, unique=True)  # PRTG 设备 id
    group_name = Column(Text)  # PRTG 组，如 Core Switching
    device_name = Column(Text, nullable=False)
    device_type = Column(Text)  # 设备类型原文（如 Cisco Device）
    active = Column(Boolean, nullable=False, default=True)
    first_seen_at = Column(DateTime)
    last_seen_at = Column(DateTime)


class PrtgSensor(Base):
    """PRTG 传感器目录 + 最新状态（每分钟刷新状态，每小时刷新目录字段）。"""

    __tablename__ = "prtg_sensors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    objid = Column(Integer, nullable=False, unique=True)  # PRTG 传感器 id
    device_objid = Column(Integer)
    group_name = Column(Text)
    device_name = Column(Text)
    sensor_name = Column(Text, nullable=False)
    sensortype = Column(Text)  # 如 "SNMP Traffic 64bit" / "Ping"
    source_system = Column(Text, nullable=False, default="PRTG")  # 跨系统汇总时按列筛来源
    interval_seconds = Column(Integer)  # 扫描间隔（实测 60）
    status = Column(Text)  # Up / Unusual / Down / Unknown
    status_raw = Column(Integer)  # PRTG status_raw（Up=3 实测）
    priority = Column(Integer)  # 1–5 星
    last_value = Column(Text)  # 传感器主通道显示值（含单位原文）
    last_value_raw = Column(Float)  # 主通道原始值
    uptime_pct = Column(Float)  # 详情页 Uptime %
    downtime_pct = Column(Float)  # 详情页 Downtime %
    uptime_minutes = Column(Float)  # uptimetime（累计可用分钟）
    downtime_minutes = Column(Float)  # downtimetime（累计停机分钟，SLA 差分用）
    updown_total_minutes = Column(Float)  # updowntotal（累计监控分钟）
    updown_since = Column(DateTime)  # updownsince（起算时间）
    last_up = Column(DateTime)
    last_down = Column(DateTime)
    last_check = Column(DateTime)
    active = Column(Boolean, nullable=False, default=True)
    first_seen_at = Column(DateTime)
    last_seen_at = Column(DateTime)
    updated_at = Column(DateTime)
    last_message = Column(Text)  # PRTG 当前状态说明（table.json message 列去 HTML；Unusual 时即基线判定原因）
    last_message_at = Column(DateTime)  # 最近一次轮询看到它的时间（迁移 0014）


class PrtgSensorChannel(Base):
    """传感器通道目录（每小时同步；lastvalue 不在目录里，走采样表）。"""

    __tablename__ = "prtg_sensor_channels"
    __table_args__ = (
        UniqueConstraint("sensor_objid", "channel_objid", name="uq_prtg_channel_sensor_ch"),
        Index("ix_prtg_channel_sensor", "sensor_objid"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sensor_objid = Column(Integer, nullable=False)
    channel_objid = Column(Integer, nullable=False)  # -1/0/1/2/3/10/11/12/13/14
    name = Column(Text, nullable=False)
    unit = Column(Text)  # 原始单位（byte/s、count 等）
    value_kind = Column(Text)  # rate+volume / interval / none
    updated_at = Column(DateTime)


class PrtgSensorStatus(Base):
    """传感器状态采样（只在状态变化与每小时心跳时落行，避免 25 行/分钟膨胀）。"""

    __tablename__ = "prtg_sensor_status"
    __table_args__ = (
        UniqueConstraint("sensor_objid", "sampled_at", name="uq_prtg_status_sensor_time"),
        Index("ix_prtg_status_time", "sampled_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sensor_objid = Column(Integer, nullable=False)
    status = Column(Text, nullable=False)
    status_raw = Column(Integer)
    sampled_at = Column(DateTime, nullable=False)
    collected_at = Column(DateTime, nullable=False)


class PrtgChannelSample(Base):
    """60 秒原始采样（每传感器每通道每分钟一行，幂等唯一键）。"""

    __tablename__ = "prtg_channel_samples"
    __table_args__ = (
        UniqueConstraint(
            "sensor_objid", "channel_objid", "sample_time",
            name="uq_prtg_sample_sensor_ch_time",
        ),
        Index("ix_prtg_sample_time", "sample_time"),
        Index("ix_prtg_sample_sensor_time", "sensor_objid", "sample_time"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sensor_objid = Column(Integer, nullable=False)
    channel_objid = Column(Integer, nullable=False)
    source_system = Column(Text, nullable=False, default="PRTG")
    sample_time = Column(DateTime, nullable=False)  # naive UTC，分钟对齐
    value_num = Column(Float)  # 速率原始值（流量=byte/s，计数=count/s）
    value_display = Column(Text)  # 速率显示值原文（kbit/s、#/s）
    volume_num = Column(Float)  # 区间体积/计数原始值（byte、count）
    volume_display = Column(Text)  # 体积显示值原文（KByte、#）
    unit = Column(Text)
    collected_at = Column(DateTime, nullable=False)


class PrtgHistoricalSample(Base):
    """historicdata 回填的 60 秒原始行（与实时采样分开，便于核对来源）。"""

    __tablename__ = "prtg_historical_samples"
    __table_args__ = (
        UniqueConstraint(
            "sensor_objid", "channel_objid", "sample_time",
            name="uq_prtg_hist_sensor_ch_time",
        ),
        Index("ix_prtg_hist_time", "sample_time"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sensor_objid = Column(Integer, nullable=False)
    channel_objid = Column(Integer, nullable=False)
    source_system = Column(Text, nullable=False, default="PRTG")
    sample_time = Column(DateTime, nullable=False)
    value_num = Column(Float)  # 速率原始值（byte/s、count/s）
    value_display = Column(Text)
    volume_num = Column(Float)  # 区间体积/计数原始值
    volume_display = Column(Text)
    source = Column(Text, nullable=False, default="historic")  # historic / csv
    collected_at = Column(DateTime, nullable=False)


class PrtgChannelSample5m(Base):
    """5 分钟聚合（1 年保留），供 24h / 7d 视图。"""

    __tablename__ = "prtg_channel_samples_5m"
    __table_args__ = (
        UniqueConstraint(
            "sensor_objid", "channel_objid", "bucket_time",
            name="uq_prtg_5m_sensor_ch_bucket",
        ),
        Index("ix_prtg_5m_bucket", "bucket_time"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sensor_objid = Column(Integer, nullable=False)
    channel_objid = Column(Integer, nullable=False)
    bucket_time = Column(DateTime, nullable=False)
    avg_value = Column(Float)  # 速率均值
    max_value = Column(Float)  # 速率峰值
    avg_volume = Column(Float)  # 区间体积/计数均值
    max_volume = Column(Float)
    samples = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime)


class PrtgChannelSample1h(Base):
    """小时聚合（长期保留），供 30d 视图与月报。"""

    __tablename__ = "prtg_channel_samples_1h"
    __table_args__ = (
        UniqueConstraint(
            "sensor_objid", "channel_objid", "bucket_time",
            name="uq_prtg_1h_sensor_ch_bucket",
        ),
        Index("ix_prtg_1h_bucket", "bucket_time"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sensor_objid = Column(Integer, nullable=False)
    channel_objid = Column(Integer, nullable=False)
    bucket_time = Column(DateTime, nullable=False)
    avg_value = Column(Float)  # 速率均值
    max_value = Column(Float)  # 速率峰值
    avg_volume = Column(Float)  # 区间体积/计数均值
    max_volume = Column(Float)
    samples = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime)


class PrtgSensorEvent(Base):
    """传感器状态事件（Up↔Unusual↔Down），供 SLA 与异常诊断。"""

    __tablename__ = "prtg_sensor_events"
    __table_args__ = (
        Index("ix_prtg_event_sensor_time", "sensor_objid", "happened_at"),
        Index("ix_prtg_event_time", "happened_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sensor_objid = Column(Integer, nullable=False)
    event_type = Column(Text, nullable=False)  # status_change / down / up / unusual
    from_status = Column(Text)
    to_status = Column(Text)
    message = Column(Text)
    happened_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime)


class NetworkLinkCapacity(Base):
    """链路容量（利用率分母）。PRTG 没有该数据，来源只允许 snmp_ifspeed / manual。

    缺失（无行或 capacity_mbps 为空）时消费方必须显示「待补全」，
    不得按 0 或猜测值算利用率（#226/#231 口径）。
    """

    __tablename__ = "network_link_capacity"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sensor_objid = Column(Integer, nullable=False, unique=True)
    capacity_mbps = Column(Float)
    capacity_source = Column(Text)  # snmp_ifspeed / manual
    note = Column(Text)
    updated_at = Column(DateTime)


class NetworkAnomalyEvent(Base):
    """门户自建规则的异常事件（#229）。不写 realtime_alarm / history_alarm。"""

    __tablename__ = "network_anomaly_events"
    __table_args__ = (
        Index("ix_prtg_anomaly_sensor_time", "sensor_objid", "started_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sensor_objid = Column(Integer, nullable=False)
    rule_code = Column(Text, nullable=False)  # below_baseline_30 / zero_traffic / above_baseline_200 ...
    severity = Column(Text, nullable=False)  # info / warning / critical
    message = Column(Text)
    evidence_json = Column(Text)  # 触发时的分子分母/基线，供口径解释
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime)
    created_at = Column(DateTime)


class PrtgSyncState(Base):
    """采集同步状态（每类作业一行）：数据新鲜度、断点、最近错误。"""

    __tablename__ = "prtg_sync_state"

    state_key = Column(Text, primary_key=True)  # catalog / live / backfill / aggregate / prune
    status = Column(Text)  # ok / running / error
    last_run_at = Column(DateTime)
    last_success_at = Column(DateTime)
    cursor = Column(Text)  # 回填断点（如 "sensor:day"）
    detail = Column(Text)  # 最近一次结果摘要（不含凭证）
    error = Column(Text)
    updated_at = Column(DateTime)


class PrtgPingState(Base):
    """Ping 抖动状态机（#229）：只有状态转换才写 network_anomaly_events。

    状态：UNKNOWN → OK → SUSPECT（连续 2 次失败）→ DOWN（第 3 次失败）→
    RECOVERING（1 次成功）→ OK（连续 2 次成功）。失败判定 = PRTG 状态非 Up
    或无有效 RTT；成功 = Up 且有 RTT。
    """

    __tablename__ = "prtg_ping_state"

    sensor_objid = Column(Integer, primary_key=True)
    state = Column(Text, nullable=False, default="UNKNOWN")
    consecutive_failures = Column(Integer, nullable=False, default=0)
    consecutive_successes = Column(Integer, nullable=False, default=0)
    last_rtt_ms = Column(Float)
    last_status = Column(Text)
    last_transition_at = Column(DateTime)
    updated_at = Column(DateTime)


class PrtgSlaDaily(Base):
    """每日 SLA 快照（#231）：用 PRTG 累计 Uptime/Downtime 的日差分算月度可用率。

    PRTG 的 uptime/downtime 是**累计值**（以传感器监控覆盖时间为分母，
    Paused/维护窗口不计入）。每天快照一次累计停机分钟数，月报用相邻快照
    差分得到当天停机分钟，避免依赖不存在的逐日状态历史。
    """

    __tablename__ = "prtg_sla_daily"
    __table_args__ = (
        UniqueConstraint("sensor_objid", "day", name="uq_prtg_sla_sensor_day"),
        Index("ix_prtg_sla_day", "day"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sensor_objid = Column(Integer, nullable=False)
    day = Column(DateTime, nullable=False)  # UTC 当天 00:00
    uptime_pct = Column(Float)  # 快照时的累计可用率
    downtime_pct = Column(Float)
    downtime_minutes_cum = Column(Float)  # 累计停机分钟（PRTG downtimetime）
    updown_total_minutes_cum = Column(Float)  # 累计监控覆盖分钟（PRTG updowntotal 差分 = 分母）
    downtime_delta_minutes = Column(Float)  # 相对上一快照的停机分钟（当月故障时长）
    down_count = Column(Integer, nullable=False, default=0)  # 当天 down 事件数
    availability_pct = Column(Float)  # 当天可用率（=100 - delta/覆盖率分钟）
    coverage_minutes = Column(Float)  # 当天分母（相邻快照间隔，≤1440）
    created_at = Column(DateTime)

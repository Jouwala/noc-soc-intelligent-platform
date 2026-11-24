"""PRTG 采集服务与独立建表（Issue #227）

Revision ID: 0008_prtg_collector
Revises: 0007_prtg_sensor_mapping
Create Date: 2026-11-24

背景
----
#225 已定版通道语义字典、#226 已建映射/盲区表；本迁移落采集与看板所需的
独立表，**不塞现有告警表**（realtime_alarm / history_alarm 不动）：

  prtg_devices                设备目录（每小时同步）
  prtg_sensors                传感器目录 + 最新状态
  prtg_sensor_channels        通道目录
  prtg_sensor_status          状态采样（变化 + 每小时心跳）
  prtg_channel_samples        60 秒原始采样（90 天）
  prtg_historical_samples     historicdata 回填原始行
  prtg_channel_samples_5m     5 分钟聚合（1 年）
  prtg_channel_samples_1h     小时聚合（2–3 年）
  prtg_sensor_events          状态事件（SLA / 异常诊断）
  network_link_capacity       链路容量（利用率分母，PRTG 无此数据）
  network_anomaly_events      门户自建规则异常事件（#229）
  prtg_sync_state             同步状态 / 断点 / 数据新鲜度

方言
----
SQLite / PostgreSQL 都跑：只使用方言中立写法，不写 schema 前缀；唯一键
用 UniqueConstraint 显式命名（PG 的 ON CONFLICT 推导依赖它）。回退按
逆序删表，均为本任务新建、无存量数据依赖。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_prtg_collector"
down_revision: Union[str, None] = "0007_prtg_sensor_mapping"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prtg_devices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("objid", sa.Integer(), nullable=False, unique=True),
        sa.Column("group_name", sa.Text()),
        sa.Column("device_name", sa.Text(), nullable=False),
        sa.Column("device_type", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("first_seen_at", sa.DateTime()),
        sa.Column("last_seen_at", sa.DateTime()),
    )

    op.create_table(
        "prtg_sensors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("objid", sa.Integer(), nullable=False, unique=True),
        sa.Column("device_objid", sa.Integer()),
        sa.Column("group_name", sa.Text()),
        sa.Column("device_name", sa.Text()),
        sa.Column("sensor_name", sa.Text(), nullable=False),
        sa.Column("sensortype", sa.Text()),
        sa.Column("interval_seconds", sa.Integer()),
        sa.Column("status", sa.Text()),
        sa.Column("status_raw", sa.Integer()),
        sa.Column("priority", sa.Integer()),
        sa.Column("last_value", sa.Text()),
        sa.Column("last_value_raw", sa.Float()),
        sa.Column("uptime_pct", sa.Float()),
        sa.Column("downtime_pct", sa.Float()),
        sa.Column("last_up", sa.DateTime()),
        sa.Column("last_down", sa.DateTime()),
        sa.Column("last_check", sa.DateTime()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("first_seen_at", sa.DateTime()),
        sa.Column("last_seen_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )

    op.create_table(
        "prtg_sensor_channels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sensor_objid", sa.Integer(), nullable=False),
        sa.Column("channel_objid", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text()),
        sa.Column("value_kind", sa.Text()),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("sensor_objid", "channel_objid", name="uq_prtg_channel_sensor_ch"),
    )
    op.create_index("ix_prtg_channel_sensor", "prtg_sensor_channels", ["sensor_objid"])

    op.create_table(
        "prtg_sensor_status",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sensor_objid", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("status_raw", sa.Integer()),
        sa.Column("sampled_at", sa.DateTime(), nullable=False),
        sa.Column("collected_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("sensor_objid", "sampled_at", name="uq_prtg_status_sensor_time"),
    )
    op.create_index("ix_prtg_status_time", "prtg_sensor_status", ["sampled_at"])

    op.create_table(
        "prtg_channel_samples",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sensor_objid", sa.Integer(), nullable=False),
        sa.Column("channel_objid", sa.Integer(), nullable=False),
        sa.Column("sample_time", sa.DateTime(), nullable=False),
        sa.Column("value_num", sa.Float()),
        sa.Column("value_display", sa.Text()),
        sa.Column("volume_num", sa.Float()),
        sa.Column("volume_display", sa.Text()),
        sa.Column("unit", sa.Text()),
        sa.Column("collected_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "sensor_objid", "channel_objid", "sample_time",
            name="uq_prtg_sample_sensor_ch_time",
        ),
    )
    op.create_index("ix_prtg_sample_time", "prtg_channel_samples", ["sample_time"])
    op.create_index("ix_prtg_sample_sensor_time", "prtg_channel_samples",
                    ["sensor_objid", "sample_time"])

    op.create_table(
        "prtg_historical_samples",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sensor_objid", sa.Integer(), nullable=False),
        sa.Column("channel_objid", sa.Integer(), nullable=False),
        sa.Column("sample_time", sa.DateTime(), nullable=False),
        sa.Column("value_num", sa.Float()),
        sa.Column("value_display", sa.Text()),
        sa.Column("volume_num", sa.Float()),
        sa.Column("volume_display", sa.Text()),
        sa.Column("source", sa.Text(), nullable=False, server_default="historic"),
        sa.Column("collected_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "sensor_objid", "channel_objid", "sample_time",
            name="uq_prtg_hist_sensor_ch_time",
        ),
    )
    op.create_index("ix_prtg_hist_time", "prtg_historical_samples", ["sample_time"])

    for table_name, uq_name in (
        ("prtg_channel_samples_5m", "uq_prtg_5m_sensor_ch_bucket"),
        ("prtg_channel_samples_1h", "uq_prtg_1h_sensor_ch_bucket"),
    ):
        op.create_table(
            table_name,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("sensor_objid", sa.Integer(), nullable=False),
            sa.Column("channel_objid", sa.Integer(), nullable=False),
            sa.Column("bucket_time", sa.DateTime(), nullable=False),
            sa.Column("avg_value", sa.Float()),
            sa.Column("max_value", sa.Float()),
            sa.Column("avg_volume", sa.Float()),
            sa.Column("max_volume", sa.Float()),
            sa.Column("samples", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime()),
            sa.UniqueConstraint("sensor_objid", "channel_objid", "bucket_time", name=uq_name),
        )
        op.create_index(f"ix_prtg_{table_name.rsplit('_', 1)[-1]}_bucket", table_name, ["bucket_time"])

    op.create_table(
        "prtg_sensor_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sensor_objid", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text()),
        sa.Column("to_status", sa.Text()),
        sa.Column("message", sa.Text()),
        sa.Column("happened_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_prtg_event_sensor_time", "prtg_sensor_events",
                    ["sensor_objid", "happened_at"])
    op.create_index("ix_prtg_event_time", "prtg_sensor_events", ["happened_at"])

    op.create_table(
        "network_link_capacity",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sensor_objid", sa.Integer(), nullable=False, unique=True),
        sa.Column("capacity_mbps", sa.Float()),
        sa.Column("capacity_source", sa.Text()),
        sa.Column("note", sa.Text()),
        sa.Column("updated_at", sa.DateTime()),
    )

    op.create_table(
        "network_anomaly_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sensor_objid", sa.Integer(), nullable=False),
        sa.Column("rule_code", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("evidence_json", sa.Text()),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_prtg_anomaly_sensor_time", "network_anomaly_events",
                    ["sensor_objid", "started_at"])

    op.create_table(
        "prtg_sync_state",
        sa.Column("state_key", sa.Text(), primary_key=True),
        sa.Column("status", sa.Text()),
        sa.Column("last_run_at", sa.DateTime()),
        sa.Column("last_success_at", sa.DateTime()),
        sa.Column("cursor", sa.Text()),
        sa.Column("detail", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("updated_at", sa.DateTime()),
    )


def downgrade() -> None:
    op.drop_table("prtg_sync_state")
    op.drop_index("ix_prtg_anomaly_sensor_time", table_name="network_anomaly_events")
    op.drop_table("network_anomaly_events")
    op.drop_table("network_link_capacity")
    op.drop_index("ix_prtg_event_time", table_name="prtg_sensor_events")
    op.drop_index("ix_prtg_event_sensor_time", table_name="prtg_sensor_events")
    op.drop_table("prtg_sensor_events")
    for table_name, idx_name in (
        ("prtg_channel_samples_1h", "ix_prtg_1h_bucket"),
        ("prtg_channel_samples_5m", "ix_prtg_5m_bucket"),
    ):
        op.drop_index(idx_name, table_name=table_name)
        op.drop_table(table_name)
    op.drop_index("ix_prtg_hist_time", table_name="prtg_historical_samples")
    op.drop_table("prtg_historical_samples")
    op.drop_index("ix_prtg_sample_sensor_time", table_name="prtg_channel_samples")
    op.drop_index("ix_prtg_sample_time", table_name="prtg_channel_samples")
    op.drop_table("prtg_channel_samples")
    op.drop_index("ix_prtg_status_time", table_name="prtg_sensor_status")
    op.drop_table("prtg_sensor_status")
    op.drop_index("ix_prtg_channel_sensor", table_name="prtg_sensor_channels")
    op.drop_table("prtg_sensor_channels")
    op.drop_table("prtg_sensors")
    op.drop_table("prtg_devices")

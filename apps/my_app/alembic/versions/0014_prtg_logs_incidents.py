"""PRTG 日志与设备级事件（P0：日志采集 / 异常原因 / 连带故障合并）

Revision ID: 0014_prtg_logs_incidents
Revises: 0013_network_topology
Create Date: 2026-11-26

背景
----
2026-11-26 只读摸底（TMP/prtg_survey）实测：PRTG 日志只保留约 30 天，采集器此前没采，
过期即丢；传感器 message 列里有 PRTG 自己的异常原因（unusually low/high…）但没入库；
一台设备出事会让多个传感器相继 Down，被当成多条链路故障。本迁移：

  prtg_log_messages   PRTG 日志原文 + 原因分类（dedup_key 唯一，幂等增量）
  prtg_incidents      设备级事件（device_name + started_at 唯一，重算幂等）
  prtg_sensors        + last_message / last_message_at（PRTG 当前状态说明）

全部是新增，不改任何旧列；downgrade 删两表两列，旧数据不受影响。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_prtg_logs_incidents"
down_revision: Union[str, None] = "0013_network_topology"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prtg_log_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sensor_objid", sa.Integer()),
        sa.Column("device_name", sa.Text()),
        sa.Column("sensor_name", sa.Text()),
        sa.Column("sensor_type", sa.Text()),
        sa.Column("happened_at", sa.DateTime(), nullable=False),
        sa.Column("datetime_raw", sa.Float()),
        sa.Column("status", sa.Text()),
        sa.Column("status_raw", sa.Integer()),
        sa.Column("message", sa.Text()),
        sa.Column("reason", sa.Text(), nullable=False, server_default="other"),
        sa.Column("value_num", sa.Float()),
        sa.Column("value_unit", sa.Text()),
        sa.Column("dedup_key", sa.Text(), nullable=False, unique=True),
        sa.Column("collected_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_prtg_log_sensor_time", "prtg_log_messages", ["sensor_objid", "happened_at"])
    op.create_index("ix_prtg_log_time", "prtg_log_messages", ["happened_at"])
    op.create_index("ix_prtg_log_device_time", "prtg_log_messages", ["device_name", "happened_at"])

    op.create_table(
        "prtg_incidents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("device_name", sa.Text(), nullable=False),
        sa.Column("device_objid", sa.Integer()),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime()),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("sensor_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("sensors_json", sa.Text()),
        sa.Column("evidence_json", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("device_name", "started_at", name="uq_prtg_incident_device_start"),
    )
    op.create_index("ix_prtg_incident_start", "prtg_incidents", ["started_at"])

    with op.batch_alter_table("prtg_sensors") as batch:
        batch.add_column(sa.Column("last_message", sa.Text()))
        batch.add_column(sa.Column("last_message_at", sa.DateTime()))


def downgrade() -> None:
    with op.batch_alter_table("prtg_sensors") as batch:
        batch.drop_column("last_message_at")
        batch.drop_column("last_message")
    op.drop_index("ix_prtg_incident_start", table_name="prtg_incidents")
    op.drop_table("prtg_incidents")
    op.drop_index("ix_prtg_log_device_time", table_name="prtg_log_messages")
    op.drop_index("ix_prtg_log_time", table_name="prtg_log_messages")
    op.drop_index("ix_prtg_log_sensor_time", table_name="prtg_log_messages")
    op.drop_table("prtg_log_messages")

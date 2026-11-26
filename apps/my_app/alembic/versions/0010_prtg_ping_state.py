"""PRTG Ping 抖动状态机表（Issue #229）

Revision ID: 0010_prtg_ping_state
Revises: 0009_prtg_source_system
Create Date: 2026-11-24

背景
----
AML TOKADEH1 的 Ping 传感器会反复在 Up / Unusual 之间横跳，若按 PRTG 状态
直出告警会每跳一条。本表保存每传感器的状态机（OK / SUSPECT / DOWN /
RECOVERING）与连续失败/成功计数，只有**状态转换**才写
`network_anomaly_events`，避免抖动刷屏。

与既有表的关系：不写 realtime_alarm / history_alarm；事件走
`network_anomaly_events`（#229 统一事件适配层）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_prtg_ping_state"
down_revision: Union[str, None] = "0009_prtg_source_system"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prtg_ping_state",
        sa.Column("sensor_objid", sa.Integer(), primary_key=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="UNKNOWN"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_successes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_rtt_ms", sa.Float()),
        sa.Column("last_status", sa.Text()),
        sa.Column("last_transition_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )


def downgrade() -> None:
    op.drop_table("prtg_ping_state")

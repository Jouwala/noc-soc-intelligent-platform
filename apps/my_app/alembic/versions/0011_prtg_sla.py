"""PRTG SLA 日快照与累计停机时长列（Issue #231）

Revision ID: 0011_prtg_sla
Revises: 0010_prtg_ping_state
Create Date: 2026-11-24

背景
----
#231 要用 PRTG 现成的 Uptime %/Downtime 出月报。PRTG 给的是**累计值**
（`uptimetime` = "956 d"、`downtimetime` = "28 h 9 m"、`updowntotal` = "958 d"），
以监控覆盖时间为分母、Paused/维护窗口不计入。为了能按「月」算故障时长，
每天快照一次累计停机分钟，相邻快照差分即当天停机分钟。

本迁移：
  - `prtg_sensors` 补 4 列：累计可用分钟、累计停机分钟、监控总分钟、起算时间；
  - 新建 `prtg_sla_daily`（唯一键 sensor_objid + day）。
均为新增、可回退。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_prtg_sla"
down_revision: Union[str, None] = "0010_prtg_ping_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("prtg_sensors", sa.Column("uptime_minutes", sa.Float()))
    op.add_column("prtg_sensors", sa.Column("downtime_minutes", sa.Float()))
    op.add_column("prtg_sensors", sa.Column("updown_total_minutes", sa.Float()))
    op.add_column("prtg_sensors", sa.Column("updown_since", sa.DateTime()))

    op.create_table(
        "prtg_sla_daily",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sensor_objid", sa.Integer(), nullable=False),
        sa.Column("day", sa.DateTime(), nullable=False),
        sa.Column("uptime_pct", sa.Float()),
        sa.Column("downtime_pct", sa.Float()),
        sa.Column("downtime_minutes_cum", sa.Float()),
        sa.Column("downtime_delta_minutes", sa.Float()),
        sa.Column("down_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("availability_pct", sa.Float()),
        sa.Column("coverage_minutes", sa.Float()),
        sa.Column("created_at", sa.DateTime()),
        sa.UniqueConstraint("sensor_objid", "day", name="uq_prtg_sla_sensor_day"),
    )
    op.create_index("ix_prtg_sla_day", "prtg_sla_daily", ["day"])


def downgrade() -> None:
    op.drop_index("ix_prtg_sla_day", table_name="prtg_sla_daily")
    op.drop_table("prtg_sla_daily")
    op.drop_column("prtg_sensors", "updown_since")
    op.drop_column("prtg_sensors", "updown_total_minutes")
    op.drop_column("prtg_sensors", "downtime_minutes")
    op.drop_column("prtg_sensors", "uptime_minutes")

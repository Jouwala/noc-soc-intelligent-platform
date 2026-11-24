"""PRTG 传感器映射表与盲区设备表（Issue #226）

Revision ID: 0007_prtg_sensor_mapping
Revises: 0006_manual_stop_flag
Create Date: 2026-11-24

背景
----
PRTG 只有传感器视角（objid / 设备名 / 传感器名），没有门户需要的
site_id、region 与链路容量（capacity_mbps）。#225 已定版通道语义字典，
本迁移落两张人工可维护的映射/跟踪表（模型见 backend/models/prtg.py）：

- prtg_sensor_mapping：传感器 ↔ 拓扑站点映射（多对多，一行一个
  (sensor_id, site_id) 对）；未匹配显式 site_id="Unknown"，不静默丢弃；
  capacity_mbps 缺失留空，消费方不得据此算利用率。
- prtg_blindspot_device：0 传感器设备盲区清单（Core Firewalls 2 台 +
  VSAT Customers 23 台），monitored=False，作扩容跟踪。

数据由 backend/services/prtg/build_mapping_seed.py 生成种子后人工确认
写入，本迁移只建表、不灌数据。

方言
----
SQLite / PostgreSQL 都要能跑：只用 op.create_table 的方言中立写法，
不加 schema 前缀（与既有迁移一致）。回退直接删表（两张表都是本任务
新建、无存量数据依赖）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_prtg_sensor_mapping"
down_revision: Union[str, None] = "0006_manual_stop_flag"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prtg_sensor_mapping",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sensor_id", sa.Integer(), nullable=False),
        sa.Column("device_name", sa.Text(), nullable=False),
        sa.Column("sensor_name", sa.Text(), nullable=False),
        sa.Column("interface_no", sa.Text()),
        sa.Column("peer_name", sa.Text()),
        sa.Column("site_id", sa.Text(), nullable=False, server_default="Unknown"),
        sa.Column("region", sa.Text()),
        sa.Column("link_type", sa.Text()),
        sa.Column("capacity_mbps", sa.Float()),
        sa.Column("capacity_source", sa.Text()),
        sa.Column("criticality", sa.Integer()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("match_status", sa.Text(), nullable=False, server_default="unknown"),
        sa.UniqueConstraint("sensor_id", "site_id", name="uq_prtg_mapping_sensor_site"),
    )
    op.create_index("ix_prtg_mapping_sensor", "prtg_sensor_mapping", ["sensor_id"])
    op.create_table(
        "prtg_blindspot_device",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("device_name", sa.Text(), nullable=False),
        sa.Column("group_name", sa.Text(), nullable=False),
        sa.Column("region", sa.Text()),
        sa.Column("customer", sa.Text()),
        sa.Column(
            "reason", sa.Text(), nullable=False,
            server_default="PRTG 未配置传感器（0 sensors）",
        ),
        sa.Column("owner", sa.Text()),
        sa.Column("monitored", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("device_name", "group_name", name="uq_prtg_blindspot_device_group"),
    )


def downgrade() -> None:
    op.drop_table("prtg_blindspot_device")
    op.drop_index("ix_prtg_mapping_sensor", table_name="prtg_sensor_mapping")
    op.drop_table("prtg_sensor_mapping")

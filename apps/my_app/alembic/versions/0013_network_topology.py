"""门户流量拓扑表（Issue #230）

Revision ID: 0013_network_topology
Revises: 0012_prtg_sla_coverage
Create Date: 2026-11-24

背景
----
#230 要求在门户 NetworkTopologyView 下新增「Network Traffic Topology」并列视图，
核心建模明确：Sensor ≠ Interface ≠ Link ≠ Edge。本迁移落四张表：

  network_topology_nodes   节点（设备/对端/客户/未知），坐标 0–1000 相对值
  network_links            链路（一条链路绑定一个 PRTG 传感器，含两端节点与分方向容量）
  network_node_aliases     节点别名（PRTG 设备名 ↔ 门户站名人工确认）
  network_link_layouts     链路折线偏移（防重叠，人工拖动保存）

节点/链路由 `backend/services/prtg/topology.py::ensure_topology()` 从 PRTG
传感器幂等生成；容量来自 `network_link_capacity`（PRTG 不提供容量），缺失时
利用率必须为 null，前端显示「待补全」，不得按 0 或猜测值算。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_network_topology"
down_revision: Union[str, None] = "0012_prtg_sla_coverage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "network_topology_nodes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("node_key", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("node_type", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("device_objid", sa.Integer()),
        sa.Column("x", sa.Float()),
        sa.Column("y", sa.Float()),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("meta", sa.Text()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_table(
        "network_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("link_key", sa.Text(), nullable=False, unique=True),
        sa.Column("sensor_objid", sa.Integer(), nullable=False, unique=True),
        sa.Column("source_node_id", sa.Integer(), nullable=False),
        sa.Column("target_node_id", sa.Integer(), nullable=False),
        sa.Column("link_type", sa.Text()),
        sa.Column("capacity_ab_mbps", sa.Float()),
        sa.Column("capacity_ba_mbps", sa.Float()),
        sa.Column("capacity_source", sa.Text()),
        sa.Column("mapping_confidence", sa.Text(), nullable=False, server_default="discovered"),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_table(
        "network_node_aliases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_table(
        "network_link_layouts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("link_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("bend_offset", sa.Float()),
        sa.Column("updated_at", sa.DateTime()),
    )


def downgrade() -> None:
    op.drop_table("network_link_layouts")
    op.drop_table("network_node_aliases")
    op.drop_table("network_links")
    op.drop_table("network_topology_nodes")

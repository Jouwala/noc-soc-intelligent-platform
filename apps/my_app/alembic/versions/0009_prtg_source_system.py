"""PRTG 表补 source_system 溯源列（Issue #227 验收「保留原始 ID + source_system=PRTG」）

Revision ID: 0009_prtg_source_system
Revises: 0008_prtg_collector
Create Date: 2026-11-24

背景
----
#227 要求保留 PRTG 原始 ID 并标注来源。表名已带 prtg_ 前缀，但跨系统汇总
（后续 #230/#231 与外部报表）需要能直接按列筛来源，所以在传感器与两张采样表
补 `source_system`（默认 'PRTG'）。只加列 + server_default，存量行自动回填，
不改任何既有列。

方言：SQLite 3.35+/PostgreSQL 都支持 ADD COLUMN with constant default；
回退 drop column（SQLite 需要 batch 模式，alembic env 已在 sqlite 启用）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_prtg_source_system"
down_revision: Union[str, None] = "0008_prtg_collector"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("prtg_sensors", "prtg_channel_samples", "prtg_historical_samples")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("source_system", sa.Text(), nullable=False, server_default="PRTG"),
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, "source_system")

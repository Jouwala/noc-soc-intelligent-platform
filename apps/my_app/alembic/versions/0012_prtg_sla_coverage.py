"""PRTG SLA 快照补累计监控覆盖分钟（Issue #231 审核修复）

Revision ID: 0012_prtg_sla_coverage
Revises: 0011_prtg_sla
Create Date: 2026-11-24

背景
----
0011 的日快照用「相邻快照的自然日期间隔」当可用率分母，但 PRTG 的分母是
**实际监控覆盖时间**（`updowntotal`，Paused/维护窗口不计入）。审核实测：
半天 Paused 时自然日分母 1440 分钟会把当天算成 95.83%，按覆盖口径应为
91.67%。本迁移给 `prtg_sla_daily` 补累计 `updowntotal`，日分母改用它的
差分；跨多天补跑也不再被 1440 截断。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_prtg_sla_coverage"
down_revision: Union[str, None] = "0011_prtg_sla"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("prtg_sla_daily", sa.Column("updown_total_minutes_cum", sa.Float()))


def downgrade() -> None:
    op.drop_column("prtg_sla_daily", "updown_total_minutes_cum")

"""assembly carrier counterparty

Revision ID: as01_assembly_carrier
Revises: wh05_wb_goods_returns
Create Date: 2026-04-22 12:41:51.107966

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "as01_assembly_carrier"
down_revision: str | None = "wh05_wb_goods_returns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assembly_requests",
        sa.Column("counterparty_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_assembly_requests_counterparty",
        "assembly_requests",
        "counterparty",
        ["counterparty_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_assembly_requests_counterparty_id",
        "assembly_requests",
        ["counterparty_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_assembly_requests_counterparty_id", table_name="assembly_requests")
    op.drop_constraint("fk_assembly_requests_counterparty", "assembly_requests", type_="foreignkey")
    op.drop_column("assembly_requests", "counterparty_id")

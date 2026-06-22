"""cost_opening_balance — opening inventory seed for FIFO/moving-avg valuation

Revision ID: cost01_opening_balance
Revises: asm01_stock_distribution_daily
Create Date: 2026-06-20
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cost01_opening_balance"
down_revision: str | None = "asm01_stock_distribution_daily"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cost_opening_balance",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("barcode", sa.String(length=50), nullable=False),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unit_cost", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "barcode", name="uq_cost_opening_balance_pb"),
    )
    op.create_index("ix_cost_opening_balance_project", "cost_opening_balance", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_cost_opening_balance_project", table_name="cost_opening_balance")
    op.drop_table("cost_opening_balance")

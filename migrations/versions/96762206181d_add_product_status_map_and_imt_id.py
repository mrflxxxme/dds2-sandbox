"""add_product_status_map_and_imt_id

Revision ID: 96762206181d
Revises: f3754b9a9aa9
Create Date: 2026-03-27 10:22:47.426469

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "96762206181d"
down_revision: str | None = "f3754b9a9aa9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add imt_id column to nomenclature
    op.add_column("nomenclature", sa.Column("imt_id", sa.Integer(), nullable=True))

    # 2. Create product_status_map table
    op.create_table(
        "product_status_map",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("nm_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "nm_id", name="uq_product_status_nm"),
    )
    op.create_index(
        "ix_product_status_map_project_nm_id",
        "product_status_map",
        ["project_id", "nm_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_product_status_map_project_nm_id", table_name="product_status_map")
    op.drop_table("product_status_map")
    op.drop_column("nomenclature", "imt_id")

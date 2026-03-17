"""add wb_tariffs table

Revision ID: 407b3968044b
Revises: l1_missing_proj_id_idx
Create Date: 2026-03-17 11:34:13.865428

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "407b3968044b"
down_revision: str | None = "l1_missing_proj_id_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_tariffs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("subject_name", sa.String(length=200), nullable=False),
        sa.Column("commission_rate", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "subject_name", name="uq_wb_tariff_project_subject"),
    )
    op.create_index("ix_wb_tariff_project", "wb_tariffs", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_wb_tariff_project", table_name="wb_tariffs")
    op.drop_table("wb_tariffs")

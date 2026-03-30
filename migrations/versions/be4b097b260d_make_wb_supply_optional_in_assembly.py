"""make_wb_supply_optional_in_assembly

Revision ID: be4b097b260d
Revises: add_user_telegram_username
Create Date: 2026-03-30 06:58:04.677361

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "be4b097b260d"
down_revision: str | None = "add_user_telegram_username"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Make wb_fbo_supply_id nullable
    op.alter_column(
        "assembly_requests",
        "wb_fbo_supply_id",
        existing_type=sa.Integer(),
        nullable=True,
    )

    # 2. Recreate partial unique index with wb_fbo_supply_id IS NOT NULL condition
    op.drop_index(
        "ix_assembly_requests_fbo_unique",
        table_name="assembly_requests",
    )
    op.create_index(
        "ix_assembly_requests_fbo_unique",
        "assembly_requests",
        ["project_id", "wb_fbo_supply_id"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false AND status != 'CANCELLED' AND wb_fbo_supply_id IS NOT NULL"),
    )


def downgrade() -> None:
    # 1. Recreate original index
    op.drop_index(
        "ix_assembly_requests_fbo_unique",
        table_name="assembly_requests",
    )
    op.create_index(
        "ix_assembly_requests_fbo_unique",
        "assembly_requests",
        ["project_id", "wb_fbo_supply_id"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false AND status != 'CANCELLED'"),
    )

    # 2. Make wb_fbo_supply_id NOT NULL again
    op.alter_column(
        "assembly_requests",
        "wb_fbo_supply_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

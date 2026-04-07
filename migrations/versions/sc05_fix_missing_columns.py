"""fix missing columns: vehicle_status_history.changed_by/changed_at

Revision ID: sc05_fix_missing_columns
Revises: sc04_add_payment_ref
Create Date: 2026-04-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "sc05_fix_missing_columns"
down_revision: str | None = "sc04_add_payment_ref"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOTE: project_id for factory_order_items already added by migration 0252f6d56397

    # 1. vehicle_status_history: add missing changed_by column
    op.add_column(
        "vehicle_status_history",
        sa.Column("changed_by", sa.String(100), nullable=True),
    )

    # 3. vehicle_status_history: fix changed_at timezone (DateTime → DateTime(timezone=True))
    op.alter_column(
        "vehicle_status_history",
        "changed_at",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        existing_nullable=False,
    )


def downgrade() -> None:
    # 2. Revert changed_at timezone
    op.alter_column(
        "vehicle_status_history",
        "changed_at",
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )

    # 1. Remove changed_by
    op.drop_column("vehicle_status_history", "changed_by")

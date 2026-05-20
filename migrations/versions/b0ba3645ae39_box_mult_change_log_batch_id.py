"""box_mult_change_log batch_id

Revision ID: b0ba3645ae39
Revises: box_mult_change_log
Create Date: 2026-05-20 12:12:02.365648

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b0ba3645ae39"
down_revision: str | None = "box_mult_change_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "box_multiplicity_change_log",
        sa.Column("batch_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_box_mult_change_log_batch",
        "box_multiplicity_change_log",
        ["batch_id"],
        unique=False,
        postgresql_where=sa.text("batch_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_box_mult_change_log_batch",
        table_name="box_multiplicity_change_log",
        postgresql_where=sa.text("batch_id IS NOT NULL"),
    )
    op.drop_column("box_multiplicity_change_log", "batch_id")

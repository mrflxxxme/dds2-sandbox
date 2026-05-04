"""sc18 cost_order_items.added_after_ship for post-shipment additions

Revision ID: 8dd2f6d8f782
Revises: as02_pending_to_in_progress
Create Date: 2026-05-04 06:17:40.256970

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8dd2f6d8f782"
down_revision: str | None = "as02_pending_to_in_progress"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cost_order_items",
        sa.Column(
            "added_after_ship",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("cost_order_items", "added_after_ship")

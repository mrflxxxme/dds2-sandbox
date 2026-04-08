"""cleanup: NULL status for old planning orders, remove server_default

Old cost_orders (from planning, not supply chain) got status='FORMING'
because sc01 migration used server_default='FORMING'. This migration:
1. Sets status=NULL for orders WITHOUT any factory_order_item links
2. Removes the server_default so new non-vehicle orders stay NULL

Revision ID: sc07_cleanup_status
Revises: sc06_add_dispatched
Create Date: 2026-04-07

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "sc07_cleanup_status"
down_revision: str | None = "sc06_add_dispatched"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. NULL out status for cost_orders that have NO items linked to factory_order_items
    #    These are old planning orders, not supply chain vehicles
    op.execute(
        """
        UPDATE cost_orders
        SET status = NULL
        WHERE status IS NOT NULL
          AND id NOT IN (
              SELECT DISTINCT co.id
              FROM cost_orders co
              JOIN cost_order_items coi ON coi.order_no = co.order_no
              WHERE coi.factory_order_item_id IS NOT NULL
          )
        """
    )

    # 2. Remove server_default so new cost_orders (non-vehicle) get NULL status
    op.alter_column("cost_orders", "status", server_default=None)


def downgrade() -> None:
    # Restore server_default
    op.alter_column("cost_orders", "status", server_default="FORMING")
    # Note: cannot restore old status values for planning orders

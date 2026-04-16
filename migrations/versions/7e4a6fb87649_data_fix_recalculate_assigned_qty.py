"""data_fix: recalculate assigned_qty from actual CostOrderItems

Fixes inconsistency where factory_order_items.assigned_qty was not
restored when vehicles were deleted via cost module (delete_cost_order).

Revision ID: 7e4a6fb87649
Revises: sc14_drop_factory_order_ready
Create Date: 2026-04-16 07:50:51.520358

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7e4a6fb87649"
down_revision: str | None = "sc14_drop_factory_order_ready"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Soft-delete orphan CostOrderItems (linked to soft-deleted CostOrders)
    op.execute("""
        UPDATE cost_order_items coi
        SET is_deleted = true, deleted_at = NOW()
        FROM cost_orders co
        WHERE coi.order_no = co.order_no
          AND co.is_deleted = true
          AND coi.is_deleted = false
    """)

    # 2. Recalculate assigned_qty based on actual non-deleted CostOrderItems
    op.execute("""
        UPDATE factory_order_items foi
        SET assigned_qty = COALESCE(sub.real_qty, 0)
        FROM (
            SELECT
                coi.factory_order_item_id,
                SUM(coi.qty) AS real_qty
            FROM cost_order_items coi
            JOIN cost_orders co ON co.order_no = coi.order_no
            WHERE coi.is_deleted = false
              AND co.is_deleted = false
              AND coi.factory_order_item_id IS NOT NULL
            GROUP BY coi.factory_order_item_id
        ) sub
        WHERE foi.id = sub.factory_order_item_id
          AND foi.is_deleted = false
          AND foi.assigned_qty != COALESCE(sub.real_qty, 0)
    """)

    # 3. Zero out assigned_qty for items with NO active CostOrderItems at all
    op.execute("""
        UPDATE factory_order_items foi
        SET assigned_qty = 0
        WHERE foi.is_deleted = false
          AND foi.assigned_qty > 0
          AND NOT EXISTS (
              SELECT 1
              FROM cost_order_items coi
              JOIN cost_orders co ON co.order_no = coi.order_no
              WHERE coi.factory_order_item_id = foi.id
                AND coi.is_deleted = false
                AND co.is_deleted = false
          )
    """)


def downgrade() -> None:
    # Data fix — no rollback needed, assigned_qty is a derived counter
    pass

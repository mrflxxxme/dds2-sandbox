"""Drop READY from FactoryOrderStatus — convert existing READY orders to FORMING/DISTRIBUTED.

READY was a legacy manual status that no auto-transition logic ever set. It
confused operators because a "Готов" badge could appear on an order with only
1% distributed. We drop the status from the enum entirely and rebase any
remaining READY rows onto real distribution progress:
- sum(assigned_qty) >= sum(qty) > 0 → DISTRIBUTED
- otherwise                         → FORMING

factory_orders.status is a String(20) column (not a Postgres ENUM type), so no
ALTER TYPE is needed — a plain UPDATE is enough.

Revision ID: sc14_drop_factory_order_ready
Revises: sc13_fix_factory_order_statuses
Create Date: 2026-04-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "sc14_drop_factory_order_ready"
down_revision: str | None = "sc13_fix_factory_order_statuses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.exec_driver_sql(
        """
        WITH aggregated AS (
            SELECT
                fo.id         AS order_id,
                fo.project_id AS project_id,
                COALESCE(SUM(CASE WHEN foi.is_deleted = false THEN foi.qty          ELSE 0 END), 0) AS total_qty,
                COALESCE(SUM(CASE WHEN foi.is_deleted = false THEN foi.assigned_qty ELSE 0 END), 0) AS total_assigned
            FROM factory_orders fo
            LEFT JOIN factory_order_items foi ON foi.factory_order_id = fo.id
            WHERE fo.is_deleted = false
              AND fo.status = 'READY'
            GROUP BY fo.id, fo.project_id
        ),
        target AS (
            SELECT
                order_id,
                project_id,
                CASE
                    WHEN total_qty > 0 AND total_assigned >= total_qty THEN 'DISTRIBUTED'
                    ELSE 'FORMING'
                END AS new_status
            FROM aggregated
        ),
        updated AS (
            UPDATE factory_orders fo
            SET status = t.new_status
            FROM target t
            WHERE fo.id = t.order_id
            RETURNING fo.id AS order_id, fo.project_id, t.new_status
        )
        INSERT INTO factory_order_history (
            project_id, factory_order_id, event_type, old_status, new_status,
            details, changed_at, changed_by
        )
        SELECT
            project_id, order_id, 'status_change', 'READY', new_status,
            'Миграция sc14: статус READY удалён из системы (переведён в ' || new_status || ')',
            NOW(), 'system'
        FROM updated;
        """
    )


def downgrade() -> None:
    # Data-only migration — READY is removed from the enum, so we cannot
    # restore it on downgrade without losing the information this migration
    # computed. Intentional no-op.
    pass

"""Data fix: recompute FactoryOrder.status from assigned_qty.

Some factory orders were stuck in FORMING even after all items were fully
distributed to vehicles. This happened because add_items_to_vehicle
(vehicle-side path) did not trigger the auto-transition in older code.

This migration walks all non-deleted factory orders and:
- FORMING/READY → DISTRIBUTED  when sum(assigned_qty) >= sum(qty) and total > 0
- DISTRIBUTED   → FORMING      when sum(assigned_qty) <  sum(qty)  (items were
                                removed from vehicles before status fix)
- CLOSED        → untouched    (CLOSED is final)

Also inserts a history entry for each corrected order so operators see the fix.

Revision ID: sc13_fix_factory_order_statuses
Revises: sc12_vehicle_country_rub
Create Date: 2026-04-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "sc13_fix_factory_order_statuses"
down_revision: str | None = "sc12_vehicle_country_rub"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    # Aggregate qty / assigned_qty per factory_order (only non-deleted items)
    conn.exec_driver_sql(
        """
        WITH aggregated AS (
            SELECT
                fo.id             AS order_id,
                fo.project_id     AS project_id,
                fo.status         AS old_status,
                COALESCE(SUM(CASE WHEN foi.is_deleted = false THEN foi.qty          ELSE 0 END), 0) AS total_qty,
                COALESCE(SUM(CASE WHEN foi.is_deleted = false THEN foi.assigned_qty ELSE 0 END), 0) AS total_assigned
            FROM factory_orders fo
            LEFT JOIN factory_order_items foi ON foi.factory_order_id = fo.id
            WHERE fo.is_deleted = false
              AND fo.status <> 'CLOSED'
            GROUP BY fo.id, fo.project_id, fo.status
        ),
        target AS (
            SELECT
                order_id,
                project_id,
                old_status,
                CASE
                    WHEN total_qty > 0 AND total_assigned >= total_qty THEN 'DISTRIBUTED'
                    WHEN old_status = 'DISTRIBUTED' AND total_assigned <  total_qty THEN 'FORMING'
                    ELSE old_status
                END AS new_status
            FROM aggregated
        ),
        updated AS (
            UPDATE factory_orders fo
            SET status = t.new_status
            FROM target t
            WHERE fo.id = t.order_id
              AND t.new_status <> t.old_status
            RETURNING fo.id AS order_id, fo.project_id, t.old_status, t.new_status
        )
        INSERT INTO factory_order_history (
            project_id, factory_order_id, event_type, old_status, new_status,
            details, changed_at, changed_by
        )
        SELECT
            project_id, order_id, 'status_change', old_status, new_status,
            'Автосмена статуса (миграция sc13): ' || old_status || ' → ' || new_status,
            NOW(), 'system'
        FROM updated;
        """
    )


def downgrade() -> None:
    # This is a data fix; no schema changes to revert. Intentional no-op.
    # Statuses reflect current assigned_qty state — rolling back would create
    # inconsistency with the fresh auto-transition logic, so we leave data as-is.
    pass

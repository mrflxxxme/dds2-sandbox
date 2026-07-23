"""assembly logistics_by_warehouse: логистику оказывает склад забора

Revision ID: asm790_logistics_by_warehouse
Revises: smd01_stock_mismatch_daily
Create Date: 2026-07-23

Булев флаг на `assembly_requests`: при назначении машины перевозчик берётся из
контрагента склада-источника (`warehouses.counterparty_id`), а не из введённого
ИНН/названия подрядчика. Нужен, чтобы UI показывал режим и помнил выбор при
переоткрытии модалки назначения.
"""

import sqlalchemy as sa
from alembic import op

revision = "asm790_logistics_by_warehouse"
down_revision = "smd01_stock_mismatch_daily"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assembly_requests",
        sa.Column(
            "logistics_by_warehouse",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("assembly_requests", "logistics_by_warehouse")

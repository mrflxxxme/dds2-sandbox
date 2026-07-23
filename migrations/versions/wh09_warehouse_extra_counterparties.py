"""warehouse: extra counterparties (many юр.лиц per ФФ склад)

Link table `warehouse_counterparty` — дополнительные контрагенты склада поверх
основного `warehouses.counterparty_id`. Их ИНН тоже категоризируются в
«Фулфилмент» при импорте выписок.

Revision ID: wh09_wh_extra_cp
Revises: asm790_logistics_by_warehouse
Create Date: 2026-07-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "wh09_wh_extra_cp"
down_revision: str | None = "asm790_logistics_by_warehouse"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "warehouse_counterparty",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("counterparty_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.ForeignKeyConstraint(["counterparty_id"], ["counterparty.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("warehouse_id", "counterparty_id", name="uq_warehouse_counterparty"),
    )
    op.create_index("ix_warehouse_counterparty_warehouse_id", "warehouse_counterparty", ["warehouse_id"])
    op.create_index("ix_warehouse_counterparty_counterparty_id", "warehouse_counterparty", ["counterparty_id"])
    op.create_index("ix_warehouse_counterparty_project_id", "warehouse_counterparty", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_warehouse_counterparty_project_id", table_name="warehouse_counterparty")
    op.drop_index("ix_warehouse_counterparty_counterparty_id", table_name="warehouse_counterparty")
    op.drop_index("ix_warehouse_counterparty_warehouse_id", table_name="warehouse_counterparty")
    op.drop_table("warehouse_counterparty")

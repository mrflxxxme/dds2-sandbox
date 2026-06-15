# ruff: noqa: RUF002, RUF003
"""supply_projects table + supply_project_id/is_archived on factory_orders

Группировка фабричных заказов в пользовательские «проекты» (кампании/партии),
плюс ручной флаг архива заказа (скрыть из общего списка, не трогая статус отгрузки).

Revision ID: ff04_supply_projects
Revises: ff03_local_archive
Create Date: 2026-06-15 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ff04_supply_projects"
down_revision: str | None = "ff03_local_archive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "supply_projects",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("color", sa.String(20), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_supply_projects_project_id", "supply_projects", ["project_id"])

    op.add_column("factory_orders", sa.Column("supply_project_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_factory_orders_supply_project_id",
        "factory_orders",
        "supply_projects",
        ["supply_project_id"],
        ["id"],
    )
    op.create_index("ix_factory_orders_supply_project_id", "factory_orders", ["supply_project_id"])

    op.add_column(
        "factory_orders",
        sa.Column("is_archived", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    # FK on cost_order_items.factory_order_item_id had no index — the merge re-point
    # (UPDATE ... WHERE factory_order_item_id=...) and check_and_close_order both filter it.
    op.create_index(
        "ix_cost_order_items_factory_item",
        "cost_order_items",
        ["factory_order_item_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_cost_order_items_factory_item", table_name="cost_order_items")
    op.drop_column("factory_orders", "is_archived")
    op.drop_index("ix_factory_orders_supply_project_id", table_name="factory_orders")
    op.drop_constraint("fk_factory_orders_supply_project_id", "factory_orders", type_="foreignkey")
    op.drop_column("factory_orders", "supply_project_id")
    op.drop_index("ix_supply_projects_project_id", table_name="supply_projects")
    op.drop_table("supply_projects")

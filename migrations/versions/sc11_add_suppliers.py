"""Add suppliers table and supplier_id FK to factory_orders.

New Supplier model for tracking factory/vendor details
including country (CHINA/RUSSIA), currency (CNY/RUB), delivery times.

FactoryOrder gets supplier_id FK for linking to Supplier.

Revision ID: sc11_add_suppliers
Revises: bugfix02_soft_del_items
Create Date: 2026-04-09
"""

import sqlalchemy as sa
from alembic import op

revision = "sc11_add_suppliers"
down_revision = "bugfix02_soft_del_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("country", sa.String(20), nullable=False, server_default="CHINA"),
        sa.Column("currency", sa.String(5), nullable=False, server_default="CNY"),
        sa.Column("delivery_days_min", sa.Integer(), nullable=True),
        sa.Column("delivery_days_max", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_supplier_project_name"),
    )
    op.create_index("ix_suppliers_project_id", "suppliers", ["project_id"])

    op.add_column("factory_orders", sa.Column("supplier_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_factory_orders_supplier_id",
        "factory_orders",
        "suppliers",
        ["supplier_id"],
        ["id"],
    )
    op.create_index("ix_factory_orders_supplier_id", "factory_orders", ["supplier_id"])


def downgrade() -> None:
    op.drop_index("ix_factory_orders_supplier_id", table_name="factory_orders")
    op.drop_constraint("fk_factory_orders_supplier_id", "factory_orders", type_="foreignkey")
    op.drop_column("factory_orders", "supplier_id")
    op.drop_index("ix_suppliers_project_id", table_name="suppliers")
    op.drop_table("suppliers")

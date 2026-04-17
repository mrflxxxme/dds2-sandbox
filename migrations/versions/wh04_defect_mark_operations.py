"""warehouse: add defect_mark_operations and defect_mark_operation_items

Document-based mark-as-defect operations (DM-N) with status ACCEPTED/CANCELLED.
Previously mark_defect_bulk only wrote StockMovement rows (no document).

Revision ID: wh04_mark_ops
Revises: wh03_rs_defect
Create Date: 2026-04-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "wh04_mark_ops"
down_revision: str | None = "wh03_rs_defect"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "defect_mark_operations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ACCEPTED"),
        sa.Column("actual_date", sa.Date(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_defect_mark_operations_project_id", "defect_mark_operations", ["project_id"])
    op.create_index("ix_defect_mark_operations_warehouse_id", "defect_mark_operations", ["warehouse_id"])

    op.create_table(
        "defect_mark_operation_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "operation_id",
            sa.Integer(),
            sa.ForeignKey("defect_mark_operations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nomenclature_id", sa.Integer(), sa.ForeignKey("nomenclature.id"), nullable=False),
        sa.Column("barcode", sa.String(length=50), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
    )
    op.create_index("ix_defect_mark_operation_items_project_id", "defect_mark_operation_items", ["project_id"])
    op.create_index("ix_defect_mark_operation_items_operation_id", "defect_mark_operation_items", ["operation_id"])
    op.create_index(
        "ix_defect_mark_operation_items_nomenclature_id", "defect_mark_operation_items", ["nomenclature_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_defect_mark_operation_items_nomenclature_id", table_name="defect_mark_operation_items")
    op.drop_index("ix_defect_mark_operation_items_operation_id", table_name="defect_mark_operation_items")
    op.drop_index("ix_defect_mark_operation_items_project_id", table_name="defect_mark_operation_items")
    op.drop_table("defect_mark_operation_items")

    op.drop_index("ix_defect_mark_operations_warehouse_id", table_name="defect_mark_operations")
    op.drop_index("ix_defect_mark_operations_project_id", table_name="defect_mark_operations")
    op.drop_table("defect_mark_operations")

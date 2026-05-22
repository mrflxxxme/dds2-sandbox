# ruff: noqa: RUF002, RUF003
"""add box_multiplicity_change_log table

Revision ID: box_mult_change_log
Revises: box_qty_pw_size
Create Date: 2026-05-19 12:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "box_mult_change_log"
down_revision: str | None = "box_qty_pw_size"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Журнал изменений кратности/размера коробки — история + откат.
    op.create_table(
        "box_multiplicity_change_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("barcode", sa.String(length=50), nullable=False),
        # NULL warehouse_id = изменение SKU-уровня (Nomenclature); иначе per-ФФ.
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=True),
        # field: box_qty_override | box_qty | box_size | use_box_multiplicity
        sa.Column("field", sa.String(length=30), nullable=False),
        sa.Column("old_value", sa.String(length=100), nullable=True),
        sa.Column("new_value", sa.String(length=100), nullable=True),
        # change_source: manual | bulk | revert
        sa.Column(
            "change_source",
            sa.String(length=20),
            nullable=False,
            server_default="manual",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_box_mult_change_log_project_bc",
        "box_multiplicity_change_log",
        ["project_id", "barcode"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_box_mult_change_log_project_bc",
        table_name="box_multiplicity_change_log",
    )
    op.drop_table("box_multiplicity_change_log")

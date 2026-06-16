# ruff: noqa: RUF002, RUF003
"""ff box→loose manual overrides: fulfillment_box_overrides

Ручное сопоставление короб→россыпь, когда авто-вывод (ITF14→EAN13 + «короб N шт.»)
не справился: по ШК короба привязываем существующую номенклатуру + штук в коробе.
Побеждает авто-вывод при синке.

Revision ID: ff06_box_overrides
Revises: ff05_box_packs
Create Date: 2026-06-16 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ff06_box_overrides"
down_revision: str | None = "ff05_box_packs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fulfillment_box_overrides",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("box_barcode", sa.String(length=100), nullable=False),
        sa.Column("nomenclature_id", sa.Integer(), nullable=False),
        sa.Column("base_barcode", sa.String(length=100), nullable=False),
        sa.Column("units_per_box", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.ForeignKeyConstraint(["nomenclature_id"], ["nomenclature.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "warehouse_id", "box_barcode", name="uq_ff_box_override_wh_barcode"),
    )
    op.create_index("ix_ff_box_overrides_project_wh", "fulfillment_box_overrides", ["project_id", "warehouse_id"])
    op.create_index("ix_fulfillment_box_overrides_nomenclature_id", "fulfillment_box_overrides", ["nomenclature_id"])


def downgrade() -> None:
    op.drop_index("ix_fulfillment_box_overrides_nomenclature_id", table_name="fulfillment_box_overrides")
    op.drop_index("ix_ff_box_overrides_project_wh", table_name="fulfillment_box_overrides")
    op.drop_table("fulfillment_box_overrides")

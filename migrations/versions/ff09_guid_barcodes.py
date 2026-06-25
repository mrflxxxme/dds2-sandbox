# ruff: noqa: RUF002, RUF003
"""ff manual guid→barcode overrides: fulfillment_guid_barcodes

Ручной ШК для товара ФФ, у которого карточка провайдера БЕЗ штрихкода (migfull
отдаёт ШК только в `GET /products/{guid}`; у части карточек `barcodes[]` пуст).
Привязка ШК (короба ITF14 / россыпи EAN13) к product_guid; применяется в синке и
в живой деталке приёмки — побеждает пустую карточку и кэш остатков.

Revision ID: ff09_guid_barcodes
Revises: cost01_opening_balance
Create Date: 2026-06-23 06:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ff09_guid_barcodes"
down_revision: str | None = "cost01_opening_balance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fulfillment_guid_barcodes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("product_guid", sa.String(length=100), nullable=False),
        sa.Column("barcode", sa.String(length=100), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "warehouse_id", "product_guid", name="uq_ff_guid_barcode_wh_guid"),
    )
    op.create_index("ix_ff_guid_barcodes_project_wh", "fulfillment_guid_barcodes", ["project_id", "warehouse_id"])


def downgrade() -> None:
    op.drop_index("ix_ff_guid_barcodes_project_wh", table_name="fulfillment_guid_barcodes")
    op.drop_table("fulfillment_guid_barcodes")

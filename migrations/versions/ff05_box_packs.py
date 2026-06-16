# ruff: noqa: RUF002, RUF003
"""ff box→loose packaging: base_barcode + units_per_box on fulfillment_stocks

Короб (ITF14) сводится к россыпи (EAN13) при выводе остатков. migfull/«Натали»
принимает часть остатков коробами и присваивает ШК короба; у нас учёт россыпью.
base_barcode — ШК россыпи, к которому относится строка-короб; units_per_box —
штук в коробе (для россыпи NULL/1). Авто-вывод при синке: ITF14→EAN13 по GTIN-14
+ «короб N шт.» из названия товара.

Revision ID: ff05_box_packs
Revises: de04_cost_item_lookup_indexes
Create Date: 2026-06-16 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ff05_box_packs"
down_revision: str | None = "de04_cost_item_lookup_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("fulfillment_stocks", sa.Column("base_barcode", sa.String(length=100), nullable=True))
    op.add_column(
        "fulfillment_stocks",
        sa.Column("units_per_box", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("fulfillment_stocks", "units_per_box")
    op.drop_column("fulfillment_stocks", "base_barcode")

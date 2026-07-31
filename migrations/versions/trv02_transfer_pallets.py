# ruff: noqa: RUF002, RUF003
"""Транспортная единица переезда: паллеты или короба (как у заявки на сборку)

У заявки на сборку единица есть всегда (pallets_count / pallet_weight_kg /
shipped_as_boxes), у перемещения её не было. При конвертации «заявка →
переезд» единица терялась, хотя во всех заявках кейса «транзит Питер / ЕКБ»
паллеты проставлены (5, 6, 1, 2, 2, 1). Без неё также не посчитать ₽/паллета
по переездам — только ₽/штуку.

shipped_as_boxes=False → паллеты (по умолчанию), True → короба; флаг меняет
смысл pallets_count и pallet_weight_kg («вес 1 короба») и подписи в UI.
Nullable, в отличие от заявки: переезд можно завести без транспортной оценки.

Revision ID: trv02_transfer_pallets
Revises: trv01_transfer_vehicle
Create Date: 2026-07-31 15:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "trv02_transfer_pallets"
down_revision: str | None = "trv01_transfer_vehicle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("stock_transfers", sa.Column("pallets_count", sa.Integer(), nullable=True))
    op.add_column("stock_transfers", sa.Column("pallet_weight_kg", sa.Numeric(10, 2), nullable=True))
    op.add_column(
        "stock_transfers",
        sa.Column(
            "shipped_as_boxes",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("stock_transfers", "shipped_as_boxes")
    op.drop_column("stock_transfers", "pallet_weight_kg")
    op.drop_column("stock_transfers", "pallets_count")

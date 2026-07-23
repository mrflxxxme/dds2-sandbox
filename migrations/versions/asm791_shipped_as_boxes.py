"""assembly shipped_as_boxes: единица поставки паллета/короб

Revision ID: asm791_shipped_as_boxes
Revises: wh09_wh_extra_cp
Create Date: 2026-07-23

Булев флаг на `assembly_requests`: единица поставки — паллеты (False, по умолчанию)
или короба (True). Меняет только единицу измерения `pallets_count`/`pallet_weight_kg`
и подписи в UI. НЕ связано с `package_type` (тип приёмки WB). Все существующие заявки
остаются паллетами (server_default false).
"""

import sqlalchemy as sa
from alembic import op

revision = "asm791_shipped_as_boxes"
down_revision = "wh09_wh_extra_cp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assembly_requests",
        sa.Column(
            "shipped_as_boxes",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("assembly_requests", "shipped_as_boxes")

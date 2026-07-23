"""outbound_shipments.shipped_as_boxes: снимок единицы поставки (паллета/короб)

Revision ID: asm792_outbound_shipped_as_boxes
Revises: asm791_shipped_as_boxes
Create Date: 2026-07-23

Снимок единицы поставки на отгрузке-заборе (`OutboundShipment`): False = паллеты
(по умолчанию), True = короба. Пишется из `AssemblyRequest.shipped_as_boxes` при
`ship_request`. Нужен, чтобы «История отправок» и «Оплаты» (лист логиста) показывали
единицу забора, а не всегда «паллет».
"""

import sqlalchemy as sa
from alembic import op

revision = "asm792_outbound_shipped_as_boxes"
down_revision = "asm791_shipped_as_boxes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outbound_shipments",
        sa.Column(
            "shipped_as_boxes",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("outbound_shipments", "shipped_as_boxes")

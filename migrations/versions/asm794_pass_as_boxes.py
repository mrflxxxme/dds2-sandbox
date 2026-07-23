"""assembly_wb_supply.pass_as_boxes: способ отгрузки пропуска (короба/паллеты)

Revision ID: asm794_pass_as_boxes
Revises: asm793_backfill_shipment_unit
Create Date: 2026-07-23

Булев флаг на `assembly_wb_supply`: способ отгрузки WB-пропуска (setTRNDetails
boxTypeName) — False = паллеты («pallets», по умолчанию), True = отдельные короба
(«box»). По умолчанию из AssemblyRequest.shipped_as_boxes.
"""

import sqlalchemy as sa
from alembic import op

revision = "asm794_pass_as_boxes"
down_revision = "asm793_backfill_shipment_unit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assembly_wb_supply",
        sa.Column("pass_as_boxes", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("assembly_wb_supply", "pass_as_boxes")

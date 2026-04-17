"""warehouse: add is_defect/defect_reason to inbound_receipts and outbound_shipments

Support for defective goods as first-class documents (receipt/shipment) with is_defect flag,
by analogy with stock_transfers.is_defect (wh02).

Revision ID: wh03_rs_defect
Revises: wh02_defect_stock
Create Date: 2026-04-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "wh03_rs_defect"
down_revision: str | None = "wh02_defect_stock"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("inbound_receipts", sa.Column("is_defect", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("inbound_receipts", sa.Column("defect_reason", sa.Text(), nullable=True))

    op.add_column("outbound_shipments", sa.Column("is_defect", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("outbound_shipments", sa.Column("defect_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("outbound_shipments", "defect_reason")
    op.drop_column("outbound_shipments", "is_defect")
    op.drop_column("inbound_receipts", "defect_reason")
    op.drop_column("inbound_receipts", "is_defect")

# ruff: noqa: RUF002, RUF003
"""ff request enrichment: total_qty + dest_warehouse on fulfillment_requests

Состав заявки для списочного UI: skladbot — из деталки при синке
(GET /v1/requests/show/{id}: sum(products.amount) + поле «Склад МП»),
wmscelicom — из raw списка (packages.items / shipped_target).

Revision ID: ff02_request_enrichment
Revises: ff01_fulfillment_layer
Create Date: 2026-06-11 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ff02_request_enrichment"
down_revision: str | None = "ff01_fulfillment_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("fulfillment_requests", sa.Column("total_qty", sa.Integer(), nullable=True))
    op.add_column("fulfillment_requests", sa.Column("dest_warehouse", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("fulfillment_requests", "dest_warehouse")
    op.drop_column("fulfillment_requests", "total_qty")

# ruff: noqa: RUF002, RUF003
"""payment-shipment archive: маркер «забор убран из рабочего списка оплат»

Лёгкая таблица-маркер: наличие строки = забор в архиве рабочего списка логиста.
Разархивация = удаление строки. Аддитивно.

Revision ID: pay02_payment_shipment_archive
Revises: pay01_payment_request
Create Date: 2026-06-25 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "pay02_payment_shipment_archive"
down_revision: str | None = "pay01_payment_request"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_shipment_archive",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("outbound_shipment_id", sa.Integer(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("archived_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["outbound_shipment_id"], ["outbound_shipments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["archived_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_shipment_archive_project_id", "payment_shipment_archive", ["project_id"])
    op.create_index(
        "uq_payment_shipment_archive",
        "payment_shipment_archive",
        ["project_id", "outbound_shipment_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_payment_shipment_archive", table_name="payment_shipment_archive")
    op.drop_index("ix_payment_shipment_archive_project_id", table_name="payment_shipment_archive")
    op.drop_table("payment_shipment_archive")

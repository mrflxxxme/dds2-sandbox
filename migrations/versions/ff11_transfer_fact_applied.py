"""fulfillment_requests.transfer_fact_applied_at — маркер применённого авто-приёма TR

Идемпотентность авто-приёма перемещения по факту связанной ФФ-приёмки
(kind=inbound + stock_transfer_id): is_completed у провайдера остаётся True
навсегда, и без маркера каждый синк применял бы факт повторно.

Revision ID: ff11_transfer_fact_applied
Revises: loan06_mirror
"""

import sqlalchemy as sa
from alembic import op

revision = "ff11_transfer_fact_applied"
down_revision = "loan06_mirror"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fulfillment_requests",
        sa.Column("transfer_fact_applied_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fulfillment_requests", "transfer_fact_applied_at")

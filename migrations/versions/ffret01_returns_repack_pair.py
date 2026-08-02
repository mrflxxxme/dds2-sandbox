"""Возвраты ФФ и пара «вскрытие коробов»: repack_return_id у заявок ФФ.

Склад Натали (migfull) оформляет вскрытие коробов под FBS парой документов:
«Возврат» списывает короб-SKU, «Поступление» приходует те же товары россыпью
(живой пример PVB-0000069 ↔ PVB-0000133, 30.07.2026). Возвраты синкаются новым
kind='return' (колонка kind — varchar, расширения схемы не требует); пара
связывается repack_return_id на стороне поступления. Помеченная пара — внутренняя
переупаковка ФФ: наш сток не двигается, поступление исключается из резерва
«в приёмке» и кандидатов привязки.

Revision ID: ffret01_returns_repack
Revises: fbsasm02_order_events
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op

revision = "ffret01_returns_repack"
down_revision = "fbsasm02_order_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fulfillment_requests",
        sa.Column("repack_return_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "fulfillment_requests",
        sa.Column("repack_matched_at", sa.DateTime(), nullable=True),
    )
    op.create_foreign_key(
        "fk_fulfillment_requests_repack_return_id",
        "fulfillment_requests",
        "fulfillment_requests",
        ["repack_return_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_fulfillment_requests_repack_return_id",
        "fulfillment_requests",
        ["repack_return_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_fulfillment_requests_repack_return_id", table_name="fulfillment_requests")
    op.drop_constraint(
        "fk_fulfillment_requests_repack_return_id", "fulfillment_requests", type_="foreignkey"
    )
    op.drop_column("fulfillment_requests", "repack_matched_at")
    op.drop_column("fulfillment_requests", "repack_return_id")

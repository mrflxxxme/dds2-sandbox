# ruff: noqa: RUF002, RUF003
"""FF-портал (Хамза): внешний юзер + claim приёмки + брак по позициям

Отдельный портал для внешнего фулфилмента (Хамза) работает ВНУТРИ нашей системы:
он принимает заявки на сборку и приёмки по своему складу в нескольких проектах.

Изменения схемы (аддитивные, без переноса данных):
  - users.is_external                         — флаг внешнего аккаунта (блок не-/ff/* в middleware)
  - inbound_receipts.assigned_to_user_id      — «взял в работу» (кто), nullable FK users
  - inbound_receipts.work_started_at          — «взял в работу» (когда)
  - inbound_receipt_items.defect_qty          — брак по позиции при приёмке (0 по умолчанию)
  - inbound_receipt_items.defect_reason       — причина брака/расхождения по позиции

Revision ID: ffp1_ff_portal
Revises: asm02_ship_attempt_chain
Create Date: 2026-06-25 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ffp1_ff_portal"
down_revision: str | None = "asm02_ship_attempt_chain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── users: внешний аккаунт (фулфилмент-оператор) ──────────────────────
    op.add_column(
        "users",
        sa.Column("is_external", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # ─── inbound_receipts: «взял в работу» ────────────────────────────────
    op.add_column(
        "inbound_receipts",
        sa.Column("assigned_to_user_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "inbound_receipts",
        sa.Column("work_started_at", sa.DateTime(), nullable=True),
    )
    op.create_foreign_key(
        "fk_inbound_receipts_assigned_to_user_id",
        "inbound_receipts",
        "users",
        ["assigned_to_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_inbound_receipts_assigned_to_user_id",
        "inbound_receipts",
        ["assigned_to_user_id"],
    )

    # ─── inbound_receipt_items: брак по позиции ───────────────────────────
    op.add_column(
        "inbound_receipt_items",
        sa.Column("defect_qty", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "inbound_receipt_items",
        sa.Column("defect_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("inbound_receipt_items", "defect_reason")
    op.drop_column("inbound_receipt_items", "defect_qty")

    op.drop_index("ix_inbound_receipts_assigned_to_user_id", table_name="inbound_receipts")
    op.drop_constraint("fk_inbound_receipts_assigned_to_user_id", "inbound_receipts", type_="foreignkey")
    op.drop_column("inbound_receipts", "work_started_at")
    op.drop_column("inbound_receipts", "assigned_to_user_id")

    op.drop_column("users", "is_external")

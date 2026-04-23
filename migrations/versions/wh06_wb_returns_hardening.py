# ruff: noqa: RUF001, RUF002, RUF003 — русские комментарии и префикс 'ВЗ-'
"""wb_returns hardening: BigInteger + partial unique indexes

1. ALTER nm_id, order_id: INTEGER → BIGINT (WB nmID уже около 2^30, подходит к overflow)
2. CREATE UNIQUE INDEX on wb_goods_returns (project_id, srid)
   WHERE inbound_receipt_id IS NULL AND is_deleted = false
   — защищает от двойного линка одного srid в две разные приёмки (race condition)
3. CREATE UNIQUE INDEX on inbound_receipts (project_id, number)
   WHERE is_deleted = false AND number LIKE 'ВЗ-%'
   — защищает только от коллизии auto-generated номеров ВЗ-yymmdd-N при параллельных
   create-receipt. Ручные номера (IN-…, VZ-… и пр.) не ограничиваются — юзер мог
   вводить один номер на разных складах исторически.

Revision ID: wh06_wb_returns_hardening
Revises: as01_assembly_carrier
Create Date: 2026-04-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "wh06_wb_returns_hardening"
down_revision: str | None = "as01_assembly_carrier"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. BigInteger для nm_id / order_id
    op.alter_column(
        "wb_goods_returns",
        "nm_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )
    op.alter_column(
        "wb_goods_returns",
        "order_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )

    # 2. Partial unique: один srid не может быть одновременно "свободен"
    #    (inbound_receipt_id IS NULL) дважды для одного project_id.
    #    Старый ix_wb_goods_returns_active остаётся как обычный индекс по project_id.
    op.create_index(
        "uq_wb_goods_returns_unlinked_srid",
        "wb_goods_returns",
        ["project_id", "srid"],
        unique=True,
        postgresql_where=sa.text("inbound_receipt_id IS NULL AND is_deleted = false"),
    )

    # 3. Partial unique ТОЛЬКО на auto-generated номерах ВЗ-yymmdd-N
    #    (префикс 'ВЗ-'). Ручные номера (IN-…, VZ-… и пр.) не ограничиваются —
    #    исторически юзер мог вводить одинаковый номер на разных складах.
    op.create_index(
        "uq_inbound_receipts_wb_returns_number",
        "inbound_receipts",
        ["project_id", "number"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false AND number LIKE 'ВЗ-%'"),
    )


def downgrade() -> None:
    op.drop_index("uq_inbound_receipts_wb_returns_number", table_name="inbound_receipts")
    op.drop_index("uq_wb_goods_returns_unlinked_srid", table_name="wb_goods_returns")
    op.alter_column(
        "wb_goods_returns",
        "order_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
    op.alter_column(
        "wb_goods_returns",
        "nm_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )

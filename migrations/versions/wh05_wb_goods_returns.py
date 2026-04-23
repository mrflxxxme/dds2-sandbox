"""wb_returns: create wb_goods_returns table

Отчёт WB по возвратам и перемещению товаров. Mirror API + линк на InboundReceipt.

Revision ID: wh05_wb_goods_returns
Revises: fb7a1ce8e903
Create Date: 2026-04-22

Note: изначально эта миграция форкалась от 1800fcf4d87e, но параллельно в main
появилась ветка FBO audit (1800fcf4d87e → 16112b2c4038 → ... → fb7a1ce8e903).
Чтобы не создавать multiple heads, пересажено на fb7a1ce8e903.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "wh05_wb_goods_returns"
down_revision: str | None = "fb7a1ce8e903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_goods_returns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        # WB API fields
        sa.Column("srid", sa.String(length=64), nullable=False),
        sa.Column("shk_id", sa.String(length=32), nullable=True),
        sa.Column("sticker_id", sa.String(length=32), nullable=True),
        sa.Column("barcode", sa.String(length=50), nullable=True),
        sa.Column("nm_id", sa.Integer(), nullable=True),
        sa.Column("subject_name", sa.String(length=200), nullable=True),
        sa.Column("brand", sa.String(length=200), nullable=True),
        sa.Column("tech_size", sa.String(length=50), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("order_dt", sa.Date(), nullable=True),
        sa.Column("ready_to_return_dt", sa.DateTime(), nullable=True),
        sa.Column("expired_dt", sa.DateTime(), nullable=True),
        sa.Column("completed_dt", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=100), nullable=True),
        sa.Column("return_type", sa.String(length=100), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "is_status_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("dst_office_id", sa.Integer(), nullable=True),
        sa.Column("dst_office_address", sa.String(length=500), nullable=True),
        # Our fields
        sa.Column(
            "inbound_receipt_id",
            sa.Integer(),
            sa.ForeignKey("inbound_receipts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "synced_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Mixins
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "project_id",
            "srid",
            name="uq_wb_goods_returns_project_srid",
        ),
    )

    op.create_index(
        "ix_wb_goods_returns_project_id",
        "wb_goods_returns",
        ["project_id"],
    )
    op.create_index(
        "ix_wb_goods_returns_inbound_receipt_id",
        "wb_goods_returns",
        ["inbound_receipt_id"],
    )
    op.create_index(
        "ix_wb_goods_returns_nm_id",
        "wb_goods_returns",
        ["nm_id"],
    )
    op.create_index(
        "ix_wb_goods_returns_is_status_active",
        "wb_goods_returns",
        ["is_status_active"],
    )
    # Partial index for soft-delete fast-path (новая таблица — CONCURRENTLY не нужен)
    op.create_index(
        "ix_wb_goods_returns_active",
        "wb_goods_returns",
        ["project_id"],
        postgresql_where=sa.text("is_deleted = false"),
    )


def downgrade() -> None:
    op.drop_index("ix_wb_goods_returns_active", table_name="wb_goods_returns")
    op.drop_index("ix_wb_goods_returns_is_status_active", table_name="wb_goods_returns")
    op.drop_index("ix_wb_goods_returns_nm_id", table_name="wb_goods_returns")
    op.drop_index(
        "ix_wb_goods_returns_inbound_receipt_id",
        table_name="wb_goods_returns",
    )
    op.drop_index("ix_wb_goods_returns_project_id", table_name="wb_goods_returns")
    op.drop_table("wb_goods_returns")

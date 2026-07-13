"""wb_ad_upd + wb_ad_payments: история затрат и пополнений рекламного кабинета

Затраты — WB /adv/v1/upd (первичные списания по кампаниям), пополнения — WB /adv/v1/payments
(история пополнений счёта WB Продвижение). Оба нужны как «сырые данные» рекламы.

Revision ID: ads04_ad_finance
Revises: ads03_ad_nm_daily
Create Date: 2026-07-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ads04_ad_finance"
down_revision: str | None = "ads03_ad_nm_daily"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_ad_upd",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("advert_id", sa.Integer(), nullable=False),
        sa.Column("upd_time", sa.DateTime(), nullable=False),
        sa.Column("upd_sum", sa.Numeric(18, 2), nullable=True),
        sa.Column("upd_num", sa.Integer(), nullable=True),
        sa.Column("camp_name", sa.String(length=255), nullable=True),
        sa.Column("advert_type", sa.Integer(), nullable=True),
        sa.Column("payment_type", sa.String(length=50), nullable=True),
        sa.Column("advert_status", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "advert_id", "upd_time", "upd_num", "upd_sum", name="uq_ad_upd"),
    )
    op.create_index("ix_ad_upd_project_time", "wb_ad_upd", ["project_id", "upd_time"])
    op.create_index("ix_ad_upd_advert", "wb_ad_upd", ["project_id", "advert_id", "upd_time"])

    op.create_table(
        "wb_ad_payments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("wb_id", sa.BigInteger(), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("payment_type", sa.Integer(), nullable=True),
        sa.Column("status_id", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("card_status", sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "wb_id", name="uq_ad_payment"),
    )
    op.create_index("ix_ad_payment_project_date", "wb_ad_payments", ["project_id", "paid_at"])


def downgrade() -> None:
    op.drop_index("ix_ad_payment_project_date", table_name="wb_ad_payments")
    op.drop_table("wb_ad_payments")
    op.drop_index("ix_ad_upd_advert", table_name="wb_ad_upd")
    op.drop_index("ix_ad_upd_project_time", table_name="wb_ad_upd")
    op.drop_table("wb_ad_upd")

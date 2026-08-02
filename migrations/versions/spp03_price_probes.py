"""wb_spp_probes — журнал проб цены

Проба: ставим товару целевую цену, ждём реакции витрины, фиксируем СПП и
возвращаем цену назад. Единственное место в проекте, где DDS2 пишет цену в ВБ,
поэтому каждый шаг протоколируется: что стояло, что поставили, что увидели,
вернули ли цену. Строка живёт и после отката — она и есть результат замера.

Revision ID: spp03_price_probes
Revises: spp02_hourly_points
"""

import sqlalchemy as sa
from alembic import op

revision = "spp03_price_probes"
down_revision = "spp02_hourly_points"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wb_spp_probes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="RUNNING"),
        # что было до пробы — этим же значением откатываемся
        sa.Column("base_price_before", sa.Numeric(18, 2), nullable=False),
        sa.Column("discount_before", sa.Numeric(5, 2), nullable=False),
        sa.Column("seller_price_before", sa.Numeric(18, 2), nullable=False),
        sa.Column("buyer_price_before", sa.Numeric(18, 2)),
        sa.Column("spp_before", sa.Numeric(5, 2)),
        # что ставили и что увидели
        sa.Column("target_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("seller_price_after", sa.Numeric(18, 2)),
        sa.Column("buyer_price_after", sa.Numeric(18, 2)),
        sa.Column("spp_after", sa.Numeric(5, 2)),
        sa.Column("reacted_after_sec", sa.Integer()),  # через сколько витрина сдвинулась
        sa.Column("polls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reverted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_spp_probes_project_started", "wb_spp_probes", ["project_id", "started_at"])
    op.create_index("ix_spp_probes_project_nm", "wb_spp_probes", ["project_id", "nm_id"])


def downgrade() -> None:
    op.drop_index("ix_spp_probes_project_nm", table_name="wb_spp_probes")
    op.drop_index("ix_spp_probes_project_started", table_name="wb_spp_probes")
    op.drop_table("wb_spp_probes")

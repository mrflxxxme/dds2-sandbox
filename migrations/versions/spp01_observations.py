"""wb_spp_observations — история точек «наша цена → СПП → цена клиента»

СПП раньше жил только в Redis со сроком 2 суток (`pricing_spp:*`) — история
терялась, и построить кривую СПП(цена) было не из чего. Таблица копит точки из
двух источников: снимок публичного card-API (`card`) и ретро из `wb_orders`
(`orders`, поштучный spp за 90 дней).

Одна строка = (проект, nm, день, источник, уровень нашей цены): цена меняется
редко, а СПП внутри дня гуляет по покупателям — храним медиану дня.

Revision ID: spp01_observations
Revises: fin01_acquiring_fee
"""

import sqlalchemy as sa
from alembic import op

revision = "spp01_observations"
down_revision = "fin01_acquiring_fee"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wb_spp_observations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("seller_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("buyer_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("spp_rate", sa.Numeric(5, 2), nullable=False),
        sa.Column("obs_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "nm_id", "observed_on", "source", "seller_price", name="uq_spp_obs_point"
        ),
    )
    op.create_index(
        "ix_spp_obs_project_nm_day", "wb_spp_observations", ["project_id", "nm_id", "observed_on"]
    )
    op.create_index("ix_spp_obs_project_day", "wb_spp_observations", ["project_id", "observed_on"])


def downgrade() -> None:
    op.drop_index("ix_spp_obs_project_day", table_name="wb_spp_observations")
    op.drop_index("ix_spp_obs_project_nm_day", table_name="wb_spp_observations")
    op.drop_table("wb_spp_observations")

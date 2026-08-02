"""wb_spp_observations: часовые снимки вместо одного на день

СПП меняется в течение дня, и если это связано с ценой — меняется сразу. При
уникальности «день + цена» часовые снимки затирали друг друга, и внутридневное
движение было не видно. Добавляем МСК-час в ключ.

Ретро-точки из заказов остаются с часом 0: там источник дневной по устройству
(медиана заказов дня на уровне цены).

Revision ID: spp02_hourly_points
Revises: spp01_observations
"""

import sqlalchemy as sa
from alembic import op

revision = "spp02_hourly_points"
down_revision = "spp01_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wb_spp_observations",
        sa.Column("observed_hour", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.drop_constraint("uq_spp_obs_point", "wb_spp_observations", type_="unique")
    op.create_unique_constraint(
        "uq_spp_obs_point",
        "wb_spp_observations",
        ["project_id", "nm_id", "observed_on", "observed_hour", "source", "seller_price"],
    )


def downgrade() -> None:
    # схлопываем часы обратно в день: оставляем последний снимок каждого уровня
    op.execute(
        """
        DELETE FROM wb_spp_observations a
        USING wb_spp_observations b
        WHERE a.project_id = b.project_id AND a.nm_id = b.nm_id
          AND a.observed_on = b.observed_on AND a.source = b.source
          AND a.seller_price = b.seller_price AND a.id < b.id
        """
    )
    op.drop_constraint("uq_spp_obs_point", "wb_spp_observations", type_="unique")
    op.create_unique_constraint(
        "uq_spp_obs_point",
        "wb_spp_observations",
        ["project_id", "nm_id", "observed_on", "source", "seller_price"],
    )
    op.drop_column("wb_spp_observations", "observed_hour")

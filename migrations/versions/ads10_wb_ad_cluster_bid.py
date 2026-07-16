"""wb_ad_cluster_bid — паспорт текущей пофразовой CPM-ставки (когда/на основании чего)

Revision ID: ads10_wb_ad_cluster_bid
Revises: ads09_ad_campaign_default_bid
Create Date: 2026-07-15

Одна строка на (project, кампания, товар, фраза) — состояние текущей пофразовой ставки:
applied_bid + applied_at (старт таймера «сбора статистики»), source (recommendation|manual),
basis_drr / basis_cpm / target_drr (точка отсчёта на момент применения). Нужна для колонки
«Стоит N дн» в кластеризаторе: видно, с какого дня стоит ставка и от каких данных считали.
"""

import sqlalchemy as sa
from alembic import op

revision = "ads10_wb_ad_cluster_bid"
down_revision = "ads09_ad_campaign_default_bid"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wb_ad_cluster_bid",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("norm_query", sa.Text(), nullable=False),
        sa.Column("applied_bid", sa.Numeric(18, 2), nullable=False),
        sa.Column("applied_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=True),
        sa.Column("basis_drr", sa.Numeric(18, 2), nullable=True),
        sa.Column("basis_cpm", sa.Numeric(18, 2), nullable=True),
        sa.Column("target_drr", sa.Numeric(18, 2), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "campaign_id", "nm_id", "norm_query", name="uq_ad_cluster_bid"
        ),
    )
    op.create_index(
        "ix_ad_cluster_bid_campaign_nm",
        "wb_ad_cluster_bid",
        ["project_id", "campaign_id", "nm_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ad_cluster_bid_campaign_nm", table_name="wb_ad_cluster_bid")
    op.drop_table("wb_ad_cluster_bid")

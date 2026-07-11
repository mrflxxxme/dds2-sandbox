"""wb_search_positions: снимки органической позиции товара по поисковой фразе

Для кластеризатора (колонки «Позиция»/«Была»): позицию товара по фразе собираем из
публичного поиска WB и копим историю. Позиция — свойство (товар, фраза), не кампании.

Revision ID: ads07_search_positions
Revises: ads06_ad_snapshots
Create Date: 2026-07-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ads07_search_positions"
down_revision: str | None = "ads06_ad_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_search_positions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("phrase", sa.String(length=300), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_search_pos_lookup",
        "wb_search_positions",
        ["project_id", "nm_id", "phrase", "captured_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_search_pos_lookup", table_name="wb_search_positions")
    op.drop_table("wb_search_positions")

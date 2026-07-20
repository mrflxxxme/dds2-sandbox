"""Индекс (project_id, imt_id) на nomenclature — под группировку по склейкам

Склейку читают вкладка «Склейки» в управлении рекламой, ценообразование (markup,
ai_advisor), склад и БДР — все через `imt_id IS NOT NULL`, до сих пор seq scan.
Таблица небольшая (~2k строк), поэтому обычный CREATE INDEX без CONCURRENTLY.

Revision ID: ads11_nomenclature_imt_index
Revises: vibe01_vibecoding_stats
"""

import sqlalchemy as sa
from alembic import op

revision = "ads11_nomenclature_imt_index"
down_revision = "vibe01_vibecoding_stats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_nomenclature_project_imt",
        "nomenclature",
        ["project_id", "imt_id"],
        unique=False,
        postgresql_where=sa.text("imt_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_nomenclature_project_imt", table_name="nomenclature")

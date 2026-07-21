# ruff: noqa: RUF001, RUF002
"""dch01: assembly_draft_category_hourly — почасовой снимок черновиков по категориям

Черновик сборки хранит только текущее состояние distribution — динамику
«разбирают менеджеры или висит» задним числом не восстановить. Таблица копит
её вперёд: один срез в час × категория (units rows/prebook, positions, boxes,
pallets-футпринт). Пишется scheduler-джобой draft_category_snapshot.

Revision ID: dch01_draft_category_hourly
Revises: ads11_nomenclature_imt_index
Create Date: 2026-07-21 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "dch01_draft_category_hourly"
down_revision = "ads11_nomenclature_imt_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assembly_draft_category_hourly",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("taken_at", sa.DateTime(), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("positions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("units_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("units_prebook", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("boxes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pallets", sa.Numeric(10, 1), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "taken_at", "category", name="uq_asm_draft_cat_hourly"),
    )
    op.create_index(
        "ix_asm_draft_cat_hourly_project_taken",
        "assembly_draft_category_hourly",
        ["project_id", "taken_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_asm_draft_cat_hourly_project_taken", table_name="assembly_draft_category_hourly")
    op.drop_table("assembly_draft_category_hourly")

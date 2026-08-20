# ruff: noqa: RUF002, RUF003
"""design: персональная раскладка виджетов дашборда аналитики

Revision ID: dsn06_design_dashboard_layout
Revises: dsn05_design_labels_attributes
Create Date: 2026-08-20

Волна D v2 (Р23). Одна таблица: раскладка виджетов персональна для пары
пользователь × проект. `user_id` пишется ИЗ СЕССИИ, никогда из тела запроса.

CHECK на состав `widgets` в БД нет намеренно: набор виджетов будет меняться
чаще схемы, валидация живёт в Pydantic (CONTRACT-V2 §4).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "dsn06_design_dashboard_layout"
down_revision = "dsn05_design_labels_attributes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "design_dashboard_layouts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("widgets", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_design_dashboard_project_user"),
    )
    op.create_index(
        "ix_design_dashboard_project_user",
        "design_dashboard_layouts",
        ["project_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_design_dashboard_project_user", table_name="design_dashboard_layouts")
    op.drop_table("design_dashboard_layouts")

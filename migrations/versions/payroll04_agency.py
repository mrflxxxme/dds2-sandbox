"""Агентство: клиентские проекты консалтинга и ручные суммы.

Клиент платит фикс / ставку «Команда» лестницы от недельной ЧВ своего
кабинета / fee_percent от ЧП месяца. manager_share (45%) уходит команде
агентства как обычное начисление, остаток — агентству (в ОПиУ позже).
Внутренние кабинеты (linked_project_id) считаются из их БДР, внешние —
из ручных недельных баз (payroll_client_entry).

Revision ID: payroll04_agency
Revises: payroll03_salary_periods
"""

import sqlalchemy as sa
from alembic import op

revision = "payroll04_agency"
down_revision = "payroll03_salary_periods"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payroll_client_project",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("billing_mode", sa.String(length=20), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("payroll_team.id"), nullable=True),
        sa.Column("linked_project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("fixed_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("fee_percent", sa.Numeric(6, 4), nullable=True),
        sa.Column("manager_share", sa.Numeric(6, 4), nullable=False, server_default="0.45"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_payroll_client_project_project_id", "payroll_client_project", ["project_id"])
    op.create_index("ix_payroll_client_project_team_id", "payroll_client_project", ["team_id"])
    op.create_index(
        "ix_payroll_client_project_linked_project_id", "payroll_client_project", ["linked_project_id"]
    )

    op.create_table(
        "payroll_client_entry",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("payroll_client_project.id"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("client_id", "kind", "date_from", name="uq_payroll_client_entry"),
    )
    op.create_index("ix_payroll_client_entry_client_id", "payroll_client_entry", ["client_id"])


def downgrade() -> None:
    op.drop_table("payroll_client_entry")
    op.drop_table("payroll_client_project")

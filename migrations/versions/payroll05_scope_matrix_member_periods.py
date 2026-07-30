"""Композитные скоупы, месячные границы членства, периоды формата оплаты клиента.

Кейс Марии: отдельная команда со скоупом «ну-ну × коврики в ванную» —
композит вытесняет категорию из базы brand-only команды «ну-ну».
Кейс Брыссина (клиент консалтинга): формат оплаты периодами — percent до
июня, fixed с июля; формат месяца = период с max(valid_from) <= месяц.

payroll_team_scope: kind/value → brand_value/subject_value (backfill),
uq с NULLS NOT DISTINCT (PG15). payroll_team_member: + valid_from/valid_to.
payroll_client_billing_period: новая таблица, backfill из колонок клиента
(valid_from 2020-01-01, поведение сохраняется), колонки удаляются.

Revision ID: payroll05_scope_matrix
Revises: payroll04_agency
"""

import sqlalchemy as sa
from alembic import op

revision = "payroll05_scope_matrix"
down_revision = "payroll04_agency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payroll_team_scope", sa.Column("brand_value", sa.String(length=300), nullable=True))
    op.add_column("payroll_team_scope", sa.Column("subject_value", sa.String(length=300), nullable=True))
    op.execute(
        """
        UPDATE payroll_team_scope
        SET brand_value = CASE WHEN kind = 'brand' THEN value END,
            subject_value = CASE WHEN kind = 'subject' THEN value END
        """
    )
    op.drop_constraint("uq_payroll_team_scope", "payroll_team_scope", type_="unique")
    op.drop_column("payroll_team_scope", "kind")
    op.drop_column("payroll_team_scope", "value")
    op.execute(
        "ALTER TABLE payroll_team_scope ADD CONSTRAINT uq_payroll_team_scope "
        "UNIQUE NULLS NOT DISTINCT (team_id, brand_value, subject_value)"
    )

    op.add_column("payroll_team_member", sa.Column("valid_from", sa.Date(), nullable=True))
    op.add_column("payroll_team_member", sa.Column("valid_to", sa.Date(), nullable=True))

    op.create_table(
        "payroll_client_billing_period",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("payroll_client_project.id"), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("billing_mode", sa.String(length=20), nullable=False),
        sa.Column("fixed_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("fee_percent", sa.Numeric(6, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("client_id", "valid_from", name="uq_payroll_client_billing_period"),
    )
    op.create_index(
        "ix_payroll_client_billing_period_client_id", "payroll_client_billing_period", ["client_id"]
    )
    op.execute(
        """
        INSERT INTO payroll_client_billing_period
            (client_id, valid_from, billing_mode, fixed_amount, fee_percent, created_at)
        SELECT id, DATE '2020-01-01', billing_mode, fixed_amount, fee_percent, NOW()
        FROM payroll_client_project
        """
    )
    op.drop_column("payroll_client_project", "billing_mode")
    op.drop_column("payroll_client_project", "fixed_amount")
    op.drop_column("payroll_client_project", "fee_percent")


def downgrade() -> None:
    op.add_column(
        "payroll_client_project",
        sa.Column("billing_mode", sa.String(length=20), nullable=True),
    )
    op.add_column("payroll_client_project", sa.Column("fixed_amount", sa.Numeric(18, 2), nullable=True))
    op.add_column("payroll_client_project", sa.Column("fee_percent", sa.Numeric(6, 4), nullable=True))
    # Восстанавливаем последним (максимальный valid_from) периодом.
    op.execute(
        """
        UPDATE payroll_client_project pc
        SET billing_mode = bp.billing_mode,
            fixed_amount = bp.fixed_amount,
            fee_percent = bp.fee_percent
        FROM (
            SELECT DISTINCT ON (client_id) client_id, billing_mode, fixed_amount, fee_percent
            FROM payroll_client_billing_period
            ORDER BY client_id, valid_from DESC
        ) bp
        WHERE bp.client_id = pc.id
        """
    )
    op.execute(
        "UPDATE payroll_client_project SET billing_mode = 'fixed' WHERE billing_mode IS NULL"
    )
    op.alter_column("payroll_client_project", "billing_mode", nullable=False)
    op.drop_table("payroll_client_billing_period")

    op.drop_column("payroll_team_member", "valid_to")
    op.drop_column("payroll_team_member", "valid_from")

    op.add_column("payroll_team_scope", sa.Column("kind", sa.String(length=10), nullable=True))
    op.add_column("payroll_team_scope", sa.Column("value", sa.String(length=300), nullable=True))
    # Композитные скоупы сворачиваем в brand (subject-уточнение теряется).
    op.execute(
        """
        UPDATE payroll_team_scope
        SET kind = CASE WHEN brand_value IS NOT NULL THEN 'brand' ELSE 'subject' END,
            value = COALESCE(brand_value, subject_value)
        """
    )
    op.execute("DELETE FROM payroll_team_scope WHERE value IS NULL")
    op.execute(
        # дедуп после сворачивания композитов
        """
        DELETE FROM payroll_team_scope a USING payroll_team_scope b
        WHERE a.id > b.id AND a.team_id = b.team_id AND a.kind = b.kind AND a.value = b.value
        """
    )
    op.alter_column("payroll_team_scope", "kind", nullable=False)
    op.alter_column("payroll_team_scope", "value", nullable=False)
    op.drop_constraint("uq_payroll_team_scope", "payroll_team_scope", type_="unique")
    op.create_unique_constraint(
        "uq_payroll_team_scope", "payroll_team_scope", ["team_id", "kind", "value"]
    )
    op.drop_column("payroll_team_scope", "subject_value")
    op.drop_column("payroll_team_scope", "brand_value")

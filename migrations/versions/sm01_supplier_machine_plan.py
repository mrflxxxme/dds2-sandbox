"""supplier_machine_plan — плановая дата оплаты остатка по машине (per supplier)

Revision ID: sm01_supplier_machine_plan
Revises: prb01_payment_request_brand
Create Date: 2026-06-29

Остаток по машине считается на пару (поставщик, машина) — у каждого поставщика
своя доля. Эта таблица хранит плановую дату «когда оплатят остаток» per
(project, supplier, order_no). Upsert по уникальному ключу.
"""

from alembic import op
import sqlalchemy as sa

revision = "sm01_supplier_machine_plan"
down_revision = "prb01_payment_request_brand"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supplier_machine_plan",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("order_no", sa.String(length=50), nullable=False),
        sa.Column("remaining_due_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("project_id", "supplier_id", "order_no", name="uq_supplier_machine_plan"),
    )
    op.create_index(
        "ix_supplier_machine_plan_lookup", "supplier_machine_plan", ["project_id", "supplier_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_supplier_machine_plan_lookup", table_name="supplier_machine_plan")
    op.drop_table("supplier_machine_plan")

# ruff: noqa: RUF002, RUF003
"""gazelka_orders: лог отправки заявки логиста в Газельку (gazelka.space)

Audit-запись каждой попытки передать заявку перевозчику Газельке:
snapshot payload, исход (SENT/UNCERTAIN/FAILED), номер у Газельки, выдержка ответа.

Revision ID: gz01_gazelka_orders
Revises: pay06_payment_request_shipment
Create Date: 2026-06-26 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "gz01_gazelka_orders"
down_revision: str | None = "pay06_payment_request_shipment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gazelka_orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("assembly_request_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("gazelka_ref", sa.String(length=50), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_excerpt", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["assembly_request_id"], ["assembly_requests.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gazelka_orders_project_id", "gazelka_orders", ["project_id"])
    op.create_index("ix_gazelka_orders_assembly_request_id", "gazelka_orders", ["assembly_request_id"])


def downgrade() -> None:
    op.drop_index("ix_gazelka_orders_assembly_request_id", table_name="gazelka_orders")
    op.drop_index("ix_gazelka_orders_project_id", table_name="gazelka_orders")
    op.drop_table("gazelka_orders")

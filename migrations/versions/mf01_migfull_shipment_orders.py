# ruff: noqa: RUF002, RUF003
"""migfull_shipment_orders: лог создания заявки на отгрузку в портале ФФ «Натали»

Audit-запись каждой попытки создать заявку в migfull-портале (plusvb.migfull.app):
snapshot шапки+строк описи, исход (SENT/UNCERTAIN/FAILED), guid/reference заявки,
имя описи, выдержка ответа. НЕ путать с read-only API (service="migfull").

Revision ID: mf01_migfull_shipment_orders
Revises: gz01_gazelka_orders
Create Date: 2026-06-26 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "mf01_migfull_shipment_orders"
down_revision: str | None = "gz01_gazelka_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "migfull_shipment_orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("assembly_request_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("shipment_guid", sa.String(length=64), nullable=True),
        sa.Column("shipment_number", sa.String(length=100), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("opis_filename", sa.String(length=200), nullable=True),
        sa.Column("response_excerpt", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["assembly_request_id"], ["assembly_requests.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_migfull_shipment_orders_project_id", "migfull_shipment_orders", ["project_id"])
    op.create_index(
        "ix_migfull_shipment_orders_assembly_request_id", "migfull_shipment_orders", ["assembly_request_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_migfull_shipment_orders_assembly_request_id", table_name="migfull_shipment_orders")
    op.drop_index("ix_migfull_shipment_orders_project_id", table_name="migfull_shipment_orders")
    op.drop_table("migfull_shipment_orders")

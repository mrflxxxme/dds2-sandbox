"""vehicle_documents_and_status_history

Revision ID: ab60160de0fc
Revises: 91fc7e6711f1
Create Date: 2026-04-06 14:56:05.559012

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ab60160de0fc"
down_revision: str | None = "91fc7e6711f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vehicle_documents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("order_no", sa.String(length=50), nullable=False),
        sa.Column("doc_type", sa.String(length=30), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_url", sa.String(length=500), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vehicle_documents_order_no", "vehicle_documents", ["order_no"])
    op.create_index("ix_vehicle_documents_project_id", "vehicle_documents", ["project_id"])

    op.create_table(
        "vehicle_status_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("order_no", sa.String(length=50), nullable=False),
        sa.Column("old_status", sa.String(length=20), nullable=True),
        sa.Column("new_status", sa.String(length=20), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vehicle_status_history_order_no", "vehicle_status_history", ["order_no"])
    op.create_index("ix_vehicle_status_history_project_id", "vehicle_status_history", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_vehicle_status_history_project_id", table_name="vehicle_status_history")
    op.drop_index("ix_vehicle_status_history_order_no", table_name="vehicle_status_history")
    op.drop_table("vehicle_status_history")

    op.drop_index("ix_vehicle_documents_project_id", table_name="vehicle_documents")
    op.drop_index("ix_vehicle_documents_order_no", table_name="vehicle_documents")
    op.drop_table("vehicle_documents")

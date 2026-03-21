"""Create assembly_status_history table.

Revision ID: m5_history
Revises: m4_assembly
Create Date: 2026-03-21
"""

import sqlalchemy as sa
from alembic import op

revision = "m5_history"
down_revision = "m4_assembly"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assembly_status_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("assembly_request_id", sa.Integer(), nullable=False),
        sa.Column("old_status", sa.String(20), nullable=True),
        sa.Column("new_status", sa.String(20), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.Column("changed_by", sa.String(50), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["assembly_request_id"],
            ["assembly_requests.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_assembly_status_history_request_id",
        "assembly_status_history",
        ["assembly_request_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_assembly_status_history_request_id", table_name="assembly_status_history")
    op.drop_table("assembly_status_history")

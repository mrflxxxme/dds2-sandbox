"""add wb_fbo_supply_audit

Revision ID: fb7a1ce8e903
Revises: a27da659cd77
Create Date: 2026-04-23 08:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "fb7a1ce8e903"
down_revision: str | None = "a27da659cd77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_fbo_supply_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("supply_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reverted_audit_id", sa.Integer(), nullable=True),
        sa.Column("reverted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["supply_id"], ["wb_fbo_supplies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["reverted_audit_id"],
            ["wb_fbo_supply_audit.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_wb_fbo_supply_audit_supply",
        "wb_fbo_supply_audit",
        ["supply_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_wb_fbo_supply_audit_project",
        "wb_fbo_supply_audit",
        ["project_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_wb_fbo_supply_audit_project", table_name="wb_fbo_supply_audit")
    op.drop_index("ix_wb_fbo_supply_audit_supply", table_name="wb_fbo_supply_audit")
    op.drop_table("wb_fbo_supply_audit")

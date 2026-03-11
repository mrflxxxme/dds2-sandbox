"""Add audit_log table

Revision ID: i1_add_audit_log
Revises: h1_project_id_not_null
Create Date: 2026-03-11
"""
from alembic import op
import sqlalchemy as sa

revision = "i1_add_audit_log"
down_revision = "h1_project_id_not_null"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("endpoint", sa.String(300), nullable=False),
        sa.Column("status_code", sa.Integer, nullable=False),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("payload_summary", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_project_id", "audit_log", ["project_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade():
    op.drop_index("ix_audit_log_created_at")
    op.drop_index("ix_audit_log_project_id")
    op.drop_index("ix_audit_log_user_id")
    op.drop_table("audit_log")

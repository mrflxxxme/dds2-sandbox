"""make project_id not null on transactions and import_log

Revision ID: h1_project_id_not_null
Revises: g1_add_bonus_type_name
Create Date: 2026-03-11
"""
from alembic import op

revision = "h1_project_id_not_null"
down_revision = "g1_add_bonus_type_name"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("transactions", "project_id", nullable=False)
    op.alter_column("import_log", "project_id", nullable=False)


def downgrade():
    op.alter_column("transactions", "project_id", nullable=True)
    op.alter_column("import_log", "project_id", nullable=True)

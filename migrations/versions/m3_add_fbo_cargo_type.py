"""Add cargo_type column to wb_fbo_supplies.

Revision ID: m3_fbo_cargo
Revises: m2_wb_fbo
Create Date: 2026-03-21
"""

import sqlalchemy as sa
from alembic import op

revision = "m3_fbo_cargo"
down_revision = "m2_wb_fbo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wb_fbo_supplies",
        sa.Column("cargo_type", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wb_fbo_supplies", "cargo_type")

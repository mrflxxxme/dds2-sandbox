"""add_assembly_drafts

Revision ID: 93f1cbd9fd52
Revises: wh08_wb_stocks_qty_full
Create Date: 2026-05-05 08:43:49.809032

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "93f1cbd9fd52"
down_revision: str | None = "wh08_wb_stocks_qty_full"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assembly_drafts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column(
            "name",
            sa.String(length=200),
            nullable=False,
            server_default="Черновик сборки",
        ),
        sa.Column(
            "distribution",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assembly_drafts_project_id",
        "assembly_drafts",
        ["project_id"],
    )
    op.create_index(
        "ix_assembly_drafts_project_active",
        "assembly_drafts",
        ["project_id"],
        postgresql_where=sa.text("is_deleted = false"),
    )


def downgrade() -> None:
    op.drop_index("ix_assembly_drafts_project_active", table_name="assembly_drafts")
    op.drop_index("ix_assembly_drafts_project_id", table_name="assembly_drafts")
    op.drop_table("assembly_drafts")

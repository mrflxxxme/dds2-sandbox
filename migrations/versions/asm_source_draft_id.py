# ruff: noqa: RUF002, RUF003
"""add source_draft_id to assembly_requests

Revision ID: asm_source_draft_id
Revises: b0ba3645ae39
Create Date: 2026-05-21 23:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "asm_source_draft_id"
down_revision: str | None = "b0ba3645ae39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Черновик-источник заявки (поток distribute → «В сборку»). NULL для заявок,
    # созданных иными путями (FBO/ручные). Нужен для per-draft «История — в сборке».
    op.add_column(
        "assembly_requests",
        sa.Column("source_draft_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_assembly_requests_source_draft_id",
        "assembly_requests",
        ["source_draft_id"],
    )
    op.create_foreign_key(
        "fk_assembly_requests_source_draft_id",
        "assembly_requests",
        "assembly_drafts",
        ["source_draft_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_assembly_requests_source_draft_id",
        "assembly_requests",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_assembly_requests_source_draft_id",
        table_name="assembly_requests",
    )
    op.drop_column("assembly_requests", "source_draft_id")

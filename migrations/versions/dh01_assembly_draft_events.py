# ruff: noqa: RUF002, RUF003
"""assembly: таблица assembly_draft_events (история изменений черновика + откат)

История значимых изменений черновика сборки для вкладки «🕘 История» с откатом:
  • PREBOOK_TOPUP / MATRIX_WRITE — before-снапшот distribution (откат = вернуть снапшот,
    только если черновик не менялся после: сверка draft.updated_at == draft_updated_at_after);
  • COMMIT_REQUEST — committed_rows + created_request_ids (откат = удалить заявки под
    гвардом «не на ФФ и не WB-поставка» + вернуть строки в черновик).

Additive, новая таблица, без backfill.

Revision ID: dh01_assembly_draft_events
Revises: pm01_assembly_pallet_manifest
Create Date: 2026-07-08 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "dh01_assembly_draft_events"
down_revision: str | None = "pm01_assembly_pallet_manifest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assembly_draft_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column("before_distribution", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("committed_rows", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_request_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("draft_updated_at_after", sa.DateTime(), nullable=True),
        sa.Column("reverted_at", sa.DateTime(), nullable=True),
        sa.Column("reverted_by", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["draft_id"], ["assembly_drafts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assembly_draft_events_project_id", "assembly_draft_events", ["project_id"])
    op.create_index("ix_assembly_draft_events_draft_id", "assembly_draft_events", ["draft_id"])


def downgrade() -> None:
    op.drop_index("ix_assembly_draft_events_draft_id", table_name="assembly_draft_events")
    op.drop_index("ix_assembly_draft_events_project_id", table_name="assembly_draft_events")
    op.drop_table("assembly_draft_events")

# ruff: noqa: RUF002, RUF003
"""ff local archive: local_archived + local_archived_at on fulfillment_requests

Локальный архив заявок ФФ (сборка/приёмка): пометка пользователя в DDS,
независимая от archived-зеркала провайдера (то затирается каждым синком).

Revision ID: ff03_local_archive
Revises: ff02_request_enrichment
Create Date: 2026-06-12 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ff03_local_archive"
down_revision: str | None = "ff02_request_enrichment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fulfillment_requests",
        sa.Column("local_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("fulfillment_requests", sa.Column("local_archived_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("fulfillment_requests", "local_archived_at")
    op.drop_column("fulfillment_requests", "local_archived")

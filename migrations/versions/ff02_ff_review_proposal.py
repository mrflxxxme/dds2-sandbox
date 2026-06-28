# ruff: noqa: RUF002, RUF003
"""ff-портал: предложение правки состава на согласование (ff_proposed_*)

Revision ID: ff02_ff_review_proposal
Revises: ff01_archive_flags
Create Date: 2026-06-27

Хамза правит состав → правка НЕ применяется к нашему канону, а кладётся как
ПРЕДЛОЖЕНИЕ (ff_proposed_items) со временем/автором; в DDS его согласуют/отклоняют
кнопкой. Пока ff_proposed_items не NULL — заявка «ожидает согласования ФФ».
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ff02_ff_review_proposal"
down_revision = "ff01_archive_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assembly_requests",
        sa.Column("ff_proposed_items", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("assembly_requests", sa.Column("ff_proposed_at", sa.DateTime(), nullable=True))
    op.add_column("assembly_requests", sa.Column("ff_proposed_by", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("assembly_requests", "ff_proposed_by")
    op.drop_column("assembly_requests", "ff_proposed_at")
    op.drop_column("assembly_requests", "ff_proposed_items")

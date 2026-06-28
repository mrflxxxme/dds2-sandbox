# ruff: noqa: RUF002, RUF003
"""ff-портал: is_archived на assembly_requests и inbound_receipts

Revision ID: ff01_archive_flags
Revises: jf01_joint_fbo_supply
Create Date: 2026-06-27

Архив для FF-портала: оператор прячет заявку/приёмку из активного списка,
не меняя статус и не удаляя (ортогонально is_deleted). NOT NULL default false.
"""

import sqlalchemy as sa
from alembic import op

revision = "ff01_archive_flags"
down_revision = "jf01_joint_fbo_supply"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assembly_requests",
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "inbound_receipts",
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_assembly_requests_is_archived", "assembly_requests", ["is_archived"])
    op.create_index("ix_inbound_receipts_is_archived", "inbound_receipts", ["is_archived"])


def downgrade() -> None:
    op.drop_index("ix_inbound_receipts_is_archived", table_name="inbound_receipts")
    op.drop_index("ix_assembly_requests_is_archived", table_name="assembly_requests")
    op.drop_column("inbound_receipts", "is_archived")
    op.drop_column("assembly_requests", "is_archived")

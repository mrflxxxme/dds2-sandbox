"""assembly_requests.package_type

Revision ID: b3a7d24f8c10
Revises: 4a8f1c2e9b6d
Create Date: 2026-05-09 14:30:00.000000

Adds `package_type` column to assembly_requests so each request carries
the WB acceptance unit (BOX | MONOPALLET | SUPERSAFE).

One transport unit = one package_type, so on creation we group items by
(warehouse, package_type) — see services/assembly/crud.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3a7d24f8c10"
down_revision: str | None = "4a8f1c2e9b6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assembly_requests",
        sa.Column(
            "package_type",
            sa.String(length=20),
            nullable=False,
            server_default="BOX",
        ),
    )


def downgrade() -> None:
    op.drop_column("assembly_requests", "package_type")

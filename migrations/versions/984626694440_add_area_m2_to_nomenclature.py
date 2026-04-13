"""add_area_m2_to_nomenclature

Revision ID: 984626694440
Revises: 1085d098624a
Create Date: 2026-04-13 11:34:47.067915

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "984626694440"
down_revision: str | None = "1085d098624a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("nomenclature", sa.Column("area_m2", sa.Numeric(10, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("nomenclature", "area_m2")

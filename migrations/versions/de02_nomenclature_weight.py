"""add weight_kg to nomenclature (reference weight for WEIGHT-basis duty)

Mirrors area_m2: a per-barcode reference weight used as fallback when the items
Excel has no weight, so «От веса» (WEIGHT-basis) duty can still be computed.

Revision ID: de02_nomenclature_weight
Revises: de01_duty_exceptions
Create Date: 2026-06-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "de02_nomenclature_weight"
down_revision: str | None = "de01_duty_exceptions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("nomenclature", sa.Column("weight_kg", sa.Numeric(10, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("nomenclature", "weight_kg")

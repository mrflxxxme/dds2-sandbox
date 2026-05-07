"""nomenclature first_sale_date

Revision ID: 4a8f1c2e9b6d
Revises: dccee1b75a78
Create Date: 2026-05-07 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4a8f1c2e9b6d"
down_revision: str | None = "dccee1b75a78"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "nomenclature",
        sa.Column("first_sale_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("nomenclature", "first_sale_date")

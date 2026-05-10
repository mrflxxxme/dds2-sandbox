"""add use_box_multiplicity flag to nomenclature

Revision ID: 571a514c279d
Revises: 7884bb220c30
Create Date: 2026-05-10 09:28:46.551932

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "571a514c279d"
down_revision: str | None = "7884bb220c30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "nomenclature",
        sa.Column(
            "use_box_multiplicity",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("nomenclature", "use_box_multiplicity")

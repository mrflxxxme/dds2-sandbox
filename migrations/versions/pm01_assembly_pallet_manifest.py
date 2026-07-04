# ruff: noqa: RUF002, RUF003
"""assembly: JSONB-поле pallet_manifest (ручная раскладка коробов по паллетам)

Хранит перетасовку короб→паллета для вкладки «Раскладка по паллетам» на деталке
сборки. Additive, nullable, без backfill: NULL = «авто» (раскладка считается на лету
из pallets_count/геометрии), непустой список = ручная раскладка оператора.

Форма (см. schemas/assembly.PalletBox/BoxContent):
  [{"pallet_no": int, "boxes": [{"barcode": str, "box_count": int, "loose_units": int}]}]

Revision ID: pm01_assembly_pallet_manifest
Revises: pb01_assembly_prebooking_predist
Create Date: 2026-07-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "pm01_assembly_pallet_manifest"
down_revision: str | None = "pb01_assembly_prebooking_predist"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assembly_requests",
        sa.Column("pallet_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assembly_requests", "pallet_manifest")

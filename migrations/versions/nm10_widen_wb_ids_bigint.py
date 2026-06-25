# ruff: noqa: RUF002, RUF003
"""widen WB id columns int32 → bigint (imtID outgrew int32)

WB `imtID` пересёк границу int32 (наблюдалось 2 726 044 315 > 2 147 483 647) →
INSERT в `nomenclature` падал `value out of int32 range`, отравлял сессию и синк
номенклатуры отдавал 500. nmID (`article_wb`) уже на ~1.19 млрд и тоже близко к
потолку. Расширяем WB-идентификаторы на маленьких таблицах до BIGINT.

Затрагивает только мелкие таблицы (nomenclature ~1.4k, imt_aliases 0) — ALTER TYPE
мгновенный. Большие funnel/analytics nm_id-колонки СОЗНАТЕЛЬНО не трогаем (nmID
ещё < int32, а rewrite больших таблиц = долгий ACCESS EXCLUSIVE lock на проде).

Revision ID: nm10_widen_wb_ids_bigint
Revises: ff09_guid_barcodes
Create Date: 2026-06-25 06:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "nm10_widen_wb_ids_bigint"
down_revision: str | None = "ff09_guid_barcodes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "nomenclature",
        "imt_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )
    op.alter_column(
        "nomenclature",
        "article_wb",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )
    op.alter_column(
        "imt_aliases",
        "imt_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "imt_aliases",
        "imt_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
    op.alter_column(
        "nomenclature",
        "article_wb",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
    op.alter_column(
        "nomenclature",
        "imt_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )

# ruff: noqa: RUF002, RUF003
"""Слияние четырёх голов alembic — пустая merge-ревизия

DDL не несёт: её задача — свести ветки, разошедшиеся за время параллельной
работы, в одну голову. Без неё `alembic upgrade head` на проде падает с
«Multiple head revisions are present».

Головы: trv08 (переезды: Газелька, забор по попытке, архив), rbac01 (разделы),
ffret01 (вскрытие коробов Натали), mrg01 (ценовые срезы и stock-watch).

Revision ID: mrg02_merge_four_heads
Revises: ffret01_returns_repack, mrg01_spp03_sw02, rbac01_pages_upd, trv08_transfer_archive
Create Date: 2026-08-02 15:12:08.882027

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "mrg02_merge_four_heads"
down_revision: Union[str, None] = ('ffret01_returns_repack', 'mrg01_spp03_sw02', 'rbac01_pages_upd', 'trv08_transfer_archive')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

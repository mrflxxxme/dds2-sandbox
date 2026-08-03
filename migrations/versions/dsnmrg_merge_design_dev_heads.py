# ruff: noqa: RUF002, RUF003
"""Слияние голов «Дизайн карточек» и dev — пустая merge-ревизия

DDL не несёт: её задача — свести две ветки, разошедшиеся от `spp03_price_probes`,
в одну голову. Без неё `alembic upgrade head` падает с «Multiple head revisions
are present».

Головы: dsn03 (дизайн карточек: индексы FK доменных таблиц), mrg02 (dev: переезды,
разделы RBAC, вскрытие коробов Натали, ценовые срезы и stock-watch).

Revision ID: dsnmrg_design_dev
Revises: dsn03_design_fk_indexes, mrg02_merge_four_heads
Create Date: 2026-08-03 09:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "dsnmrg_design_dev"
down_revision: Union[str, None] = ("dsn03_design_fk_indexes", "mrg02_merge_four_heads")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

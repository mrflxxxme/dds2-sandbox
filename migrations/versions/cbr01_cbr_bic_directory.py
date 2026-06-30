# ruff: noqa: RUF001, RUF002
"""cbr01: national CBR BIC directory (ED807) — БИК → корр. счёт банка

Национальный справочник БИК ЦБ РФ. Заполняется джобом из cbr.ru (ED807-XML).
Источник истины для к/с банка получателя в платёжке (к/с НЕ выводится формулой
из БИК). Без project_id и soft-delete — глобальная справочная таблица, полный
upsert при синке. Идемпотентна (guard через sa.inspect) — на проде/чистой БД создаёт таблицу.

Revision ID: cbr01_cbr_bic_directory
Revises: prb01_payment_request_brand
Create Date: 2026-06-30 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "cbr01_cbr_bic_directory"
down_revision = "prb01_payment_request_brand"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "cbr_bic" in insp.get_table_names():
        return
    op.create_table(
        "cbr_bic",
        sa.Column("bic", sa.String(length=9), primary_key=True),
        sa.Column("corr_account", sa.String(length=20), nullable=False),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("cbr_bic")

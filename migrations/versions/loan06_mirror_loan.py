"""loan: зеркальная связь двух сторон одного договора между проектами

Revision ID: loan06_mirror
Revises: loan05_schedule_fees
Create Date: 2026-07-27

Займ между своими юрлицами (ИП учредителя → ООО) — одна сделка и две книги:
у одного долг, у другого актив. Одной строкой это не описать: правило «каждый
запрос фильтрует project_id» требует своей записи в каждом проекте. Ссылка
держит их вместе, движения вводятся один раз и зеркалятся на вторую сторону.
"""

import sqlalchemy as sa
from alembic import op

revision = "loan06_mirror"
down_revision = "loan05_schedule_fees"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("loan", sa.Column("mirror_loan_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_loan_mirror", "loan", "loan", ["mirror_loan_id"], ["id"])
    # FK обязан иметь индекс; частичный — строк со связью единицы на весь реестр.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_loan_mirror "
        "ON loan (mirror_loan_id) WHERE mirror_loan_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_loan_mirror")
    op.drop_constraint("fk_loan_mirror", "loan", type_="foreignkey")
    op.drop_column("loan", "mirror_loan_id")

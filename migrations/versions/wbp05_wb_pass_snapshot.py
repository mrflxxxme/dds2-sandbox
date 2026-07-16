"""assembly_wb_supply: снимок пропуска из кабинета WB (для сверки ДДС↔ВБ)

Revision ID: wbp05_wb_pass_snapshot
Revises: tg01_supply_notify_enabled
Create Date: 2026-07-15

Синк состояний (`sync_all_states`) теперь тянет и сам пропуск из кабинета WB
(`trn_details`) и кладёт снимок: есть ли пропуск на ВБ, госномер/паллеты/водитель.
Нужно, чтобы блок «Расхождение поставок ФФ» сверял наш пропуск (pass_*) с
кабинетным без построчного HTTP. Все поля nullable (null = ещё не синкали).
"""

import sqlalchemy as sa
from alembic import op

revision = "wbp05_wb_pass_snapshot"
down_revision = "tg01_supply_notify_enabled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assembly_wb_supply", sa.Column("wb_pass_present", sa.Boolean(), nullable=True))
    op.add_column("assembly_wb_supply", sa.Column("wb_pass_car_number", sa.String(length=30), nullable=True))
    op.add_column("assembly_wb_supply", sa.Column("wb_pass_pallets", sa.Integer(), nullable=True))
    op.add_column("assembly_wb_supply", sa.Column("wb_pass_driver", sa.String(length=200), nullable=True))
    op.add_column("assembly_wb_supply", sa.Column("wb_pass_synced_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("assembly_wb_supply", "wb_pass_synced_at")
    op.drop_column("assembly_wb_supply", "wb_pass_driver")
    op.drop_column("assembly_wb_supply", "wb_pass_pallets")
    op.drop_column("assembly_wb_supply", "wb_pass_car_number")
    op.drop_column("assembly_wb_supply", "wb_pass_present")

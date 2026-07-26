"""WB FBS: `reject_dt` поставки — четвёртое состояние вместо схлопнутого `done`

Marketplace API не отдаёт статус поставки: в payload'е только `done`, `scanDt`
и `rejectDt`. Первые два уже хранились, третий терялся — и весь `done=true`
показывался одним ярлыком «Передана». На проде это давало 52 «переданных»
против «В доставке 44» в кабинете, а отклонённую поставку было не отличить
от уехавшей. Колонка закрывает разложение в `FbsSupplyStatus`.

Revision ID: fbs06_supply_reject_dt
Revises: fbs05_overrides_replace_rules
Create Date: 2026-07-26
"""

import sqlalchemy as sa
from alembic import op

revision: str = "fbs06_supply_reject_dt"
down_revision: str | None = "fbs05_overrides_replace_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable без бэкфилла намеренно: значение придёт ближайшим синком поставок
    # (`raw` уже несёт `rejectDt`, просто не раскладывался в колонку), а NULL
    # означает ровно то же, что и у WB, — «не отклонена».
    op.add_column("wb_fbs_supplies", sa.Column("reject_dt", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("wb_fbs_supplies", "reject_dt")

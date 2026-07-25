"""WB FBS: режим склада (наблюдение / трансляция) + гейт по FBO-остатку

Два решения владельца 2026-07-24:
  1. Подключение склада НЕ должно ничего перезаписывать в кабинете. Дефолтный
     режим — `observe`: считаем и показываем расхождение «в WB / у нас», в WB
     не пишем. Запись включается тумблером per-склад.
  2. Продавать по FBS со своего склада то, что ЗАКОНЧИЛОСЬ на складах WB (FBO):
     `fbo_max_qty` = порог доступного FBO-остатка, при превышении позиция
     в FBS не транслируется. NULL — на FBO не смотрим (прежнее поведение).

Revision ID: fbs04_observe_mode_fbo_gate
Revises: fbs03_drop_draft_reserve
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op

revision: str = "fbs04_observe_mode_fbo_gate"
down_revision: str | None = "fbs03_drop_draft_reserve"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wb_fbs_warehouses",
        sa.Column("mode", sa.String(length=10), server_default="observe", nullable=False),
    )
    op.add_column("wb_fbs_warehouses", sa.Column("fbo_max_qty", sa.Integer(), nullable=True))
    # Уже подключённые склады остаются в прежнем поведении: они были заведены
    # до появления режима и владелец их осознанно включал.
    op.execute("UPDATE wb_fbs_warehouses SET mode = 'translate' WHERE is_active = true")


def downgrade() -> None:
    op.drop_column("wb_fbs_warehouses", "fbo_max_qty")
    op.drop_column("wb_fbs_warehouses", "mode")

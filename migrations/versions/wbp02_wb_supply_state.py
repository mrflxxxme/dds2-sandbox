"""assembly_wb_supply: живое состояние поставки WB (bulk-синк listSupplies)

Revision ID: wbp02_wb_supply_state
Revises: mrg01_wbp_ads
Create Date: 2026-07-09

Три колонки под F1-фазу-2: периодический bulk-синк статуса FBW-поставки из
кабинета WB (supply/listSupplies + supply/listStatuses). Храним имя статуса
(«В сборке» / «Готово к отгрузке» / «В пути» …), числовой statusID портала и
отдельную метку синхронизации (не смешивать с операционным last_synced_at).
"""

from alembic import op
import sqlalchemy as sa

revision = "wbp02_wb_supply_state"
down_revision = "mrg01_wbp_ads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assembly_wb_supply",
        sa.Column("wb_supply_state", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "assembly_wb_supply",
        sa.Column("wb_supply_state_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "assembly_wb_supply",
        sa.Column("wb_state_synced_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assembly_wb_supply", "wb_state_synced_at")
    op.drop_column("assembly_wb_supply", "wb_supply_state_id")
    op.drop_column("assembly_wb_supply", "wb_supply_state")

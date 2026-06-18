"""assembly_stock_distribution_daily: ежедневный снимок «где товар»

Накопительная история распределения остатков (склад ФФ / в сборке / готово /
в пути) по складу и статусу товара. FulfillmentStock хранит лишь текущий снимок
(перезатир каждым синком) → динамику склада ФФ нельзя восстановить задним числом,
эта таблица копит её вперёд. Пишется ежедневной scheduler-джобой (idempotent).

Revision ID: asm01_stock_distribution_daily
Revises: ff08_ff_board_warehouse
Create Date: 2026-06-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "asm01_stock_distribution_daily"
down_revision: str | None = "ff08_ff_board_warehouse"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assembly_stock_distribution_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("product_status", sa.String(length=20), nullable=False),
        sa.Column("ff_stock", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("in_assembly", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ready", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("in_transit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "snapshot_date",
            "warehouse_id",
            "product_status",
            name="uq_asm_stock_dist_daily",
        ),
    )
    op.create_index(
        "ix_asm_stock_dist_daily_project_date",
        "assembly_stock_distribution_daily",
        ["project_id", "snapshot_date"],
    )
    op.create_index(
        "ix_asm_stock_dist_daily_warehouse_id",
        "assembly_stock_distribution_daily",
        ["warehouse_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_asm_stock_dist_daily_warehouse_id",
        table_name="assembly_stock_distribution_daily",
    )
    op.drop_index(
        "ix_asm_stock_dist_daily_project_date",
        table_name="assembly_stock_distribution_daily",
    )
    op.drop_table("assembly_stock_distribution_daily")

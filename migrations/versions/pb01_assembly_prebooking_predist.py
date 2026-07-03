# ruff: noqa: RUF002, RUF003
"""assembly: колонки предзаявки (is_prebooking) и предраспределения машины

Формализация трёх колонок на ``assembly_requests``, до этого добавленных в dev DB
вручную (см. память assembly-need-unified):
  - source_vehicle_id  — привязка заявки к машине-источнику (cost_orders.id) при
    предраспределении (статус PRE_DISTRIBUTED, до физприёмки).
  - is_pre_distribution — флаг предраспределения, остаётся True на всю жизнь заявки.
  - is_prebooking       — флаг предзаявки (бронь целой моно-паллеты на WB-склад без
    лимита приёмки), остаётся True на всю жизнь заявки.

Revision ID: pb01_assembly_prebooking_predist
Revises: mf01_migfull_shipment_orders
Create Date: 2026-07-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "pb01_assembly_prebooking_predist"
down_revision: str | None = "mf01_migfull_shipment_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assembly_requests",
        sa.Column("source_vehicle_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "assembly_requests",
        sa.Column(
            "is_pre_distribution",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "assembly_requests",
        sa.Column(
            "is_prebooking",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_assembly_requests_source_vehicle_id",
        "assembly_requests",
        ["source_vehicle_id"],
    )
    op.create_foreign_key(
        "assembly_requests_source_vehicle_id_fkey",
        "assembly_requests",
        "cost_orders",
        ["source_vehicle_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "assembly_requests_source_vehicle_id_fkey",
        "assembly_requests",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_assembly_requests_source_vehicle_id",
        table_name="assembly_requests",
    )
    op.drop_column("assembly_requests", "is_prebooking")
    op.drop_column("assembly_requests", "is_pre_distribution")
    op.drop_column("assembly_requests", "source_vehicle_id")

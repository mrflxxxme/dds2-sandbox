# ruff: noqa: RUF002, RUF003
"""Перемещение как полноценный переезд: машина, логистика, оплата, связь с заявкой

Переезд между ФФ везёт наёмная машина и оплачивается так же, как обычная
заявка на сборку. До этой миграции stock_transfers знал только маршрут,
состав и статус — машину и деньги вести было негде, и переезды оформляли
заявками на сборку (кейс «транзит Питер / транзит ЕКБ», 31.07.2026).

Что добавляется:
  1. stock_transfers — блок машины и логистики (зеркало AssemblyRequest:
     госномер, марка, водитель, перевозчик, дата/слот забора, стоимость)
     + converted_from_assembly_id (переезд, созданный из заявки).
  2. outbound_shipments.stock_transfer_id — забор переезда: носитель денег
     (заявка на оплату + связка с выпиской). Стока НЕ двигает: списание
     остаётся за самим перемещением (reference_type='TRANSFER').

Статусная цепочка перемещения (DRAFT → IN_TRANSIT → COMPLETED) НЕ меняется:
её читают авто-приём ФФ и отчёты.

Revision ID: trv01_transfer_vehicle
Revises: mfin01_inbound_push
Create Date: 2026-07-31 13:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "trv01_transfer_vehicle"
down_revision: str | None = "mfin01_inbound_push"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── 1. Машина и логистика на перемещении ──────────────────────────────
    op.add_column("stock_transfers", sa.Column("vehicle_info", sa.String(length=300), nullable=True))
    op.add_column("stock_transfers", sa.Column("vehicle_brand", sa.String(length=100), nullable=True))
    op.add_column("stock_transfers", sa.Column("driver_first_name", sa.String(length=100), nullable=True))
    op.add_column("stock_transfers", sa.Column("driver_last_name", sa.String(length=100), nullable=True))
    op.add_column("stock_transfers", sa.Column("driver_phone", sa.String(length=30), nullable=True))
    op.add_column("stock_transfers", sa.Column("counterparty_id", sa.Integer(), nullable=True))
    op.add_column(
        "stock_transfers",
        sa.Column(
            "logistics_by_warehouse",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("stock_transfers", sa.Column("pickup_date", sa.Date(), nullable=True))
    op.add_column("stock_transfers", sa.Column("pickup_time_slot", sa.String(length=20), nullable=True))
    # Деньги — Numeric(18, 2), не Float (Iron rule проекта).
    op.add_column("stock_transfers", sa.Column("pickup_cost", sa.Numeric(18, 2), nullable=True))
    op.add_column("stock_transfers", sa.Column("delivery_date", sa.Date(), nullable=True))
    op.add_column("stock_transfers", sa.Column("vehicle_assigned_at", sa.DateTime(), nullable=True))
    op.add_column("stock_transfers", sa.Column("converted_from_assembly_id", sa.Integer(), nullable=True))

    op.create_foreign_key(
        "fk_stock_transfers_counterparty_id",
        "stock_transfers",
        "counterparty",
        ["counterparty_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_stock_transfers_converted_from_assembly_id",
        "stock_transfers",
        "assembly_requests",
        ["converted_from_assembly_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_stock_transfers_counterparty_id",
        "stock_transfers",
        ["counterparty_id"],
    )
    op.create_index(
        "ix_stock_transfers_converted_from_assembly_id",
        "stock_transfers",
        ["converted_from_assembly_id"],
    )

    # ─── 2. Забор переезда — носитель оплаты ───────────────────────────────
    op.add_column("outbound_shipments", sa.Column("stock_transfer_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_outbound_shipments_stock_transfer_id",
        "outbound_shipments",
        "stock_transfers",
        ["stock_transfer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_outbound_shipments_stock_transfer_id",
        "outbound_shipments",
        ["stock_transfer_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_outbound_shipments_stock_transfer_id", table_name="outbound_shipments")
    op.drop_constraint("fk_outbound_shipments_stock_transfer_id", "outbound_shipments", type_="foreignkey")
    op.drop_column("outbound_shipments", "stock_transfer_id")

    op.drop_index("ix_stock_transfers_converted_from_assembly_id", table_name="stock_transfers")
    op.drop_index("ix_stock_transfers_counterparty_id", table_name="stock_transfers")
    op.drop_constraint("fk_stock_transfers_converted_from_assembly_id", "stock_transfers", type_="foreignkey")
    op.drop_constraint("fk_stock_transfers_counterparty_id", "stock_transfers", type_="foreignkey")
    op.drop_column("stock_transfers", "converted_from_assembly_id")
    op.drop_column("stock_transfers", "vehicle_assigned_at")
    op.drop_column("stock_transfers", "delivery_date")
    op.drop_column("stock_transfers", "pickup_cost")
    op.drop_column("stock_transfers", "pickup_time_slot")
    op.drop_column("stock_transfers", "pickup_date")
    op.drop_column("stock_transfers", "logistics_by_warehouse")
    op.drop_column("stock_transfers", "counterparty_id")
    op.drop_column("stock_transfers", "driver_phone")
    op.drop_column("stock_transfers", "driver_last_name")
    op.drop_column("stock_transfers", "driver_first_name")
    op.drop_column("stock_transfers", "vehicle_brand")
    op.drop_column("stock_transfers", "vehicle_info")

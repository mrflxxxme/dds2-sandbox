# ruff: noqa: RUF002, RUF003
"""assembly shipping-attempt chain: per-attempt logistics on outbound/inbound docs

Одна заявка на сборку может пройти НЕСКОЛЬКО попыток отгрузки (отгрузили → WB не
принял → вернули на склад → переотгрузили новым водителем по новой FBW-поставке).
Раньше AssemblyRequest хранил лишь ОДНУ попытку и затирал её при переотгрузке.

Делаем связь заявка→отгрузка долговечной (1:N) и кладём снимок логистики попытки
на сам OutboundShipment (водитель/машина/FBW/стоимость/палеты на момент отгрузки).
Возврат-приёмку (InboundReceipt) привязываем к заявке+попытке; её warehouse_id =
склад, НА который вернули (может отличаться от склада-источника).

Бэкфилл: текущая отгрузка каждой заявки → попытка №1 (снимок из AssemblyRequest);
приёмки-возвраты привязываем по номеру заявки в комментарии (best-effort).

Revision ID: asm02_ship_attempt_chain
Revises: nm10_widen_wb_ids_bigint
Create Date: 2026-06-25 10:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "asm02_ship_attempt_chain"
down_revision: str | None = "nm10_widen_wb_ids_bigint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── OutboundShipment: per-attempt logistics snapshot + durable back-links ──
    op.add_column(
        "outbound_shipments",
        sa.Column("assembly_request_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "outbound_shipments",
        sa.Column("wb_fbo_supply_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "outbound_shipments",
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("outbound_shipments", sa.Column("pickup_cost", sa.Numeric(18, 2), nullable=True))
    op.add_column("outbound_shipments", sa.Column("vehicle_info", sa.String(length=300), nullable=True))
    op.add_column("outbound_shipments", sa.Column("vehicle_brand", sa.String(length=100), nullable=True))
    op.add_column("outbound_shipments", sa.Column("driver_phone", sa.String(length=30), nullable=True))
    op.add_column("outbound_shipments", sa.Column("counterparty_id", sa.Integer(), nullable=True))
    op.add_column("outbound_shipments", sa.Column("pickup_date", sa.Date(), nullable=True))
    op.add_column("outbound_shipments", sa.Column("pickup_time_slot", sa.String(length=20), nullable=True))
    op.add_column("outbound_shipments", sa.Column("delivery_date", sa.Date(), nullable=True))
    op.add_column("outbound_shipments", sa.Column("pallets_count", sa.Integer(), nullable=True))
    op.add_column("outbound_shipments", sa.Column("pallet_weight_kg", sa.Numeric(10, 2), nullable=True))

    op.create_foreign_key(
        "fk_outbound_shipments_assembly_request_id",
        "outbound_shipments",
        "assembly_requests",
        ["assembly_request_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_outbound_shipments_wb_fbo_supply_id",
        "outbound_shipments",
        "wb_fbo_supplies",
        ["wb_fbo_supply_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_outbound_shipments_counterparty_id",
        "outbound_shipments",
        "counterparty",
        ["counterparty_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_outbound_shipments_assembly_request_id", "outbound_shipments", ["assembly_request_id"]
    )
    op.create_index("ix_outbound_shipments_wb_fbo_supply_id", "outbound_shipments", ["wb_fbo_supply_id"])
    op.create_index("ix_outbound_shipments_counterparty_id", "outbound_shipments", ["counterparty_id"])

    # ─── InboundReceipt: link return-receipt to assembly request + attempt ──────
    op.add_column("inbound_receipts", sa.Column("assembly_request_id", sa.Integer(), nullable=True))
    op.add_column("inbound_receipts", sa.Column("assembly_attempt_no", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_inbound_receipts_assembly_request_id",
        "inbound_receipts",
        "assembly_requests",
        ["assembly_request_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_inbound_receipts_assembly_request_id", "inbound_receipts", ["assembly_request_id"]
    )

    # ─── Backfill: текущая отгрузка каждой заявки → попытка №1 ───────────────────
    # Снимок логистики из AssemblyRequest на связанную отгрузку.
    op.execute(
        sa.text(
            """
            UPDATE outbound_shipments os
            SET assembly_request_id = ar.id,
                attempt_no = 1,
                wb_fbo_supply_id = ar.wb_fbo_supply_id,
                pickup_cost = ar.pickup_cost,
                vehicle_info = ar.vehicle_info,
                vehicle_brand = ar.vehicle_brand,
                driver_phone = ar.driver_phone,
                counterparty_id = ar.counterparty_id,
                pickup_date = ar.pickup_date,
                pickup_time_slot = ar.pickup_time_slot,
                delivery_date = ar.delivery_date,
                pallets_count = ar.pallets_count,
                pallet_weight_kg = ar.pallet_weight_kg
            FROM assembly_requests ar
            WHERE ar.outbound_shipment_id = os.id
              AND ar.is_deleted = false
              AND os.is_deleted = false
              AND os.assembly_request_id IS NULL
            """
        )
    )

    # Приёмки-возвраты привязываем к заявке по её номеру в авто-комментарии
    # «Возврат по заявке {number} ...» (best-effort: attempt_no неизвестен → NULL).
    op.execute(
        sa.text(
            """
            UPDATE inbound_receipts ir
            SET assembly_request_id = ar.id
            FROM assembly_requests ar
            WHERE ar.project_id = ir.project_id
              AND ar.is_deleted = false
              AND ir.comment LIKE 'Возврат по заявке ' || ar.number || ' %'
              AND ir.assembly_request_id IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_inbound_receipts_assembly_request_id", table_name="inbound_receipts")
    op.drop_constraint("fk_inbound_receipts_assembly_request_id", "inbound_receipts", type_="foreignkey")
    op.drop_column("inbound_receipts", "assembly_attempt_no")
    op.drop_column("inbound_receipts", "assembly_request_id")

    op.drop_index("ix_outbound_shipments_counterparty_id", table_name="outbound_shipments")
    op.drop_index("ix_outbound_shipments_wb_fbo_supply_id", table_name="outbound_shipments")
    op.drop_index("ix_outbound_shipments_assembly_request_id", table_name="outbound_shipments")
    op.drop_constraint("fk_outbound_shipments_counterparty_id", "outbound_shipments", type_="foreignkey")
    op.drop_constraint("fk_outbound_shipments_wb_fbo_supply_id", "outbound_shipments", type_="foreignkey")
    op.drop_constraint("fk_outbound_shipments_assembly_request_id", "outbound_shipments", type_="foreignkey")
    for col in (
        "pallet_weight_kg",
        "pallets_count",
        "delivery_date",
        "pickup_time_slot",
        "pickup_date",
        "counterparty_id",
        "driver_phone",
        "vehicle_brand",
        "vehicle_info",
        "pickup_cost",
        "attempt_no",
        "wb_fbo_supply_id",
        "assembly_request_id",
    ):
        op.drop_column("outbound_shipments", col)

# ruff: noqa: RUF002, RUF003
"""
FF billing: тарифы услуг фулфилмента, посуточное хранение, счета ФФ и их сверка.

Механика (зеркалит паттерн «Оплаты» Листа логиста, но для услуг ФФ):
  1. У каждого ФФ-склада — свои тарифы (`WarehouseTariff`).
  2. Джоба пишет посуточное начисление хранения (`FfStorageDaily`):
     штуки → короба (box_multiplicity_service) → паллеты (pallet_boxes_by_size).
  3. Счёт от ФФ (`FfInvoice`) разносится строками (`FfInvoiceLine`) по заявкам
     сборки / дням хранения / приёмкам / заборам, сверяется с нашим расчётом
     и метчится с оплатой по реквизитам (ИНН юрлиц склада).
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin, TimestampMixin
from backend.utils.time import utcnow

# ─── Enums ──────────────────────────────────────────────────────────────────


class FfServiceType(str, enum.Enum):
    PALLETIZING = "PALLETIZING"  # паллетирование, ₽/паллета отправки
    BOX_PROCESSING = "BOX_PROCESSING"  # обработка коробки, ₽/короб отправки
    STORAGE = "STORAGE"  # хранение, ₽/паллета/день
    TRUCK_UNLOADING = "TRUCK_UNLOADING"  # выгрузка фуры, ₽/машина (приёмка поставки)
    LOADING = "LOADING"  # погрузка, ₽/паллета ИЛИ ₽/короб (unit)


class FfTariffUnit(str, enum.Enum):
    PALLET = "PALLET"
    BOX = "BOX"


class FfInvoiceKind(str, enum.Enum):
    SHIPMENT = "SHIPMENT"  # услуги отправок (паллетирование+обработка+погрузка)
    STORAGE = "STORAGE"  # хранение за период
    RECEIVING = "RECEIVING"  # выгрузка фур (приёмки поставок)
    LOGISTICS = "LOGISTICS"  # перевозка силами ФФ (заборы, где перевозчик = юрлицо склада)
    MIXED = "MIXED"


class FfInvoiceStatus(str, enum.Enum):
    NEW = "NEW"  # загружен/создан, не сверен
    RECONCILED = "RECONCILED"  # сверен, расхождение в допуске
    DISPUTED = "DISPUTED"  # сверен, расхождение вне допуска
    PAID = "PAID"  # привязан платёж (выписка или заявка на оплату)


class FfInvoiceLineKind(str, enum.Enum):
    ASSEMBLY = "ASSEMBLY"  # услуги отправки по заявке сборки
    STORAGE = "STORAGE"  # хранение за диапазон дней
    RECEIVING = "RECEIVING"  # выгрузка фуры (приёмка)
    LOGISTICS = "LOGISTICS"  # забор силами ФФ
    CUSTOM = "CUSTOM"  # ручная строка (вне тарифной сетки)


# ─── Тарифы услуг ФФ ────────────────────────────────────────────────────────


class WarehouseTariff(Base, TimestampMixin, SoftDeleteMixin):
    """Ставка услуги ФФ на складе, с историей (valid_from/valid_to).

    `unit` осмыслен только для LOADING (PALLET|BOX — чем биллит склад);
    у остальных услуг единица фиксирована типом (см. FfServiceType) и unit=NULL.
    Пересечения периодов одного (склад, услуга) режет сервис, не БД.
    """

    __tablename__ = "warehouse_tariffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    service_type: Mapped[str] = mapped_column(String(20), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(10), nullable=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        CheckConstraint("rate >= 0", name="ck_warehouse_tariffs_rate_nonneg"),
        Index("ix_warehouse_tariffs_project_id", "project_id"),
        Index("ix_warehouse_tariffs_wh_service", "warehouse_id", "service_type"),
    )


# ─── Посуточное хранение ────────────────────────────────────────────────────


class FfStorageDaily(Base):
    """Суточное начисление хранения на ФФ-складе (пишет scheduler-джоба).

    Штуки/короба/паллеты фиксируются НА МОМЕНТ среза (защита истории от
    последующих правок кратности). storage_rate/storage_cost = NULL, если на
    дату среза у склада нет тарифа STORAGE. Источник штук — FulfillmentStock,
    для ФФ без API-зеркала — WarehouseStock (грабля «апл/хамза»).
    """

    __tablename__ = "ff_storage_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    boxes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pallets: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    storage_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    # Drill-down по ШК: {barcode: {"units": int, "boxes": int}}
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        # project_id покрыт leftmost-колонкой uq_ff_storage_daily_day.
        UniqueConstraint("project_id", "warehouse_id", "snapshot_date", name="uq_ff_storage_daily_day"),
        Index("ix_ff_storage_daily_wh_date", "warehouse_id", "snapshot_date"),
    )


# ─── Счета ФФ ───────────────────────────────────────────────────────────────


class FfInvoice(Base, TimestampMixin, SoftDeleteMixin):
    """Счёт фулфилмента за период (отправки / хранение / приёмки / логистика).

    Метчинг с оплатой — два пути (оба по реквизитам юрлиц склада):
      * `payment_request_id` — через заявку на оплату категории FULFILLMENT;
      * `matched_transaction_id` — прямая привязка к дебету выписки. БЕЗ
        unique: недельный платёж одной суммой может закрывать N счетов.
    """

    __tablename__ = "ff_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    # NULL = склад не отрезолвился по ИНН — требует ручного выбора.
    warehouse_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=True)
    counterparty_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("counterparty.id", ondelete="SET NULL"), nullable=True
    )
    number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FfInvoiceKind.MIXED.value, server_default=FfInvoiceKind.MIXED.value
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FfInvoiceStatus.NEW.value, server_default=FfInvoiceStatus.NEW.value
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    # Наш расчёт на момент последней сверки (Σ our_cost строк).
    our_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    payee_inn: Mapped[str | None] = mapped_column(String(12), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Файл счёта (MinIO), как у PaymentRequestDocument.
    minio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    payment_request_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("payment_request.id", ondelete="SET NULL"), nullable=True
    )
    matched_transaction_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    matched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    lines: Mapped[list["FfInvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_ff_invoices_amount_positive"),
        # project_id покрыт leftmost-колонкой ix_ff_invoices_project_status.
        Index("ix_ff_invoices_project_status", "project_id", "status"),
        Index("ix_ff_invoices_warehouse_id", "warehouse_id"),
        Index("ix_ff_invoices_counterparty_id", "counterparty_id"),
        Index("ix_ff_invoices_payment_request_id", "payment_request_id"),
        Index("ix_ff_invoices_matched_transaction_id", "matched_transaction_id"),
    )


class FfInvoiceLine(Base, TimestampMixin):
    """Строка разнесения счёта: на что и сколько (наш расчёт vs доля счёта).

    Ровно один объект-якорь по kind: ASSEMBLY → assembly_request_id,
    RECEIVING → inbound_receipt_id, LOGISTICS → outbound_shipment_id,
    STORAGE → date_from/date_to, CUSTOM → только label. Строки пересобираются
    сервисом целиком (hard delete через cascade) — без SoftDeleteMixin.
    """

    __tablename__ = "ff_invoice_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    invoice_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ff_invoices.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    assembly_request_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("assembly_requests.id", ondelete="SET NULL"), nullable=True
    )
    outbound_shipment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("outbound_shipments.id", ondelete="SET NULL"), nullable=True
    )
    inbound_receipt_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("inbound_receipts.id", ondelete="SET NULL"), nullable=True
    )
    date_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    qty_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qty_boxes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qty_pallets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    our_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    allocated_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    label: Mapped[str | None] = mapped_column(String(300), nullable=True)

    invoice: Mapped["FfInvoice"] = relationship(back_populates="lines")

    __table_args__ = (
        Index("ix_ff_invoice_lines_invoice_id", "invoice_id"),
        Index("ix_ff_invoice_lines_project_id", "project_id"),
        Index("ix_ff_invoice_lines_assembly_request_id", "assembly_request_id"),
        Index("ix_ff_invoice_lines_outbound_shipment_id", "outbound_shipment_id"),
        Index("ix_ff_invoice_lines_inbound_receipt_id", "inbound_receipt_id"),
    )

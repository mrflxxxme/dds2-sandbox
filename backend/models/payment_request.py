# ruff: noqa: RUF002, RUF003
"""
Payment-request models: PaymentRequest, PaymentRequestDocument, PaymentRequestEvent.

«Заявка на оплату» — логист по ОТПРАВЛЕННОЙ отгрузке (OutboundShipment) формирует
заявку на оплату перевозчику: реквизиты (ИНН/р-с/БИК) либо вручную, либо авто-
заполнением из контрагента; прикладывает счёт+акт (MinIO); передаёт в оплату.

Жизненный цикл (status хранится как String(20), не pg-enum — анти-Alembic-churn):

    DRAFT ─submit─▶ PENDING_REVIEW ─согласовать─▶ APPROVED ─оплачено─▶ PAID
      │                 │   │                         │
      │                 │   └─«Создать оплату»─▶ DRAFT_CREATED ─выписка─▶ PAID
      │                 │                       ▲     └─REJECTED / CANCELLED
      │                 │   APPROVED ─«Создать оплату»┘  (банк после ручного согласования)
      └─CANCELLED       └─REJECTED / CANCELLED / DRAFT (вернуть на правку)

PAID ставит ЛИБО админ вручную (APPROVED→PAID, оплата вне системы), ЛИБО матчер
выписки (etl/sync_payment_requests) после DRAFT_CREATED — исходящий дебет с тем же
ИНН и суммой. См. backend/DOMAIN_PAYMENT_REQUEST.md.

`project_id` nullable: заявка может быть БЕЗ проекта («общая» — её подаёт любой
пользователь, видна в едином инбоксе согласования). `category` — назначение оплаты
(логистика/фотоконтент/таможня/другое).
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin, TimestampMixin
from backend.utils.time import utcnow

if TYPE_CHECKING:
    from backend.models.auth import Project


# ─── Enums (stored as String to avoid Alembic enum churn) ────────────────────


class PaymentRequestStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"          # согласовано админом (оплата вне системы)
    DRAFT_CREATED = "DRAFT_CREATED"
    PAID = "PAID"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class PaymentRequestSource(str, enum.Enum):
    MANUAL = "MANUAL"          # реквизиты введены вручную
    COUNTERPARTY = "COUNTERPARTY"  # авто-заполнение из контрагента/отгрузки


class PaymentRequestCategory(str, enum.Enum):
    """Назначение оплаты. LOGISTICS — исторический дефолт (заявки логиста по забору)."""

    LOGISTICS = "LOGISTICS"          # логистика / перевозка
    PHOTO_CONTENT = "PHOTO_CONTENT"  # фотоконтент
    CUSTOMS = "CUSTOMS"              # таможенное оформление
    FULFILLMENT = "FULFILLMENT"      # фулфилмент
    DESIGN = "DESIGN"                # дизайн
    HOUSEHOLD = "HOUSEHOLD"          # хозрасходы
    OTHER = "OTHER"                  # другое


class PaymentRequestDocType(str, enum.Enum):
    INVOICE = "INVOICE"  # счёт
    ACT = "ACT"          # акт


class PaymentCategory(Base, SoftDeleteMixin):
    """Справочник «Назначение оплаты» — редактируемый список категорий заявок.

    `code` — стабильный ключ, хранится в `payment_requests.category` (НЕ меняется при
    переименовании). `label` — отображение (редактируемое). `is_system` — встроенная
    категория (7 дефолтов): можно переименовать, нельзя удалить (LOGISTICS/OTHER —
    дефолты сервиса, LOGISTICS несёт спец-логику привязки к отгрузке/банку).
    project_id NULL = глобальная (общая для всех брендов)."""

    __tablename__ = "payment_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    __table_args__ = (
        Index("ix_payment_categories_project_id", "project_id"),
        # partial-unique по коду — соблюдает SoftDeleteMixin (повторный INSERT после
        # soft-delete не падает); создаётся в миграции как partial index WHERE NOT is_deleted.
    )


# Declarative state machine — single source of truth, mirrors ASSEMBLY_TRANSITIONS.
PAYMENT_REQUEST_TRANSITIONS: dict[PaymentRequestStatus, set[PaymentRequestStatus]] = {
    PaymentRequestStatus.DRAFT: {
        PaymentRequestStatus.PENDING_REVIEW,
        PaymentRequestStatus.CANCELLED,
    },
    PaymentRequestStatus.PENDING_REVIEW: {
        PaymentRequestStatus.APPROVED,       # согласовать (ручная оплата вне системы)
        PaymentRequestStatus.DRAFT_CREATED,  # «Создать оплату» (платёжка в банк)
        PaymentRequestStatus.DRAFT,          # вернуть на правку
        PaymentRequestStatus.REJECTED,
        PaymentRequestStatus.CANCELLED,
    },
    PaymentRequestStatus.APPROVED: {
        PaymentRequestStatus.DRAFT_CREATED,  # «Создать оплату» (платёжка в банк) и после согласования
        PaymentRequestStatus.PAID,           # отметить оплаченным вручную
        PaymentRequestStatus.REJECTED,
        PaymentRequestStatus.CANCELLED,
    },
    PaymentRequestStatus.DRAFT_CREATED: {
        PaymentRequestStatus.PAID,
        PaymentRequestStatus.REJECTED,
        PaymentRequestStatus.CANCELLED,
    },
    PaymentRequestStatus.PAID: set(),       # terminal
    PaymentRequestStatus.REJECTED: set(),   # terminal
    PaymentRequestStatus.CANCELLED: set(),  # terminal
}


# ─── PaymentRequest ──────────────────────────────────────────────────────────


class PaymentRequest(Base, TimestampMixin, SoftDeleteMixin):
    """Заявка на оплату перевозчику по отгрузке."""

    __tablename__ = "payment_request"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # nullable: «общая» заявка без проекта (подаёт любой пользователь, единый инбокс согласования).
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PaymentRequestStatus.DRAFT.value, server_default="DRAFT"
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PaymentRequestSource.MANUAL.value, server_default="MANUAL"
    )

    # Provenance links (the отгрузка/контрагент this payment is for) — all nullable.
    counterparty_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("counterparty.id", ondelete="SET NULL"), nullable=True
    )
    outbound_shipment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("outbound_shipments.id", ondelete="SET NULL"), nullable=True
    )
    assembly_request_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("assembly_requests.id", ondelete="SET NULL"), nullable=True
    )

    # Frozen requisites snapshot — survives Counterparty edits.
    payee_inn: Mapped[str | None] = mapped_column(String(12), nullable=True)
    payee_kpp: Mapped[str | None] = mapped_column(String(9), nullable=True)
    payee_account: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payee_bik: Mapped[str | None] = mapped_column(String(9), nullable=True)
    payee_bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payee_corr_account: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payee_name: Mapped[str | None] = mapped_column(String(500), nullable=True)

    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB", server_default="RUB")
    pickup_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Назначение оплаты (PaymentRequestCategory). NULL у старых заявок = логистика.
    category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Бренд (атрибут товара из wb_finance_rows.brand_name). NULL = «Все бренды» — общий
    # расход по проекту. Тег для фильтра в списке; на отчёты пока не влияет.
    brand: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Bank-statement match (set by the auto-matcher) → flips status to PAID.
    matched_transaction_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    matched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Bank payment draft (Faktura write) — idempotency guid + returned bank doc id.
    bank_guid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    bank_doc_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    # Relationships
    documents: Mapped[list["PaymentRequestDocument"]] = relationship(
        back_populates="payment_request",
        foreign_keys="PaymentRequestDocument.payment_request_id",
        cascade="all, delete-orphan",
    )
    events: Mapped[list["PaymentRequestEvent"]] = relationship(
        back_populates="payment_request",
        foreign_keys="PaymentRequestEvent.payment_request_id",
        cascade="all, delete-orphan",
    )
    shipments: Mapped[list["PaymentRequestShipment"]] = relationship(
        back_populates="payment_request",
        cascade="all, delete-orphan",
    )
    # Read-only проект — для отображения имени в деталке (project_name). Не управляем им.
    project: Mapped["Project | None"] = relationship(
        "Project", viewonly=True, foreign_keys="PaymentRequest.project_id"
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payment_request_amount_positive"),
        Index("ix_payment_request_project_id", "project_id"),
        Index("ix_payment_request_status", "project_id", "status"),
        Index("ix_payment_request_outbound_shipment_id", "outbound_shipment_id"),
        Index("ix_payment_request_counterparty_id", "counterparty_id"),
        # Partial unique/indexes created in the migration (WHERE is_deleted = false):
        #   uq_payment_request_project_number   UNIQUE (project_id, number)
        #   uq_payment_request_general_number   UNIQUE (number) WHERE project_id IS NULL  (pay07; «общие» заявки)
        #   uq_payment_request_bank_guid        UNIQUE (bank_guid) WHERE bank_guid IS NOT NULL
        #   uq_payment_request_matched_txn      UNIQUE (matched_transaction_id) WHERE matched_transaction_id IS NOT NULL
    )


# ─── PaymentRequestDocument ──────────────────────────────────────────────────


class PaymentRequestDocument(Base, TimestampMixin, SoftDeleteMixin):
    """Счёт/акт, приложенные к заявке (хранятся в MinIO). Зеркало CounterpartyDocument."""

    __tablename__ = "payment_request_document"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # nullable: зеркалит project_id заявки (у «общей» заявки — NULL).
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    payment_request_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("payment_request.id", ondelete="CASCADE"), nullable=False
    )
    minio_path: Mapped[str] = mapped_column(String(500), nullable=False)
    doc_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PaymentRequestDocType.INVOICE.value, server_default="INVOICE"
    )
    original_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    payment_request: Mapped["PaymentRequest"] = relationship(
        back_populates="documents",
        foreign_keys=[payment_request_id],
    )

    __table_args__ = (
        Index("ix_payment_request_document_project_id", "project_id"),
        Index("ix_payment_request_document_request_id", "payment_request_id"),
    )


# ─── PaymentRequestEvent ─────────────────────────────────────────────────────


class PaymentRequestEvent(Base):
    """Аудит-лог переходов статуса (включая авто-матч из выписки). Зеркало AssemblyStatusHistory."""

    __tablename__ = "payment_request_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_request_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("payment_request.id", ondelete="CASCADE"), nullable=False
    )
    old_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    changed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    payment_request: Mapped["PaymentRequest"] = relationship(
        back_populates="events",
        foreign_keys=[payment_request_id],
    )

    __table_args__ = (Index("ix_payment_request_event_request_id", "payment_request_id"),)


class PaymentRequestShipment(Base):
    """Связь «заявка на оплату ↔ забор». Одна заявка может покрывать НЕСКОЛЬКО заборов
    (один счёт перевозчика за две отгрузки). Один забор — максимум в одной активной заявке
    (проверяется в сервисе). Legacy: PaymentRequest.outbound_shipment_id = первый/основной забор.
    """

    __tablename__ = "payment_request_shipment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    payment_request_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("payment_request.id", ondelete="CASCADE"), nullable=False
    )
    outbound_shipment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("outbound_shipments.id", ondelete="CASCADE"), nullable=False
    )

    payment_request: Mapped["PaymentRequest"] = relationship(back_populates="shipments")

    __table_args__ = (
        Index("ix_payment_request_shipment_project_id", "project_id"),
        Index("ix_payment_request_shipment_request_id", "payment_request_id"),
        Index("ix_payment_request_shipment_outbound_shipment_id", "outbound_shipment_id"),
        UniqueConstraint(
            "payment_request_id", "outbound_shipment_id", name="uq_payment_request_shipment"
        ),
    )


# ─── PaymentShipmentArchive ──────────────────────────────────────────────────


class PaymentShipmentArchive(Base):
    """Маркер «забор убран из рабочего списка оплат» (архив). Разархивация = удаление строки."""

    __tablename__ = "payment_shipment_archive"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    outbound_shipment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("outbound_shipments.id", ondelete="CASCADE"), nullable=False
    )
    archived_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    archived_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        Index("ix_payment_shipment_archive_project_id", "project_id"),
        # Partial-unique (project_id, outbound_shipment_id) создаётся в миграции.
    )


class PaymentTxnArchive(Base):
    """Маркер «исходящий платёж перевозчику не имеет забора, но разобран — не считать аномалией».

    Используется на сверке оплат в карточке контрагента (платёж есть, забора нет).
    Разархивация = удаление строки.
    """

    __tablename__ = "payment_txn_archive"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    transaction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False
    )
    archived_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    archived_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        Index("ix_payment_txn_archive_project_id", "project_id"),
        # Partial-unique (project_id, transaction_id) создаётся в миграции.
    )

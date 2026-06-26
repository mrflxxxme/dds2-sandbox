"""
Counterparty models: Counterparty, CounterpartyDocument + enums.

Represents the counterparty reference book — suppliers, fulfillment centers,
carriers, banks, government bodies, etc. tied to a project.
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin, TimestampMixin
from backend.utils.time import utcnow

if TYPE_CHECKING:
    from backend.models.loan import Loan


# ─── Enums ──────────────────────────────────────────────────────────────────


class CounterpartyType(str, enum.Enum):
    """13 types of counterparty (primary_type)."""

    SUPPLIER = "SUPPLIER"
    FULFILLMENT = "FULFILLMENT"
    CARRIER = "CARRIER"
    CUSTOMS_BROKER = "CUSTOMS_BROKER"
    DESIGNER = "DESIGNER"
    LEGAL = "LEGAL"
    LANDLORD = "LANDLORD"
    IT_SERVICE = "IT_SERVICE"
    MARKETPLACE = "MARKETPLACE"
    BANK = "BANK"
    GOVERNMENT = "GOVERNMENT"
    AFFILIATED = "AFFILIATED"
    OTHER = "OTHER"


class DocType(str, enum.Enum):
    """Type of counterparty document."""

    CONTRACT = "CONTRACT"
    CERTIFICATE = "CERTIFICATE"
    INVOICE = "INVOICE"
    OTHER = "OTHER"


# ─── Counterparty ────────────────────────────────────────────────────────────


class Counterparty(Base, TimestampMixin, SoftDeleteMixin):
    """
    Counterparty reference book entry.

    Multi-role: primary_type (main) + secondary_types[] (additional roles).
    INN uniqueness is partial (WHERE is_deleted=false AND inn IS NOT NULL).
    Chinese suppliers (no INN) are identified by contract_number.
    """

    __tablename__ = "counterparty"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    inn: Mapped[str | None] = mapped_column(String(12), nullable=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    primary_type: Mapped[str] = mapped_column(String(20), nullable=False, default="OTHER", server_default="OTHER")
    secondary_types: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(20)), nullable=True, default=list, server_default="{}"
    )
    kpp: Mapped[str | None] = mapped_column(String(9), nullable=True)
    # Banking requisites (для «заявок на оплату» — авто-заполнение реквизитов перевозчика)
    bank_account: Mapped[str | None] = mapped_column(String(20), nullable=True)  # расчётный счёт
    bik: Mapped[str | None] = mapped_column(String(9), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    corr_account: Mapped[str | None] = mapped_column(String(20), nullable=True)  # корр. счёт
    contract_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    contacts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by_import: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    # Relationships
    loans: Mapped[list["Loan"]] = relationship(
        back_populates="counterparty",
        foreign_keys="Loan.counterparty_id",
    )
    documents: Mapped[list["CounterpartyDocument"]] = relationship(
        back_populates="counterparty",
        foreign_keys="CounterpartyDocument.counterparty_id",
    )

    # ─── Table args ──────────────────────────────────────────────────

    __table_args__ = (
        # Regular indexes (non-partial, created inline)
        Index("ix_counterparty_project_id", "project_id"),
        Index("ix_counterparty_type", "project_id", "primary_type"),
        # Partial unique/partial indexes created via CONCURRENTLY in migration:
        #   uq_counterparty_project_inn    UNIQUE (project_id, inn) WHERE inn IS NOT NULL AND is_deleted = false
        #   uq_counterparty_project_contract  UNIQUE (project_id, contract_number) WHERE contract_number IS NOT NULL AND is_deleted = false
        #   ix_counterparty_active         (project_id, primary_type) WHERE is_deleted = false
    )


# ─── CounterpartyDocument ────────────────────────────────────────────────────


class CounterpartyDocument(Base, TimestampMixin, SoftDeleteMixin):
    """
    Document attached to a counterparty (stored in MinIO).

    Soft-deleted when removed; physical MinIO object is also deleted.
    """

    __tablename__ = "counterparty_document"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    counterparty_id: Mapped[int] = mapped_column(Integer, ForeignKey("counterparty.id"), nullable=False)
    minio_path: Mapped[str] = mapped_column(String(500), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(20), nullable=False, default="OTHER", server_default="OTHER")
    original_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    # Relationships
    counterparty: Mapped["Counterparty"] = relationship(
        back_populates="documents",
        foreign_keys=[counterparty_id],
    )

    __table_args__ = (
        Index("ix_counterparty_document_project_id", "project_id"),
        # Partial index created via CONCURRENTLY in migration:
        #   ix_counterparty_document_cp  (counterparty_id) WHERE is_deleted = false
    )

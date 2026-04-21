"""
Transaction models: Transaction, CategoryChangeLog, ImportLog.
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.enums import EventType2
from backend.models.mixins import SoftDeleteMixin
from backend.utils.time import utcnow

if TYPE_CHECKING:
    from backend.models.refs import Account


class Transaction(Base, SoftDeleteMixin):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    bank: Mapped[str] = mapped_column(String(20), nullable=False)
    account: Mapped[str] = mapped_column(String(50), ForeignKey("accounts.account"), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    counterparty: Mapped[str | None] = mapped_column(String(300))
    inn: Mapped[str | None] = mapped_column(String(20))
    counterparty_account: Mapped[str | None] = mapped_column(String(50))
    purpose: Mapped[str | None] = mapped_column(Text)
    income: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    expense: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    txn_id: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    cp_key: Mapped[str | None] = mapped_column(String(100))
    net: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    event_type: Mapped[str | None] = mapped_column(String(30))
    is_cashflow: Mapped[int | None] = mapped_column(Integer)
    cat_lvl1: Mapped[str | None] = mapped_column(String(100))
    cat_lvl2: Mapped[str | None] = mapped_column(String(100))
    order_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str | None] = mapped_column(String(20))
    account_text: Mapped[str | None] = mapped_column(String(50))
    is_fx: Mapped[bool] = mapped_column(Boolean, default=False)
    event_type2: Mapped[str | None] = mapped_column(SAEnum(EventType2))
    is_cashflow2: Mapped[int] = mapped_column(Integer, default=1)
    cat_lvl1_2: Mapped[str | None] = mapped_column(String(100))
    cat_lvl2_2: Mapped[str | None] = mapped_column(String(100))
    # SRC_IMP enrichment
    purpose_tag: Mapped[str | None] = mapped_column(String(30))
    invoice_id: Mapped[str | None] = mapped_column(String(100))
    annex_id: Mapped[str | None] = mapped_column(String(50))

    # Counterparty & Loan links (Phase 1: counterparties-loans)
    counterparty_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("counterparty.id"), nullable=True)
    contract_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unk_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    loan_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("loan.id"), nullable=True)
    loan_payment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("loan_payment.id"), nullable=True)
    # String (not SAEnum) to avoid Alembic complications when enum values change
    loan_payment_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

    account_ref: Mapped[Optional["Account"]] = relationship(back_populates="transactions", foreign_keys=[account])

    __table_args__ = (
        Index("ix_txn_date", "date"),
        Index("ix_txn_account", "account"),
        Index("ix_txn_currency", "currency"),
        Index("ix_txn_cat", "cat_lvl1_2", "cat_lvl2_2"),
        Index("ix_txn_status", "status"),
        Index("ix_txn_cp_key", "cp_key"),
        Index("ix_txn_cashflow2", "is_cashflow2"),
        Index("ix_txn_cashflow_unassigned", "is_cashflow2", "cat_lvl1_2"),
        Index("ix_txn_project_date", "project_id", "date"),
        Index("ix_txn_project_cashflow", "project_id", "is_cashflow2", "cat_lvl1_2"),
        Index("ix_txn_project_status", "project_id", "status"),
    )


class CategoryChangeLog(Base):
    __tablename__ = "category_change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    txn_id: Mapped[str] = mapped_column(String(300), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    changed_by: Mapped[str | None] = mapped_column(String(100))
    old_cat_lvl1: Mapped[str | None] = mapped_column(String(100))
    old_cat_lvl2: Mapped[str | None] = mapped_column(String(100))
    new_cat_lvl1: Mapped[str | None] = mapped_column(String(100))
    new_cat_lvl2: Mapped[str | None] = mapped_column(String(100))
    scope: Mapped[str] = mapped_column(String(20))  # 'txn' or 'cp'

    __table_args__ = (Index("ix_category_change_log_project_id", "project_id"),)


class ImportLog(Base):
    __tablename__ = "import_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(300))
    source_type: Mapped[str] = mapped_column(String(30))
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    rows_raw: Mapped[int] = mapped_column(Integer, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="OK")
    error_msg: Mapped[str | None] = mapped_column(Text)
    file_url: Mapped[str | None] = mapped_column(String(500))  # MinIO object path

    __table_args__ = (Index("ix_import_log_project_id", "project_id"),)

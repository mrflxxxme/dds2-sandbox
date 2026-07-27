"""
Loan models: Loan, LoanPayment + enums.

Represents loans received (INCOMING), issued (OUTGOING), and intra-affiliated (AFFILIATED).
LoanPayment links a loan to a bank transaction.
"""

import enum
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from backend.models.counterparty import Counterparty


# ─── Enums ──────────────────────────────────────────────────────────────────


class LoanDirection(str, enum.Enum):
    """Direction of loan from project's perspective."""

    INCOMING = "INCOMING"  # loan received (we owe someone)
    OUTGOING = "OUTGOING"  # loan issued (someone owes us)
    AFFILIATED = "AFFILIATED"  # intra-group transfer


class LoanStatus(str, enum.Enum):
    """Loan lifecycle status."""

    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    DEFAULTED = "DEFAULTED"


class LoanEntityType(str, enum.Enum):
    """Legal entity the loan was taken on (как у заёмщика: физлицо или ИП)."""

    PHYSICAL = "PHYSICAL"  # физлицо
    IP = "IP"  # индивидуальный предприниматель


class LoanKind(str, enum.Enum):
    """Вид займа: срочный (тело фиксировано) или кредитная линия (выборки/погашения)."""

    TERM = "TERM"  # обычный заём: тело задано при выдаче
    CREDIT_LINE = "CREDIT_LINE"  # ВКЛ: тело = выборки − погашения, есть лимит


class LoanAccrualKind(str, enum.Enum):
    """Календарь начисления процентов — у разных кредиторов он разный."""

    PERIOD_25 = "PERIOD_25"  # частные займы: с 25-го по 25-е
    CALENDAR_MONTH = "CALENDAR_MONTH"  # банк: календарный месяц, платёж в начале следующего


class LoanPaymentType(str, enum.Enum):
    """Type of loan payment event."""

    DISBURSEMENT = "DISBURSEMENT"  # initial disbursement
    PRINCIPAL_REPAY = "PRINCIPAL_REPAY"  # principal repayment
    INTEREST_PAY = "INTEREST_PAY"  # interest payment
    PENALTY = "PENALTY"  # penalty / late fee


# ─── Loan ────────────────────────────────────────────────────────────────────


class Loan(Base, TimestampMixin, SoftDeleteMixin):
    """
    Loan (credit) linked to a counterparty.

    direction determines perspective (INCOMING = we borrowed, OUTGOING = we lent).
    rate is annual rate as fraction (0.185 = 18.5%).
    """

    __tablename__ = "loan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    counterparty_id: Mapped[int] = mapped_column(Integer, ForeignKey("counterparty.id"), nullable=False)
    direction: Mapped[str] = mapped_column(
        String(20), nullable=False, default=LoanDirection.INCOMING, server_default="INCOMING"
    )
    principal: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB", server_default="RUB")
    rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    contract_number: Mapped[str] = mapped_column(String(100), nullable=False)
    contract_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    maturity_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=LoanStatus.ACTIVE, server_default="ACTIVE")
    # Сущность заёмщика (физлицо / ИП) — «был займ на ип или физ».
    entity_type: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # Банк лендера, куда выплачиваются проценты (сбер/альфа/…) — справочно.
    lender_bank: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Вид займа и календарь начисления. Дефолты повторяют прежнее поведение,
    # поэтому существующие займы считаются ровно как раньше.
    loan_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default=LoanKind.TERM, server_default="TERM"
    )
    accrual_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default=LoanAccrualKind.PERIOD_25, server_default="PERIOD_25"
    )
    # Лимит кредитной линии; у срочного займа не заполняется.
    credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    # Комиссия за НЕиспользованный лимит, годовая доля (0.02 = 2 %). Начисляется
    # на разницу «лимит − выбрано» — банк берёт плату за зарезервированные деньги.
    unused_limit_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    # День месяца, когда гасят проценты за период. ВБ Банк — 5-е число следующего
    # месяца; у частных займов платёж совпадает с концом периода (25-е).
    payment_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Цепочка продлений: при продлении старый займ закрывается, новый ссылается на него.
    parent_loan_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("loan.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    counterparty: Mapped["Counterparty"] = relationship(
        back_populates="loans",
        foreign_keys=[counterparty_id],
    )
    payments: Mapped[list["LoanPayment"]] = relationship(
        back_populates="loan",
        foreign_keys="LoanPayment.loan_id",
    )
    rate_periods: Mapped[list["LoanRatePeriod"]] = relationship(
        back_populates="loan",
        foreign_keys="LoanRatePeriod.loan_id",
    )
    parent_loan: Mapped["Loan | None"] = relationship(
        remote_side=[id],
        foreign_keys=[parent_loan_id],
    )

    __table_args__ = (
        Index("ix_loan_project_id", "project_id"),
        Index("ix_loan_counterparty", "counterparty_id"),
        # Partial indexes created via CONCURRENTLY in migration:
        #   ix_loan_project_status  (project_id, status) WHERE is_deleted = false
        #   ix_loan_parent          (parent_loan_id) WHERE parent_loan_id IS NOT NULL
    )


# ─── LoanPayment ─────────────────────────────────────────────────────────────


class LoanPayment(Base, TimestampMixin):
    """
    A single payment event attached to a Loan.

    transaction_id is optional (set when a bank transaction is matched).
    UNIQUE constraint on transaction_id ensures 1 transaction → 1 LoanPayment.
    """

    __tablename__ = "loan_payment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    loan_id: Mapped[int] = mapped_column(Integer, ForeignKey("loan.id"), nullable=False)
    transaction_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("transactions.id"), nullable=True)
    payment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    paid_at: Mapped[date] = mapped_column(Date, nullable=False)

    # Relationships
    loan: Mapped["Loan"] = relationship(
        back_populates="payments",
        foreign_keys=[loan_id],
    )

    __table_args__ = (
        Index("ix_loan_payment_loan_date", "loan_id", "paid_at"),
        # Partial unique index created via CONCURRENTLY in migration:
        #   uq_loan_payment_transaction  UNIQUE (transaction_id) WHERE transaction_id IS NOT NULL
    )


# ─── LoanRatePeriod ──────────────────────────────────────────────────────────


class LoanRatePeriod(Base, TimestampMixin):
    """
    Период действия ставки по займу — для плавающих ставок.

    У ВКЛ ставка = ключевая ЦБ + спред и меняется вслед за ключевой прямо внутри
    периода начисления: в июне 2026 половина месяца шла под 19.5 %, остаток под
    19.25 %. Одним полем `Loan.rate` такое не описать — исторические начисления
    пересчитались бы по новой ставке и разошлись с выпиской банка.

    Пусто → действует `Loan.rate` (обычный заём с фиксированной ставкой).
    """

    __tablename__ = "loan_rate_period"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    loan_id: Mapped[int] = mapped_column(Integer, ForeignKey("loan.id"), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)  # ставка действует С этого дня
    rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)  # годовая доля: 0.195
    # Справочно: из чего сложилась ставка (ключевая ЦБ + фиксированная надбавка)
    base_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    spread: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    loan: Mapped["Loan"] = relationship(back_populates="rate_periods", foreign_keys=[loan_id])

    __table_args__ = (
        Index("ix_loan_rate_period_loan_from", "loan_id", "valid_from"),
    )

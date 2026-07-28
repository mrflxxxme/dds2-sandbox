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
    Boolean,
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
    COMMISSION = "COMMISSION"  # комиссия банка (за выдачу, за резерв лимита)


class LoanFeeKind(str, enum.Enum):
    """Вид разовой комиссии — то, за что банк взял деньги помимо процентов."""

    ORIGINATION = "ORIGINATION"  # за выдачу займа (Симпл Финанс — 4.25 %)
    LIMIT_SETUP = "LIMIT_SETUP"  # за установление лимита (ВКЛ — 0.25 %)
    OTHER = "OTHER"


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
    # Вторая сторона ТОГО ЖЕ договора в другом проекте. Займ между своими юрлицами
    # (ИП учредителя → ООО) — это одна сделка и две книги: у одного долг, у другого
    # актив. Одной строкой это не описать — правило «каждый запрос фильтрует
    # project_id» требует, чтобы у каждого проекта была своя запись. Ссылка держит
    # их вместе: движения вводятся один раз и зеркалятся на вторую сторону.
    mirror_loan_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("loan.id"), nullable=True)
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
    schedule: Mapped[list["LoanScheduleEntry"]] = relationship(
        back_populates="loan",
        foreign_keys="LoanScheduleEntry.loan_id",
    )
    fees: Mapped[list["LoanFee"]] = relationship(
        back_populates="loan",
        foreign_keys="LoanFee.loan_id",
    )
    parent_loan: Mapped["Loan | None"] = relationship(
        remote_side=[id],
        foreign_keys=[parent_loan_id],
    )
    mirror_loan: Mapped["Loan | None"] = relationship(
        remote_side=[id],
        foreign_keys=[mirror_loan_id],
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


# ─── LoanScheduleEntry ───────────────────────────────────────────────────────


class LoanScheduleEntry(Base, TimestampMixin):
    """
    Строка ПЛАНОВОГО графика платежей — как его выдал кредитор.

    У аннуитета (Симпл Финанс) платёж фиксирован, а деление на тело и проценты
    меняется от строки к строке — вывести его формулой нельзя, банк считает по
    своему округлению. Поэтому график храним как факт договора и сверяем с ним
    реальные платежи: «заплатили ли то, что должны, и когда».

    `days` и границы периода нужны, чтобы проверить сам график: проценты строки =
    остаток × ставка × дни / 365 (сходится с приложением № 1 до копейки).
    """

    __tablename__ = "loan_schedule_entry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    loan_id: Mapped[int] = mapped_column(Integer, ForeignKey("loan.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # № платежа в графике
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)  # дата платежа
    days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    principal_due: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    interest_due: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    payment_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    # Остаток ссудной задолженности ПОСЛЕ платежа — по графику кредитора.
    balance_after: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    # Строка графика — комиссия, а не проценты. У Симпл Финанса первый «платёж»
    # (1 275 000 ₽ за выдачу) напечатан в колонке процентов, но экономически это
    # разовая комиссия: в стоимость денег она заходит через `LoanFee`, иначе
    # расход задвоится и весь март покажет цену, которой не было.
    is_fee: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Строка — ПЛАН ПОГАШЕНИЯ накопленного долга, а не график начисления кредитора.
    # Разница принципиальная: обычный график ОТМЕНЯЕТ формулу — движок берёт
    # проценты из него (у аннуитета банк считает своё округление). План погашения
    # так вести нельзя: проценты по займу продолжают капать по ставке, а строки
    # лишь говорят, когда и сколько платить в счёт уже начисленного. Без этого
    # флага пять строк «по 2,59 млн 25-го числа» подменили бы собой всё начисление
    # займа и обнулили бы долг, который они гасят.
    is_debt_plan: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    loan: Mapped["Loan"] = relationship(back_populates="schedule", foreign_keys=[loan_id])

    __table_args__ = (
        Index("ix_loan_schedule_loan_seq", "loan_id", "seq", unique=True),
        Index("ix_loan_schedule_loan_due", "loan_id", "due_date"),
    )


# ─── LoanFee ─────────────────────────────────────────────────────────────────


class LoanFee(Base, TimestampMixin):
    """
    РАЗОВАЯ комиссия по займу: за выдачу, за установление лимита и т. п.

    Отделена от `LoanPayment` намеренно: платёж — это касса (когда ушли деньги),
    а комиссия — расход, который относится ко ВСЕМУ сроку договора. 252 500 ₽ за
    лимит и 1 275 000 ₽ за выдачу — плата за доступ к деньгам на год, а не расход
    одного дня. Целиком в месяц уплаты они ломают сравнение месяцев: май дорожает
    вдвое, а остальные выглядят дешевле, чем есть.

    `amortize` = размазывать по дням окна [amortize_from, amortize_to]; иначе
    расход падает целиком в месяц `charged_at`. Касса при этом не меняется —
    она живёт в `LoanPayment` с типом COMMISSION (ссылка `payment_id`).
    """

    __tablename__ = "loan_fee"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    loan_id: Mapped[int] = mapped_column(Integer, ForeignKey("loan.id"), nullable=False)
    fee_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default=LoanFeeKind.OTHER, server_default="OTHER"
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    charged_at: Mapped[date] = mapped_column(Date, nullable=False)  # дата начисления/уплаты
    amortize: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # Окно амортизации; пусто → срок займа (start_date … maturity_date).
    amortize_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    amortize_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Касса: строка LoanPayment с типом COMMISSION, если деньги уже ушли.
    payment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("loan_payment.id"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    loan: Mapped["Loan"] = relationship(back_populates="fees", foreign_keys=[loan_id])

    __table_args__ = (
        Index("ix_loan_fee_loan_charged", "loan_id", "charged_at"),
        Index("ix_loan_fee_payment", "payment_id"),
    )

from datetime import datetime, date
from decimal import Decimal
from typing import Optional
import enum

from sqlalchemy import (
    String, Integer, Boolean, DateTime, Date, Numeric, Text,
    ForeignKey, UniqueConstraint, Index, Enum as SAEnum
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class EventType2(str, enum.Enum):
    INTERNAL_TRANSFER = "INTERNAL_TRANSFER"
    FX_BUY = "FX_BUY"
    CUSTOMS_PAYMENT = "CUSTOMS_PAYMENT"
    OPER = "OPER"


class TransactionStatus(str, enum.Enum):
    OK = "OK"
    UNASSIGNED = "UNASSIGNED"
    NO_CASHFLOW = "NO_CASHFLOW"


class PurposeTag(str, enum.Enum):
    COMMISSION = "Комиссия"
    LOGISTICS = "Логистика"
    ORDER = "Заказ"
    OTHER = "Другое"


# ─── Reference tables ───────────────────────────────────────────────────────

class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    bank: Mapped[str] = mapped_column(String(20), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    account_type: Mapped[Optional[str]] = mapped_column(String(30))
    is_our_account: Mapped[bool] = mapped_column(Boolean, default=True)
    account_name: Mapped[Optional[str]] = mapped_column(String(100))
    is_customs_payee: Mapped[bool] = mapped_column(Boolean, default=False)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account_ref")


class CounterpartyCategory(Base):
    __tablename__ = "counterparty_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cp_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    cp_name: Mapped[Optional[str]] = mapped_column(String(200))
    cat_lvl1: Mapped[Optional[str]] = mapped_column(String(100))
    cat_lvl2: Mapped[Optional[str]] = mapped_column(String(100))
    note: Mapped[Optional[str]] = mapped_column(Text)
    cp_key_auto: Mapped[Optional[str]] = mapped_column(String(100))
    cp_name_auto: Mapped[Optional[str]] = mapped_column(String(200))


class Override(Base):
    __tablename__ = "overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    txn_id: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    cat_lvl1: Mapped[Optional[str]] = mapped_column(String(100))
    cat_lvl2: Mapped[Optional[str]] = mapped_column(String(100))
    order_id: Mapped[Optional[str]] = mapped_column(String(100))
    comment: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OpeningBalance(Base):
    __tablename__ = "opening_balances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date_open: Mapped[date] = mapped_column(Date, nullable=False)
    account: Mapped[str] = mapped_column(String(50), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)

    __table_args__ = (UniqueConstraint("date_open", "account", "currency"),)


# ─── Main transaction table ──────────────────────────────────────────────────

class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    bank: Mapped[str] = mapped_column(String(20), nullable=False)
    account: Mapped[str] = mapped_column(String(50), ForeignKey("accounts.account"), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    counterparty: Mapped[Optional[str]] = mapped_column(String(300))
    inn: Mapped[Optional[str]] = mapped_column(String(20))
    counterparty_account: Mapped[Optional[str]] = mapped_column(String(50))
    purpose: Mapped[Optional[str]] = mapped_column(Text)
    income: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    expense: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    txn_id: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    cp_key: Mapped[Optional[str]] = mapped_column(String(100))
    net: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    event_type: Mapped[Optional[str]] = mapped_column(String(30))
    is_cashflow: Mapped[Optional[int]] = mapped_column(Integer)
    cat_lvl1: Mapped[Optional[str]] = mapped_column(String(100))
    cat_lvl2: Mapped[Optional[str]] = mapped_column(String(100))
    order_id: Mapped[Optional[str]] = mapped_column(String(100))
    status: Mapped[Optional[str]] = mapped_column(String(20))
    account_text: Mapped[Optional[str]] = mapped_column(String(50))
    is_fx: Mapped[bool] = mapped_column(Boolean, default=False)
    event_type2: Mapped[Optional[str]] = mapped_column(SAEnum(EventType2))
    is_cashflow2: Mapped[int] = mapped_column(Integer, default=1)
    cat_lvl1_2: Mapped[Optional[str]] = mapped_column(String(100))
    cat_lvl2_2: Mapped[Optional[str]] = mapped_column(String(100))
    # SRC_IMP enrichment
    purpose_tag: Mapped[Optional[str]] = mapped_column(String(30))
    invoice_id: Mapped[Optional[str]] = mapped_column(String(100))
    annex_id: Mapped[Optional[str]] = mapped_column(String(50))

    account_ref: Mapped[Optional["Account"]] = relationship(back_populates="transactions", foreign_keys=[account])

    __table_args__ = (
        Index("ix_txn_date", "date"),
        Index("ix_txn_account", "account"),
        Index("ix_txn_currency", "currency"),
        Index("ix_txn_cat", "cat_lvl1_2", "cat_lvl2_2"),
        Index("ix_txn_status", "status"),
    )


class CategoryChangeLog(Base):
    __tablename__ = "category_change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    txn_id: Mapped[str] = mapped_column(String(300), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    changed_by: Mapped[Optional[str]] = mapped_column(String(100))
    old_cat_lvl1: Mapped[Optional[str]] = mapped_column(String(100))
    old_cat_lvl2: Mapped[Optional[str]] = mapped_column(String(100))
    new_cat_lvl1: Mapped[Optional[str]] = mapped_column(String(100))
    new_cat_lvl2: Mapped[Optional[str]] = mapped_column(String(100))
    scope: Mapped[str] = mapped_column(String(20))  # 'txn' or 'cp'


# ─── Customs ─────────────────────────────────────────────────────────────────

class CustomsTopup(Base):
    __tablename__ = "customs_topup"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topup_txn_id: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    purpose: Mapped[Optional[str]] = mapped_column(Text)
    account: Mapped[Optional[str]] = mapped_column(String(50))
    counterparty_account: Mapped[Optional[str]] = mapped_column(String(50))

    allocs: Mapped[list["CustomsAlloc"]] = relationship(back_populates="topup", cascade="all, delete-orphan")


class CustomsAlloc(Base):
    __tablename__ = "customs_alloc"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topup_txn_id: Mapped[str] = mapped_column(String(300), ForeignKey("customs_topup.topup_txn_id"), nullable=False)
    pay_date: Mapped[Optional[date]] = mapped_column(Date)
    pay_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    order_no: Mapped[Optional[int]] = mapped_column(Integer)
    alloc_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    comment: Mapped[Optional[str]] = mapped_column(Text)

    topup: Mapped["CustomsTopup"] = relationship(back_populates="allocs")


# ─── Planning ─────────────────────────────────────────────────────────────────

class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_name: Mapped[Optional[str]] = mapped_column(String(200))
    category: Mapped[Optional[str]] = mapped_column(String(100))
    transport_type: Mapped[Optional[str]] = mapped_column(String(30))
    order_no: Mapped[Optional[int]] = mapped_column(Integer, unique=True)
    supplier: Mapped[Optional[str]] = mapped_column(String(100))
    planned_ship_date: Mapped[Optional[date]] = mapped_column(Date)
    actual_ship_date: Mapped[Optional[date]] = mapped_column(Date)
    order_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    deposit: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    logistics_cny: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    customs_rub: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    source_file: Mapped[Optional[str]] = mapped_column(String(200))


class LeadTime(Base):
    __tablename__ = "lead_time"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    direction: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)


class PlannedPayment(Base):
    __tablename__ = "planned_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pay_date: Mapped[Optional[date]] = mapped_column(Date)
    order_no: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("orders.order_no"))
    direction: Mapped[Optional[str]] = mapped_column(String(50))
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    currency: Mapped[Optional[str]] = mapped_column(String(10))
    fx_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6))
    amount_rub: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    paid_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False)


class PlannedIncome(Base):
    __tablename__ = "planned_incomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    source: Mapped[str] = mapped_column(String(50), default="WB")


# ─── Import log ───────────────────────────────────────────────────────────────

class ImportLog(Base):
    __tablename__ = "import_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(300))
    source_type: Mapped[str] = mapped_column(String(30))
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    rows_raw: Mapped[int] = mapped_column(Integer, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="OK")
    error_msg: Mapped[Optional[str]] = mapped_column(Text)


# ─── Себестоимость ────────────────────────────────────────────────────────────

class DutyBasis(str, enum.Enum):
    WEIGHT = "WEIGHT"       # евро за кг
    AREA = "AREA"           # евро за м²
    INVOICE = "INVOICE"     # % от инвойса


class Nomenclature(Base):
    __tablename__ = "nomenclature"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    barcode: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    brand: Mapped[Optional[str]] = mapped_column(String(100))
    subject: Mapped[Optional[str]] = mapped_column(String(100))
    article_seller: Mapped[Optional[str]] = mapped_column(String(100))
    article_wb: Mapped[Optional[int]] = mapped_column(Integer)
    volume_l: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DutyRule(Base):
    __tablename__ = "duty_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    basis: Mapped[str] = mapped_column(SAEnum(DutyBasis), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    util_collect_rub: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    note: Mapped[Optional[str]] = mapped_column(String(200))


class CostOrder(Base):
    __tablename__ = "cost_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    invoice_no: Mapped[Optional[str]] = mapped_column(String(100))
    ship_date: Mapped[Optional[date]] = mapped_column(Date)
    actual_arrival_date: Mapped[Optional[date]] = mapped_column(Date)
    transport_type: Mapped[Optional[str]] = mapped_column(String(30), default="AUTO")
    delivery_cost_cny: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    delivery_cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    rate_cny: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=1)
    rate_eur: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=1)
    rate_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=1)
    note: Mapped[Optional[str]] = mapped_column(Text)
    dt_number: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    items: Mapped[list["CostOrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class CostOrderItem(Base):
    __tablename__ = "cost_order_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(50), ForeignKey("cost_orders.order_no"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    subject: Mapped[Optional[str]] = mapped_column(String(100))
    article_seller: Mapped[Optional[str]] = mapped_column(String(100))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    price_cny: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0)
    weight_kg: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    area_m2: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    volume_m3: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    # Calculated fields (stored for reporting)
    cost_rub: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    delivery_rub: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    duty_rub: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    vat_rub: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    util_rub: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    total_rub: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    total_cny: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    unrecognized: Mapped[bool] = mapped_column(Boolean, default=False)
    order: Mapped["CostOrder"] = relationship(back_populates="items")
    __table_args__ = (Index("ix_cost_item_order", "order_no"),)


class PaymentFactLink(Base):
    """Manual link between PlannedPayment and Transaction for fact matching."""
    __tablename__ = "payment_fact_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_id: Mapped[int] = mapped_column(Integer, ForeignKey("planned_payments.id"), nullable=False)
    txn_id: Mapped[str] = mapped_column(String(100), nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(200))


class CustomsDT(Base):
    """Parsed DT declaration from FTS report, linked to order."""
    __tablename__ = "customs_dt"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dt_number: Mapped[str] = mapped_column(String(100), nullable=False)
    dt_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    order_no: Mapped[Optional[int]] = mapped_column(Integer)
    note: Mapped[Optional[str]] = mapped_column(Text)
    __table_args__ = (Index("ix_customs_dt_number", "dt_number"),)


class CategoryRef(Base):
    """Reference categories for income/expense."""
    __tablename__ = "category_ref"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # "income" or "expense"
    cat_lvl1: Mapped[str] = mapped_column(String(100), nullable=False)
    cat_lvl2: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("direction", "cat_lvl1", "cat_lvl2", name="uq_cat_ref"),
    )

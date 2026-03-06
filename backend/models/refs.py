"""
Reference models: Account, CounterpartyCategory, Override, OpeningBalance, CategoryRef.
"""

from datetime import datetime, date, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, Date, Numeric, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"))
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
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"))
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
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"))
    txn_id: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    cat_lvl1: Mapped[Optional[str]] = mapped_column(String(100))
    cat_lvl2: Mapped[Optional[str]] = mapped_column(String(100))
    order_id: Mapped[Optional[str]] = mapped_column(String(100))
    comment: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class OpeningBalance(Base):
    __tablename__ = "opening_balances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"))
    date_open: Mapped[date] = mapped_column(Date, nullable=False)
    account: Mapped[str] = mapped_column(String(50), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)

    __table_args__ = (UniqueConstraint("date_open", "account", "currency"),)


class CategoryRef(Base):
    """Reference categories for income/expense."""
    __tablename__ = "category_ref"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"))
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # "income" or "expense"
    cat_lvl1: Mapped[str] = mapped_column(String(100), nullable=False)
    cat_lvl2: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("project_id", "direction", "cat_lvl1", "cat_lvl2", name="uq_cat_ref"),
    )

"""
Customs models: CustomsTopup, CustomsAlloc, CustomsDT.
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import String, Integer, Date, Numeric, Text, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


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

"""
Customs models: CustomsTopup, CustomsAlloc, CustomsDT.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin


class CustomsTopup(Base, SoftDeleteMixin):
    __tablename__ = "customs_topup"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    topup_txn_id: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    purpose: Mapped[str | None] = mapped_column(Text)
    account: Mapped[str | None] = mapped_column(String(50))
    counterparty_account: Mapped[str | None] = mapped_column(String(50))

    # Counterparty FK (Phase 1: counterparties-loans). Backfilled for ФТС (INN 7730176610).
    counterparty_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("counterparty.id"), nullable=True)

    allocs: Mapped[list["CustomsAlloc"]] = relationship(back_populates="topup", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("project_id", "topup_txn_id", name="uq_customs_topup_project_txn"),
        # Partial index created via CONCURRENTLY in migration:
        #   ix_customs_topup_counterparty_id  (counterparty_id)
    )


class CustomsAlloc(Base):
    __tablename__ = "customs_alloc"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    topup_txn_id: Mapped[str] = mapped_column(String(300), ForeignKey("customs_topup.topup_txn_id"), nullable=False)
    pay_date: Mapped[date | None] = mapped_column(Date)
    pay_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    order_no: Mapped[int | None] = mapped_column(Integer)
    alloc_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    comment: Mapped[str | None] = mapped_column(Text)

    topup: Mapped["CustomsTopup"] = relationship(back_populates="allocs")

    __table_args__ = (Index("ix_customs_alloc_project_id", "project_id"),)


class CustomsDT(Base, SoftDeleteMixin):
    """Parsed DT declaration from FTS report, linked to order."""

    __tablename__ = "customs_dt"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    dt_number: Mapped[str] = mapped_column(String(100), nullable=False)
    dt_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    order_no: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index("ix_customs_dt_number", "dt_number"),
        Index("ix_customs_dt_project_id", "project_id"),
    )

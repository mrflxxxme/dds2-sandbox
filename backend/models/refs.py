"""
Reference models: Account, CounterpartyCategory, Override, OpeningBalance, CategoryRef.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.models.transactions import Transaction
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from backend.models.mixins import SoftDeleteMixin
from backend.utils.time import utcnow


class Account(Base, SoftDeleteMixin):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    account: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    bank: Mapped[str] = mapped_column(String(20), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    account_type: Mapped[str | None] = mapped_column(String(30))
    is_our_account: Mapped[bool] = mapped_column(Boolean, default=True)
    account_name: Mapped[str | None] = mapped_column(String(100))
    is_customs_payee: Mapped[bool] = mapped_column(Boolean, default=False)

    transactions: Mapped[list[Transaction]] = relationship(back_populates="account_ref")

    __table_args__ = (Index("ix_accounts_project_id", "project_id"),)


class CounterpartyCategory(Base, SoftDeleteMixin):
    __tablename__ = "counterparty_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    cp_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    cp_name: Mapped[str | None] = mapped_column(String(200))
    cat_lvl1: Mapped[str | None] = mapped_column(String(100))
    cat_lvl2: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(Text)
    cp_key_auto: Mapped[str | None] = mapped_column(String(100))
    cp_name_auto: Mapped[str | None] = mapped_column(String(200))

    # Counterparty FK (Phase 1: counterparties-loans). cp_key kept as legacy.
    counterparty_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("counterparty.id"), nullable=True)

    __table_args__ = (
        Index("ix_cp_cat_project_id", "project_id"),
        # Partial index created via CONCURRENTLY in migration:
        #   ix_counterparty_categories_counterparty_id  (counterparty_id)
    )


class Override(Base, SoftDeleteMixin):
    __tablename__ = "overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    txn_id: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    cat_lvl1: Mapped[str | None] = mapped_column(String(100))
    cat_lvl2: Mapped[str | None] = mapped_column(String(100))
    order_id: Mapped[str | None] = mapped_column(String(100))
    comment: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_overrides_project_id", "project_id"),)


class OpeningBalance(Base):
    __tablename__ = "opening_balances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    date_open: Mapped[date] = mapped_column(Date, nullable=False)
    account: Mapped[str] = mapped_column(String(50), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)

    __table_args__ = (
        UniqueConstraint("date_open", "account", "currency"),
        Index("ix_opening_bal_project_id", "project_id"),
    )


class CategoryRef(Base, SoftDeleteMixin):
    """Reference categories for income/expense."""

    __tablename__ = "category_ref"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # "income" or "expense"
    cat_lvl1: Mapped[str] = mapped_column(String(100), nullable=False)
    cat_lvl2: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # «Себестоимость товара»: расходы с такой категорией исключаются из расходных
    # отчётов (дашборд, ДДС) — они капитализируются в стоимость товара, не опер. расход.
    is_cogs: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    __table_args__ = (UniqueConstraint("project_id", "direction", "cat_lvl1", "cat_lvl2", name="uq_cat_ref"),)


class ProjectSetting(Base):
    """Key-value settings per project (JSON values)."""

    __tablename__ = "project_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_project_setting"),)


class ProductTag(Base, SoftDeleteMixin):
    """Tags assigned to products."""

    __tablename__ = "product_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#3B82F6")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_product_tag_name"),)


class ProductTagMap(Base):
    """Mapping between product tags and nm_ids."""

    __tablename__ = "product_tag_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    tag_id: Mapped[int] = mapped_column(Integer, ForeignKey("product_tags.id", ondelete="CASCADE"), nullable=False)
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("tag_id", "nm_id", name="uq_product_tag_map"),
        Index("ix_product_tag_map_project_nm_id", "project_id", "nm_id"),
    )


class ProductStatusMap(Base):
    """One status per product per project."""

    __tablename__ = "product_status_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "nm_id", name="uq_product_status_nm"),
        Index("ix_product_status_map_project_nm_id", "project_id", "nm_id"),
    )


class ImtAlias(Base):
    """User-defined alias for imt_id groups."""

    __tablename__ = "imt_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    imt_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # WB imtID outgrew int32
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    __table_args__ = (UniqueConstraint("project_id", "imt_id", name="uq_imt_alias"),)


class SizeAlias(Base):
    """Display name for a size value (rename + merge): raw_size → display_name."""

    __tablename__ = "size_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    raw_size: Mapped[str] = mapped_column(String(50), nullable=False)  # значение размера (parse/override)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("project_id", "raw_size", name="uq_size_alias"),)


class SizeOverride(Base):
    """Manual size assignment per product (overrides size parsed from article)."""

    __tablename__ = "size_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    size_value: Mapped[str] = mapped_column(String(50), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "nm_id", name="uq_size_override_nm"),
        Index("ix_size_overrides_project_nm_id", "project_id", "nm_id"),
    )


class CategoryOverride(Base):
    """Manual category assignment per product (overrides WB subject in funnel grouping)."""

    __tablename__ = "category_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    category_value: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "nm_id", name="uq_category_override_nm"),
        Index("ix_category_overrides_project_nm_id", "project_id", "nm_id"),
    )


class ProductSubcategory(Base, SoftDeleteMixin):
    """Sub-category reference within sizes (e.g. винтаж / обычные)."""

    __tablename__ = "product_subcategories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#8B5CF6")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_product_subcategory_name"),)


class ProductSubcategoryMap(Base):
    """One sub-category per product per project (single-select)."""

    __tablename__ = "product_subcategory_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    subcategory_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("product_subcategories.id", ondelete="CASCADE"), nullable=False
    )
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "nm_id", name="uq_product_subcategory_map_nm"),
        Index("ix_product_subcategory_map_project_nm_id", "project_id", "nm_id"),
    )


class CbrBic(Base):
    """Справочник БИК ЦБ РФ (формат ED807): БИК → корр. счёт банка + название.

    Национальный справочник (без project_id, без soft-delete) — полностью обновляется
    джобом из cbr.ru. Источник истины для к/с банка получателя в платёжке (к/с НЕ выводится
    формулой из БИК). Резолв — `services/bank_directory.resolve_bank_db` (с фолбэком на
    зашитый набор крупных банков, если таблица пуста / банк отсутствует)."""

    __tablename__ = "cbr_bic"

    bic: Mapped[str] = mapped_column(String(9), primary_key=True)
    corr_account: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

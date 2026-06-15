# ruff: noqa: RUF002, RUF003
"""
Cost models: Nomenclature, DutyRule, CostOrder, CostOrderItem.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.enums import DutyBasis, VehicleStatus
from backend.models.mixins import SoftDeleteMixin
from backend.utils.time import utcnow


class Nomenclature(Base):
    __tablename__ = "nomenclature"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(100))
    subject: Mapped[str | None] = mapped_column(String(100))
    article_seller: Mapped[str | None] = mapped_column(String(100))
    article_wb: Mapped[int | None] = mapped_column(Integer)
    imt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume_l: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    area_m2: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    # Reference per-barcode weight (kg) — fallback for WEIGHT-basis duty when the
    # items Excel has no weight. Mirrors area_m2. Filled via /cost/nomenclature/bulk_weight.
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    first_sale_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    box_qty_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    use_box_multiplicity: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("project_id", "barcode", name="uq_nomenclature_project_barcode"),)


class BoxQtyPerWarehouse(Base):
    """Per-RF override кратности коробки.

    Резолюция при сборке для (barcode, RF-склад):
      box_qty_per_warehouse.box_qty   ← если задан
      → Nomenclature.box_qty_override ← глобальный SKU-override
      → последняя DELIVERED машина (cost_order_items.pcs_per_box_override)
      → активный FactoryOrderItem.pcs_per_box

    use_box_multiplicity флаг тоже per-RF: если выключен — для этого SKU+RF
    распределение идёт без округления, даже если box_qty задан.
    """

    __tablename__ = "box_qty_per_warehouse"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    box_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    box_size: Mapped[str | None] = mapped_column(String(50), nullable=True)
    use_box_multiplicity: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "barcode", "warehouse_id", name="uq_box_qty_pw_project_bc_wh"),
        Index("ix_box_qty_pw_project_bc", "project_id", "barcode"),
    )


class BoxQtyPerOrder(Base):
    """УСТАРЕЛО. Не используется ни одним сервисом/роутером.

    Заказы фабрики больше не источник кратности — box-multiplicity резолвит
    кратность per-ФФ из принятых машин (`BoxQtyPerWarehouse`). Таблица и модель
    оставлены до отдельной миграции на удаление.
    """

    __tablename__ = "box_qty_per_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    factory_order_item_id: Mapped[int] = mapped_column(Integer, ForeignKey("factory_order_items.id"), nullable=False)
    box_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("project_id", "factory_order_item_id", name="uq_box_qty_po_project_foi"),)


class BoxMultiplicityChangeLog(Base):
    """Журнал изменений кратности/размера коробки — для «Истории изменений» и отката.

    Одна строка на одно изменение поля. `warehouse_id IS NULL` — изменение
    SKU-уровня (`Nomenclature`), иначе per-ФФ (`BoxQtyPerWarehouse`).
    `old_value`/`new_value` — строковое представление (`None` = очистка/«не задано»).
    """

    __tablename__ = "box_multiplicity_change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    warehouse_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=True)
    # field: box_qty_override | box_qty | box_size | use_box_multiplicity
    field: Mapped[str] = mapped_column(String(30), nullable=False)
    old_value: Mapped[str | None] = mapped_column(String(100), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # change_source: manual | bulk | revert
    change_source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual", server_default="manual")
    # UUID одной bulk-операции (apply из буфера) — позволяет откатить всю
    # вставку одним кликом. NULL для manual/revert или старых записей.
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_box_mult_change_log_project_bc", "project_id", "barcode"),
        Index("ix_box_mult_change_log_batch", "batch_id", postgresql_where="batch_id IS NOT NULL"),
    )


class DutyRule(Base, SoftDeleteMixin):
    __tablename__ = "duty_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    basis: Mapped[str] = mapped_column(SAEnum(DutyBasis), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    util_collect_rub: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    note: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (UniqueConstraint("project_id", "subject", name="uq_duty_rule_project_subject"),)


class DutyException(Base, SoftDeleteMixin):
    """Article-level duty override.

    For specific seller articles inside a category, apply a different duty
    basis+rate than the category's :class:`DutyRule`. Matched on
    ``article_seller``; takes precedence over the subject-keyed rule when
    computing ``duty_rub`` for an order item.

    Only the *duty* (basis + rate) is overridden — ``util_collect_rub`` still
    comes from the category rule (matched by subject), by design.
    """

    __tablename__ = "duty_exceptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    article_seller: Mapped[str] = mapped_column(String(100), nullable=False)
    basis: Mapped[str] = mapped_column(SAEnum(DutyBasis), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))

    # Partial unique index (not a plain UniqueConstraint): a soft-deleted row
    # must not occupy the (project_id, article_seller) slot, else re-adding the
    # same article 500s with IntegrityError. See learnings.md "SoftDelete mine".
    __table_args__ = (
        Index(
            "uq_duty_exc_project_article",
            "project_id",
            "article_seller",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class CostOrder(Base, SoftDeleteMixin):
    __tablename__ = "cost_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    order_no: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    invoice_no: Mapped[str | None] = mapped_column(String(100))
    payment_ref: Mapped[str | None] = mapped_column(String(100))
    ship_date: Mapped[date | None] = mapped_column(Date)
    actual_arrival_date: Mapped[date | None] = mapped_column(Date)
    transport_type: Mapped[str | None] = mapped_column(String(30), default="AUTO")
    delivery_cost_cny: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    delivery_cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    delivery_cost_rub: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    country: Mapped[str] = mapped_column(String(10), nullable=False, default="CHINA", server_default="CHINA")
    rate_cny: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=1)
    rate_eur: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=1)
    rate_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=1)
    note: Mapped[str | None] = mapped_column(Text)
    dt_number: Mapped[str | None] = mapped_column(String(100))
    container_type: Mapped[str | None] = mapped_column(String(20))
    vehicle_name: Mapped[str | None] = mapped_column(String(200))
    plate_number: Mapped[str | None] = mapped_column(String(50))
    actual_ship_date: Mapped[date | None] = mapped_column(Date)
    estimated_arrival_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Supply chain fields
    status: Mapped[str | None] = mapped_column(SAEnum(VehicleStatus))
    target_warehouse_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("warehouses.id"))
    inbound_receipt_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("inbound_receipts.id"))
    items: Mapped[list["CostOrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_cost_orders_project_id", "project_id"),)


class CostOrderItem(Base, SoftDeleteMixin):
    __tablename__ = "cost_order_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    order_no: Mapped[str] = mapped_column(String(50), ForeignKey("cost_orders.order_no"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(100))
    article_seller: Mapped[str | None] = mapped_column(String(100))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    price_cny: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    area_m2: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    volume_m3: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    # Calculated fields (stored for reporting)
    cost_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    delivery_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    duty_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    vat_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    util_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    total_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    total_cny: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    unrecognized: Mapped[bool] = mapped_column(Boolean, default=False)
    factory_order_item_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("factory_order_items.id"))
    box_size_override: Mapped[str | None] = mapped_column(String(50))
    pcs_per_box_override: Mapped[int | None] = mapped_column(Integer)
    box_detail_override: Mapped[list[int] | None] = mapped_column(JSON)
    added_after_ship: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    order: Mapped["CostOrder"] = relationship(back_populates="items")
    __table_args__ = (
        Index("ix_cost_item_project_id", "project_id"),
        Index("ix_cost_item_order", "order_no"),
        # FK lookup index — used by factory-order merge re-point and check_and_close_order.
        Index("ix_cost_order_items_factory_item", "factory_order_item_id"),
    )

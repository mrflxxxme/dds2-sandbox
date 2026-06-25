"""
Assembly Request models: AssemblyRequest + AssemblyRequestItem.
Manages assembly workflow for FBO WB supplies.

TZ: see backend/DOMAIN_ASSEMBLY.md for full spec.
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin, TimestampMixin
from backend.utils.time import utcnow

if TYPE_CHECKING:
    from backend.models.warehouse import Warehouse
    from backend.models.wb_fbo import WbFboSupply

# ─── Enums ──────────────────────────────────────────────────────────────────


class AssemblyStatus(enum.StrEnum):
    """Assembly request status — strict sequential transitions."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    READY = "READY"
    VEHICLE_ASSIGNED = "VEHICLE_ASSIGNED"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"  # WB accepted the supply (auto from FBO sync)
    RETURNED = "RETURNED"  # WB не принял — товар вернулся на склад; ждёт переотгрузки или закрытия
    CLOSED = "CLOSED"  # терминал: заявка закрыта после возврата(ов), новая поставка WB заводится отдельно
    CANCELLED = "CANCELLED"


class PackageType(enum.StrEnum):
    """Package type for WB FBO acceptance.

    Mapped 1:1 to WB acceptance/options flags:
      BOX        ← canBox=true
      MONOPALLET ← canMonopallet=true
      SUPERSAFE  ← canSupersafe=true (rare; high-value categories)

    One AssemblyRequest = one transport unit = one PackageType. Mixing types
    in a single request is not allowed at the WB acceptance gate.
    """

    BOX = "BOX"
    MONOPALLET = "MONOPALLET"
    SUPERSAFE = "SUPERSAFE"


# Allowed status transitions (from → set of valid next statuses)
ASSEMBLY_TRANSITIONS: dict[AssemblyStatus, set[AssemblyStatus]] = {
    AssemblyStatus.PENDING: {AssemblyStatus.IN_PROGRESS, AssemblyStatus.CANCELLED},
    AssemblyStatus.IN_PROGRESS: {AssemblyStatus.READY, AssemblyStatus.CANCELLED},
    AssemblyStatus.READY: {AssemblyStatus.VEHICLE_ASSIGNED, AssemblyStatus.IN_PROGRESS, AssemblyStatus.CANCELLED},
    AssemblyStatus.VEHICLE_ASSIGNED: {AssemblyStatus.SHIPPED, AssemblyStatus.READY, AssemblyStatus.CANCELLED},
    # WB не принял отгрузку → RETURNED (товар вернулся на склад). Из RETURNED заявку
    # либо переотгружают (→ READY: новая FBW-поставка + новый водитель), либо закрывают.
    AssemblyStatus.SHIPPED: {
        AssemblyStatus.DELIVERED,
        AssemblyStatus.READY,
        AssemblyStatus.RETURNED,
        AssemblyStatus.CANCELLED,
    },
    # WB принял, но позже выяснилось, что часть/всё вернулось → возврат либо закрытие.
    AssemblyStatus.DELIVERED: {AssemblyStatus.RETURNED, AssemblyStatus.CLOSED},
    # Возврат на склад: переотгрузка (READY) ‖ закрытие (CLOSED) ‖ отмена.
    AssemblyStatus.RETURNED: {AssemblyStatus.READY, AssemblyStatus.CLOSED, AssemblyStatus.CANCELLED},
    AssemblyStatus.CLOSED: set(),  # final status — заявка закрыта, логистика попыток сохранена
    AssemblyStatus.CANCELLED: set(),
}


# ─── Assembly Request ───────────────────────────────────────────────────────


class AssemblyRequest(Base, TimestampMixin, SoftDeleteMixin):
    """
    Assembly request for FBO supply shipment.

    Lifecycle: PENDING → IN_PROGRESS → READY → VEHICLE_ASSIGNED → SHIPPED
    Ship creates OutboundShipment + deducts stock.
    Cancel from SHIPPED rolls back stock.
    """

    __tablename__ = "assembly_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=AssemblyStatus.PENDING, nullable=False)

    # FBO supply link (1:1, unique per active project)
    wb_fbo_supply_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("wb_fbo_supplies.id"), nullable=True)

    # Created on SHIPPED, cleared on rollback
    outbound_shipment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("outbound_shipments.id"), nullable=True
    )

    # Dates
    estimated_ready_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_ready_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Pallets & weight
    pallets_count: Mapped[int] = mapped_column(Integer, nullable=False)
    pallet_weight_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    # Vehicle & shipping
    vehicle_info: Mapped[str | None] = mapped_column(String(300), nullable=True)
    vehicle_brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    driver_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    counterparty_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("counterparty.id"), nullable=True)
    pickup_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    pickup_time_slot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pickup_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    vehicle_assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Manual WB warehouse name (used when no FBO supply is linked)
    wb_warehouse_name_manual: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Draft this request was created from (assembly-distribute flow). NULL for
    # requests created via other paths (FBO/manual). Drives the per-draft
    # «История — в сборке» list on the source-FF page.
    source_draft_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("assembly_drafts.id"), nullable=True)

    # WB acceptance package type — defines the transport unit for this request.
    # Set when the request is built (default BOX; switched to MONOPALLET when
    # WB acceptance/options says canBox=false but canMonopallet=true).
    package_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PackageType.BOX.value,
        server_default=PackageType.BOX.value,
    )

    # ─── Relationships ──────────────────────────────────────────────────

    warehouse: Mapped["Warehouse"] = relationship()
    wb_fbo_supply: Mapped["WbFboSupply | None"] = relationship()
    items: Mapped[list["AssemblyRequestItem"]] = relationship(
        back_populates="assembly_request",
        cascade="all, delete-orphan",
    )

    # ─── Table args ─────────────────────────────────────────────────────

    __table_args__ = (
        Index("ix_assembly_requests_project_id", "project_id"),
        Index("ix_assembly_requests_warehouse_id", "warehouse_id"),
        Index("ix_assembly_requests_status", "status"),
        Index("ix_assembly_requests_counterparty_id", "counterparty_id"),
        Index("ix_assembly_requests_source_draft_id", "source_draft_id"),
        # Partial unique: allow new request for same FBO after cancel
        Index(
            "ix_assembly_requests_fbo_unique",
            "project_id",
            "wb_fbo_supply_id",
            unique=True,
            postgresql_where="is_deleted = false AND status != 'CANCELLED' AND wb_fbo_supply_id IS NOT NULL",
        ),
    )


# ─── Assembly Request Item ──────────────────────────────────────────────────


class AssemblyRequestItem(Base):
    """Line item for assembly request."""

    __tablename__ = "assembly_request_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    assembly_request_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("assembly_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relationship
    assembly_request: Mapped["AssemblyRequest"] = relationship(back_populates="items")

    __table_args__ = (
        Index("ix_assembly_request_items_project_id", "project_id"),
        Index("ix_assembly_request_items_request_id", "assembly_request_id"),
    )


# ─── Assembly Status History ─────────────────────────────────────────────────


class AssemblyStatusHistory(Base):
    """Audit log for assembly request status changes."""

    __tablename__ = "assembly_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    assembly_request_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("assembly_requests.id", ondelete="CASCADE"), nullable=False
    )
    old_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    changed_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_assembly_status_history_project_id", "project_id"),
        Index("ix_assembly_status_history_request_id", "assembly_request_id"),
    )


# ─── Assembly Draft ─────────────────────────────────────────────────────────


class AssemblyDraft(Base, TimestampMixin, SoftDeleteMixin):
    """
    Draft of an assembly distribution: source FF warehouses x WB target warehouses.

    Lives until commit_draft() turns it into N AssemblyRequests (one per
    unique source/target pair). Soft-deleted afterwards. Persisted in DB so
    the user can reopen across devices and survive accidental tab close.

    distribution JSON shape:
    {
      "source_warehouse_ids": [int, ...],   # selected RF warehouses
      "target_warehouse_names": [str, ...], # selected WB warehouse names
      "rows": [
        {
          "nm_id": int,
          "barcode": str,
          "vendor_code": str,
          "src": {"<warehouse_id>": qty, ...},  # how much to take per FF
          "tgt": {"<wb_warehouse_name>": qty, ...},  # how much to ship per WB
        },
        ...
      ]
    }
    """

    __tablename__ = "assembly_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="Черновик сборки")
    distribution: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_assembly_drafts_project_id", "project_id"),)


# ─── Stock distribution daily snapshot ──────────────────────────────────────


class AssemblyStockDistributionDaily(Base, TimestampMixin):
    """Ежедневный снимок распределения «где товар» по складу и статусу товара.

    Накопительная история для вкладки «Распределение остатков»: остаток на
    складе ФФ (`FulfillmentStock`) хранится лишь как текущий снимок (перезатир
    каждым синком), поэтому динамику склада ФФ нельзя восстановить задним числом —
    эта таблица копит её вперёд (одна строка на день x склад x статус товара).
    Бакеты в штуках (как в `services/assembly/stock_distribution.py`):
    ff_stock = max(qty_good - qty_reserve, 0)*units_per_box; in_assembly/ready/
    in_transit — по статусам сборки. Пишется ежедневной scheduler-джобой
    (idempotent: снимок дня перезаписывается).
    """

    __tablename__ = "assembly_stock_distribution_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    # Статус товара: active / new / clearance / none (ProductStatusMap; none = без статуса).
    product_status: Mapped[str] = mapped_column(String(20), nullable=False)
    ff_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    in_assembly: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ready: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    in_transit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "snapshot_date",
            "warehouse_id",
            "product_status",
            name="uq_asm_stock_dist_daily",
        ),
        Index("ix_asm_stock_dist_daily_project_date", "project_id", "snapshot_date"),
    )

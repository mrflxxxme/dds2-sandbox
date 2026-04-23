"""
WB FBO Supplies models: WbFboSupply, WbFboSupplyItem.
Sync data from WB Marketplace API — read-only statuses, linked to OutboundShipment.
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.mixins import TimestampMixin
from backend.utils.time import utcnow

# ─── Enums ──────────────────────────────────────────────────────────────────


class WbSupplyStatus(str, enum.Enum):
    """WB supply statuses — read-only, set only via WB API sync."""

    ACTIVE = "ACTIVE"  # Запланирована
    ON_DELIVERY = "ON_DELIVERY"  # В пути  # noqa: RUF003
    IN_PROGRESS = "IN_PROGRESS"  # Разгрузка разрешена
    ACCEPTED = "ACCEPTED"  # Принята
    CANCELLED = "CANCELLED"  # Отменена


# ─── WB FBO Supply ──────────────────────────────────────────────────────────


class WbFboSupply(Base, TimestampMixin):
    """
    FBO supply from WB Marketplace API.
    Statuses are read-only — updated only via sync.
    Linked to OutboundShipment via outbound_shipment_id.
    """

    __tablename__ = "wb_fbo_supplies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    wb_supply_id: Mapped[str] = mapped_column(String(50), nullable=False)
    wb_status: Mapped[str] = mapped_column(String(30), nullable=False, default=WbSupplyStatus.ACTIVE)
    name: Mapped[str | None] = mapped_column(String(200))
    created_at_wb: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    planned_date: Mapped[date | None] = mapped_column(Date)
    actual_date: Mapped[date | None] = mapped_column(Date)
    warehouse_name: Mapped[str | None] = mapped_column(String(200))
    cargo_type: Mapped[str | None] = mapped_column(String(50))
    total_qty: Mapped[int] = mapped_column(Integer, default=0)
    accepted_qty: Mapped[int] = mapped_column(Integer, default=0)
    outbound_shipment_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("outbound_shipments.id"),
        nullable=True,
    )
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Set when user registered a return for unaccepted qty (goods/defect/utilized).
    # Supply is excluded from the partial-acceptance summary/filter afterwards.
    return_processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # GOODS / DEFECT / UTILIZED (FboReturnType). Populated together with
    # return_processed_at. Used to aggregate the "недоприёмка" dashboard
    # (возвращено на склад vs списано).
    return_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Actual qty returned (sum across items). Useful when user returns less
    # than the full delta, so aggregates stay accurate.
    return_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Manual override for the auto archive rule (Project.accounting_started_at):
    #   NULL  → auto: archived iff created_at_wb < project.accounting_started_at
    #   TRUE  → always archived (manually moved to archive)
    #   FALSE → always active (manually restored from archive)
    is_archived: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Excess (overshoot) handling — symmetric to return_processed_at:
    # when WB accepted MORE than we shipped (sum(item.accepted) > sum(item.qty)),
    # user clicks the write-off button — we create an OutboundShipment for the
    # delta and stamp these fields. Supply then drops out of the excess filter.
    excess_processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    excess_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Reassignment: when WB creates a separate micro-supply with items actually
    # belonging to a prior supply's under-acceptance. User links source → target;
    # target's item.accepted_qty increments, source goes to archive with pointer.
    reassigned_to_supply_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("wb_fbo_supplies.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    items: Mapped[list["WbFboSupplyItem"]] = relationship(
        back_populates="supply",
        cascade="all, delete-orphan",
    )
    outbound_shipment = relationship(
        "OutboundShipment",
        foreign_keys=[outbound_shipment_id],
    )
    reassigned_to: Mapped["WbFboSupply | None"] = relationship(
        "WbFboSupply",
        remote_side="WbFboSupply.id",
        foreign_keys=[reassigned_to_supply_id],
        back_populates="reassigned_from",
    )
    reassigned_from: Mapped[list["WbFboSupply"]] = relationship(
        "WbFboSupply",
        foreign_keys=[reassigned_to_supply_id],
        back_populates="reassigned_to",
    )

    __table_args__ = (
        UniqueConstraint("project_id", "wb_supply_id", name="uq_wb_fbo_supply"),
        Index("ix_wb_fbo_supplies_project_id", "project_id"),
        Index("ix_wb_fbo_supplies_wb_status", "wb_status"),
        Index("ix_wb_fbo_supplies_created_at_wb", "created_at_wb"),
        Index("ix_wb_fbo_supplies_reassigned_to", "reassigned_to_supply_id"),
    )


# ─── WB FBO Supply Item ────────────────────────────────────────────────────


class WbFboSupplyItem(Base):
    """Item (order) within a WB FBO supply."""

    __tablename__ = "wb_fbo_supply_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    supply_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("wb_fbo_supplies.id", ondelete="CASCADE"),
        nullable=False,
    )
    wb_order_id: Mapped[str] = mapped_column(String(50), nullable=False)
    nm_id: Mapped[int | None] = mapped_column(Integer)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    article_seller: Mapped[str | None] = mapped_column(String(100))
    product_name: Mapped[str | None] = mapped_column(String(500))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    accepted_qty: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    supply: Mapped["WbFboSupply"] = relationship(back_populates="items")

    __table_args__ = (
        Index("ix_wb_fbo_supply_items_project_id", "project_id"),
        Index("ix_wb_fbo_supply_items_supply_id", "supply_id"),
    )


# ─── Audit trail for FBO user actions ─────────────────────────────────────


class FboAuditAction(str, enum.Enum):
    """Actions tracked in FboSupplyAudit. Automatic WB-sync changes are not logged."""

    ARCHIVE = "ARCHIVE"  # manual move to archive (is_archived=True)
    UNARCHIVE = "UNARCHIVE"  # manual restore (is_archived=False) or reset to auto (None)
    BULK_RESTORE = "BULK_RESTORE"  # restore all linked supplies from archive
    RETURN = "RETURN"  # process unaccepted qty (GOODS/DEFECT/UTILIZED/MIXED)
    EXCESS = "EXCESS"  # write off overshoot from stock
    REASSIGN = "REASSIGN"  # link this supply as a доприёмка to another
    REVERT = "REVERT"  # undo of a prior audit entry (reverted_audit_id set)


class FboSupplyAudit(Base):
    """Append-only log of user-initiated changes to a WbFboSupply.

    Only reversible trivial actions (ARCHIVE/UNARCHIVE/REASSIGN) can be
    rolled back via UI — RETURN/EXCESS touch physical stock and require
    a separate handled-by-human workflow.
    """

    __tablename__ = "wb_fbo_supply_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    # SET NULL (not CASCADE) — audit is append-only, must survive supply removal.
    supply_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("wb_fbo_supplies.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    # payload — snapshot of what changed (old/new values, related IDs)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    # For REVERT rows — which audit entry was rolled back (self-FK, append-only)
    reverted_audit_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("wb_fbo_supply_audit.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Set on the ORIGINAL row when it gets reverted (null → still active)
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_wb_fbo_supply_audit_supply", "supply_id", "created_at"),
        Index("ix_wb_fbo_supply_audit_project", "project_id", "created_at"),
    )

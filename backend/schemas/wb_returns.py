# ruff: noqa: RUF002 — русские комментарии
"""Pydantic schemas for WB Goods Returns."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class WbGoodsReturnOut(BaseModel):
    """One return item as shown in UI."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    srid: str
    shk_id: str | None
    sticker_id: str | None
    barcode: str | None
    nm_id: int | None
    subject_name: str | None
    brand: str | None
    tech_size: str | None
    order_id: int | None
    order_dt: date | None
    ready_to_return_dt: datetime | None
    expired_dt: datetime | None
    completed_dt: datetime | None
    status: str | None
    return_type: str | None
    reason: str | None
    is_status_active: bool
    dst_office_id: int | None
    dst_office_address: str | None

    inbound_receipt_id: int | None
    inbound_receipt_number: str | None = None
    inbound_receipt_status: str | None = None
    inbound_warehouse_id: int | None = None
    inbound_warehouse_name: str | None = None

    # Derived UI state (см. wb_returns_service.classify_ui_state):
    # "ready_for_pickup" | "in_transit_to_pvz" | "pickup_planned" |
    # "picked_up_pending_receipt" | "received" | "expired" | "picked_without_receipt"
    ui_state: str
    synced_at: datetime


class WbGoodsReturnPvzGroup(BaseModel):
    """Grouping by ПВЗ in Tab 1."""

    dst_office_id: int | None
    dst_office_address: str | None
    total: int
    ready_count: int
    in_transit_count: int
    picked_without_receipt_count: int = 0
    nearest_expired_dt: datetime | None
    items: list[WbGoodsReturnOut]


class WbGoodsReturnSummary(BaseModel):
    """KPI counters above tabs."""

    ready_for_pickup: int
    in_transit_to_pvz: int
    soon_expires: int  # ready_for_pickup with expired_dt <= now + 3 days
    pickup_planned: int
    picked_up_pending_receipt: int
    picked_without_receipt: int  # retro-receipt needed
    expired: int
    received: int


class CreateReceiptFromReturnsIn(BaseModel):
    """Input: выбираем несколько srid и создаём pending InboundReceipt."""

    warehouse_id: int = Field(..., gt=0)
    srids: list[str] = Field(..., min_length=1, max_length=500)
    comment: str | None = None


class CreateReceiptFromReturnsOut(BaseModel):
    """Output: созданная приёмка.

    skipped_srids — srid, у которых нет barcode (нельзя создать InboundReceiptItem).
    Линк проставляется, но в `items_count` они не входят. UI показывает warning.
    """

    receipt_id: int
    receipt_number: str
    warehouse_id: int
    status: str
    items_count: int
    linked_srids: list[str]
    skipped_srids: list[str] = Field(default_factory=list)


class WbGoodsReturnsSyncIn(BaseModel):
    """Manual sync trigger."""

    date_from: date | None = None
    date_to: date | None = None


class WbGoodsReturnsSyncOut(BaseModel):
    """Result of manual sync."""

    rows_fetched: int
    rows_upserted: int
    started_at: datetime
    finished_at: datetime


class WbGoodsReturnsListResponse(BaseModel):
    """Paginated list response for returns."""

    items: list[WbGoodsReturnOut]
    total: int

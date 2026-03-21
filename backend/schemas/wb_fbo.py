"""
WB FBO Supplies schemas: request/response models.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

# ─── Supply Item ────────────────────────────────────────────────────────────


class WbFboSupplyItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    supply_id: int
    wb_order_id: str
    nm_id: int | None = None
    barcode: str
    article_seller: str | None = None
    product_name: str | None = None
    quantity: int
    accepted_qty: int = 0


# ─── Supply ─────────────────────────────────────────────────────────────────


class WbFboSupplySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    wb_supply_id: str
    wb_status: str
    name: str | None = None
    created_at_wb: datetime
    planned_date: date | None = None
    actual_date: date | None = None
    warehouse_name: str | None = None
    total_qty: int = 0
    accepted_qty: int = 0
    outbound_shipment_id: int | None = None
    synced_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WbFboSupplyWithItemsSchema(WbFboSupplySchema):
    """Supply with expanded items list."""

    items: list[WbFboSupplyItemSchema] = []


# ─── List response with pagination ──────────────────────────────────────────


class WbFboSupplyListResponse(BaseModel):
    items: list[WbFboSupplySchema]
    total: int


# ─── Link request ───────────────────────────────────────────────────────────


class FboSupplyLinkRequest(BaseModel):
    """Link FBO supply to an OutboundShipment."""

    outbound_shipment_id: int


# ─── Sync response ──────────────────────────────────────────────────────────


class FboSyncResultSchema(BaseModel):
    """Result of FBO supplies sync operation."""

    synced: int = 0
    created: int = 0
    updated: int = 0
    errors: int = 0
    message: str = ""

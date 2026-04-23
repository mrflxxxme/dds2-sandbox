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
    cargo_type: str | None = None
    total_qty: int = 0
    accepted_qty: int = 0
    outbound_shipment_id: int | None = None
    outbound_shipment_number: str | None = None
    outbound_shipment_status: str | None = None
    outbound_shipment_warehouse_id: int | None = None
    is_archived: bool | None = None
    excess_processed_at: datetime | None = None
    excess_qty: int | None = None
    reassigned_to_supply_id: int | None = None
    reassigned_to_wb_supply_id: str | None = None
    reassigned_from_wb_supply_ids: list[str] = []
    assembly_request_id: int | None = None
    assembly_request_number: str | None = None
    assembly_request_status: str | None = None
    source_warehouse_id: int | None = None
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


# ─── Sync response ──────────────────────────────────────────────────────────


class FboSyncResultSchema(BaseModel):
    """Result of FBO supplies sync operation."""

    synced: int = 0
    created: int = 0
    updated: int = 0
    errors: int = 0
    message: str = ""


class FboArchiveRequest(BaseModel):
    """PATCH body for archive flag override.
    None = reset to auto rule, True = manual archive, False = manual restore."""

    is_archived: bool | None = None


class FboExcessItem(BaseModel):
    """One row of excess write-off — per barcode."""

    barcode: str
    quantity: int


class FboExcessRequest(BaseModel):
    """Write off the excess (overshoot) qty from our warehouse.
    WB accepted more than we shipped → take the delta off the source warehouse."""

    warehouse_id: int
    items: list[FboExcessItem]
    comment: str | None = None


class FboExcessResponse(BaseModel):
    supply_id: int
    shipment_id: int
    shipment_number: str
    total_qty: int


# ─── Return (недоприёмка) ──────────────────────────────────────────────────


class FboReturnItem(BaseModel):
    """One row of the return — split per barcode with its own disposition."""

    barcode: str
    quantity: int
    return_type: str  # GOODS | UTILIZED (DEFECT kept server-side for back-compat)


class FboReturnRequest(BaseModel):
    """
    Handle unaccepted qty for a supply with partial acceptance.

    Each item in `items` carries its own `return_type` so the user can split
    one supply between «Вернуть на склад» (GOODS) and «Утилизация» (UTILIZED)
    in a single submit. `warehouse_id` is required if any item has GOODS/DEFECT.
    """

    warehouse_id: int | None = None
    items: list[FboReturnItem]
    comment: str | None = None


class FboReturnResponse(BaseModel):
    supply_id: int
    receipt_id: int | None = None
    receipt_number: str | None = None


# ─── Reassignment (link under-delivery micro-supply to parent) ────────────


class FboReassignCandidateItem(BaseModel):
    """Matching SKU breakdown inside a candidate supply."""

    barcode: str
    article_seller: str | None = None
    product_name: str | None = None
    quantity: int
    accepted_qty: int
    unaccepted_delta: int


class FboReassignCandidate(BaseModel):
    """A supply that has under-acceptance matching the source's barcodes."""

    id: int
    wb_supply_id: str
    warehouse_name: str | None = None
    created_at_wb: datetime
    total_qty: int
    accepted_qty: int
    same_warehouse: bool
    matched_items: list[FboReassignCandidateItem]
    matched_qty: int


class FboReassignCandidatesResponse(BaseModel):
    source_supply_id: int
    candidates: list[FboReassignCandidate]


class FboReassignRequest(BaseModel):
    """Link source supply (this one) to target supply with under-acceptance.
    Source accepted_qty is added to target's matching items.
    Source is archived and gets reassigned_to_supply_id = target.id."""

    target_supply_id: int


class FboReassignResponse(BaseModel):
    source_supply_id: int
    target_supply_id: int
    target_wb_supply_id: str
    reassigned_qty: int


# ─── Audit trail ──────────────────────────────────────────────────────────


class FboAuditEntry(BaseModel):
    id: int
    supply_id: int
    action: str
    payload: dict
    user_id: int | None = None
    created_at: datetime
    reverted_audit_id: int | None = None
    reverted_at: datetime | None = None
    revertible: bool = False


class FboAuditResponse(BaseModel):
    supply_id: int
    entries: list[FboAuditEntry]


class FboAuditListEntry(FboAuditEntry):
    """Global-feed entry — includes supply reference for deep-linking."""

    supply_wb_id: str
    warehouse_name: str | None = None
    username: str | None = None


class FboAuditListResponse(BaseModel):
    entries: list[FboAuditListEntry]
    total: int


class FboAuditRevertResponse(BaseModel):
    audit_id: int
    revert_id: int
    action: str

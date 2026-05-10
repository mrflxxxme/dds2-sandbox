# ruff: noqa: RUF002, RUF003
"""Pydantic schemas for box-multiplicity (кратность коробки) feature."""

from pydantic import BaseModel, Field


class BoxMultiplicityRow(BaseModel):
    """One SKU row with effective box-qty resolved from overrides + sources."""

    nm_id: int
    vendor_code: str | None = None
    barcode: str
    brand: str | None = None
    subject: str | None = None
    box_qty_override: int | None = None  # Nomenclature.box_qty_override (manual)
    box_qty_from_vehicle: int | None = None  # latest DELIVERED cost_order_item
    vehicle_order_no: str | None = None
    vehicle_received_at: str | None = None  # ISO date or None
    box_qty_from_factory: int | None = None  # latest factory_order_item
    effective_box_qty: int | None = None  # priority: override → vehicle → factory
    use_box_multiplicity: bool = True  # whether to round assembly distribution to ppb
    # ─── stock metrics for filters ────────────────────────────────────────
    rf_stock: int = 0  # total qty across active fulfillment warehouses
    in_assembly: int = 0  # reserved in PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED
    in_transit: int = 0  # SHIPPED assemblies en-route to WB
    wb_stock: int = 0  # total qty across all WB warehouses


class BoxMultiplicityResponse(BaseModel):
    items: list[BoxMultiplicityRow]
    total: int


class BoxMultiplicityUpdate(BaseModel):
    """Either field optional. `box_qty_override=null` clears manual ppb;
    positive int sets it. `use_box_multiplicity` toggles whether ppb is
    applied during assembly distribution.
    """

    box_qty_override: int | None = Field(default=None, ge=1, le=10000)
    use_box_multiplicity: bool | None = None


class BoxMultiplicityBulkItem(BaseModel):
    """One paste row: match by barcode, partial update."""

    barcode: str = Field(min_length=1, max_length=50)
    box_qty_override: int | None = Field(default=None, ge=1, le=10000)
    use_box_multiplicity: bool | None = None


class BoxMultiplicityBulkRequest(BaseModel):
    items: list[BoxMultiplicityBulkItem] = Field(default_factory=list, max_length=5000)


class BoxMultiplicityBulkResponse(BaseModel):
    updated: list[BoxMultiplicityRow]  # rows actually changed
    not_found: list[str]  # barcodes that don't exist in this project
    matched_count: int  # how many barcodes matched (some may have had no diff)

"""
Pydantic schemas for AssemblyDraft.

Used by the distribution UI to plan an NxM split (RF source warehouses x
WB target warehouses) before committing it as N AssemblyRequests.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AssemblyDraftRow(BaseModel):
    """One article in the distribution matrix."""

    nm_id: int
    barcode: str = ""
    vendor_code: str = ""
    src: dict[str, int] = Field(default_factory=dict)  # warehouse_id (str) -> qty
    tgt: dict[str, int] = Field(default_factory=dict)  # wb_warehouse_name -> qty


class AssemblyDraftDistribution(BaseModel):
    """Full distribution payload stored in AssemblyDraft.distribution JSONB."""

    source_warehouse_ids: list[int] = Field(default_factory=list)
    target_warehouse_names: list[str] = Field(default_factory=list)
    rows: list[AssemblyDraftRow] = Field(default_factory=list)
    pallets_count: int = 1
    pallet_weight_kg: float = 0.0
    estimated_ready_date: str | None = None  # YYYY-MM-DD


class AssemblyDraftCreate(BaseModel):
    name: str = "Черновик сборки"
    distribution: AssemblyDraftDistribution
    comment: str | None = None


class AssemblyDraftUpdate(BaseModel):
    name: str | None = None
    distribution: AssemblyDraftDistribution | None = None
    comment: str | None = None


class AssemblyDraftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    distribution: AssemblyDraftDistribution
    comment: str | None
    created_at: datetime
    updated_at: datetime


class AssemblyDraftCommitResponse(BaseModel):
    """Returned after a draft is committed into N AssemblyRequests."""

    created_request_ids: list[int]
    draft_id: int

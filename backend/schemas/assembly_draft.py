# ruff: noqa: RUF001, RUF002, RUF003
"""
Pydantic schemas for AssemblyDraft.

Used by the distribution UI to plan an NxM split (RF source warehouses x
WB target warehouses) before committing it as N AssemblyRequests.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PackageTypeStr = Literal["BOX", "MONOPALLET", "SUPERSAFE"]


class AssemblyDraftRow(BaseModel):
    """One article in the distribution matrix."""

    nm_id: int
    barcode: str = ""
    vendor_code: str = ""
    src: dict[str, int] = Field(default_factory=dict)  # warehouse_id (str) -> qty
    tgt: dict[str, int] = Field(default_factory=dict)  # wb_warehouse_name -> qty
    # Per-SKU acceptance package type (set by /warehouse/acceptance-check).
    # Drives grouping into AssemblyRequests on commit_draft —
    # one request per (source_ff, target_wb, package_type).
    package_type: PackageTypeStr = "BOX"


class HandedUnitItem(BaseModel):
    """Позиция замороженной заявки-юнита (передан на ФФ)."""

    nm_id: int
    barcode: str
    vendor_code: str = ""
    qty: int


class HandedUnit(BaseModel):
    """Заявка-юнит (source_ff × target_wb × упаковка × новизна), вырезанная из
    черновика и переданная на ФФ. Снимок заморожен: правки распределения его не
    трогают (его уже нет в rows). `в сборке` создаёт из него AssemblyRequest."""

    source_ff_id: int
    target_wb_name: str
    package_type: PackageTypeStr = "BOX"
    is_newcomer: bool = False
    status: str = "handed"  # пока только "handed" (передан на ФФ)
    items: list[HandedUnitItem] = Field(default_factory=list)


class AssemblyDraftDistribution(BaseModel):
    """Full distribution payload stored in AssemblyDraft.distribution JSONB."""

    source_warehouse_ids: list[int] = Field(default_factory=list)
    target_warehouse_names: list[str] = Field(default_factory=list)
    rows: list[AssemblyDraftRow] = Field(default_factory=list)
    pallets_count: int = 1
    pallet_weight_kg: float = 0.0
    estimated_ready_date: str | None = None  # YYYY-MM-DD
    # Cold-start доли по WB-складам (warehouse_name → доля 0..1, не проценты).
    # Если задано — Авто-баланс на странице distribute распределяет qty
    # пропорционально этим долям (вместо wbNeed). None для обычных сборок.
    cold_start_shares: dict[str, float] | None = None
    # Замороженные заявки-юниты, переданные на ФФ (вырезаны из rows).
    handed_units: list[HandedUnit] = Field(default_factory=list)

    @field_validator("rows", "source_warehouse_ids", "target_warehouse_names", "handed_units", mode="before")
    @classmethod
    def coerce_null_to_empty_list(cls, v: object) -> object:
        """Guard against explicit null in stored JSONB (null → [])."""
        return v if v is not None else []


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
    # SKU-новинки в этом draft (Nomenclature.first_sale_date IS NULL OR ≥ today-14d).
    # Заполняется роутером при возврате; в БД не хранится — derived из nomenclature.
    # UI использует чтобы (a) показать бейдж 🆕 в матрице, (b) посчитать сколько
    # отдельных заявок будет создано (новинки идут отдельно от обычных).
    newcomer_nm_ids: list[int] = Field(default_factory=list)


class AssemblyDraftCommitResponse(BaseModel):
    """Returned after a draft is committed into N AssemblyRequests."""

    created_request_ids: list[int]
    draft_id: int


class AssemblyDraftUnitRef(BaseModel):
    """Ссылка на заявку-юнит черновика (для hand-off / revert / commit)."""

    source_ff_id: int
    target_wb_name: str
    package_type: PackageTypeStr = "BOX"
    is_newcomer: bool = False


class AssemblyDraftUnitEdit(AssemblyDraftUnitRef):
    """Замена наполнения заявки-юнита (ручная правка черновика)."""

    items: list[HandedUnitItem] = Field(default_factory=list)


class AssemblyDraftUnitMove(AssemblyDraftUnitRef):
    """Перенос заявки-юнита на другой WB-склад (только для этого ФФ)."""

    new_target_wb_name: str


class AssemblyDraftMergeRequest(BaseModel):
    """Request body for POST /assembly/drafts/merge.

    Merges N drafts into one: rows with matching (nm_id, package_type) are
    summed element-wise; source_warehouse_ids and target_warehouse_names are
    unioned. cold_start_shares is dropped (user re-runs auto-balance).
    """

    draft_ids: list[int] = Field(
        ...,
        min_length=2,
        description="IDs of drafts to merge (≥2 distinct values required)",
    )

    @field_validator("draft_ids")
    @classmethod
    def dedup_and_validate(cls, v: list[int]) -> list[int]:
        """Dedup while preserving order; ensure ≥2 distinct ids remain."""
        seen: set[int] = set()
        result: list[int] = []
        for x in v:
            if x not in seen:
                seen.add(x)
                result.append(x)
        if len(result) < 2:
            raise ValueError("draft_ids must contain at least 2 distinct IDs")
        return result

"""
Counterparty schemas: CRUD + filters + stats + document upload/response.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─── Allowed enum values ─────────────────────────────────────────────────────

ALLOWED_CP_TYPES = [
    "SUPPLIER",
    "FULFILLMENT",
    "CARRIER",
    "CUSTOMS_BROKER",
    "DESIGNER",
    "LEGAL",
    "LANDLORD",
    "IT_SERVICE",
    "MARKETPLACE",
    "BANK",
    "GOVERNMENT",
    "AFFILIATED",
    "OTHER",
]

ALLOWED_DOC_TYPES = ["CONTRACT", "CERTIFICATE", "INVOICE", "OTHER"]

# ─── Counterparty ─────────────────────────────────────────────────────────────


class CounterpartyBase(BaseModel):
    inn: str | None = Field(None, min_length=10, max_length=12, pattern=r"^\d{10,12}$")
    name: str = Field(..., min_length=1, max_length=500)
    primary_type: str = Field(default="OTHER")
    secondary_types: list[str] | None = None
    kpp: str | None = Field(None, max_length=9, pattern=r"^\d{9}$")
    contract_number: str | None = Field(None, max_length=100)
    notes: str | None = None
    contacts: dict | None = None

    @field_validator("primary_type")
    @classmethod
    def validate_primary_type(cls, v: str) -> str:
        if v not in ALLOWED_CP_TYPES:
            raise ValueError(f"primary_type must be one of: {ALLOWED_CP_TYPES}")
        return v

    @field_validator("secondary_types")
    @classmethod
    def validate_secondary_types(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        invalid = [t for t in v if t not in ALLOWED_CP_TYPES]
        if invalid:
            raise ValueError(f"secondary_types contains invalid values: {invalid}")
        return v


class CounterpartyCreate(CounterpartyBase):
    pass


class CounterpartyUpdate(BaseModel):
    """All fields optional for PATCH."""

    inn: str | None = Field(None, min_length=10, max_length=12, pattern=r"^\d{10,12}$")
    name: str | None = Field(None, min_length=1, max_length=500)
    primary_type: str | None = None
    secondary_types: list[str] | None = None
    kpp: str | None = Field(None, max_length=9, pattern=r"^\d{9}$")
    contract_number: str | None = Field(None, max_length=100)
    notes: str | None = None
    contacts: dict | None = None

    @field_validator("primary_type")
    @classmethod
    def validate_primary_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_CP_TYPES:
            raise ValueError(f"primary_type must be one of: {ALLOWED_CP_TYPES}")
        return v

    @field_validator("secondary_types")
    @classmethod
    def validate_secondary_types(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        invalid = [t for t in v if t not in ALLOWED_CP_TYPES]
        if invalid:
            raise ValueError(f"secondary_types contains invalid values: {invalid}")
        return v


class CounterpartyStats(BaseModel):
    """Turnover stats for a single currency."""

    in_sum: Decimal = Field(default=Decimal("0"))
    out_sum: Decimal = Field(default=Decimal("0"))
    net: Decimal = Field(default=Decimal("0"))
    tx_count: int = Field(default=0)


class CounterpartyListItem(CounterpartyBase):
    """Compact list item response."""

    id: int
    created_by_import: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class CounterpartyDetail(CounterpartyListItem):
    """Full counterparty card with stats, linked entities, docs."""

    stats_rub: CounterpartyStats = Field(default_factory=CounterpartyStats)
    stats_cny: CounterpartyStats = Field(default_factory=CounterpartyStats)
    linked_warehouses: list[dict] = Field(default_factory=list)
    linked_suppliers: list[dict] = Field(default_factory=list)
    active_loans: list[dict] = Field(default_factory=list)
    docs_count: int = 0


class CounterpartyListResponse(BaseModel):
    """Paginated list response."""

    items: list[CounterpartyListItem]
    total: int


class CounterpartyFilter(BaseModel):
    """Query filters for GET /counterparties."""

    type: str | None = None
    q: str | None = None
    active_only: bool = False
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_CP_TYPES:
            raise ValueError(f"type must be one of: {ALLOWED_CP_TYPES}")
        return v


# ─── CounterpartyDocument ─────────────────────────────────────────────────────


class CounterpartyDocumentCreate(BaseModel):
    """Used internally after file upload."""

    doc_type: str = Field(default="OTHER")
    original_filename: str | None = Field(None, max_length=500)
    file_size: int | None = None
    mime_type: str | None = Field(None, max_length=100)
    minio_path: str = Field(..., max_length=500)

    @field_validator("doc_type")
    @classmethod
    def validate_doc_type(cls, v: str) -> str:
        if v not in ALLOWED_DOC_TYPES:
            raise ValueError(f"doc_type must be one of: {ALLOWED_DOC_TYPES}")
        return v


class CounterpartyDocumentResponse(BaseModel):
    """Document list/upload response (signed_url for download)."""

    id: int
    counterparty_id: int
    doc_type: str
    original_filename: str | None = None
    file_size: int | None = None
    mime_type: str | None = None
    uploaded_at: datetime
    signed_url: str | None = None  # set by service (TTL=300s)

    model_config = ConfigDict(from_attributes=True)

"""
Counterparty schemas: CRUD + filters + stats + document upload/response.
"""

from datetime import date, datetime
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
    # Банковские реквизиты (мягкая валидация — карточка показывает/правит, выписка заполняет автоматом)
    bank_account: str | None = Field(None, max_length=20)
    bik: str | None = Field(None, max_length=9)
    bank_name: str | None = Field(None, max_length=255)
    corr_account: str | None = Field(None, max_length=20)

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
    bank_account: str | None = Field(None, max_length=20)
    bik: str | None = Field(None, max_length=9)
    bank_name: str | None = Field(None, max_length=255)
    corr_account: str | None = Field(None, max_length=20)

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

    # Read models echo stored data — they must not re-apply input constraints.
    # Legacy/imported rows can hold malformed inn/kpp (e.g. '-' from a bank
    # statement); inheriting CounterpartyBase's min_length/pattern would 500 the
    # whole list on a single bad row. Strict validation stays on the write
    # models (CounterpartyCreate / CounterpartyUpdate).
    inn: str | None = None
    kpp: str | None = None

    id: int
    created_by_import: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Per-period turnover (populated when date_from/date_to are passed). None = not computed.
    income_rub: Decimal | None = None
    expense_rub: Decimal | None = None
    income_cny: Decimal | None = None
    expense_cny: Decimal | None = None
    tx_count: int | None = None
    # Expense category (level-2) — populated by the list endpoint from the mapping.
    cat_lvl1: str | None = None
    cat_lvl2: str | None = None

    model_config = ConfigDict(from_attributes=True)


class CounterpartyDetail(CounterpartyListItem):
    """Full counterparty card with stats, linked entities, docs."""

    # Expense category (level-2) — managed on the card, propagated to transactions.
    cat_lvl1: str | None = None
    cat_lvl2: str | None = None
    stats_rub: CounterpartyStats = Field(default_factory=CounterpartyStats)
    stats_cny: CounterpartyStats = Field(default_factory=CounterpartyStats)
    linked_warehouses: list[dict] = Field(default_factory=list)
    linked_suppliers: list[dict] = Field(default_factory=list)
    active_loans: list[dict] = Field(default_factory=list)
    docs_count: int = 0


class SetExpenseCategoryRequest(BaseModel):
    """Set/clear the expense category on a counterparty (cat_lvl1=None clears)."""

    cat_lvl1: str | None = Field(None, max_length=100)
    cat_lvl2: str | None = Field(None, max_length=100)


class SetExpenseCategoryResponse(BaseModel):
    applied: int
    cp_key: str
    cat_lvl1: str | None = None
    cat_lvl2: str | None = None


class BulkCategoryRequest(BaseModel):
    """Apply a type and/or expense category to many counterparties at once."""

    ids: list[int] = Field(..., min_length=1, max_length=1000)
    cat_lvl1: str | None = Field(None, max_length=100)
    cat_lvl2: str | None = Field(None, max_length=100)
    primary_type: str | None = None

    @field_validator("primary_type")
    @classmethod
    def validate_primary_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_CP_TYPES:
            raise ValueError(f"primary_type must be one of: {ALLOWED_CP_TYPES}")
        return v


class BulkCategoryResponse(BaseModel):
    counterparties: int
    transactions: int


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


class CounterpartyTransactionItem(BaseModel):
    """Single bank transaction linked to a counterparty."""

    id: int
    date: datetime
    account: str
    currency: str
    income: Decimal
    expense: Decimal
    purpose: str | None = None
    event_type2: str | None = None
    loan_payment_type: str | None = None
    contract_number: str | None = None

    model_config = ConfigDict(from_attributes=True)


class CounterpartyTransactionsResponse(BaseModel):
    """Paginated list of transactions for a counterparty."""

    items: list[CounterpartyTransactionItem]
    total: int


class CounterpartyCategorySummary(BaseModel):
    """Aggregate turnover for one primary_type over a date range."""

    primary_type: str
    count_cps: int
    income_rub: Decimal = Decimal("0")
    expense_rub: Decimal = Decimal("0")
    income_cny: Decimal = Decimal("0")
    expense_cny: Decimal = Decimal("0")
    tx_count: int = 0


class CounterpartySummaryResponse(BaseModel):
    """Summary grouped by primary_type."""

    items: list[CounterpartyCategorySummary]
    date_from: date | None = None
    date_to: date | None = None


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

"""
Loan schemas: CRUD + LoanPaymentMatch + LoanDetail.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─── Allowed enum values ─────────────────────────────────────────────────────

ALLOWED_DIRECTIONS = ["INCOMING", "OUTGOING", "AFFILIATED"]
ALLOWED_STATUSES = ["ACTIVE", "CLOSED", "DEFAULTED"]
ALLOWED_PAYMENT_TYPES = ["DISBURSEMENT", "PRINCIPAL_REPAY", "INTEREST_PAY", "PENALTY"]

# ─── Loan ─────────────────────────────────────────────────────────────────────


class LoanBase(BaseModel):
    counterparty_id: int
    direction: str = Field(default="INCOMING")
    principal: Decimal = Field(..., gt=0)
    currency: str = Field(default="RUB", max_length=3)
    rate: Decimal | None = Field(None, ge=0, le=1)
    contract_number: str = Field(..., min_length=1, max_length=100)
    contract_date: date
    start_date: date
    maturity_date: date | None = None
    status: str = Field(default="ACTIVE")
    notes: str | None = None

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        if v not in ALLOWED_DIRECTIONS:
            raise ValueError(f"direction must be one of: {ALLOWED_DIRECTIONS}")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in ALLOWED_STATUSES:
            raise ValueError(f"status must be one of: {ALLOWED_STATUSES}")
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        return v.upper()


class LoanCreate(LoanBase):
    pass


class LoanUpdate(BaseModel):
    """All fields optional for PATCH."""

    direction: str | None = None
    principal: Decimal | None = Field(None, gt=0)
    currency: str | None = Field(None, max_length=3)
    rate: Decimal | None = Field(None, ge=0, le=1)
    contract_number: str | None = Field(None, min_length=1, max_length=100)
    contract_date: date | None = None
    start_date: date | None = None
    maturity_date: date | None = None
    status: str | None = None
    notes: str | None = None

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_DIRECTIONS:
            raise ValueError(f"direction must be one of: {ALLOWED_DIRECTIONS}")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_STATUSES:
            raise ValueError(f"status must be one of: {ALLOWED_STATUSES}")
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class LoanShort(BaseModel):
    """Compact loan item for counterparty card."""

    id: int
    direction: str
    principal: Decimal
    currency: str
    status: str
    start_date: date
    maturity_date: date | None = None

    model_config = ConfigDict(from_attributes=True)


class LoanResponse(LoanBase):
    """Full loan response."""

    id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# ─── LoanPayment ─────────────────────────────────────────────────────────────


class LoanPaymentCreate(BaseModel):
    """Create a manual loan payment entry."""

    payment_type: str
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(..., max_length=3)
    paid_at: date
    transaction_id: int | None = None

    @field_validator("payment_type")
    @classmethod
    def validate_payment_type(cls, v: str) -> str:
        if v not in ALLOWED_PAYMENT_TYPES:
            raise ValueError(f"payment_type must be one of: {ALLOWED_PAYMENT_TYPES}")
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        return v.upper()


class LoanPaymentMatch(BaseModel):
    """Match an existing bank transaction to a loan payment."""

    transaction_id: int
    payment_type: str
    amount: Decimal = Field(..., gt=0)

    @field_validator("payment_type")
    @classmethod
    def validate_payment_type(cls, v: str) -> str:
        if v not in ALLOWED_PAYMENT_TYPES:
            raise ValueError(f"payment_type must be one of: {ALLOWED_PAYMENT_TYPES}")
        return v


class LoanPaymentResponse(BaseModel):
    """Loan payment response."""

    id: int
    loan_id: int
    transaction_id: int | None = None
    payment_type: str
    amount: Decimal
    currency: str
    paid_at: date
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# ─── LoanDetail ───────────────────────────────────────────────────────────────


class LoanScheduleSummary(BaseModel):
    """Payment totals summary for a loan."""

    principal_paid: Decimal = Decimal("0")
    interest_paid: Decimal = Decimal("0")
    penalty_paid: Decimal = Decimal("0")
    remaining: Decimal = Decimal("0")


class LoanDetail(LoanResponse):
    """Extended loan detail with counterparty info, payments, schedule."""

    counterparty_name: str | None = None
    counterparty_inn: str | None = None
    payments: list[LoanPaymentResponse] = Field(default_factory=list)
    schedule_summary: LoanScheduleSummary = Field(default_factory=LoanScheduleSummary)


# ─── Loan list response ───────────────────────────────────────────────────────


class LoanDirectionTotals(BaseModel):
    """Aggregated totals for a single direction."""

    count: int = 0
    sum_rub: Decimal = Decimal("0")
    sum_cny: Decimal = Decimal("0")


class LoanListResponse(BaseModel):
    """Paginated loans response with direction totals."""

    items: list[LoanResponse]
    totals_by_direction: dict[str, LoanDirectionTotals] = Field(default_factory=dict)
    total: int = 0


# ─── Filters ─────────────────────────────────────────────────────────────────


class LoanFilter(BaseModel):
    """Query filters for GET /loans."""

    direction: str | None = None
    status: str | None = None
    counterparty_id: int | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_DIRECTIONS:
            raise ValueError(f"direction must be one of: {ALLOWED_DIRECTIONS}")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_STATUSES:
            raise ValueError(f"status must be one of: {ALLOWED_STATUSES}")
        return v

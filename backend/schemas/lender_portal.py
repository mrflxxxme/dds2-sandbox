# ruff: noqa: RUF002, RUF003
"""
Лендер-портал — «безопасные» read-only схемы для заёмщика (кредитора).

Намеренно НЕ переиспользуем LoanResponse/LoanDetail целиком: заёмщику не нужны
project_id, direction, parent_loan_id и пр. Только: сколько в займе, ставка,
когда выплата процентов, когда возврат, история выплат.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class LenderProjectInfo(BaseModel):
    slug: str
    name: str


class LenderTotals(BaseModel):
    active_count: int = 0
    outstanding: Decimal = Decimal("0")  # тело в займе сейчас
    accrued_interest: Decimal = Decimal("0")  # начислено % (неоплач.)
    monthly_interest: Decimal = Decimal("0")  # % в месяц (run-rate)
    interest_paid: Decimal = Decimal("0")  # всего выплачено %
    next_interest_date: date | None = None
    next_maturity_date: date | None = None


class LenderMeResponse(BaseModel):
    username: str
    display_name: str  # имя контрагента-заёмщика
    projects: list[LenderProjectInfo] = Field(default_factory=list)
    totals: LenderTotals = Field(default_factory=LenderTotals)


class LenderLoanRow(BaseModel):
    id: int
    contract_number: str
    principal: Decimal
    currency: str
    rate: Decimal | None = None
    status: str
    entity_type: str | None = None
    start_date: date
    maturity_date: date | None = None
    remaining_principal: Decimal = Decimal("0")
    accrued_interest: Decimal = Decimal("0")
    monthly_interest: Decimal = Decimal("0")
    next_interest_date: date | None = None
    days_to_maturity: int | None = None
    is_overdue: bool = False


class LenderLoanListResponse(BaseModel):
    items: list[LenderLoanRow] = Field(default_factory=list)
    totals: LenderTotals = Field(default_factory=LenderTotals)


class LenderPaymentRow(BaseModel):
    payment_type: str
    amount: Decimal
    currency: str
    paid_at: date


class LenderLoanDetail(LenderLoanRow):
    contract_date: date
    lender_bank: str | None = None
    # NB: internal admin `notes` are deliberately NOT exposed to the external lender.
    interest_paid: Decimal = Decimal("0")
    principal_repaid: Decimal = Decimal("0")
    total_interest_projected: Decimal = Decimal("0")
    payments: list[LenderPaymentRow] = Field(default_factory=list)
    created_at: datetime | None = None

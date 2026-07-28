"""
Payroll schemas: сотрудники, команды, тарифная лестница, месячная ведомость.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ALLOWED_SCOPE_KINDS = ["brand", "subject"]

# ─── Сотрудники ──────────────────────────────────────────────────────────────


class PayDayShare(BaseModel):
    """Строка графика выплат фикс-оклада: день месяца и доля."""

    day: int = Field(..., ge=1, le=28)
    share: Decimal = Field(..., gt=0, le=1)


class EmployeeIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    counterparty_id: int | None = None
    fixed_salary: Decimal | None = Field(None, ge=0)
    # None — дефолт 50/50 на 10-е и 25-е; сумма долей должна быть равна 1.
    fixed_pay_days: list[PayDayShare] | None = None
    is_active: bool = True
    notes: str | None = None

    @field_validator("fixed_pay_days")
    @classmethod
    def validate_shares(cls, v: list[PayDayShare] | None) -> list[PayDayShare] | None:
        if v is not None:
            if not v:
                raise ValueError("fixed_pay_days must be non-empty or null")
            total = sum(item.share for item in v)
            if abs(total - Decimal(1)) > Decimal("0.001"):
                raise ValueError("fixed_pay_days shares must sum to 1")
            days = [item.day for item in v]
            if len(days) != len(set(days)):
                raise ValueError("fixed_pay_days days must be unique")
        return v


class EmployeeUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    counterparty_id: int | None = None
    clear_counterparty: bool = False
    fixed_salary: Decimal | None = Field(None, ge=0)
    clear_fixed_salary: bool = False
    fixed_pay_days: list[PayDayShare] | None = None
    is_active: bool | None = None
    notes: str | None = None

    validate_shares = field_validator("fixed_pay_days")(EmployeeIn.validate_shares.__func__)  # type: ignore[arg-type]


class EmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    counterparty_id: int | None
    counterparty_name: str | None = None
    fixed_salary: Decimal | None
    fixed_pay_days: list[PayDayShare] | None
    is_active: bool
    notes: str | None
    team_names: list[str] = []


class EmployeeListResponse(BaseModel):
    items: list[EmployeeResponse]


# ─── Команды ─────────────────────────────────────────────────────────────────


class TeamScopeIn(BaseModel):
    kind: str
    value: str = Field(..., min_length=1, max_length=300)

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        if v not in ALLOWED_SCOPE_KINDS:
            raise ValueError(f"kind must be one of: {ALLOWED_SCOPE_KINDS}")
        return v


class TeamIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    is_active: bool = True


class TeamUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    is_active: bool | None = None


class TeamScopesReplace(BaseModel):
    scopes: list[TeamScopeIn]


class TeamMembersReplace(BaseModel):
    employee_ids: list[int]


class TeamMemberOut(BaseModel):
    employee_id: int
    name: str


class TeamResponse(BaseModel):
    id: int
    name: str
    is_active: bool
    scopes: list[TeamScopeIn]
    members: list[TeamMemberOut]


class TeamListResponse(BaseModel):
    items: list[TeamResponse]


class ScopeOptionsResponse(BaseModel):
    """Доступные значения брендов/категорий из wb_finance_rows проекта."""

    brands: list[str]
    subjects: list[str]


# ─── Тарифная лестница ───────────────────────────────────────────────────────


class TariffStepIn(BaseModel):
    threshold: Decimal = Field(..., gt=0)
    company_rate: Decimal = Field(..., ge=0, le=1)
    team_rate: Decimal = Field(..., ge=0, le=1)


class TariffReplace(BaseModel):
    """Полная замена набора ступеней для даты valid_from."""

    valid_from: date
    steps: list[TariffStepIn] = Field(..., min_length=1)

    @field_validator("steps")
    @classmethod
    def validate_unique_thresholds(cls, v: list[TariffStepIn]) -> list[TariffStepIn]:
        thresholds = [s.threshold for s in v]
        if len(thresholds) != len(set(thresholds)):
            raise ValueError("thresholds must be unique")
        return v


class TariffStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    threshold: Decimal
    company_rate: Decimal
    team_rate: Decimal


class TariffResponse(BaseModel):
    valid_from: date | None
    steps: list[TariffStepOut]


# ─── Месячная ведомость ──────────────────────────────────────────────────────


class SheetWeekAccrual(BaseModel):
    """Начисление команды за одну неделю WB (Пн-Вс)."""

    date_from: date
    date_to: date
    base_amount: Decimal  # «Чистая выплата» по скоупам команды за неделю
    threshold: Decimal | None  # ступень лестницы (None — выплата <= 0, ставка 0)
    team_rate: Decimal
    team_amount: Decimal
    per_member: Decimal


class SheetTeam(BaseModel):
    team_id: int
    name: str
    scopes: list[TeamScopeIn]
    member_names: list[str]
    weeks: list[SheetWeekAccrual]
    total_amount: Decimal


class SheetTeamAccrual(BaseModel):
    team_id: int
    team_name: str
    amount: Decimal


class OfficialTxn(BaseModel):
    date: datetime
    amount: Decimal
    purpose: str | None
    bank: str


class SheetPayout(BaseModel):
    """Строка плана выплат: день (10/25/из графика фикса) месяца, следующего за расчётным."""

    pay_day: int
    pay_date: date
    amount: Decimal
    paid: bool
    paid_amount: Decimal | None
    comment: str | None


class SheetEmployee(BaseModel):
    employee_id: int
    name: str
    counterparty_id: int | None
    team_accruals: list[SheetTeamAccrual]
    fixed_accrual: Decimal
    accrued_total: Decimal
    official_paid: Decimal  # факт из выписки за месяц выплат (следующий за расчётным)
    official_txns: list[OfficialTxn]
    unofficial_due: Decimal  # accrued_total − official_paid
    payouts: list[SheetPayout]


class SheetWeekRef(BaseModel):
    date_from: date
    date_to: date


class SheetTotals(BaseModel):
    accrued_total: Decimal
    official_total: Decimal
    unofficial_total: Decimal


class PayrollSheetResponse(BaseModel):
    month: str  # 'YYYY-MM'
    weeks: list[SheetWeekRef]  # недели Пн-Вс, привязанные к месяцу (по четвергу)
    teams: list[SheetTeam]
    employees: list[SheetEmployee]
    totals: SheetTotals


# ─── Отметки выплат ──────────────────────────────────────────────────────────


class PayoutMarkIn(BaseModel):
    employee_id: int
    month: str  # 'YYYY-MM' — расчётный месяц
    pay_day: int = Field(..., ge=1, le=28)
    paid: bool
    amount: Decimal | None = Field(None, ge=0)
    comment: str | None = None

    @field_validator("month")
    @classmethod
    def validate_month(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m")
        except ValueError as exc:
            raise ValueError("month must be 'YYYY-MM'") from exc
        return v


class OkResponse(BaseModel):
    ok: bool = True

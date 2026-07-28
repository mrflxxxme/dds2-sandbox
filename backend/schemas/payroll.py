"""
Payroll schemas: сотрудники, команды, тарифная лестница, месячная ведомость.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ALLOWED_SCOPE_KINDS = ["brand", "subject"]

# ─── Сотрудники ──────────────────────────────────────────────────────────────


class PayDayShare(BaseModel):
    """Строка графика выплат фикс-оклада: день месяца и доля."""

    day: int = Field(..., ge=1, le=28)
    share: Decimal = Field(..., gt=0, le=1)


class SalaryPeriodIn(BaseModel):
    """Период оклада: с месяца month действует amount (история изменений)."""

    month: str  # 'YYYY-MM'
    amount: Decimal = Field(..., ge=0)

    @field_validator("month")
    @classmethod
    def validate_month(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m")
        except ValueError as exc:
            raise ValueError("month must be 'YYYY-MM'") from exc
        return v


class SalaryPeriodOut(BaseModel):
    month: str
    amount: Decimal


class EmployeeIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    position: str | None = Field(None, max_length=100)
    counterparty_id: int | None = None
    # История фикс-окладов; оклад месяца = период с max(month) <= месяц,
    # до первого периода — 0.
    salary_periods: list[SalaryPeriodIn] | None = None
    # None — дефолт 50/50 на 10-е и 25-е; сумма долей должна быть равна 1.
    fixed_pay_days: list[PayDayShare] | None = None
    is_active: bool = True
    notes: str | None = None

    @field_validator("salary_periods")
    @classmethod
    def validate_periods(cls, v: list[SalaryPeriodIn] | None) -> list[SalaryPeriodIn] | None:
        if v:
            months = [p.month for p in v]
            if len(months) != len(set(months)):
                raise ValueError("salary_periods months must be unique")
        return v

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
    position: str | None = Field(None, max_length=100)
    clear_position: bool = False
    counterparty_id: int | None = None
    clear_counterparty: bool = False
    # None — не трогать; список (в т.ч. пустой) — полная замена истории.
    salary_periods: list[SalaryPeriodIn] | None = None
    fixed_pay_days: list[PayDayShare] | None = None
    is_active: bool | None = None
    notes: str | None = None

    validate_shares = field_validator("fixed_pay_days")(EmployeeIn.validate_shares.__func__)  # type: ignore[arg-type]
    validate_periods = field_validator("salary_periods")(EmployeeIn.validate_periods.__func__)  # type: ignore[arg-type]


class EmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    position: str | None
    counterparty_id: int | None
    counterparty_name: str | None = None
    salary_periods: list[SalaryPeriodOut] = []
    # Оклад, действующий в текущем месяце (для таблицы; None — нет периода).
    current_salary: Decimal | None = None
    fixed_pay_days: list[PayDayShare] | None
    is_active: bool
    notes: str | None
    team_names: list[str] = []


class EmployeeListResponse(BaseModel):
    items: list[EmployeeResponse]


# ─── Команды ─────────────────────────────────────────────────────────────────


class TeamScopeIn(BaseModel):
    """
    Скоуп: бренд, категория или пересечение (бренд × категория).

    Композит (оба поля) вытесняет одноимённые brand-only/subject-only скоупы
    других команд: их база считается за вычетом закреплённых композитов.
    """

    brand: str | None = Field(None, min_length=1, max_length=300)
    subject: str | None = Field(None, min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_at_least_one(self) -> "TeamScopeIn":
        # model_validator: field-валидатор не запускается на ОПУЩЕННОМ поле
        # с дефолтом — пустой скоуп {} проходил бы валидацию.
        if self.brand is None and self.subject is None:
            raise ValueError("scope requires brand and/or subject")
        return self


class TeamIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    is_active: bool = True


class TeamUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    is_active: bool | None = None


class TeamScopesReplace(BaseModel):
    scopes: list[TeamScopeIn]


class TeamMemberIn(BaseModel):
    """Участие в команде с границами по месяцам (None = без границы)."""

    employee_id: int
    from_month: str | None = None  # 'YYYY-MM'
    to_month: str | None = None  # 'YYYY-MM' включительно

    @field_validator("from_month", "to_month")
    @classmethod
    def validate_months(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                datetime.strptime(v, "%Y-%m")
            except ValueError as exc:
                raise ValueError("month must be 'YYYY-MM'") from exc
        return v


class TeamMembersReplace(BaseModel):
    members: list[TeamMemberIn]

    @field_validator("members")
    @classmethod
    def validate_unique(cls, v: list[TeamMemberIn]) -> list[TeamMemberIn]:
        ids = [m.employee_id for m in v]
        if len(ids) != len(set(ids)):
            raise ValueError("employee_ids must be unique")
        return v


class TeamMemberOut(BaseModel):
    employee_id: int
    name: str
    from_month: str | None = None
    to_month: str | None = None


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
    position: str | None = None
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


# ─── Агентство (консалтинг) ──────────────────────────────────────────────────

ALLOWED_BILLING_MODES = ["fixed", "percent", "profit_share"]
ALLOWED_ENTRY_KINDS = ["week_base", "month_profit"]


class ClientBillingPeriodIn(BaseModel):
    """Период формата оплаты: с месяца month действует billing_mode с параметрами."""

    month: str  # 'YYYY-MM'
    billing_mode: str
    fixed_amount: Decimal | None = Field(None, ge=0)  # для fixed
    fee_percent: Decimal | None = Field(None, ge=0, le=1)  # доля от ЧП (profit_share)

    @field_validator("month")
    @classmethod
    def validate_month(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m")
        except ValueError as exc:
            raise ValueError("month must be 'YYYY-MM'") from exc
        return v

    @field_validator("billing_mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ALLOWED_BILLING_MODES:
            raise ValueError(f"billing_mode must be one of: {ALLOWED_BILLING_MODES}")
        return v


class ClientBillingPeriodOut(BaseModel):
    month: str
    billing_mode: str
    fixed_amount: Decimal | None
    fee_percent: Decimal | None


def _validate_billing_periods(
    v: list[ClientBillingPeriodIn] | None,
) -> list[ClientBillingPeriodIn] | None:
    if v:
        months = [p.month for p in v]
        if len(months) != len(set(months)):
            raise ValueError("billing_periods months must be unique")
    return v


class ClientProjectIn(BaseModel):
    """
    Клиент агентства. Сплит: manager_share команде, остаток агентству.

    Формат оплаты — историей периодов: формат месяца M = период с
    max(month) <= M; до первого периода начисления нет («у Брыссина раньше
    был процент, с июля оклад» — два периода).
    """

    name: str = Field(..., min_length=1, max_length=200)
    team_id: int | None = None
    linked_project_id: int | None = None  # кабинет клиента в системе (percent)
    billing_periods: list[ClientBillingPeriodIn] | None = None
    manager_share: Decimal = Field(Decimal("0.45"), ge=0, le=1)
    is_active: bool = True
    notes: str | None = None

    validate_billing = field_validator("billing_periods")(_validate_billing_periods)


class ClientProjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    team_id: int | None = None
    clear_team: bool = False
    linked_project_id: int | None = None
    clear_linked_project: bool = False
    # None — не трогать; список (в т.ч. пустой) — полная замена истории.
    billing_periods: list[ClientBillingPeriodIn] | None = None
    manager_share: Decimal | None = Field(None, ge=0, le=1)
    is_active: bool | None = None
    notes: str | None = None

    validate_billing = field_validator("billing_periods")(_validate_billing_periods)


class ClientEntryOut(BaseModel):
    kind: str
    date_from: date
    amount: Decimal


class ClientProjectResponse(BaseModel):
    id: int
    name: str
    team_id: int | None
    team_name: str | None = None
    linked_project_id: int | None
    linked_project_name: str | None = None
    billing_periods: list[ClientBillingPeriodOut] = []
    # Формат, действующий в текущем месяце (для карточки; None — нет периода).
    current_billing: ClientBillingPeriodOut | None = None
    manager_share: Decimal
    is_active: bool
    notes: str | None
    entries: list[ClientEntryOut] = []


class ClientProjectListResponse(BaseModel):
    items: list[ClientProjectResponse]


class ClientEntryUpsert(BaseModel):
    """Ручная сумма: недельная база внешнего кабинета либо ЧП месяца."""

    kind: str
    date_from: date  # week_base — понедельник недели; month_profit — 1-е число
    amount: Decimal = Field(..., ge=0)

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        if v not in ALLOWED_ENTRY_KINDS:
            raise ValueError(f"kind must be one of: {ALLOWED_ENTRY_KINDS}")
        return v


class ProjectOption(BaseModel):
    """Проект инсталляции для привязки кабинета клиента."""

    id: int
    name: str
    slug: str


class ProjectOptionsResponse(BaseModel):
    items: list[ProjectOption]


class AgencyClientWeek(BaseModel):
    """Неделя percent-режима: база, ступень, ставка «Команда», fee."""

    date_from: date
    date_to: date
    base_amount: Decimal
    threshold: Decimal | None
    team_rate: Decimal
    fee: Decimal
    manual: bool  # база введена руками (внешний кабинет)
    has_entry: bool = False  # ручная запись week_base реально существует (0 ≠ «не введено»)


class AgencySheetClient(BaseModel):
    client_id: int
    name: str
    # Формат оплаты, действовавший В РАСЧЁТНОМ месяце (None — период не задан).
    billing_mode: str | None
    team_id: int | None
    team_name: str | None
    manager_share: Decimal
    weeks: list[AgencyClientWeek] = []  # percent
    profit_amount: Decimal | None = None  # profit_share: введённая ЧП месяца
    fee_percent: Decimal | None = None
    fee_total: Decimal
    manager_amount: Decimal  # уходит команде (в ведомость и ФОТ «Менеджеры»)
    agency_amount: Decimal  # остаток агентству (в ОПиУ пока не включается)
    # Не хватает данных: нет команды / нет ЧП месяца / нет недельных баз внешнего.
    warnings: list[str] = []


class AgencySheetResponse(BaseModel):
    month: str
    clients: list[AgencySheetClient]
    totals_fee: Decimal
    totals_manager: Decimal
    totals_agency: Decimal


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

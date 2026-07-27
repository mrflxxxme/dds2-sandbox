"""
Loan schemas: CRUD + LoanPaymentMatch + LoanDetail.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─── Allowed enum values ─────────────────────────────────────────────────────

ALLOWED_DIRECTIONS = ["INCOMING", "OUTGOING", "AFFILIATED"]
ALLOWED_STATUSES = ["ACTIVE", "CLOSED", "DEFAULTED"]
ALLOWED_PAYMENT_TYPES = ["DISBURSEMENT", "PRINCIPAL_REPAY", "INTEREST_PAY", "PENALTY", "COMMISSION"]
ALLOWED_ENTITY_TYPES = ["PHYSICAL", "IP"]

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
    entity_type: str | None = Field(default=None)  # PHYSICAL / IP
    lender_bank: str | None = Field(default=None, max_length=50)
    parent_loan_id: int | None = None
    notes: str | None = None

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        if v not in ALLOWED_DIRECTIONS:
            raise ValueError(f"direction must be one of: {ALLOWED_DIRECTIONS}")
        return v

    @field_validator("entity_type")
    @classmethod
    def validate_entity_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_ENTITY_TYPES:
            raise ValueError(f"entity_type must be one of: {ALLOWED_ENTITY_TYPES}")
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
    entity_type: str | None = None
    lender_bank: str | None = Field(None, max_length=50)
    notes: str | None = None

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_DIRECTIONS:
            raise ValueError(f"direction must be one of: {ALLOWED_DIRECTIONS}")
        return v

    @field_validator("entity_type")
    @classmethod
    def validate_entity_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_ENTITY_TYPES:
            raise ValueError(f"entity_type must be one of: {ALLOWED_ENTITY_TYPES}")
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
    """Full loan response (with optional computed/enrichment fields for the registry)."""

    id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Enrichment (populated by the service for list/detail views; None elsewhere)
    counterparty_name: str | None = None
    counterparty_inn: str | None = None
    remaining_principal: Decimal | None = None  # тело за вычетом возвратов
    accrued_interest: Decimal | None = None  # начислено в текущем периоде 25→25
    interest_due_period: Decimal | None = None  # за весь период — платёж на дату выплаты
    accrual_period_start: date | None = None  # начало периода начисления
    accrual_period_end: date | None = None  # дата выплаты = метка периода
    monthly_interest: Decimal | None = None  # ставка/12 от остатка (run-rate)
    days_to_maturity: int | None = None  # до возврата (может быть отрицательным = просрочка)
    next_interest_date: date | None = None  # ближайшая дата выплаты %
    has_extension: bool = False  # есть ли продление (этот займ — предшественник)

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
    entity_type: str | None = None
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


# ─── Extend (продление) ──────────────────────────────────────────────────────


class LoanRepay(BaseModel):
    """Возврат тела займа: частичный или полный (полный закрывает займ)."""

    amount: Decimal | None = Field(None, gt=0)  # None = весь остаток
    paid_at: date | None = None  # None = сегодня
    close: bool = True  # закрыть займ, если возвращён весь остаток


class LoanExtend(BaseModel):
    """Продление займа: закрыть текущий и создать преемника с новой ставкой/сроком."""

    new_rate: Decimal | None = Field(None, ge=0, le=1)  # None = та же ставка
    new_start_date: date | None = None  # дата продления (по умолч. maturity старого / сегодня)
    new_maturity_date: date | None = None  # новый срок возврата
    term_months: int | None = Field(None, ge=1, le=36)  # пресет срока: 1/2/3 мес от даты продления
    new_contract_number: str | None = Field(None, max_length=100)  # None = авто-суффикс «-NN»
    principal: Decimal | None = Field(None, gt=0)  # None = остаток тела старого
    record_repayment: bool = True  # отметить возврат тела на старом (закрытие)
    entity_type: str | None = None
    lender_bank: str | None = Field(None, max_length=50)
    notes: str | None = None

    @field_validator("entity_type")
    @classmethod
    def validate_entity_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_ENTITY_TYPES:
            raise ValueError(f"entity_type must be one of: {ALLOWED_ENTITY_TYPES}")
        return v


# ─── Dashboard ───────────────────────────────────────────────────────────────


class LoanKpis(BaseModel):
    active_count: int = 0
    total_outstanding: Decimal = Decimal("0")  # активное тело (INCOMING)
    weighted_avg_rate: Decimal | None = None
    accrued_interest: Decimal = Decimal("0")  # начисленные неоплаченные %
    monthly_interest: Decimal = Decimal("0")  # run-rate %/мес по активным
    interest_paid_total: Decimal = Decimal("0")
    lenders_count: int = 0
    next_maturity_date: date | None = None
    next_maturity_amount: Decimal = Decimal("0")


class LoanEntitySplit(BaseModel):
    entity_type: str | None = None  # PHYSICAL / IP / None
    count: int = 0
    outstanding: Decimal = Decimal("0")
    accrued_interest: Decimal = Decimal("0")  # начислено в текущем периоде 25→25
    interest_due_period: Decimal = Decimal("0")  # к выплате за весь период


class LoanRateBucket(BaseModel):
    rate: Decimal
    count: int = 0
    outstanding: Decimal = Decimal("0")


class LoanMonthlyPoint(BaseModel):
    month: str  # YYYY-MM
    disbursed: Decimal = Decimal("0")  # выдано тело
    repaid: Decimal = Decimal("0")  # возвращено тело
    outstanding: Decimal = Decimal("0")  # остаток тела на конец месяца
    interest: Decimal = Decimal("0")  # начислено % за месяц


class LoanTopLender(BaseModel):
    counterparty_id: int
    name: str
    outstanding: Decimal = Decimal("0")
    accrued_interest: Decimal = Decimal("0")
    weighted_avg_rate: Decimal | None = None


class LoanDashboard(BaseModel):
    kpis: LoanKpis = Field(default_factory=LoanKpis)
    by_entity: list[LoanEntitySplit] = Field(default_factory=list)
    by_rate: list[LoanRateBucket] = Field(default_factory=list)
    monthly: list[LoanMonthlyPoint] = Field(default_factory=list)
    top_lenders: list[LoanTopLender] = Field(default_factory=list)


# ─── By-lender rollup (Заёмщики) ─────────────────────────────────────────────


class LoanLenderRollup(BaseModel):
    counterparty_id: int
    name: str
    inn: str | None = None
    entity_type: str | None = None  # преобладающий по активным
    lender_bank: str | None = None
    active_count: int = 0
    total_count: int = 0
    outstanding: Decimal = Decimal("0")
    weighted_avg_rate: Decimal | None = None
    accrued_interest: Decimal = Decimal("0")
    interest_paid: Decimal = Decimal("0")
    monthly_interest: Decimal = Decimal("0")
    next_interest_date: date | None = None
    next_maturity_date: date | None = None
    first_loan_date: date | None = None
    last_loan_date: date | None = None
    has_portal_access: bool = False
    interest_due_period: Decimal = Decimal("0")  # к выплате за текущий период
    # Архив вычисляемый: нет ни одного активного займа. Флага в БД нет — контрагент
    # остаётся живым в остальных разделах, скрывается только в «Займах».
    is_archived: bool = False


class LoanByLenderResponse(BaseModel):
    items: list[LoanLenderRollup] = Field(default_factory=list)
    total_outstanding: Decimal = Decimal("0")
    total_accrued: Decimal = Decimal("0")
    total_due_period: Decimal = Decimal("0")
    by_entity: list[LoanEntitySplit] = Field(default_factory=list)
    accrual_period_start: date | None = None
    accrual_period_end: date | None = None
    archived_count: int = 0


# ─── Forecast (Прогноз) ──────────────────────────────────────────────────────


class LoanForecastEvent(BaseModel):
    date: date
    loan_id: int
    counterparty_id: int
    counterparty_name: str
    contract_number: str
    kind: str  # 'INTEREST' | 'MATURITY'
    amount: Decimal = Decimal("0")


class LoanForecastMonth(BaseModel):
    month: str  # YYYY-MM
    interest: Decimal = Decimal("0")  # начисляемые % за месяц
    principal_due: Decimal = Decimal("0")  # тело к возврату по срокам


class LoanForecastResponse(BaseModel):
    months: list[LoanForecastMonth] = Field(default_factory=list)
    upcoming: list[LoanForecastEvent] = Field(default_factory=list)


# ─── Lender portal access (выдача доступа заёмщику) ───────────────────────────


class LenderAccessCreate(BaseModel):
    counterparty_id: int
    username: str | None = Field(None, max_length=100)  # авто из имени если не задан
    password: str | None = Field(None, min_length=6, max_length=100)  # авто-генерация если не задан


class LenderAccessInfo(BaseModel):
    counterparty_id: int
    counterparty_name: str
    user_id: int | None = None
    username: str | None = None
    is_active: bool = False
    created_at: datetime | None = None


class LenderAccessCreated(LenderAccessInfo):
    password: str | None = None  # plaintext — показывается ОДИН раз при создании/сбросе


class LenderAccessListResponse(BaseModel):
    items: list[LenderAccessInfo] = Field(default_factory=list)


# ─── Excel import ────────────────────────────────────────────────────────────


class LoanImportResult(BaseModel):
    created_loans: int = 0
    updated_loans: int = 0
    created_counterparties: int = 0
    created_payments: int = 0
    skipped_rows: int = 0
    lenders: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LoanAccrualMonth(BaseModel):
    """Начислено за КАЛЕНДАРНЫЙ месяц — строка для ОПиУ."""

    month: str  # YYYY-MM
    interest: Decimal = Decimal("0")
    fee: Decimal = Decimal("0")  # комиссии (за резерв лимита и т. п.)
    total: Decimal = Decimal("0")
    ip: Decimal = Decimal("0")
    physical: Decimal = Decimal("0")
    avg_body: Decimal = Decimal("0")  # средний остаток тела за месяц (по дням)
    effective_rate: Decimal | None = None  # во сколько реально обошлись деньги, годовых
    days: int = 0


class LoanAccrualMonthsResponse(BaseModel):
    items: list[LoanAccrualMonth] = Field(default_factory=list)
    total_interest: Decimal = Decimal("0")
    total_fee: Decimal = Decimal("0")
    # Средняя стоимость денег за весь показанный отрезок, годовых
    effective_rate: Decimal | None = None


# ─── Кредитная линия (ВКЛ) ───────────────────────────────────────────────────


class LoanRatePeriodIn(BaseModel):
    """Ставка, действующая с даты. Для плавающих: ключевая ЦБ + надбавка."""

    valid_from: date
    rate: Decimal = Field(..., ge=0, le=1)  # итоговая годовая доля: 0.195
    base_rate: Decimal | None = Field(None, ge=0, le=1)  # ключевая ЦБ
    spread: Decimal | None = Field(None, ge=0, le=1)  # надбавка банка
    note: str | None = Field(None, max_length=200)


class LoanRatePeriodResponse(LoanRatePeriodIn):
    id: int
    loan_id: int

    model_config = ConfigDict(from_attributes=True)


class CreditLineDraw(BaseModel):
    """Выборка транша по кредитной линии — увеличивает тело долга."""

    amount: Decimal = Field(..., gt=0)
    drawn_at: date
    note: str | None = Field(None, max_length=200)


class CreditLineMovement(BaseModel):
    """Движение по линии: выборка или погашение."""

    payment_id: int
    kind: str  # DISBURSEMENT (выборка) / PRINCIPAL_REPAY (погашение)
    amount: Decimal
    happened_at: date
    balance_after: Decimal  # тело линии после движения


class CreditLineDetail(BaseModel):
    """Состояние кредитной линии: лимит, выборка, свободный остаток, движения."""

    loan_id: int
    contract_number: str
    counterparty_name: str | None = None
    credit_limit: Decimal | None = None
    drawn: Decimal = Decimal("0")  # выбрано тело сейчас
    available: Decimal | None = None  # лимит − выбрано (None, если лимита нет)
    utilization: Decimal | None = None  # доля выборки лимита, 0..1
    current_rate: Decimal | None = None  # ставка на сегодня
    accrual_kind: str = "PERIOD_25"
    accrual_period_start: date | None = None
    accrual_period_end: date | None = None
    accrued_interest: Decimal = Decimal("0")  # набежало в текущем периоде
    interest_due_period: Decimal = Decimal("0")  # за весь период
    interest_paid: Decimal = Decimal("0")
    # Комиссия за НЕиспользованный лимит: банк берёт плату за резерв
    unused_limit_rate: Decimal | None = None
    unused_fee_accrued: Decimal = Decimal("0")  # набежало на сегодня
    unused_fee_period: Decimal = Decimal("0")  # за весь период
    commission_paid: Decimal = Decimal("0")  # уплачено комиссий всего
    total_cost_period: Decimal = Decimal("0")  # проценты + комиссия за период
    payment_due_date: date | None = None  # когда платить за этот период
    status: str = "ACTIVE"
    maturity_date: date | None = None
    movements: list[CreditLineMovement] = Field(default_factory=list)
    rate_periods: list[LoanRatePeriodResponse] = Field(default_factory=list)


class CreditLineListResponse(BaseModel):
    """Все кредитные линии проекта со сводкой."""

    items: list[CreditLineDetail] = Field(default_factory=list)
    total_limit: Decimal = Decimal("0")
    total_drawn: Decimal = Decimal("0")
    total_available: Decimal = Decimal("0")
    total_due_period: Decimal = Decimal("0")


# ─── Зависшие займы (срок вышел, решения нет) ────────────────────────────────


class LoanStuckItem(BaseModel):
    """Займ, по которому срок кончился, а решения не приняли."""

    loan: LoanResponse
    days_overdue: int
    accrued_since_maturity: Decimal = Decimal("0")  # набежало после срока


class LoanStuckResponse(BaseModel):
    items: list[LoanStuckItem] = Field(default_factory=list)
    total_amount: Decimal = Decimal("0")  # остаток тела зависших
    total_accrued_since_maturity: Decimal = Decimal("0")
    count: int = 0


# ─── Карточка заёмщика (клик по имени в «Заёмщиках») ─────────────────────────


class LenderPeriodPoint(BaseModel):
    """Проценты за один период начисления 25→25 (метка = дата выплаты)."""

    period_end: date  # дата выплаты и метка периода
    period_start: date
    interest: Decimal = Decimal("0")
    is_current: bool = False  # период ещё не закрыт
    is_forecast: bool = False  # будущий период


class LenderDetail(BaseModel):
    """Всё по одному заёмщику: итоги, история займов, проценты по периодам."""

    counterparty_id: int
    name: str
    inn: str | None = None
    entity_type: str | None = None
    lender_bank: str | None = None
    is_archived: bool = False

    active_count: int = 0
    total_count: int = 0
    outstanding: Decimal = Decimal("0")
    weighted_avg_rate: Decimal | None = None
    principal_total: Decimal = Decimal("0")  # сколько всего заносил за историю
    principal_repaid: Decimal = Decimal("0")
    interest_paid: Decimal = Decimal("0")
    accrued_interest: Decimal = Decimal("0")
    interest_due_period: Decimal = Decimal("0")
    accrual_period_start: date | None = None
    accrual_period_end: date | None = None
    first_loan_date: date | None = None
    last_loan_date: date | None = None
    next_maturity_date: date | None = None
    has_portal_access: bool = False

    loans: list[LoanResponse] = Field(default_factory=list)
    periods: list[LenderPeriodPoint] = Field(default_factory=list)

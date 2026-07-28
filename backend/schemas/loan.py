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
ALLOWED_FEE_KINDS = ["ORIGINATION", "LIMIT_SETUP", "OTHER"]

# ─── Loan ─────────────────────────────────────────────────────────────────────


class LoanBase(BaseModel):
    counterparty_id: int
    direction: str = Field(default="INCOMING")
    # Ноль допустим: у револьверных займов (кредитная линия, займ учредителя)
    # тело задают движения — выборки и возвраты, — а `principal` символический.
    # Запрет на ноль заставлял заводить фиктивный рубль, чтобы пройти валидацию.
    principal: Decimal = Field(..., ge=0)
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
    # Симметрично LoanBase: у револьверных займов тело задают движения.
    principal: Decimal | None = Field(None, ge=0)
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
    # Вторая сторона того же договора в другом проекте (займ между своими юрлицами)
    mirror_loan_id: int | None = None
    mirror_project_id: int | None = None
    mirror_project_name: str | None = None

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
    # Дашборд отвечает на вопрос «сколько стоят деньги», поэтому считает по
    # КАЛЕНДАРНОМУ месяцу: периоды выплат у продуктов разные (25→25, месяц+5-е,
    # аннуитет) и в один итог не складываются.
    accrued_interest: Decimal = Decimal("0")  # проценты с 1-го числа по сегодня
    accrued_fee: Decimal = Decimal("0")  # комиссии за тот же отрезок
    accrued_cost: Decimal = Decimal("0")  # проценты + комиссии (стоимость денег)
    month_cost_projected: Decimal = Decimal("0")  # прогноз до конца месяца
    effective_rate: Decimal | None = None  # во сколько обошлись деньги, годовых
    accrual_month: str | None = None  # YYYY-MM — какой месяц считаем
    monthly_interest: Decimal = Decimal("0")  # run-rate %/мес по активным
    interest_paid_total: Decimal = Decimal("0")
    # Начислено за ВСЁ время минус уплачено: сколько процентов реально висит.
    # У банков почти ноль (платят по графику), у займа учредителя копится годами.
    interest_debt: Decimal = Decimal("0")
    lenders_count: int = 0
    next_maturity_date: date | None = None
    next_maturity_amount: Decimal = Decimal("0")


class LoanEntitySplit(BaseModel):
    entity_type: str | None = None  # PHYSICAL / IP / None
    count: int = 0
    outstanding: Decimal = Decimal("0")
    # На «Заёмщиках» это период выплат (25→25), на дашборде — календарный месяц:
    # экраны отвечают на разные вопросы («кому платить» vs «сколько стоят деньги»).
    accrued_interest: Decimal = Decimal("0")  # начислено на сегодня
    interest_due_period: Decimal = Decimal("0")  # за весь период / месяц


class LoanRateBucket(BaseModel):
    rate: Decimal
    count: int = 0
    outstanding: Decimal = Decimal("0")


class LoanMonthlyPoint(BaseModel):
    month: str  # YYYY-MM — КАЛЕНДАРНЫЙ месяц
    disbursed: Decimal = Decimal("0")  # выдано тело (вкл. выборки по линии)
    repaid: Decimal = Decimal("0")  # возвращено тело
    outstanding: Decimal = Decimal("0")  # остаток тела на конец месяца
    interest: Decimal = Decimal("0")  # начислено % за месяц
    fee: Decimal = Decimal("0")  # комиссии за месяц (резерв лимита + разовые)
    cost: Decimal = Decimal("0")  # проценты + комиссии
    is_partial: bool = False  # текущий месяц: посчитан не до конца


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
    # Начислено за всё время минус уплачено — «сколько процентов должны этому».
    interest_debt: Decimal = Decimal("0")
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
    total_interest_debt: Decimal = Decimal("0")
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


# ─── Разовые комиссии ────────────────────────────────────────────────────────


class LoanFeeIn(BaseModel):
    """
    Разовая комиссия по займу.

    `amortize` — размазывать ли расход по сроку договора. По умолчанию да:
    комиссия за выдачу и за лимит — плата за доступ к деньгам на весь срок.
    Касса от этого не меняется, меняется только «сколько стоят деньги».
    """

    fee_kind: str = "OTHER"
    amount: Decimal = Field(..., gt=0)
    charged_at: date
    amortize: bool = True
    amortize_from: date | None = None
    amortize_to: date | None = None
    payment_id: int | None = None
    note: str | None = Field(None, max_length=200)

    @field_validator("fee_kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in ALLOWED_FEE_KINDS:
            raise ValueError(f"fee_kind must be one of {ALLOWED_FEE_KINDS}")
        return v


class LoanFeeResponse(LoanFeeIn):
    id: int
    loan_id: int

    model_config = ConfigDict(from_attributes=True)


class LoanFeeListResponse(BaseModel):
    items: list[LoanFeeResponse] = Field(default_factory=list)
    total: Decimal = Decimal("0")


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
    commission_paid: Decimal = Decimal("0")  # уплачено комиссий всего (касса)
    # Разовые комиссии (за установление лимита): всего по договору и доля,
    # приходящаяся на текущий период при амортизации на срок.
    one_off_fee_total: Decimal = Decimal("0")
    one_off_fee_period: Decimal = Decimal("0")
    total_cost_period: Decimal = Decimal("0")  # проценты + все комиссии за период
    payment_due_date: date | None = None  # когда платить за этот период
    status: str = "ACTIVE"
    maturity_date: date | None = None
    movements: list[CreditLineMovement] = Field(default_factory=list)
    rate_periods: list[LoanRatePeriodResponse] = Field(default_factory=list)
    fees: list[LoanFeeResponse] = Field(default_factory=list)


class CreditLineListResponse(BaseModel):
    """Все кредитные линии проекта со сводкой."""

    items: list[CreditLineDetail] = Field(default_factory=list)
    total_limit: Decimal = Decimal("0")
    total_drawn: Decimal = Decimal("0")
    total_available: Decimal = Decimal("0")
    total_due_period: Decimal = Decimal("0")  # только проценты
    total_fee_period: Decimal = Decimal("0")  # комиссии за период (резерв + разовые)
    total_cost_period: Decimal = Decimal("0")  # полная стоимость периода


# ─── График платежей (аннуитет) ──────────────────────────────────────────────


class LoanScheduleRowIn(BaseModel):
    """Строка планового графика — как её выдал кредитор."""

    seq: int = Field(..., ge=1)
    period_start: date | None = None
    period_end: date | None = None
    due_date: date
    days: int | None = Field(None, ge=0)
    principal_due: Decimal = Field(Decimal("0"), ge=0)
    interest_due: Decimal = Field(Decimal("0"), ge=0)
    payment_total: Decimal = Field(Decimal("0"), ge=0)
    balance_after: Decimal | None = Field(None, ge=0)
    # Строка-комиссия: в графике напечатана в колонке процентов, но в стоимость
    # денег заходит через `LoanFee` (амортизацией), а не как проценты периода.
    is_fee: bool = False
    # Строка ПЛАНА ПОГАШЕНИЯ накопленного долга, а не графика кредитора: говорит,
    # когда и сколько платить в счёт уже начисленных процентов. Для движка
    # начисления невидима — проценты продолжают капать по ставке займа.
    is_debt_plan: bool = False
    note: str | None = Field(None, max_length=200)


class LoanScheduleReplace(BaseModel):
    """Полная замена графика: график приходит из договора целиком."""

    rows: list[LoanScheduleRowIn] = Field(default_factory=list, max_length=600)


class LoanScheduleRow(LoanScheduleRowIn):
    """Строка графика вместе с фактом: заплатили / сколько / когда."""

    id: int
    principal_paid: Decimal = Decimal("0")
    interest_paid: Decimal = Decimal("0")
    fee_paid: Decimal = Decimal("0")  # комиссии и пени, севшие на эту дату
    paid_total: Decimal = Decimal("0")
    paid_at: date | None = None  # дата последнего платежа по строке
    delta: Decimal = Decimal("0")  # факт − план (минус = недоплата)
    days_late: int | None = None  # + опоздание, − заплатили раньше срока
    # PAID / PARTIAL / OVERDUE / DUE (ближайший) / UPCOMING
    state: str = "UPCOMING"


class LoanScheduleResponse(BaseModel):
    """График платежей со сверкой план/факт."""

    loan_id: int
    contract_number: str | None = None
    rows: list[LoanScheduleRow] = Field(default_factory=list)
    total_principal: Decimal = Decimal("0")
    total_interest: Decimal = Decimal("0")
    total_payment: Decimal = Decimal("0")
    paid_principal: Decimal = Decimal("0")
    paid_interest: Decimal = Decimal("0")
    paid_total: Decimal = Decimal("0")
    left_principal: Decimal = Decimal("0")  # тело по графику, ещё не погашенное
    left_interest: Decimal = Decimal("0")
    left_total: Decimal = Decimal("0")
    next_due_date: date | None = None
    next_due_amount: Decimal = Decimal("0")
    overdue_count: int = 0
    overdue_amount: Decimal = Decimal("0")
    # Полная стоимость: план процентов + разовые комиссии
    total_fees: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")


# ─── Займ между своими проектами (зеркало) ───────────────────────────────────


class LoanMirrorCreate(BaseModel):
    """
    Завести вторую сторону договора в другом проекте.

    Направление переворачивается само: полученный займ у одного — выданный у
    другого. Контрагент второй стороны — это мы сами в её книге.
    """

    target_project_id: int
    counterparty_id: int | None = None  # кем мы числимся в чужой книге
    counterparty_name: str | None = Field(None, max_length=500)  # если создавать
    counterparty_inn: str | None = Field(None, max_length=12)


class LoanMirrorSide(BaseModel):
    """Одна сторона договора: чья книга, что в ней числится."""

    loan_id: int
    project_id: int
    project_name: str | None = None
    project_slug: str | None = None
    counterparty_name: str | None = None
    direction: str  # INCOMING (долг) / OUTGOING (актив)
    status: str = "ACTIVE"
    outstanding: Decimal = Decimal("0")
    accrued_interest: Decimal = Decimal("0")  # начислено за календарный месяц
    accrued_total: Decimal = Decimal("0")  # начислено за всё время
    interest_paid: Decimal = Decimal("0")
    interest_debt: Decimal = Decimal("0")  # начислено всего − уплачено
    current_rate: Decimal | None = None


class LoanChainMovement(BaseModel):
    """Движение по договору: выдача тела, возврат, выплата процентов."""

    happened_at: date
    kind: str  # DISBURSEMENT / PRINCIPAL_REPAY / INTEREST_PAY / PENALTY / COMMISSION
    amount: Decimal
    balance_after: Decimal | None = None  # тело после движения (для тела)


class LoanChain(BaseModel):
    """Договор целиком: обе книги, движения и итог."""

    contract_number: str
    contract_date: date | None = None
    start_date: date | None = None
    maturity_date: date | None = None  # пусто = до востребования
    sides: list[LoanMirrorSide] = Field(default_factory=list)
    total_disbursed: Decimal = Decimal("0")
    total_repaid: Decimal = Decimal("0")
    outstanding: Decimal = Decimal("0")
    accrued_total: Decimal = Decimal("0")
    interest_paid: Decimal = Decimal("0")
    interest_debt: Decimal = Decimal("0")
    total_debt: Decimal = Decimal("0")  # тело + долг по процентам
    rate_periods: list[LoanRatePeriodResponse] = Field(default_factory=list)
    movements: list[LoanChainMovement] = Field(default_factory=list)
    # Начисление по календарным месяцам — та же база, что и в ОПиУ
    monthly: list[LoanAccrualMonth] = Field(default_factory=list)
    in_sync: bool = True  # движения обеих сторон совпадают
    sync_note: str | None = None


class LoanChainListResponse(BaseModel):
    items: list[LoanChain] = Field(default_factory=list)
    total_outstanding: Decimal = Decimal("0")
    total_interest_debt: Decimal = Decimal("0")


# ─── Выданные займы (мы кредитор) ────────────────────────────────────────────


class LoanLentItem(BaseModel):
    """Кому мы дали денег и сколько он должен."""

    counterparty_id: int
    name: str
    loan_id: int
    contract_number: str
    rate: Decimal | None = None
    outstanding: Decimal = Decimal("0")  # тело, которое нам должны
    accrued_total: Decimal = Decimal("0")  # начислено процентов за всё время
    interest_received: Decimal = Decimal("0")
    interest_due: Decimal = Decimal("0")  # начислено минус получено
    total_due: Decimal = Decimal("0")  # тело + проценты
    accrued_month: Decimal = Decimal("0")  # начислено в текущем месяце
    mirror_project_name: str | None = None  # если вторая сторона у нас же


class LoanLentResponse(BaseModel):
    """Сводка по выданным займам: сколько нам должны и сколько это приносит."""

    items: list[LoanLentItem] = Field(default_factory=list)
    total_outstanding: Decimal = Decimal("0")
    total_accrued: Decimal = Decimal("0")
    total_received: Decimal = Decimal("0")
    total_interest_due: Decimal = Decimal("0")
    total_due: Decimal = Decimal("0")
    month_income: Decimal = Decimal("0")  # доход за текущий календарный месяц


# ─── Начисление по дням ──────────────────────────────────────────────────────


class LoanAccrualDay(BaseModel):
    """Один день начисления: тело на утро, ставка дня, проценты за сутки."""

    date: date
    balance: Decimal = Decimal("0")  # тело, на которое капало в этот день
    rate: Decimal | None = None  # ставка, действовавшая в этот день
    interest: Decimal = Decimal("0")
    cumulative: Decimal = Decimal("0")  # нарастающим итогом за период
    movement: Decimal | None = None  # движение тела в этот день (+выборка/−возврат)


class LoanAccrualDaysResponse(BaseModel):
    loan_id: int
    contract_number: str | None = None
    date_from: date
    date_to: date
    rows: list[LoanAccrualDay] = Field(default_factory=list)
    total_interest: Decimal = Decimal("0")
    avg_balance: Decimal = Decimal("0")
    effective_rate: Decimal | None = None
    # Проценты взяты из графика (аннуитет), а не из формулы «тело × ставка × дни»
    from_schedule: bool = False


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

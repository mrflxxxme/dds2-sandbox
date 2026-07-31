# ruff: noqa: RUF002, RUF003
"""
Payment-request schemas: создание/правка заявки, документы, события, отгрузки-кандидаты.

Числовые поля Numeric (amount, pickup_cost) сериализуются Pydantic v2 в JSON СТРОКОЙ
— фронт коэрсит Number() перед formatNumber (см. .claude/rules/learnings.md).
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ALLOWED_SOURCES = ["MANUAL", "COUNTERPARTY"]
ALLOWED_PR_DOC_TYPES = ["INVOICE", "ACT"]
# Системные коды категорий (сид pay08). Список «Назначение оплаты» теперь редактируемый
# справочник (PaymentCategory) — существование кода проверяет сервис; здесь только формат.
ALLOWED_CATEGORIES = ["LOGISTICS", "PHOTO_CONTENT", "CUSTOMS", "FULFILLMENT", "DESIGN", "HOUSEHOLD", "OTHER"]

_INN_RE = r"^\d{10,12}$"
_BIK_RE = r"^\d{9}$"
_ACC_RE = r"^\d{20}$"
_CATEGORY_CODE_RE = r"^[A-Z0-9_]{1,30}$"  # формат кода категории (существование — в сервисе)


def _check_category_code(v: str | None) -> str | None:
    import re

    if v is not None and not re.match(_CATEGORY_CODE_RE, v):
        raise ValueError("category: недопустимый код категории")
    return v


# ─── Write models ─────────────────────────────────────────────────────────────


class PaymentRequestCreate(BaseModel):
    """Создание заявки. source=COUNTERPARTY + outbound_shipment_id → авто-заполнение реквизитов/суммы.

    project_id: целевой проект. Поле НЕ передано → текущий проект (из X-Project-Id).
    Передано как null → «общая» заявка без проекта. Передано числом → этот проект
    (роутер проверяет членство). Различение «не передано / null» — через model_fields_set.
    category: назначение оплаты (LOGISTICS/PHOTO_CONTENT/CUSTOMS/OTHER).
    """

    source: str = Field(default="MANUAL")
    project_id: int | None = None
    category: str | None = None
    outbound_shipment_id: int | None = None
    # Несколько заборов на одну оплату (один счёт за две отгрузки) — одного перевозчика.
    # Если задано, сумма = Σ pickup_cost заборов, outbound_shipment_id = первый.
    outbound_shipment_ids: list[int] | None = None
    counterparty_id: int | None = None

    payee_inn: str | None = Field(None, pattern=_INN_RE)
    payee_kpp: str | None = Field(None, pattern=_BIK_RE)
    payee_account: str | None = Field(None, pattern=_ACC_RE)
    payee_bik: str | None = Field(None, pattern=_BIK_RE)
    payee_bank_name: str | None = Field(None, max_length=255)
    payee_corr_account: str | None = Field(None, pattern=_ACC_RE)
    payee_name: str | None = Field(None, max_length=500)

    amount: Decimal | None = Field(None, gt=0)
    currency: str = Field(default="RUB", max_length=3)
    pickup_date: date | None = None
    purpose: str | None = None
    # Бренд-атрибуция. None/не передано → «Все бренды» (общий расход по проекту).
    brand: str | None = Field(None, max_length=200)

    @field_validator("source")
    @classmethod
    def _validate_source(cls, v: str) -> str:
        if v not in ALLOWED_SOURCES:
            raise ValueError(f"source must be one of: {ALLOWED_SOURCES}")
        return v

    @field_validator("category")
    @classmethod
    def _validate_category(cls, v: str | None) -> str | None:
        return _check_category_code(v)


class PaymentRequestUpdate(BaseModel):
    """PATCH черновика — все поля опциональны (source не меняем)."""

    counterparty_id: int | None = None
    category: str | None = None
    payee_inn: str | None = Field(None, pattern=_INN_RE)
    payee_kpp: str | None = Field(None, pattern=_BIK_RE)
    payee_account: str | None = Field(None, pattern=_ACC_RE)
    payee_bik: str | None = Field(None, pattern=_BIK_RE)
    payee_bank_name: str | None = Field(None, max_length=255)
    payee_corr_account: str | None = Field(None, pattern=_ACC_RE)
    payee_name: str | None = Field(None, max_length=500)
    amount: Decimal | None = Field(None, gt=0)
    currency: str | None = Field(None, max_length=3)
    pickup_date: date | None = None
    purpose: str | None = None
    # Бренд-атрибуция. null → сбросить в «Все бренды»; не передано → не менять.
    brand: str | None = Field(None, max_length=200)

    @field_validator("category")
    @classmethod
    def _validate_category(cls, v: str | None) -> str | None:
        return _check_category_code(v)


# ─── Справочник «Назначение оплаты» (PaymentCategory) ────────────────────────────


class PaymentCategorySchema(BaseModel):
    id: int
    code: str
    label: str
    sort_order: int
    is_system: bool
    project_id: int | None = None

    model_config = ConfigDict(from_attributes=True)


class PaymentCategoryCreate(BaseModel):
    """Добавление категории. code генерит сервис из label (стабильный, неизменяемый)."""

    label: str = Field(min_length=1, max_length=100)
    sort_order: int | None = None


class PaymentCategoryUpdate(BaseModel):
    """Переименование / смена порядка. code менять нельзя (на нём висят заявки)."""

    label: str | None = Field(None, min_length=1, max_length=100)
    sort_order: int | None = None


class SubmitRequest(BaseModel):
    comment: str | None = None


class CancelRequest(BaseModel):
    """Отмена заявки на оплату (PENDING_REVIEW/DRAFT → CANCELLED) с опциональной причиной."""

    comment: str | None = None


class PaymentActionRequest(BaseModel):
    """Согласовать / Отклонить / Отметить оплаченным — ручные действия админа.
    comment: причина/примечание (для «Отклонить» — рекомендуется)."""

    comment: str | None = None


class ParsedDocument(BaseModel):
    """Один разрезанный под-документ из многостраничного файла (счёт+акт в одном PDF).
    content_b64 — base64 готового под-файла (под-PDF), который фронт прикрепит к заявке
    с указанным doc_type обычным upload'ом (без повторного распознавания). В БД не пишется."""

    doc_type: str  # "INVOICE" | "ACT" (см. ALLOWED_PR_DOC_TYPES)
    filename: str
    mime_type: str
    content_b64: str


class InvoiceParseResult(BaseModel):
    """Распознанные реквизиты из файла счёта (PDF/Word) — ПОДСКАЗКА для формы, в БД не пишется.
    Поля, не прошедшие проверку (контроль-ключ р/с по БИК, БИК в справочнике), остаются None —
    их пользователь вводит вручную. fields_found — что распознано (для ✓ в UI), warnings — что
    требует ручной проверки. Числовые поля сериализуются строкой (как и весь домен).

    documents — авто-разнесение: если в одном файле счёт И акт (многостраничный PDF), бэкенд
    режет его постранично по типам; иначе пусто (фронт прикрепляет оригинал как было)."""

    payee_name: str | None = None
    payee_inn: str | None = None
    payee_kpp: str | None = None
    payee_account: str | None = None
    payee_bik: str | None = None
    payee_bank_name: str | None = None
    payee_corr_account: str | None = None
    amount: Decimal | None = None
    purpose: str | None = None
    fields_found: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    documents: list["ParsedDocument"] = Field(default_factory=list)


class CreateDraftRequest(BaseModel):
    """«Создать оплату» — РЕАЛЬНАЯ запись черновика платёжки в банк. confirm обязателен."""

    confirm: bool = False
    payer_account_id: str | None = None


class CreateDraftsRequest(BaseModel):
    """Массовое «Создать оплату» по нескольким заявкам (каждая — реальная запись в банк)."""

    ids: list[int] = Field(default_factory=list)
    confirm: bool = False
    payer_account_id: str | None = None


class CreateDraftResult(BaseModel):
    """Результат одной заявки в массовом создании платёжек."""

    id: int
    ok: bool
    bank_doc_id: str | None = None
    error: str | None = None


class CreateDraftsResponse(BaseModel):
    results: list[CreateDraftResult] = Field(default_factory=list)
    created: int = 0
    failed: int = 0


class ArchiveShipmentsRequest(BaseModel):
    """Массовый перенос заборов в архив рабочего списка / из архива."""

    shipment_ids: list[int] = Field(default_factory=list)


# ─── Read models ──────────────────────────────────────────────────────────────


class PaymentRequestDocumentResponse(BaseModel):
    id: int
    payment_request_id: int
    doc_type: str
    original_filename: str | None = None
    file_size: int | None = None
    mime_type: str | None = None
    uploaded_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentRequestEventResponse(BaseModel):
    id: int
    old_status: str | None = None
    new_status: str
    changed_at: datetime
    changed_by: str | None = None
    comment: str | None = None

    model_config = ConfigDict(from_attributes=True)


class PaymentRequestRow(BaseModel):
    """Строка списка (страница Оплаты + бейджи на странице логиста)."""

    id: int
    number: str
    status: str
    category: str | None = None
    brand: str | None = None  # бренд-атрибуция; None = «Все бренды»
    project_id: int | None = None
    project_name: str | None = None  # имя проекта («—» для общей заявки) — заполняет сервис
    payee_name: str | None = None
    payee_inn: str | None = None
    amount: Decimal
    currency: str
    pickup_date: date | None = None
    matched_transaction_id: int | None = None
    doc_count: int = 0
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class PaymentRequestDetail(PaymentRequestRow):
    """Деталка для дроуэра на странице Оплаты."""

    source: str
    counterparty_id: int | None = None
    outbound_shipment_id: int | None = None
    assembly_request_id: int | None = None
    payee_account: str | None = None
    payee_bik: str | None = None
    payee_bank_name: str | None = None
    payee_corr_account: str | None = None
    payee_kpp: str | None = None
    purpose: str | None = None
    bank_doc_id: str | None = None
    matched_at: datetime | None = None
    # Заборы, покрытые этой заявкой (одна заявка может покрывать несколько отгрузок).
    shipment_numbers: list[str] = Field(default_factory=list)
    shipment_count: int = 0
    documents: list[PaymentRequestDocumentResponse] = Field(default_factory=list)
    events: list[PaymentRequestEventResponse] = Field(default_factory=list)


class PaymentRequestListResponse(BaseModel):
    items: list[PaymentRequestRow]
    total: int


class ShippableShipmentRow(BaseModel):
    """SHIPPED-отгрузка — кандидат на заявку на оплату (пикер в модалке логиста)."""

    outbound_shipment_id: int
    assembly_request_id: int | None = None
    # Забор внутреннего переезда: у него assembly_request_id пуст, и строка ведёт
    # не на заявку на сборку, а на деталку перемещения. Без этого поля рабочий
    # список логиста показывал бы переезд некликабельной строкой.
    stock_transfer_id: int | None = None
    number: str
    destination: str | None = None
    shipped_date: date | None = None
    pickup_cost: Decimal | None = None
    counterparty_id: int | None = None
    carrier_inn: str | None = None
    carrier_name: str | None = None
    # Банковские реквизиты перевозчика (для пред-заполнения формы; могут быть пустыми)
    carrier_kpp: str | None = None
    carrier_bank_account: str | None = None
    carrier_bik: str | None = None
    carrier_bank_name: str | None = None
    carrier_corr_account: str | None = None
    already_requested: bool = False
    # Статус существующей заявки на оплату по этому забору (для рабочего списка логиста)
    request_id: int | None = None
    request_status: str | None = None
    # Прямая авто-связка забора с дебетом выписки (без заявки на оплату).
    matched_transaction_id: int | None = None
    matched_at: datetime | None = None
    matched_txn_date: date | None = None
    # Доп. данные забора для рабочего списка
    pallets_count: int | None = None
    shipped_as_boxes: bool = False  # единица забора: паллеты (False) или короба (True)
    pickup_date: date | None = None
    source_warehouse: str | None = None
    wb_supply_number: str | None = None


class ReconciliationPaymentRow(BaseModel):
    """Исходящий платёж перевозчику из выписки + привязанные к нему заборы (сверка оплат)."""

    transaction_id: int
    date: datetime
    amount: Decimal
    purpose: str | None = None
    account: str | None = None
    counterparty_name: str | None = None
    inn: str | None = None
    archived: bool = False
    # Привязка N заборов к одной оплате (агрегированный платёж по нескольким счетам).
    linked_shipment_ids: list[int] = Field(default_factory=list)
    linked_count: int = 0
    linked_sum: Decimal = Decimal("0")  # Σ pickup_cost привязанных заборов
    diff: Decimal = Decimal("0")        # amount − linked_sum (0 = сошлось)


class CounterpartyReconciliation(BaseModel):
    """Сверка заборов и платежей одного перевозчика (карточка контрагента)."""

    shipments: list[ShippableShipmentRow] = Field(default_factory=list)
    payments: list[ReconciliationPaymentRow] = Field(default_factory=list)
    # Сводка по активным (не архивным) строкам.
    matched_count: int = 0
    unpaid_count: int = 0
    payment_count: int = 0
    unlinked_payment_count: int = 0  # платежи без единого забора
    payments_truncated: bool = False  # показаны последние _LIST_LIMIT платежей


class ArchiveTxnsRequest(BaseModel):
    """Архив/разархив платежей по transaction_id."""

    transaction_ids: list[int] = Field(default_factory=list)


class LinkShipmentsRequest(BaseModel):
    """Привязать заборы к платежу выписки (N заборов → 1 оплата)."""

    transaction_id: int
    shipment_ids: list[int] = Field(default_factory=list)


class UnlinkShipmentsRequest(BaseModel):
    """Отвязать заборы от платежа."""

    shipment_ids: list[int] = Field(default_factory=list)


class PaymentRequestStatusPoll(BaseModel):
    """Лёгкий ответ для live-поллинга бейджа."""

    id: int
    status: str
    matched_transaction_id: int | None = None
    matched_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

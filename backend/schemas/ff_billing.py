# ruff: noqa: RUF002, RUF003
"""
Pydantic schemas: FF billing (тарифы услуг ФФ, хранение по дням, счета ФФ).

API-контракт (роутер backend/routers/ff_billing.py, префикс /api/v1):
  Тарифы:
    GET    /warehouse/{warehouse_id}/ff-tariffs                → list[FfTariffRow]
    POST   /warehouse/{warehouse_id}/ff-tariffs                ← FfTariffPayload
    PATCH  /ff-tariffs/{tariff_id}                              ← FfTariffUpdatePayload
    DELETE /ff-tariffs/{tariff_id}                              (soft)
  Хранение:
    GET    /warehouse/{warehouse_id}/ff-storage-daily?date_from&date_to → list[FfStorageDailyRow]
    POST   /warehouse/{warehouse_id}/ff-storage-daily/recompute ← FfStorageRecomputePayload
  Ожидаемая стоимость услуг ФФ по заявке сборки:
    GET    /warehouse/assembly/{request_id}/ff-expected-cost     → FfAssemblyExpectedCost
    PATCH  /warehouse/assembly/{request_id}/ff-custom-cost       ← FfCustomCostPayload
  Ожидаемая стоимость услуг ФФ по переезду (склад-ИСТОЧНИК):
    GET    /warehouse/transfers/{transfer_id}/ff-expected-cost   → FfTransferExpectedCost
    PATCH  /warehouse/transfers/{transfer_id}/ff-custom-cost     ← FfCustomCostPayload
  Счета:
    GET    /ff-invoices?warehouse_id&status&kind                 → FfInvoiceListResponse
    POST   /ff-invoices                                          ← FfInvoiceCreatePayload
    POST   /ff-invoices/upload   (multipart file; parse → черновик счёта)
    GET    /ff-invoices/{invoice_id}                             → FfInvoiceDetail
    PATCH  /ff-invoices/{invoice_id}                             ← FfInvoiceUpdatePayload
    DELETE /ff-invoices/{invoice_id}                             (soft)
    GET    /ff-invoices/{invoice_id}/document/download
    POST   /ff-invoices/{invoice_id}/reconcile                   → FfInvoiceDetail (пересборка строк + diff)
    POST   /ff-invoices/{invoice_id}/create-payment-request      → {payment_request_id}
    GET    /ff-invoices/candidate-payments?invoice_id            → list[FfCandidatePayment]
    POST   /ff-invoices/payments/link                            ← FfInvoicePaymentLinkPayload
    POST   /ff-invoices/payments/unlink                          ← FfInvoicePaymentUnlinkPayload
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FfServiceTypeLiteral = Literal[
    "PALLETIZING", "BOX_PROCESSING", "STORAGE", "TRUCK_UNLOADING", "LOADING", "TRANSFER_ASSEMBLY"
]
FfTariffUnitLiteral = Literal["PALLET", "BOX"]
#: Услуги, у которых склад сам выбирает единицу биллинга (₽/паллета или ₽/короб);
#: у остальных единица фиксирована типом услуги и unit обязан быть None.
UNIT_SERVICES: frozenset[str] = frozenset({"LOADING", "TRANSFER_ASSEMBLY"})
FfInvoiceKindLiteral = Literal["SHIPMENT", "STORAGE", "RECEIVING", "LOGISTICS", "MIXED"]
FfInvoiceStatusLiteral = Literal["NEW", "RECONCILED", "DISPUTED", "PAID"]
FfInvoiceLineKindLiteral = Literal["ASSEMBLY", "STORAGE", "RECEIVING", "LOGISTICS", "CUSTOM"]

# ─── Тарифы ──────────────────────────────────────────────────────────────────


class FfTariffPayload(BaseModel):
    service_type: FfServiceTypeLiteral
    # Только для UNIT_SERVICES (LOADING, TRANSFER_ASSEMBLY): чем биллит склад
    # (паллета/короб). У прочих услуг единица фиксирована типом — unit=None.
    unit: FfTariffUnitLiteral | None = None
    rate: Decimal = Field(ge=0, le=Decimal("9999999999999999.99"))
    valid_from: date | None = None  # None = с сегодня
    valid_to: date | None = None

    @model_validator(mode="after")
    def _unit_only_for_unit_services(self) -> "FfTariffPayload":
        if self.service_type in UNIT_SERVICES and self.unit is None:
            raise ValueError(f"Для {self.service_type} укажите unit: PALLET или BOX")
        if self.service_type not in UNIT_SERVICES and self.unit is not None:
            raise ValueError("unit допустим только для LOADING и TRANSFER_ASSEMBLY")
        return self


class FfTariffUpdatePayload(BaseModel):
    unit: FfTariffUnitLiteral | None = None
    rate: Decimal | None = Field(None, ge=0)
    valid_from: date | None = None
    valid_to: date | None = None


class FfTariffRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    warehouse_id: int
    service_type: FfServiceTypeLiteral
    unit: FfTariffUnitLiteral | None = None
    rate: Decimal
    valid_from: date
    valid_to: date | None = None


# ─── Хранение по дням ────────────────────────────────────────────────────────


class FfStorageDailyRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    snapshot_date: date
    units: int
    boxes: int
    pallets: int
    storage_rate: Decimal | None = None
    storage_cost: Decimal | None = None


class FfStorageRecomputePayload(BaseModel):
    """Пересчёт стоимости хранения за диапазон (после правки тарифа задним
    числом). Пересчитываются только storage_rate/storage_cost; штуки/короба/
    паллеты зафиксированы на момент среза и не трогаются."""

    date_from: date
    date_to: date

    @model_validator(mode="after")
    def _range_ok(self) -> "FfStorageRecomputePayload":
        if self.date_from > self.date_to:
            raise ValueError("date_from > date_to")
        return self


# ─── Ожидаемая стоимость услуг ФФ по заявке ─────────────────────────────────


class FfExpectedComponent(BaseModel):
    service_type: FfServiceTypeLiteral | Literal["CUSTOM"]
    unit: str | None = None  # PALLET | BOX | VEHICLE | None (CUSTOM)
    qty: int | None = None
    rate: Decimal | None = None  # None = тариф не задан на складе
    cost: Decimal | None = None  # qty × rate; None если нет тарифа
    #: Ставка есть, но её единица (unit) не та, в которой измерен документ
    #: (тариф ₽/паллета, а переезд коробочный). Пересчёт по выдуманному курсу
    #: «коробов в паллете» дал бы неверные деньги, поэтому cost=None и
    #: строка помечена признаком — считает человек, не мы.
    unit_mismatch: bool = False


class FfAssemblyExpectedCost(BaseModel):
    assembly_request_id: int
    warehouse_id: int
    pallets: int
    boxes: int
    units: int
    components: list[FfExpectedComponent]
    custom_cost: Decimal | None = None
    custom_cost_comment: str | None = None
    total: Decimal | None = None  # Σ cost компонент (None, если ни одного тарифа)
    missing_tariffs: list[str] = []  # service_type без ставки на дату


class FfTransferExpectedCost(BaseModel):
    """Ожидаемая стоимость услуг ФФ по переезду — зеркало заявки на сборку.

    Тариф берётся у склада-ИСТОЧНИКА (`from_warehouse_id`): собирает переезд
    он. Услуга одна — TRANSFER_ASSEMBLY (₽/паллета ИЛИ ₽/короб по unit тарифа),
    плюс ручная строка CUSTOM. `qty` измерен в `unit` переезда
    (shipped_as_boxes → BOX), тарифная единица может от него отличаться —
    тогда компонент помечен `unit_mismatch` и в `total` НЕ входит.
    """

    transfer_id: int
    transfer_number: str
    from_warehouse_id: int
    from_warehouse_name: str | None = None
    to_warehouse_id: int
    unit: FfTariffUnitLiteral  # единица переезда: PALLET | BOX
    qty: int  # pallets_count в этой единице (нет объёма → 0)
    on_date: date  # дата, на которую резолвились ставки
    components: list[FfExpectedComponent]
    custom_cost: Decimal | None = None
    custom_cost_comment: str | None = None
    total: Decimal | None = None  # Σ cost компонент (None, если считать нечего)
    missing_tariffs: list[str] = []  # service_type без ставки на дату
    unit_mismatch: bool = False  # хоть один компонент с несовпадением единиц


class FfCustomCostPayload(BaseModel):
    # null = очистить
    amount: Decimal | None = Field(None, ge=0)
    comment: str | None = Field(None, max_length=300)


# ─── Счета ───────────────────────────────────────────────────────────────────


class FfInvoiceCreatePayload(BaseModel):
    warehouse_id: int | None = None
    number: str | None = Field(None, max_length=100)
    invoice_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    kind: FfInvoiceKindLiteral = "MIXED"
    amount: Decimal = Field(gt=0)
    payee_inn: str | None = Field(None, max_length=12)
    comment: str | None = None


class FfInvoiceUpdatePayload(BaseModel):
    warehouse_id: int | None = None
    number: str | None = Field(None, max_length=100)
    invoice_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    kind: FfInvoiceKindLiteral | None = None
    amount: Decimal | None = Field(None, gt=0)
    comment: str | None = None


class FfInvoiceLineRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: FfInvoiceLineKindLiteral
    assembly_request_id: int | None = None
    outbound_shipment_id: int | None = None
    inbound_receipt_id: int | None = None
    # Обогащение для UI (номер заявки/приёмки) — не из ORM-атрибутов строки.
    ref_number: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    qty_units: int | None = None
    qty_boxes: int | None = None
    qty_pallets: int | None = None
    our_cost: Decimal | None = None
    allocated_amount: Decimal | None = None
    label: str | None = None


class FfInvoiceRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    warehouse_id: int | None = None
    warehouse_name: str | None = None
    counterparty_id: int | None = None
    counterparty_name: str | None = None
    number: str | None = None
    invoice_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    kind: FfInvoiceKindLiteral
    status: FfInvoiceStatusLiteral
    amount: Decimal
    our_amount: Decimal | None = None
    diff: Decimal | None = None  # amount − our_amount
    payee_inn: str | None = None
    has_document: bool = False
    payment_request_id: int | None = None
    matched_transaction_id: int | None = None
    matched_at: datetime | None = None
    created_at: datetime


class FfInvoiceDetail(FfInvoiceRow):
    comment: str | None = None
    original_filename: str | None = None
    lines: list[FfInvoiceLineRow] = []


class FfInvoiceListResponse(BaseModel):
    items: list[FfInvoiceRow]
    total: int


class FfInvoiceUploadResponse(BaseModel):
    """Ответ upload: черновик счёта + что распознали (для формы-подтверждения)."""

    invoice: FfInvoiceDetail
    parsed_warehouse_id: int | None = None  # резолв ИНН → склад (None = не нашли)
    warnings: list[str] = []


class FfCandidatePayment(BaseModel):
    """Дебет выписки по ИНН юрлиц склада — кандидат на привязку к счёту(ам)."""

    transaction_id: int
    date: date
    amount: Decimal
    counterparty: str | None = None
    inn: str | None = None
    purpose: str | None = None
    already_linked_invoice_ids: list[int] = []


class FfInvoicePaymentLinkPayload(BaseModel):
    """Привязка N счетов → 1 платёж. Сервис проверяет |Σ amount − дебет| ≤ 0.01
    (частичная оплата одного счёта — вне v1)."""

    transaction_id: int
    invoice_ids: list[int] = Field(min_length=1)


class FfInvoicePaymentUnlinkPayload(BaseModel):
    invoice_ids: list[int] = Field(min_length=1)


class FfInvoiceCreatePaymentRequestResponse(BaseModel):
    payment_request_id: int

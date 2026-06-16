# ruff: noqa: RUF002, RUF003
"""
Pydantic schemas: fulfillment integration (skladbot, migfull).

API contract for /warehouse/{warehouse_id}/fulfillment/* endpoints.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ─── Connection ──────────────────────────────────────────────────────────────


class FulfillmentConnectPayload(BaseModel):
    provider: Literal["skladbot", "wmscelicom", "migfull"] = "skladbot"
    token: str = Field(min_length=20, max_length=4096)
    # wmscelicom: адрес клиентского инстанса ({client}.wmscelicom.ru)
    base_url: str | None = Field(None, max_length=200)
    # migfull («Натали»): GUID кабинета клиента (хост фиксированный — migfull.app)
    tenant_guid: str | None = Field(None, max_length=64)


class FulfillmentStatus(BaseModel):
    connected: bool
    provider: str | None = None
    key_preview: str | None = None  # "***xxxx", сам токен назад не отдаём
    customer_id: int | None = None
    customer_name: str | None = None
    token_expires_at: datetime | None = None
    api_base_url: str | None = None  # wmscelicom: инстанс, на который ходим
    tenant_guid: str | None = None  # migfull: GUID кабинета
    last_sync_at: datetime | None = None


# ─── Stocks ──────────────────────────────────────────────────────────────────


class FfStockRow(BaseModel):
    barcode: str
    name: str | None = None
    vendor_code: str | None = None
    nomenclature_id: int | None = None
    article_seller: str | None = None  # наш артикул (если товар сматчен)
    subject: str | None = None  # предмет из номенклатуры (если сматчен)
    brand: str | None = None  # бренд из номенклатуры (если сматчен)
    ff_good: int = 0
    ff_reserve: int = 0
    ff_defect: int = 0
    ff_nominal: int = 0
    our_quantity: int = 0
    our_defect: int = 0
    diff: int = 0  # ff_good - our_quantity


class FfStockTotals(BaseModel):
    ff_good: int = 0
    ff_reserve: int = 0
    ff_defect: int = 0
    our_quantity: int = 0
    diff: int = 0
    unmatched: int = 0  # строк ФФ без нашей номенклатуры


class FfStocksResponse(BaseModel):
    rows: list[FfStockRow]
    totals: FfStockTotals
    synced_at: datetime | None = None
    subjects: list[str] = Field(default_factory=list)  # distinct предметы для фильтра
    brands: list[str] = Field(default_factory=list)  # distinct бренды для фильтра


# ─── Requests ────────────────────────────────────────────────────────────────


class FfRequestRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str
    number: str | None = None
    kind: str  # assembly | inbound | other
    type_name: str | None = None
    status: str | None = None
    stage_code: str | None = None
    stage_title: str | None = None
    is_completed: bool = False
    archived: bool = False
    expired: bool = False
    local_archived: bool = False  # локальный архив DDS (синк не трогает)
    local_archived_at: datetime | None = None
    total_qty: int | None = None  # заявлено всего, шт (skladbot — из деталки)
    dest_warehouse: str | None = None  # склад отгрузки МП («Склад МП» / shipped_target)
    external_created_at: date | None = None
    synced_at: datetime
    assembly_request_id: int | None = None
    inbound_receipt_id: int | None = None
    # Обогащение по связанному документу (заполняет сервис)
    linked_number: str | None = None
    linked_status: str | None = None


class FfRequestDetailProduct(BaseModel):
    """Позиция заявки ФФ (из недокументированного GET /v1/requests/show/{id})."""

    barcode: str | None = None
    vendor_code: str | None = None
    name: str | None = None
    nomenclature_id: int | None = None
    article_seller: str | None = None  # наш артикул (если товар сматчен)
    qty: int = 0  # заявлено (amount)
    accepted_qty: int = 0  # принято (acceptedAmount)
    delivery_qty: int = 0  # отгружено (delivery_amount)
    defect_qty: int = 0  # брак (repairAmount)
    our_qty: int | None = None  # кол-во в связанном нашем документе; None — связи нет
    color: str | None = None
    size: str | None = None
    comment: str | None = None
    image: str | None = None


class FfMatchRow(BaseModel):
    """Строка расхождения состава: ФФ-заявка vs наш документ (по barcode)."""

    barcode: str
    article_seller: str | None = None
    name: str | None = None  # название со стороны ФФ (если позиция там есть)
    ff_qty: int = 0
    our_qty: int = 0
    diff: int = 0  # ff_qty - our_qty


class FfRequestMatch(BaseModel):
    """Итог сверки состава ФФ-заявки со связанным нашим документом."""

    matched: bool
    ff_positions: int = 0
    our_positions: int = 0
    ff_total: int = 0
    our_total: int = 0
    mismatches: list[FfMatchRow] = Field(default_factory=list)


class FfRequestStageLog(BaseModel):
    stage: str | None = None
    executor: str | None = None
    created_at: str | None = None  # формат провайдера «10.06.2026 17:53:35», отдаём как есть
    spent_time: str | None = None


class FfRequestFieldValue(BaseModel):
    """Динамическое поле заявки (Маркетплейс, Склад МП, Дата забора, ...)."""

    name: str | None = None
    field: str | None = None
    value: str | None = None


class FfRequestDetail(FfRequestRow):
    """Деталка заявки ФФ: шапка списочной строки + живой состав от провайдера."""

    comment: str | None = None
    customer_name: str | None = None
    executor: str | None = None
    creator: str | None = None
    stage_description: str | None = None
    total_qty: int = 0
    total_accepted: int = 0
    products: list[FfRequestDetailProduct] = Field(default_factory=list)
    stage_logs: list[FfRequestStageLog] = Field(default_factory=list)
    fields: list[FfRequestFieldValue] = Field(default_factory=list)
    # Сверка состава со связанным нашим документом (None — связи нет)
    match: FfRequestMatch | None = None


class FfLinkPayload(BaseModel):
    assembly_request_id: int | None = None
    inbound_receipt_id: int | None = None


class FfLinkCandidate(BaseModel):
    """Кандидат для связывания ФФ-заявки с нашим документом (модал «Связать»).

    kind=assembly → заявка на сборку; kind=inbound → приёмка.
    score/reason заполнены, когда эвристика считает кандидата похожим
    (дата ± дни, пересечение ШК состава, близость количества).
    """

    doc_id: int
    number: str
    status: str
    created_at: datetime | None = None
    total_qty: int = 0  # суммарное кол-во позиций документа, шт
    fbo_supply_number: str | None = None  # ФБО-поставка WB (assembly)
    dest_warehouse: str | None = None  # склад назначения WB (assembly)
    score: int | None = None  # 0..100, None — эвристика кандидата не выделила
    reason: str | None = None  # «дата совпадает, ШК 80%»


class FfLinkCandidatesResponse(BaseModel):
    kind: str  # assembly | inbound
    ff_number: str | None = None
    ff_total_qty: int | None = None
    # Состав ФФ-заявки удалось получить → скоринг учитывает пересечение ШК
    composition_available: bool = False
    candidates: list[FfLinkCandidate] = Field(default_factory=list)


class FfCreateAssemblyResult(BaseModel):
    """Итог создания нашей заявки на сборку из ФФ-заявки (kind=assembly)."""

    request: FfRequestRow  # обновлённая ФФ-заявка (уже связана)
    assembly_request_id: int
    assembly_number: str
    items_created: int = 0
    skipped_barcodes: list[str] = Field(default_factory=list)  # ШК без номенклатуры


# ─── История смены статусов заявок ФФ ──────────────────────────────────────


class FfStatusEvent(BaseModel):
    """Событие истории: синк зафиксировал смену стадии/статуса заявки ФФ."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    fulfillment_request_id: int
    external_id: str
    number: str | None = None
    kind: str  # assembly | inbound | other
    provider: str
    event_type: str  # created | changed
    old_status: str | None = None
    new_status: str | None = None
    old_stage_code: str | None = None
    new_stage_code: str | None = None
    old_stage_title: str | None = None
    new_stage_title: str | None = None
    old_is_completed: bool | None = None
    new_is_completed: bool | None = None
    old_archived: bool | None = None
    new_archived: bool | None = None
    changed_at: datetime


# ─── Сводная страница «Заявки ФФ» (все склады с интеграцией) ────────────────


class FfIntegratedWarehouse(BaseModel):
    warehouse_id: int
    warehouse_name: str
    provider: str  # skladbot | wmscelicom
    provider_label: str  # человекочитаемое имя провайдера
    last_sync_at: datetime | None = None
    requests_total: int = 0
    requests_unlinked: int = 0  # активные несвязанные заявки kind=assembly


class FfMatchSuggestion(BaseModel):
    """Кандидат авто-мэтчинга ФФ-заявки к нашей заявке на сборку."""

    assembly_request_id: int
    number: str
    status: str  # AssemblyStatus
    created_at: datetime
    total_qty: int = 0
    score: int  # 0..100 — уверенность эвристики
    reason: str  # объяснение: «дата ±1 дн», «ШК 80%»


class FfOverviewRequestRow(FfRequestRow):
    warehouse_id: int
    warehouse_name: str
    provider: str
    # топ-кандидаты для несвязанных активных заявок (иначе [])
    suggestions: list[FfMatchSuggestion] = Field(default_factory=list)


class FfOverviewResponse(BaseModel):
    warehouses: list[FfIntegratedWarehouse]
    requests: list[FfOverviewRequestRow]


# ─── Несвязанные наши заявки на сборку (обратный линк ФФ → ASM) ──────────────


class FfUnlinkedAssembly(BaseModel):
    """Наша заявка на сборку без связанной заявки ФФ (для модалки обратного линка).

    Активные сборки склада (IN_PROGRESS/READY/VEHICLE_ASSIGNED), которым ещё не
    сопоставлена ни одна ФФ-заявка зеркала.
    """

    id: int
    number: str
    status: str  # AssemblyStatus
    brands: str | None = None  # бренды позиций через запятую (или None)
    total_qty: int = 0  # суммарное кол-во позиций, шт
    estimated_ready_date: date | None = None
    created_at: datetime


# ─── Sync ────────────────────────────────────────────────────────────────────


class FfSyncResult(BaseModel):
    stocks_synced: int = 0
    requests_synced: int = 0
    unmatched_barcodes: int = 0
    assemblies_marked_ready: int = 0  # наших заявок переведено в READY по стадии ФФ
    synced_at: datetime


class FfSyncRun(BaseModel):
    """Один прогон синхронизации ФФ-склада (строка журнала sync_log).

    Питает вкладку «ФФ синхронизация» — видно, когда были последние обновления
    зеркала (авто-синк по расписанию + ручной «Синхронизировать сейчас»).
    """

    id: int
    service: str  # skladbot | wmscelicom | migfull
    status: str  # RUNNING | OK | ERROR
    started_at: datetime
    finished_at: datetime | None = None
    stocks_synced: int = 0  # позиций остатков (rows_inserted)
    requests_synced: int = 0  # заявок (rows_fetched − rows_inserted)
    duration_seconds: float | None = None
    error_msg: str | None = None

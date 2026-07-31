# ruff: noqa: RUF001, RUF002, RUF003
"""
Warehouse schemas: request/response models for warehouse module.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ─── Warehouse ──────────────────────────────────────────────────────────────


class WarehouseCreate(BaseModel):
    name: str
    warehouse_type: str  # EXTERNAL | FULFILLMENT
    country: str | None = None
    address: str | None = None
    assembly_days: int | None = None
    wb_acceptance_days: int = 2
    sort_order: int = 0


class WarehouseUpdate(BaseModel):
    name: str | None = None
    warehouse_type: str | None = None
    country: str | None = None
    address: str | None = None
    assembly_days: int | None = None
    wb_acceptance_days: int | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class WarehouseExtraCounterparty(BaseModel):
    """Дополнительный контрагент склада (одна строка link-таблицы)."""

    model_config = ConfigDict(from_attributes=True)
    id: int  # counterparty id
    inn: str | None = None
    name: str | None = None


class WarehouseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    name: str
    warehouse_type: str
    country: str | None = None
    address: str | None = None
    assembly_days: int | None = None
    wb_acceptance_days: int = 2
    external_id: str | None = None
    sort_order: int = 0
    is_active: bool = True
    total_stock: int = 0
    vehicles_in_transit: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    counterparty_id: int | None = None
    counterparty_inn: str | None = None
    counterparty_name: str | None = None
    extra_counterparties: list[WarehouseExtraCounterparty] = []


class WarehouseCounterpartyLink(BaseModel):
    """Body for PATCH /warehouses/{id}/counterparty."""

    inn: str | None = None
    name: str | None = None


class WarehouseExtraCounterpartyAdd(BaseModel):
    """Body for POST /warehouses/{id}/counterparties — добавить доп. контрагента."""

    inn: str
    name: str | None = None


class WarehouseReorder(BaseModel):
    """Reorder warehouses: list of {id, sort_order}."""

    items: list[dict]  # [{id: int, sort_order: int}, ...]


# ─── Inbound Receipt (Приёмка) ──────────────────────────────────────────────


class InboundReceiptItemCreate(BaseModel):
    barcode: str
    expected_qty: int
    actual_qty: int = 0


class InboundReceiptItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    receipt_id: int
    nomenclature_id: int
    barcode: str
    expected_qty: int
    actual_qty: int


class InboundReceiptCreate(BaseModel):
    planned_date: date | None = None
    comment: str | None = None
    tags: str | None = None  # JSON array string
    is_defect: bool = False
    defect_reason: str | None = None
    items: list[InboundReceiptItemCreate] = []


class InboundReceiptUpdate(BaseModel):
    planned_date: date | None = None
    comment: str | None = None
    tags: str | None = None
    defect_reason: str | None = None
    items: list[InboundReceiptItemCreate] | None = None


class InboundReceiptSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    warehouse_id: int
    number: str
    status: str
    planned_date: date | None = None
    actual_date: date | None = None
    comment: str | None = None
    tags: str | None = None
    cost_order_id: int | None = None
    is_defect: bool = False
    defect_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    items: list[InboundReceiptItemSchema] = []


# ─── Outbound Shipment (Отгрузка) ──────────────────────────────────────────


class OutboundShipmentItemCreate(BaseModel):
    barcode: str
    quantity: int


class OutboundShipmentItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    shipment_id: int
    nomenclature_id: int
    barcode: str
    quantity: int


class OutboundShipmentCreate(BaseModel):
    destination: str | None = None
    comment: str | None = None
    is_defect: bool = False
    defect_reason: str | None = None
    items: list[OutboundShipmentItemCreate] = []


class OutboundShipmentSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    warehouse_id: int
    number: str
    status: str
    destination: str | None = None
    wb_supply_id: str | None = None
    shipped_date: date | None = None
    comment: str | None = None
    is_defect: bool = False
    defect_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    items: list[OutboundShipmentItemSchema] = []
    # Непустой → это забор ВНУТРЕННЕГО ПЕРЕЕЗДА, а не отгрузка на маркетплейс:
    # UI обязан отличать (кнопка «Отменить отгрузку» на нём запрещена — сток
    # принадлежит перемещению) и вести по ссылке на деталку перемещения.
    stock_transfer_id: int | None = None


# ─── Stock Transfer (Перемещение) ──────────────────────────────────────────


class StockTransferItemCreate(BaseModel):
    barcode: str
    quantity: int


class StockTransferItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    transfer_id: int
    nomenclature_id: int
    barcode: str
    quantity: int


class StockTransferCreate(BaseModel):
    from_warehouse_id: int
    to_warehouse_id: int
    comment: str | None = None
    is_defect: bool = False
    defect_reason: str | None = None
    items: list[StockTransferItemCreate] = []
    # Транспортная единица переезда: shipped_as_boxes меняет только ЕДИНИЦУ
    # измерения pallets_count/pallet_weight_kg (паллеты по умолчанию, короба —
    # при True) и подписи в UI.
    pallets_count: int | None = Field(default=None, ge=0)
    pallet_weight_kg: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    shipped_as_boxes: bool = False


class StockTransferUpdate(BaseModel):
    """Правка перемещения (`PUT /warehouse/transfers/{id}`) — ТОЛЬКО в статусе DRAFT.

    Применяются ТОЛЬКО явно переданные поля: роутер отдаёт сервису
    `model_dump(exclude_unset=True)`. Форма карточки шлёт тело целиком, но
    частичный вызов (например «поменять только комментарий») не должен обнулить
    маршрут и транспортную единицу дефолтами схемы.

    `items` — ПОЛНАЯ ЗАМЕНА состава (как в `StockTransferCreate`, резолв по
    баркодам). Не передан — состав не трогаем; передан пустым — отказ: переезд
    без позиций всё равно не отправить (`send_transfer`), а молча стереть
    состав по недосмотру формы страшнее, чем вернуть 400.

    🔴 `shipped_as_boxes` здесь ОБЫЧНЫЙ bool, а не трёхзначный как у
    `TransferAssignVehicle`: там `None` значит «логист не уточнял единицу и не
    хочет затирать унаследованную от заявки», здесь форма карточки всегда знает,
    что выбрал пользователь. Границы числовых полей — те же, что у Create
    (`ge=0` + `Numeric(10, 2)`): отрицательный вес доехал бы снимком в забор при
    отправке и занизил бы ₽/паллета в отчёте логистики переездов.
    """

    from_warehouse_id: int | None = None
    to_warehouse_id: int | None = None
    comment: str | None = None
    is_defect: bool = False
    defect_reason: str | None = None
    items: list[StockTransferItemCreate] | None = None
    pallets_count: int | None = Field(default=None, ge=0)
    pallet_weight_kg: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    shipped_as_boxes: bool = False


class TransferFfLink(BaseModel):
    """Заявка ФФ в связке с ПЕРЕЕЗДОМ — и уже привязанная, и кандидат в привязку.

    Одна схема на оба места (`StockTransferSchema.ff_links` и
    `GET /warehouse/transfers/{id}/ff-candidates`): карточке переезда нужен
    ровно один набор полей — показать строку и собрать вызов link/unlink. Ручки
    ФФ скоуплены складом (`/warehouse/{warehouse_id}/fulfillment/...`), поэтому
    `warehouse_id` обязателен: без него фронт не построит ни ссылку на заявку,
    ни отвязку.

    🔴 НЕ ПУТАТЬ с `FfLinkCandidate` (`backend/schemas/fulfillment.py`) — та про
    ОБРАТНОЕ направление: наш документ как кандидат для ФФ-заявки (модал
    «Связать» со стороны заявки). Здесь направление от карточки переезда.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int  # fulfillment_requests.id
    warehouse_id: int  # наш склад ФФ-заявки — для ссылки и для unlink
    #: Сторона переезда: source — склад ЗАБОРА (ФФ собирает переезд у себя, для
    #: него это сборка, kind=assembly); dest — склад ПОЛУЧАТЕЛЯ (ФФ приходует,
    #: kind=inbound). Тот же расклад, что проверяет `link_request`.
    side: str
    number: str | None = None
    external_id: str
    kind: str  # assembly | inbound | return | other
    status: str | None = None
    stage_title: str | None = None
    total_qty: int | None = None  # заявлено всего, шт
    external_created_at: date | None = None


class StockTransferSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    from_warehouse_id: int
    to_warehouse_id: int
    number: str
    status: str
    comment: str | None = None
    is_defect: bool = False
    defect_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    items: list[StockTransferItemSchema] = []

    # Машина и логистика переезда (зеркало блока «Назначить машину» у заявки).
    vehicle_info: str | None = None
    vehicle_brand: str | None = None
    driver_first_name: str | None = None
    driver_last_name: str | None = None
    driver_phone: str | None = None
    counterparty_id: int | None = None
    logistics_by_warehouse: bool = False
    pickup_date: date | None = None
    pickup_time_slot: str | None = None
    pickup_cost: Decimal | None = None
    delivery_date: date | None = None
    vehicle_assigned_at: datetime | None = None
    converted_from_assembly_id: int | None = None
    pallets_count: int | None = None
    pallet_weight_kg: Decimal | None = None
    shipped_as_boxes: bool = False
    # Подписи — заполняются сервисом пачкой (не relationship): без них Лист
    # логиста догружал бы справочники складов и контрагентов отдельно, а
    # карточка показывала «Контрагент #12».
    from_warehouse_name: str | None = None
    to_warehouse_name: str | None = None
    counterparty_name: str | None = None
    # Итоги состава для СПИСКА. В списке `items` НЕ ОТДАЁТСЯ (всегда пустой) —
    # полный состав там весит мегабайты, а потребителям нужны только эти два
    # числа. За составом — `GET /warehouse/transfers/{id}`.
    units_total: int = 0
    sku_count: int = 0
    # Уже связанные заявки ФФ обеих сторон. Заполняется ТОЛЬКО в КАРТОЧКЕ
    # (`get_transfer` / ответ `PUT`), в списке всегда пусто. Раньше карточка
    # ради этих 0-2 строк тянула ДВА полных списка заявок ФФ по обоим складам
    # (~300 КБ); на складе «Натали» уже 432 заявки при лимите 500 — связка
    # вот-вот перестала бы находиться вовсе. Это про корректность, не про скорость.
    ff_links: list[TransferFfLink] = []


class TransferAssignVehicle(BaseModel):
    """Назначение машины на перемещение — контракт зеркалит AssignVehicle заявки.

    logistics_by_warehouse=True → перевозчик берётся из контрагента склада-
    ИСТОЧНИКА, поля carrier_* игнорируются. Иначе перевозчик резолвится по
    carrier_inn / carrier_name.

    Транспортная единица (pallets_count / pallet_weight_kg / shipped_as_boxes)
    здесь ОПЦИОНАЛЬНА и трёхзначна: логист часто уточняет её именно в момент
    назначения машины, но пустое поле НЕ затирает уже заданное на переезде
    (например, унаследованное от заявки при конвертации).
    """

    # Границы полей — не формальность: отрицательная стоимость забора молча
    # доехала бы снимком в OutboundShipment и занизила бы `total_cost` /
    # `cost_per_pallet` в отчёте логистики переездов (гарда на знак там нет),
    # а строка длиннее колонки или `Decimal` вне Numeric(18,2) даёт 500 из
    # asyncpg (сервис ловит только ValueError).
    vehicle_info: str | None = Field(default=None, max_length=300)
    vehicle_brand: str | None = Field(default=None, max_length=100)
    driver_first_name: str | None = Field(default=None, max_length=100)
    driver_last_name: str | None = Field(default=None, max_length=100)
    driver_phone: str | None = Field(default=None, max_length=30)
    logistics_by_warehouse: bool = False
    carrier_inn: str | None = Field(default=None, max_length=20)
    carrier_name: str | None = Field(default=None, max_length=300)
    pickup_date: date | None = None
    pickup_time_slot: str | None = Field(default=None, max_length=20)
    pickup_cost: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    delivery_date: date | None = None
    pallets_count: int | None = Field(default=None, ge=0)
    pallet_weight_kg: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    shipped_as_boxes: bool | None = None

    @model_validator(mode="after")
    def _require_vehicle_identity(self) -> "TransferAssignVehicle":
        """Пустое тело не должно «назначать» машину-призрак.

        Все поля здесь опциональны (в отличие от AssignVehicle заявки), поэтому
        `{}` проходил бы валидацию и ставил `vehicle_assigned_at`: переезд
        попадал в срез «машина назначена», рисовал блок из сплошных «—», а при
        отправке порождал забор БЕЗ перевозчика и БЕЗ суммы — ровно тот мусор в
        рабочем списке оплат, против которого написан гард в `_create_transfer_pickup`.
        Достаточно любого признака: госномер, ИНН подрядчика или «логистику
        оказывает склад забора» (там перевозчик берётся из склада).
        """
        if not (self.vehicle_info or self.carrier_inn or self.carrier_name or self.logistics_by_warehouse):
            raise ValueError(
                "Укажите госномер машины, перевозчика или отметьте «логистику оказывает склад забора»"
            )
        return self


class TransferAssignVehicleBulk(BaseModel):
    """Одна машина на N переездов (Лист логиста: три переезда на «транзит Питер»
    едут одной газелью). Реквизиты общие для всех — в отличие от заявок, где
    дата/стоимость забора задаются пер-строчно."""

    # Верхняя граница списка обязательна: `rate_limit_write` считает ЗАПРОСЫ, а
    # не элементы тела, и один POST со 150k id развернулся бы в 150k циклов
    # «SELECT FOR UPDATE → commit → invalidate_cache», где инвалидация — полный
    # SCAN по Redis, общему для ВСЕХ проектов. Назначение машины не меняет
    # статус, поэтому один и тот же id принимается повторно — своего черновика
    # хватило бы для амплификации.
    # Нижней границы намеренно нет: пустой список — законный no-op (вернёт []),
    # отбивать его 422 значит ломать безобидный вызов ради ничего.
    ids: list[int] = Field(default_factory=list, max_length=200)
    payload: TransferAssignVehicle


class AssemblyToTransfer(BaseModel):
    """«Переделать заявку в перемещение».

    Состав берётся из заявки (зеркалу ФФ по количествам не доверяем —
    у migfull total_qty не сводится ни к штукам, ни к SKU). move_ff_links
    по умолчанию False: старые зеркала ФФ остаются историей заявки, на
    переезд вяжутся свежие заявки провайдера (обе стороны переезда).
    """

    to_warehouse_id: int
    comment: str | None = None
    move_ff_links: bool = False


class AssemblyToTransferResult(BaseModel):
    transfer_id: int
    transfer_number: str
    assembly_number: str
    items_count: int
    units_total: int
    ff_links_moved: int = 0
    #: Заявка была активной и отменена конвертацией (иначе её резерв остался бы
    #: висеть на складе, а «Отгрузить» списала бы те же единицы второй раз).
    #: False — заявка была терминальной, её статус не трогали.
    assembly_cancelled: bool = False


# ─── Отчёт «Логистика переездов» ──────────────────────────────────────────
# Источник — ТОЛЬКО заборы переездов (`outbound_shipments.stock_transfer_id IS
# NOT NULL`). Намеренно отдельный от логистической аналитики сборок: там INNER
# JOIN по заявке на сборку, и маршрут «наш склад → наш склад» несопоставим с
# маршрутами на WB — смешение испортило бы медианы и прогнозную модель.
# ₽/паллета считается ТОЛЬКО по паллетным переездам (`shipped_as_boxes=False`):
# у коробочных `pallets_count` — это короба, смешивать их в одну метрику нельзя.
# Коробочный объём выборки виден отдельно (`total_boxes`).


class TransferLogisticsSummary(BaseModel):
    transfers_count: int
    total_cost: Decimal
    avg_cost: Decimal | None = None
    total_units: int
    cost_per_unit: Decimal | None = None
    paid_cost: Decimal
    unpaid_cost: Decimal
    total_pallets: int = 0
    cost_per_pallet: Decimal | None = None
    #: Σ pallets_count по переездам, едущим КОРОБАМИ (shipped_as_boxes=True).
    total_boxes: int = 0
    #: Детализация (`rows`) усечена потолком, СВОДКА при этом полная. UI обязан
    #: показать это явно: без флага «показано 1000 строк» читается как «всего
    #: 1000 переездов», хотя `transfers_count` говорит другое.
    rows_truncated: bool = False


class TransferLogisticsRoute(BaseModel):
    from_warehouse_id: int
    from_warehouse: str
    to_warehouse_id: int
    to_warehouse: str
    transfers_count: int
    total_cost: Decimal
    avg_cost: Decimal | None = None
    total_units: int
    cost_per_unit: Decimal | None = None
    total_pallets: int = 0
    cost_per_pallet: Decimal | None = None


class TransferLogisticsCarrier(BaseModel):
    counterparty_id: int | None = None
    counterparty_name: str | None = None
    transfers_count: int
    total_cost: Decimal
    avg_cost: Decimal | None = None


class TransferLogisticsPeriod(BaseModel):
    period: str
    transfers_count: int
    total_cost: Decimal
    total_units: int
    cost_per_unit: Decimal | None = None
    total_pallets: int = 0
    cost_per_pallet: Decimal | None = None


class TransferLogisticsRow(BaseModel):
    transfer_id: int
    transfer_number: str
    shipment_id: int
    shipment_number: str
    shipped_date: date | None = None
    from_warehouse: str
    to_warehouse: str
    vehicle_info: str | None = None
    counterparty_name: str | None = None
    pickup_cost: Decimal | None = None
    units_total: int
    sku_count: int
    transfer_status: str
    payment_request_number: str | None = None
    is_paid: bool
    #: Транспортная единица переезда: pallets_count — паллеты, либо короба при
    #: shipped_as_boxes=True (одно поле, две единицы — как у заявки на сборку).
    pallets_count: int | None = None
    shipped_as_boxes: bool = False


class TransferLogisticsReport(BaseModel):
    summary: TransferLogisticsSummary
    by_route: list[TransferLogisticsRoute] = []
    by_carrier: list[TransferLogisticsCarrier] = []
    by_period: list[TransferLogisticsPeriod] = []
    rows: list[TransferLogisticsRow] = []


# ─── Stock Movement (Журнал) ──────────────────────────────────────────────


class StockMovementSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    warehouse_id: int
    nomenclature_id: int
    barcode: str
    movement_type: str
    quantity: int
    defect_delta: int = 0
    reference_type: str
    reference_id: int | None = None
    comment: str | None = None
    created_at: datetime | None = None


# ─── Warehouse Stock (Баланс) ─────────────────────────────────────────────


class WarehouseStockSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    warehouse_id: int
    nomenclature_id: int
    barcode: str
    quantity: int
    in_transit: int = 0
    defect_quantity: int = 0
    defect_in_transit: int = 0
    cost_price: Decimal | None = None
    updated_at: datetime | None = None
    reserved: int = 0
    available: int = 0


class CostPriceUpdate(BaseModel):
    cost_price: Decimal


# ─── Stock Adjustment (Корректировка) ─────────────────────────────────────


class StockAdjustmentCreate(BaseModel):
    barcode: str
    delta: int
    reason: str


class StockAdjustmentSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    warehouse_id: int
    nomenclature_id: int
    barcode: str
    delta: int
    reason: str
    created_at: datetime | None = None


# ─── Defect Operations (Брак) ────────────────────────────────────────────


class DefectOperationCreate(BaseModel):
    barcode: str
    quantity: int
    reason: str


class DefectBulkItem(BaseModel):
    barcode: str
    quantity: int


class DefectBulkOperation(BaseModel):
    reason: str
    items: list[DefectBulkItem]


class DefectBulkResponse(BaseModel):
    status: str  # "ok" | "partial" | "error"
    processed: int
    failed: int
    errors: list[dict]  # [{barcode, error}]
    # Optional: set when the bulk op produced a document (mark → operation, receive → receipt, writeoff → shipment)
    operation_id: int | None = None
    receipt_id: int | None = None
    shipment_id: int | None = None
    number: str | None = None


class DefectMarkOperationItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    operation_id: int
    nomenclature_id: int
    barcode: str
    quantity: int


class DefectMarkOperationSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    warehouse_id: int
    number: str
    status: str
    actual_date: date | None = None
    reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    items: list[DefectMarkOperationItemSchema] = []


# ─── Delivery Times (Время доставки до WB) ──────────────────────────────


class DeliveryTimeItem(BaseModel):
    wb_warehouse_name: str
    delivery_days: int = 3


class DeliveryTimesUpdate(BaseModel):
    wb_acceptance_days: int | None = None
    assembly_days: int | None = None
    items: list[DeliveryTimeItem] = []


class DeliveryTimeRow(BaseModel):
    wb_warehouse_name: str
    delivery_days: int
    total_days: int  # assembly_days + delivery_days + wb_acceptance_days


class DeliveryTimesResponse(BaseModel):
    warehouse_id: int
    warehouse_name: str
    assembly_days: int
    wb_acceptance_days: int
    wb_warehouses: list[DeliveryTimeRow]


# ─── WB Acceptance Check (POST /warehouse/acceptance-check) ─────────────────


class AcceptanceCheckItemRequest(BaseModel):
    """Single SKU + its current planned distribution per WB warehouse."""

    nm_id: int
    # max_length: barcode попадает в Redis-ключ пер-баркодного кэша и в payload WB
    # (EAN ≤ 14 симв.) — без капа мусорные строки раздувают кэш (cache-dilution).
    barcode: str = Field(max_length=64)
    distribution: dict[str, int]  # {wb_warehouse_name: qty}


class AcceptanceCheckRequest(BaseModel):
    """Body for POST /warehouse/acceptance-check.

    Caller passes the article's barcode, total qty, and the planned per-WB
    distribution. We call WB API once for all barcodes, then redistribute
    qty away from closed warehouses (gео-aware via warehouse_district).
    """

    # max_length: один запрос = ceil(N/150) живых POST к WB (квота 6/мин) —
    # неограниченный список амплифицирует force-флуд (security-ревью 2026-07-03).
    items: list[AcceptanceCheckItemRequest] = Field(max_length=1000)


class AcceptanceCoefMeta(BaseModel):
    """Coefficients aggregate for one (warehouse, package_type) на ближайшие 14 дней.

    free_days_14   — coefficient ∈ {0, 1} И allowUnload=true (бесплатно)
    paid_days_14   — coefficient ≥ 2 И allowUnload=true (платно × min_coefficient)
    min_coefficient — лучший коэф среди allowUnload=true дней (None если все закрыты)
    """

    free_days_14: int = 0
    paid_days_14: int = 0
    min_coefficient: int | None = None


class AcceptanceFlags(BaseModel):
    """Per-(barcode, wb_warehouse) acceptance flags from WB API.

    can_box / can_monopallet / can_supersafe — итоговый «можно сдать»:
    options.canX = True И (нет coefficients-данных ИЛИ есть хотя бы 1 free_day).
    *_meta — детализация per-type для UI tooltip («свободно N из 14 дней»).
    """

    warehouse_id: int
    can_box: bool
    can_monopallet: bool
    can_supersafe: bool
    box_meta: AcceptanceCoefMeta | None = None
    mono_meta: AcceptanceCoefMeta | None = None
    super_meta: AcceptanceCoefMeta | None = None


class RedistributionMove(BaseModel):
    """One redistribution step: qty moved from a closed wh to an open one."""

    nm_id: int
    barcode: str
    from_warehouse: str
    to_warehouse: str | None  # None = no destination found, qty dropped
    quantity: int
    reason: str  # e.g. "closed", "box-only-required" etc.


class AcceptanceCheckSplit(BaseModel):
    """One sub-assembly: warehouses that share a single package_type.

    Каждый split = одна транспортная единица (одна `AssemblyRequest`).
    Если разные склады принимают разные типы упаковки (Электросталь — только
    моно, Краснодар — только короб), вместо отбраковки половины складов
    создаются 2 split'а с разным `package_type`.
    """

    package_type: str  # BOX | MONOPALLET | SUPERSAFE
    distribution: dict[str, int]
    warnings: list[str] = []


class AcceptanceCheckPerItem(BaseModel):
    """Per-SKU result with availability flags + chosen package_type."""

    nm_id: int
    barcode: str
    # {wb_warehouse_name: {warehouse_id, can_box, can_monopallet, can_supersafe}}
    availability: dict[str, AcceptanceFlags]
    package_type: str  # BOX | MONOPALLET | SUPERSAFE — primary (largest by qty) split
    distribution: dict[str, int]  # primary split distribution (backward-compat)
    splits: list[AcceptanceCheckSplit] = []  # all sub-assemblies (one per package_type)
    warnings: list[str] = []


class AcceptanceCheckResponse(BaseModel):
    """Response for POST /warehouse/acceptance-check."""

    items: list[AcceptanceCheckPerItem]
    moves: list[RedistributionMove]
    checked_at: datetime
    cache_hit: bool = False


# ─── WB Acceptance Limits (GET /warehouse/acceptance-limits) ────────────────


class AcceptanceLimitDay(BaseModel):
    """Один день календаря приёмки для (склад, тип упаковки).

    coefficient — WB-коэффициент: -1 закрыто, 0..1 бесплатно, ≥2 платный
    множитель к базовому тарифу. is_free = coefficient ∈ {0,1} И allow_unload.
    is_closed = coefficient == -1 ИЛИ allow_unload=false.
    """

    date: str  # ISO date (YYYY-MM-DD)
    coefficient: float
    allow_unload: bool
    is_free: bool
    is_closed: bool
    storage_coef: float | None = None
    delivery_coef: float | None = None


class AcceptanceLimitEntry(BaseModel):
    """Календарь одного (склад × тип упаковки) на ближайшие дни."""

    warehouse_id: int
    warehouse_name: str  # raw WB name
    canonical_name: str
    box_type: str  # box | mono | super
    days: list[AcceptanceLimitDay]


class AcceptanceLimitsResponse(BaseModel):
    """Response for GET /warehouse/acceptance-limits — сводный календарь лимитов.

    `dates` — отсортированные ISO-даты (колонки календаря). `warehouses` —
    по одной записи на (склад × тип упаковки), отсортированы по canonical-имени
    и типу (короб → моно → супер).
    """

    warehouses: list[AcceptanceLimitEntry]
    dates: list[str]
    fetched_at: datetime


class SupplyAcceptanceSlotRow(BaseModel):
    """Активная заявка/поставка + календарь слотов приёмки её склада WB.

    Те же дни (`days`), что и в `/warehouse/acceptance-limits`, но привязанные
    к конкретной поставке: склад берётся из ФБО-поставки (или ручного поля),
    тип упаковки — из заявки. `matched=false` — склад заявки не нашёлся в
    календаре коэффициентов (тогда `days` пуст; матч идёт по нормализованному
    имени, т.к. WbFboSupply не хранит numeric warehouseID).
    """

    assembly_request_id: int
    assembly_number: str
    status: str
    wb_supply_id: str | None = None  # ФБО-поставка WB (напр. «39950266»)
    warehouse_name: str | None = None  # effective: FBO warehouse_name или ручной
    canonical_name: str  # нормализованное имя склада (ключ матча/группировки)
    warehouse_id: int | None = None  # WB warehouseID, если склад сматчился
    box_type: str  # box | mono | super (из package_type заявки)
    package_type: str  # BOX | MONOPALLET | SUPERSAFE
    planned_date: date | None = None  # плановая «Сдача ВБ»
    actual_date: date | None = None
    wb_fbo_status: str | None = None
    matched: bool  # склад заявки найден в календаре приёмки
    days: list[AcceptanceLimitDay]


class SupplyAcceptanceSlotsResponse(BaseModel):
    """Response for GET /warehouse/acceptance-slots — слоты сдачи по поставкам.

    `rows` — по одной на активную заявку, отсортированы по складу и плановой
    дате (фронт группирует по `canonical_name`). `dates` — общая ось дат
    (ISO, 14 дней WB), как в календаре лимитов.
    """

    rows: list[SupplyAcceptanceSlotRow]
    dates: list[str]
    fetched_at: datetime


# ─── Barcode Eligibility (POST /warehouse/barcode-eligibility) ──────────────


class BarcodeEligibilityRequest(BaseModel):
    """Body for POST /warehouse/barcode-eligibility.

    Caller passes a flat list of barcodes; we return per-barcode the eligible
    WB target warehouses (by acceptance options/limits) и свободный ФФ-остаток
    по складам-источникам. Reuses the cached WB acceptance layer.

    `max_length` ограничивает фан-аут в WB acceptance/options (≤150 баркодов/чанк,
    бюджет WB 6 req/min): без капа большой paste каталога раздул бы один запрос в
    несколько живых WB-вызовов и выжег лимит приёмки на весь проект.
    """

    barcodes: list[str] = Field(..., max_length=500)


class BarcodeEligibilityTarget(BaseModel):
    """Один WB-склад, куда баркод можно сдать (по options/лимитам приёмки).

    Склад считается eligible если хотя бы один из can_box/can_monopallet/
    can_supersafe = True. `no_limit` — eligible по options, но публичный лимит
    закрыт на все ближайшие дни (free_days_14 == 0 И paid_days_14 == 0): сдавать
    можно только предзаявкой (UI рисует ⌛). free/paid_days_14 — лучшие метрики
    среди трёх типов упаковки (для tooltip).
    """

    wb_name: str
    can_box: bool
    can_monopallet: bool
    can_supersafe: bool
    free_days_14: int = 0
    paid_days_14: int = 0
    no_limit: bool = False


class BarcodeFfStock(BaseModel):
    """Свободный остаток баркода на одном ФФ-складе-источнике.

    available = quantity − in_assembly_reserved (зарезервировано активными
    сборками с этого склада). Возвращаются только склады с available > 0.
    """

    ff_id: int
    ff_name: str
    available: int


class BarcodeEligibilityItem(BaseModel):
    """Результат по одному резолвленному баркоду."""

    nm_id: int
    vendor_code: str
    barcode: str
    targets: list[BarcodeEligibilityTarget]
    ff_stock: list[BarcodeFfStock]


class BarcodeEligibilityResponse(BaseModel):
    """Response for POST /warehouse/barcode-eligibility.

    `items` — по одному на резолвленный баркод; `unknown` — баркоды без
    Nomenclature в проекте (не падаем, просто перечисляем).
    """

    items: list[BarcodeEligibilityItem]
    unknown: list[str]
    checked_at: datetime

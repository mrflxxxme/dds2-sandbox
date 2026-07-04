# ruff: noqa: RUF001, RUF002, RUF003
"""
Assembly Request schemas: Pydantic request/response models.
See backend/DOMAIN_ASSEMBLY.md for full spec.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

PackageTypeStr = Literal["BOX", "MONOPALLET", "SUPERSAFE"]


class FfLinkInfo(BaseModel):
    """Одна привязанная ФФ-заявка (зеркало фулфилмента) для деталки/списка сборки.

    Сборка может быть связана с НЕСКОЛЬКИМИ ФФ-заявками (provider=migfull / «Натали»:
    одной нашей заявке у них соответствуют 2+ заявки) — поэтому ff_links это список,
    а одиночные поля ff_request_* остаются как первая привязка для обратной совместимости.
    """

    ff_request_id: int
    ff_request_number: str | None = None
    ff_stage_title: str | None = None
    ff_warehouse_id: int | None = None


class JointSibling(BaseModel):
    """Соседняя сборка той же совместной WB-поставки («Совместного номера»).

    Совместная поставка = одна WB FBO-поставка несёт ≥2 сборок (по одной на
    ФФ-источник, напр. wms + wms2). siblings — ДРУГИЕ сборки той же поставки
    (без самой текущей), для бейджа/тултипа «Совместная · wms+wms2 → ASM-555».

    На листе логиста из siblings строится разбивка забора: склад-источник,
    паллеты, вес, статус и внутренний номер заявки в ФФ-портале (своя на каждый
    склад). Поэтому здесь же pallets_count / pallet_weight_kg / ff_request_number.
    """

    assembly_id: int
    number: str
    warehouse_id: int
    warehouse_name: str | None = None
    status: str
    pallets_count: int | None = None
    pallet_weight_kg: Decimal | None = None
    ff_request_number: str | None = None  # внутренний номер заявки в ФФ-портале (склада-источника)


# ─── Раскладка по паллетам (pallet manifest) ────────────────────────────────
# Ручная перетасовка коробов по паллетам на деталке сборки. Хранится в JSONB
# AssemblyRequest.pallet_manifest как список PalletBox; NULL = «авто» (раскладка
# считается на лету). Инвариант сохранения (строгий, иначе 409):
#   Σ(box_count · box_qty[barcode] + loose_units) по всем паллетам == quantity позиции.


class BoxContent(BaseModel):
    """Содержимое одного SKU внутри паллеты: целые короба + хвост-россыпь."""

    barcode: str
    box_count: int = 0  # целых коробов этого SKU на паллете
    loose_units: int = 0  # штук россыпью (хвост < кратности короба)


class PalletBox(BaseModel):
    """Одна физическая паллета: номер + короба/россыпь по SKU."""

    pallet_no: int
    boxes: list[BoxContent] = []


class PalletManifest(BaseModel):
    """Полная раскладка отгрузки по паллетам (список PalletBox)."""

    pallets: list[PalletBox] = []


class PalletManifestUpdate(BaseModel):
    """Тело PATCH .../pallet-manifest.

    pallets=null → сброс к «авто» (очистить поле); непустой список → сохранить
    ручную раскладку (проходит строгий инвариант Σ==quantity, иначе 409)."""

    pallets: list[PalletBox] | None = None


# ─── Request schemas ────────────────────────────────────────────────────────


class AssemblyItemCreate(BaseModel):
    barcode: str
    quantity: int


class AssemblyRequestCreate(BaseModel):
    warehouse_id: int
    wb_fbo_supply_id: int | None = None
    estimated_ready_date: date | None = None
    pallets_count: int
    pallet_weight_kg: Decimal
    comment: str | None = None
    wb_warehouse_name_manual: str | None = None
    package_type: PackageTypeStr = "BOX"
    items: list[AssemblyItemCreate]


class AssemblyRequestUpdate(BaseModel):
    warehouse_id: int | None = None  # сменить склад-источник (только до отгрузки)
    wb_fbo_supply_id: int | None = None
    estimated_ready_date: date | None = None
    pallets_count: int | None = None
    pallet_weight_kg: Decimal | None = None
    comment: str | None = None
    wb_warehouse_name_manual: str | None = None
    package_type: PackageTypeStr | None = None
    items: list[AssemblyItemCreate] | None = None  # only PENDING/IN_PROGRESS
    # Vehicle & cost — editable even after shipping
    pickup_cost: Decimal | None = None
    vehicle_info: str | None = None
    vehicle_brand: str | None = None
    driver_phone: str | None = None
    carrier_inn: str | None = None
    carrier_name: str | None = None


class AssignVehicle(BaseModel):
    vehicle_info: str
    vehicle_brand: str
    driver_phone: str
    pickup_date: date
    pickup_time_slot: str
    pickup_cost: Decimal
    delivery_date: date
    carrier_inn: str | None = None
    carrier_name: str | None = None


class BulkAssignItem(BaseModel):
    request_id: int
    pickup_date: date
    pickup_time_slot: str
    pickup_cost: Decimal
    delivery_date: date


class AssignVehicleBulk(BaseModel):
    vehicle_info: str
    vehicle_brand: str
    driver_phone: str
    carrier_inn: str | None = None
    carrier_name: str | None = None
    items: list[BulkAssignItem]


class ShipBulk(BaseModel):
    ids: list[int]


class DeleteBulk(BaseModel):
    ids: list[int]


class BulkDeleteSkip(BaseModel):
    """Одна пропущенная при массовом удалении заявка (с причиной)."""

    id: int
    number: str | None = None
    status: str | None = None
    reason: str


class BulkDeleteResult(BaseModel):
    """Итог массового удаления: сколько удалено + что пропущено и почему."""

    deleted: int
    skipped: list[BulkDeleteSkip] = []


class StatusBulk(BaseModel):
    """Массовый перевод заявок в статус — один запрос вместо N поштучных
    (поштучные быстро съедают общий write-лимит → 429 «Слишком много запросов»)."""

    ids: list[int]
    status: Literal["IN_PROGRESS", "READY"]


class BulkStatusSkip(BaseModel):
    """Одна пропущенная при массовой смене статуса заявка (с причиной)."""

    id: int
    number: str | None = None
    status: str | None = None
    reason: str


# ─── Response schemas ───────────────────────────────────────────────────────


class AssemblyItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nomenclature_id: int
    barcode: str
    quantity: int
    product_name: str | None = None
    article: str | None = None
    brand: str | None = None
    stock_quantity: int = 0


class FfProposedItem(BaseModel):
    """Позиция предложенной ФФ-оператором правки состава (ожидает согласования)."""

    barcode: str
    quantity: int
    product_name: str | None = None
    article: str | None = None


class FfReviewAction(BaseModel):
    """Решение по предложенной ФФ правке состава: применить или отклонить."""

    action: Literal["approve", "reject"]


class AssemblyRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    warehouse_id: int
    warehouse_name: str | None = None
    number: str
    status: str
    wb_fbo_supply_id: int | None = None
    wb_supply_name: str | None = None  # wb_fbo_supplies.name
    wb_warehouse_name: str | None = None  # wb_fbo_supplies.warehouse_name (WB destination)
    wb_supply_id_wb: str | None = None  # wb_fbo_supplies.wb_supply_id (WB-I-xxxx)
    wb_fbo_status: str | None = None  # wb_fbo_supplies.wb_status (ACTIVE/ON_DELIVERY/...)
    wb_fbo_planned_date: date | None = None  # wb_fbo_supplies.planned_date
    wb_fbo_actual_date: date | None = None  # wb_fbo_supplies.actual_date
    outbound_shipment_id: int | None = None
    estimated_ready_date: date | None = None
    actual_ready_date: date | None = None
    pallets_count: int
    pallet_weight_kg: Decimal
    total_weight_kg: Decimal | None = None  # computed: pallets x weight (тара, ручной)
    # Ручная раскладка коробов по паллетам (NULL/[] = «авто», считается на лету).
    pallet_manifest: list[PalletBox] | None = None
    # Расчётный вес товаров (нетто) = Σ(quantity × Nomenclature.weight_kg[barcode]).
    # None — если ни у одной позиции нет веса. Показывается отдельно, ручной
    # pallet_weight_kg НЕ перезаписывает.
    goods_weight_kg: Decimal | None = None
    # ШК позиций без веса в справочнике (дозаполнить в настройках).
    weight_missing_barcodes: list[str] = []
    vehicle_info: str | None = None
    vehicle_brand: str | None = None
    driver_phone: str | None = None
    pickup_date: date | None = None
    pickup_time_slot: str | None = None
    pickup_cost: Decimal | None = None
    delivery_date: date | None = None
    vehicle_assigned_at: datetime | None = None
    shipped_at: datetime | None = None
    counterparty_id: int | None = None
    carrier_inn: str | None = None
    carrier_name: str | None = None
    comment: str | None = None
    wb_warehouse_name_manual: str | None = None
    source_draft_id: int | None = None  # черновик-источник (поток распределения сборки)
    # Предраспределение машины в пути: заявка создана под входящий товар машины
    # (cost_orders.id) ДО физприёмки. На разгрузке статус авто → IN_PROGRESS, а
    # is_pre_distribution остаётся True навсегда (бейдж/отчёты). order_no — для бейджа
    # «Предраспределение машины {order_no}» (enrich в списке/деталке, иначе None).
    source_vehicle_id: int | None = None
    is_pre_distribution: bool = False
    source_vehicle_order_no: str | None = None
    # Предзаявка (бронь) на моно: целая моно-паллета на WB-склад без лимита приёмки (⌛).
    is_prebooking: bool = False
    package_type: PackageTypeStr = "BOX"
    effective_wb_warehouse: str | None = None  # FBO warehouse_name or manual, whichever is set
    brands: str | None = None  # comma-separated unique brands from items
    items: list[AssemblyItemResponse] = []
    created_at: datetime
    updated_at: datetime
    # Привязанная ФФ-заявка (зеркало фулфилмента) — заполняется в GET деталки и списке.
    # ff_request_* — ПЕРВАЯ привязка (обратная совместимость); ff_links — все привязки
    # (migfull/«Натали» допускает 2+ ФФ-заявки на одну нашу сборку).
    ff_request_id: int | None = None
    ff_request_number: str | None = None
    ff_stage_title: str | None = None
    ff_warehouse_id: int | None = None
    ff_links: list[FfLinkInfo] | None = None
    # Состав сборки расходится с привязанной заявкой(ами) ФФ по наполнению
    # (True — расхождение, False — совпадает, None — определить нельзя)
    ff_mismatch: bool | None = None
    # ФФ предложил правку состава, ожидает согласования в DDS («Согласовать»/«Отказать»).
    # ff_proposed_items не None ⇒ «ожидает согласования».
    ff_review_pending: bool = False
    ff_proposed_items: list[FfProposedItem] | None = None
    ff_proposed_at: datetime | None = None
    ff_proposed_by: str | None = None
    # Совместная поставка: эта сборка делит WB FBO-поставку с другими сборками
    # (≥2 сборок на одну поставку, по одной на ФФ-источник). joint_siblings —
    # ДРУГИЕ сборки той же поставки (для бейджа «Совместная» и тултипа).
    joint_supply: bool = False
    joint_siblings: list[JointSibling] | None = None
    # Совместная готова к логисту/назначению машины: ВСЕ активные сборки поставки
    # в READY и дальше (ни одна не PENDING/IN_PROGRESS). Гейтит кнопку «Назначить
    # машину» — машина назначается на всю совместную поставку, только когда готовы все.
    joint_ready: bool = False
    # Сумма паллет/веса по всем активным сборкам поставки (что грузим в одну машину).
    joint_total_pallets: int | None = None
    joint_total_weight_kg: Decimal | None = None
    # По заявке есть активная (SENT/MATCHED) отправка в Газельку — логистику ведёт
    # агрегатор. Гейтит ручное «Назначить машину» (бэк-валидация + дизейбл на фронте).
    via_gazelka: bool = False


class AssemblyListResponse(BaseModel):
    items: list[AssemblyRequestResponse]
    total: int


class BulkStatusResult(BaseModel):
    """Итог массовой смены статуса: обновлённые заявки + что пропущено и почему."""

    updated: list[AssemblyRequestResponse] = []
    skipped: list[BulkStatusSkip] = []


# ─── Предраспределение машины в пути (pre-distribution) ─────────────────────
# Машина (CostOrder CUSTOMS/DISPATCHED) везёт товар, ещё не на ФФ. До приёмки
# раскладываем её входящий товар по WB-складам как заявки на сборку со статусом
# PRE_DISTRIBUTED (без реального стока). На разгрузке машины (accept_receipt)
# заявки авто → IN_PROGRESS. См. .claude/PREDIST_DESIGN.md.


class PreDistVehicle(BaseModel):
    """Машина в пути, доступная (или нет) для предраспределения."""

    id: int
    order_no: str
    status: str  # CUSTOMS | DISPATCHED
    target_warehouse_id: int | None = None  # ФФ-склад разгрузки (источник будущих сборок)
    target_warehouse_name: str | None = None
    eta: date | None = None  # estimated_arrival_date
    total_qty: int  # Σ qty товара на машине (по позициям)
    sku_count: int  # уникальных barcode
    distributed_qty: int = 0  # уже разнесено в заявки этой машины (не CANCELLED)
    can_distribute: bool = True  # target_warehouse_id задан и это FULFILLMENT-склад
    block_reason: str | None = None  # почему нельзя (если can_distribute=False)


class PreDistPoolRow(BaseModel):
    """Строка пула машины: товар + сколько ещё можно предраспределить."""

    barcode: str
    article_seller: str | None = None
    article_wb: str | None = None
    name: str | None = None
    brand: str | None = None
    gross_qty: int  # всего на машине
    distributed_qty: int  # уже разнесено в заявки этой машины
    available_qty: int  # max(0, gross − distributed)


class PreDistVehiclePool(BaseModel):
    vehicle: PreDistVehicle
    rows: list[PreDistPoolRow]


class PreDistRow(BaseModel):
    """Одна строка раскладки: сколько ШК отправить на конкретный WB-склад."""

    barcode: str
    wb_warehouse_name: str  # склад назначения WB (станет wb_warehouse_name_manual заявки)
    qty: int
    package_type: PackageTypeStr = "BOX"


class PreDistributionCreate(BaseModel):
    """Создать предраспределение: строки группируются в заявки по (WB-склад, упаковка).

    Источник-склад заявок = vehicle.target_warehouse_id (ФФ разгрузки), не задаётся
    вручную. wb_fbo_supply_id допустим только при одном WB-складе назначения.
    """

    vehicle_id: int
    wb_fbo_supply_id: int | None = None
    rows: list[PreDistRow]


class PreDistributionCreateResult(BaseModel):
    created: int  # сколько заявок создано
    request_ids: list[int]
    requests: list[AssemblyRequestResponse]


# ─── Предзаявка (бронь) на моно ─────────────────────────────────────────────
# Целая моно-паллета на WB-склад БЕЗ лимита приёмки (⌛) — сдать можно только
# предзаявкой. Заявка на сборку создаётся сразу с флагом is_prebooking (реальный
# сток на ФФ, статус IN_PROGRESS). Источник — ФФ-склад, где лежит товар предброни.


class PrebookingRow(BaseModel):
    """Одна строка предзаявки: ШК × ФФ-источник → WB-склад назначения (моно)."""

    warehouse_id: int  # ФФ-склад-источник (где реально лежит товар предброни)
    barcode: str
    wb_warehouse_name: str  # склад назначения WB (→ wb_warehouse_name_manual заявки)
    qty: int
    package_type: PackageTypeStr = "MONOPALLET"


class PrebookingCreate(BaseModel):
    """Создать предзаявки: строки группируются в заявки по (ФФ-источник, WB-склад,
    упаковка) → одна заявка на группу. Флаг is_prebooking=True на каждой."""

    rows: list[PrebookingRow]


class PrebookingCreateResult(BaseModel):
    created: int
    request_ids: list[int]
    requests: list[AssemblyRequestResponse]


class PreDistAdvanceResult(BaseModel):
    advanced: int  # сколько PRE_DISTRIBUTED-заявок переведено в IN_PROGRESS


class CreatedRequestBrief(BaseModel):
    """Короткая карточка созданной заявки внутри группы-предпросмотра."""

    id: int
    number: str
    ff_id: int
    ff_name: str
    wb_name: str | None
    package_type: str
    status: str
    qty: int
    sku: int


class CreatedGroupResponse(BaseModel):
    """Группа созданных заявок одного черновика («Предпросмотр созданных»).
    Группировка по source_draft_id; только активные (IN_PROGRESS) заявки."""

    draft_id: int
    draft_name: str | None
    request_count: int
    total_qty: int
    total_sku: int
    requests: list[CreatedRequestBrief]


class RefreshFromFboResponse(BaseModel):
    added: int
    removed: int
    changed: int
    items: list[AssemblyItemResponse]
    # ШК из WB-состава, которых нет в номенклатуре проекта — не добавлены в заявку
    # (резолв упал), но рефреш не валим: показываем пользователю, что пропустили.
    skipped: list[str] = []


class AssemblyHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    old_status: str | None
    new_status: str
    changed_at: datetime
    changed_by: str | None
    comment: str | None


class StockDeficit(BaseModel):
    barcode: str
    need: int
    have: int


# ─── Shipping attempts (цепочка попыток отгрузки) ──────────────────────────


class ReturnToWarehouse(BaseModel):
    """Возврат отгрузки на склад (WB не принял). По умолчанию — склад-источник;
    можно вернуть на другой склад (return_warehouse_id)."""

    return_warehouse_id: int | None = None
    comment: str | None = None


class AssemblyAttempt(BaseModel):
    """Одна попытка отгрузки заявки (= один OutboundShipment + опц. возврат).

    Снимок логистики берётся с отгрузки (на момент отгрузки), исход выводится:
    accepted — WB принял; rejected — оформлен возврат на склад; in_transit — едет.
    """

    attempt_no: int
    shipment_id: int
    shipment_number: str | None = None
    shipped_at: datetime | None = None  # момент отгрузки (created_at отгрузки)
    wb_supply_id: str | None = None  # FBW-... (снимок на отгрузке)
    wb_supply_name: str | None = None  # имя связанной FBO-поставки
    wb_warehouse_name: str | None = None  # склад WB (город сдачи) — снимок destination
    wb_fbo_status: str | None = None  # статус связанной FBO-поставки (ACTIVE/ACCEPTED/...)
    vehicle_info: str | None = None
    vehicle_brand: str | None = None
    driver_phone: str | None = None
    carrier_inn: str | None = None
    carrier_name: str | None = None
    pickup_cost: Decimal | None = None
    pallets_count: int | None = None
    pickup_date: date | None = None
    delivery_date: date | None = None
    outcome: Literal["accepted", "rejected", "in_transit"]
    returned_to_warehouse_id: int | None = None
    returned_to_warehouse_name: str | None = None
    returned_at: datetime | None = None  # момент возврата (created_at приёмки-возврата)


# ─── Logistics analytics ───────────────────────────────────────────────────


class LogisticsCostSummary(BaseModel):
    total_cost: Decimal
    avg_cost_per_pallet: Decimal
    total_pallets: int
    total_shipments: int
    # Расширенные метрики периода (task: расширенная аналитика).
    total_requests: int = 0  # distinct заявок (сборок) в отгрузках
    total_items: int = 0  # суммарно штук товара по отгрузкам (OutboundShipmentItem.quantity)
    total_skus: int = 0  # distinct номенклатур
    total_weight_kg: Decimal = Decimal("0")  # суммарный вес (паллеты × вес паллеты)
    total_destinations: int = 0  # уникальных складов сдачи
    total_carriers: int = 0  # уникальных подрядчиков (с привязкой)


class LogisticsRouteStat(BaseModel):
    src_warehouse: str
    dest_warehouse: str
    avg_cost: Decimal
    shipments_count: int


class LogisticsDestStat(BaseModel):
    dest_warehouse: str
    avg_cost: Decimal  # средняя ₽/паллета
    total_cost: Decimal
    shipments_count: int
    # Контекст объёма (task: стоимость склада в зависимости от кол-ва паллет).
    total_pallets: int = 0
    avg_pallets: Decimal = Decimal("0")  # средний размер отгрузки в паллетах
    min_cost_per_pallet: Decimal = Decimal("0")
    max_cost_per_pallet: Decimal = Decimal("0")


class LogisticsCarrierStat(BaseModel):
    """Сводка по подрядчику-перевозчику (top по подрядчикам)."""

    counterparty_id: int | None = None
    carrier_inn: str | None = None
    carrier_name: str
    shipments_count: int
    total_pallets: int
    total_cost: Decimal
    avg_cost_per_pallet: Decimal
    destinations_count: int  # на сколько разных складов возит


class LogisticsPalletBucketStat(BaseModel):
    """Зависимость ₽/паллета от размера отгрузки (эффект объёма: больше паллет — дешевле)."""

    bucket: str  # "1", "2-3", "4-5", "6-10", "11+"
    sort_order: int
    shipments_count: int
    avg_pallets: Decimal
    total_pallets: int
    total_cost: Decimal
    avg_cost_per_pallet: Decimal


class LogisticsCostPoint(BaseModel):
    """Точка для scatter-графика: размер отгрузки vs ₽/паллета по складу."""

    dest_warehouse: str
    pallets: int
    cost_per_pallet: Decimal
    shipped_date: date | None = None


class LogisticsDestBucketCell(BaseModel):
    """Ячейка матрицы «склад сдачи × размер отгрузки → ₽/паллета».

    Кривая эффекта объёма отдельно по каждому складу (а не только в среднем).
    """

    dest_warehouse: str
    bucket: str  # "1", "2-3", "4-5", "6-10", "11+"
    sort_order: int
    shipments_count: int
    total_pallets: int
    avg_cost_per_pallet: Decimal


class LogisticsAnomaly(BaseModel):
    """Аномальная отгрузка: без стоимости / завышенная / заниженная ₽/паллета."""

    shipment_id: int
    assembly_request_id: int | None = None
    assembly_number: str | None = None
    dest_warehouse: str
    carrier_name: str | None = None
    pallets_count: int | None = None
    pickup_cost: Decimal | None = None
    cost_per_pallet: Decimal | None = None
    shipped_date: date | None = None
    anomaly_type: Literal["no_cost", "overpriced", "underpriced"]
    severity: Decimal  # «насколько» отклонение (z-подобная мера; для no_cost = 0)
    expected_low: Decimal | None = None  # ожидаемый коридор ₽/паллета по складу
    expected_high: Decimal | None = None
    reason: str


class LogisticsAnalyticsResponse(BaseModel):
    summary: LogisticsCostSummary
    by_destination: list[LogisticsDestStat]
    by_route: list[LogisticsRouteStat]
    by_carrier: list[LogisticsCarrierStat] = []
    pallet_buckets: list[LogisticsPalletBucketStat] = []
    dest_pallet_cells: list[LogisticsDestBucketCell] = []
    cost_points: list[LogisticsCostPoint] = []
    anomalies: list[LogisticsAnomaly] = []


# ─── Logistics shipments list (История отправок — построчно) ────────────────


class LogisticsShipmentRow(BaseModel):
    """Одна отгрузка (попытка) для таблицы «История отправок».

    Источник — OutboundShipment (а не AssemblyRequest): переотгрузки видны
    отдельными строками, что совпадает с аналитикой по попыткам отгрузки.
    """

    shipment_id: int
    attempt_no: int = 1
    assembly_request_id: int | None = None
    assembly_number: str | None = None
    status: str | None = None  # статус заявки
    brands: str | None = None
    src_warehouse: str | None = None
    dest_warehouse: str | None = None
    counterparty_id: int | None = None
    carrier_inn: str | None = None
    carrier_name: str | None = None
    wb_supply_id: str | None = None  # FBW-...
    wb_supply_name: str | None = None
    wb_fbo_status: str | None = None
    wb_fbo_planned_date: date | None = None
    wb_fbo_actual_date: date | None = None
    pallets_count: int | None = None
    pickup_cost: Decimal | None = None
    cost_per_pallet: Decimal | None = None
    total_weight_kg: Decimal | None = None
    shipped_date: date | None = None
    shipped_at: datetime | None = None
    anomaly_type: Literal["no_cost", "overpriced", "underpriced"] | None = None
    via_gazelka: bool = False  # отгрузка ушла через интеграцию с Газелькой


class LogisticsShipmentListResponse(BaseModel):
    items: list[LogisticsShipmentRow]
    total: int
    truncated: bool = False  # True — вернули кап (период шире, чем лимит строк)


# ─── Cost forecast (прогноз стоимости неназначенных заявок) ─────────────────


class CostForecastBucket(BaseModel):
    """Прогноз ₽/паллета для склада сдачи при конкретном размере отгрузки."""

    bucket: str  # "1", "2-3", "4-5", "6-10", "11+"
    sort_order: int
    cpp: Decimal  # медианная ₽/паллета
    low: Decimal  # p25 коридора
    high: Decimal  # p75 коридора
    sample_size: int


class CostForecastWarehouse(BaseModel):
    dest_warehouse: str
    cpp: Decimal  # медиана по складу (все размеры)
    low: Decimal
    high: Decimal
    sample_size: int
    buckets: list[CostForecastBucket]


class CostForecastResponse(BaseModel):
    """Модель прогноза стоимости перевозки по истории отгрузок.

    Фронт по (склад сдачи, кол-во паллет заявки) выбирает уровень:
    склад+размер → склад → глобально (по убыванию точности).
    """

    global_cpp: Decimal
    global_low: Decimal
    global_high: Decimal
    sample_size: int
    warehouses: list[CostForecastWarehouse]


# ─── Flow analytics (анализ потока сборки) ─────────────────────────────────
# Зеркало TS-контракта: frontend-react/src/types/api.ts, блок
# «Анализ потока сборки (flow analytics)».

AssemblyAnomalyKind = Literal[
    "stuck_assembly",  # IN_PROGRESS дольше порога
    "stuck_shipment",  # READY/VEHICLE_ASSIGNED дольше порога от готовности
    "wb_accepted_not_shipped",  # ВБ уже принял поставку, но заявка не отгружена
    "ff_closed_not_shipped",  # ФФ закрыл/заархивировал заявку, а наша сборка ещё не отгружена
    "shipped_not_accepted",  # SHIPPED дольше порога без DELIVERED
]


class AssemblyStageDuration(BaseModel):
    stage: str  # IN_PROGRESS | READY | VEHICLE_ASSIGNED | SHIPPED
    avg_days: float | None
    median_days: float | None
    count: int  # на скольких заявках посчитано


class AssemblyTransitionStat(BaseModel):
    from_status: str | None  # null = создание заявки
    to_status: str
    count: int
    avg_days: float | None  # среднее время в from_status до перехода, дни


class AssemblyAnomalyRow(BaseModel):
    id: int
    number: str
    status: str
    warehouse_id: int
    warehouse_name: str | None
    wb_warehouse_name: str | None
    kind: AssemblyAnomalyKind
    days_stuck: int  # сколько дней висит на текущем этапе
    since: str | None  # ISO — начало текущего этапа
    total_qty: int
    wb_fbo_status: str | None  # статус связанной WB FBO-поставки
    # Дефолты — на случай записи в кэше от прошлой версии без этих ключей.
    wb_supply_number: str | None = None  # номер WB-поставки (wb_fbo_supplies.wb_supply_id)
    pallets_count: int = 0
    ff_request_number: str | None = None  # номер ФФ-заявки (для ff_closed_not_shipped)


class AssemblyWarehouseFlowStat(BaseModel):
    warehouse_id: int
    warehouse_name: str | None
    active_count: int
    avg_cycle_days: float | None
    anomaly_count: int


class AssemblyFlowSummary(BaseModel):
    active_count: int  # заявок в работе сейчас (IN_PROGRESS..SHIPPED)
    completed_in_period: int  # дошло до DELIVERED («Принято ВБ») за период; CLOSED (возврат) не считается
    avg_cycle_days: float | None  # создание → отгрузка, дни
    avg_assembly_days: float | None  # создание → READY, дни
    anomaly_count: int


class AssemblyFlowThresholds(BaseModel):
    assembly_days: int
    ship_days: int
    delivery_days: int


class AssemblyFlowDailyStat(BaseModel):
    date: str  # ISO YYYY-MM-DD
    created_count: int  # заявок создано в этот день
    created_qty: int  # товаров (шт) в созданных заявках
    shipped_count: int  # заявок отгружено в этот день
    avg_cycle_days: float | None  # средний цикл (создание → отгрузка) отгруженных в этот день


class AssemblyFlowStageDailyStat(BaseModel):
    """Товары по этапам на конец дня (снимок): сколько шт лежало в каждом этапе."""

    date: str  # ISO YYYY-MM-DD
    in_progress_qty: int
    ready_qty: int
    vehicle_assigned_qty: int
    shipped_qty: int


class AssemblyFlowAnalyticsResponse(BaseModel):
    summary: AssemblyFlowSummary
    stages: list[AssemblyStageDuration]
    transitions: list[AssemblyTransitionStat]
    by_warehouse: list[AssemblyWarehouseFlowStat]
    anomalies: list[AssemblyAnomalyRow]
    # Дефолты — на случай записи в кэше от прошлой версии без этих ключей.
    daily: list[AssemblyFlowDailyStat] = []
    stage_daily: list[AssemblyFlowStageDailyStat] = []
    thresholds: AssemblyFlowThresholds


# ─── Связи и расхождения (link anomalies) ──────────────────────────────────
# Вкладка «Связи и расхождения» на странице «Анализ сборки». Зеркало TS-контракта:
# frontend-react/src/types/api.ts, блок «Связи и расхождения сборки».


class FfMismatchRow(BaseModel):
    """Сборка, состав которой расходится с привязанными заявками ФФ.

    Считается по зеркалу (compute_doc_ff_mismatch), только по активным сборкам
    в статусах IN_PROGRESS / READY / VEHICLE_ASSIGNED («в сборке» + «готово»).
    """

    assembly_id: int
    number: str
    status: str
    warehouse_id: int
    warehouse_name: str | None
    ff_request_numbers: list[str]
    our_total: int  # наш итог, шт
    ff_total: int  # итог по привязанным заявкам ФФ, шт
    diff: int  # ff_total - our_total (знаковая разница)
    mode: Literal["barcode", "total"]  # сверка по ШК либо по суммарному кол-ву


class UnlinkedAssemblyRow(BaseModel):
    """Наша сборка на ФФ-складе без привязанной заявки ФФ."""

    assembly_id: int
    number: str
    status: str
    warehouse_id: int
    warehouse_name: str | None
    provider: str | None  # провайдер ФФ-интеграции склада, если определён
    total_qty: int
    created_at: str | None  # ISO
    age_days: int  # сколько дней заявка живёт без привязки


class UnlinkedFfRow(BaseModel):
    """Заявка ФФ без привязанной нашей сборки (kind=assembly, не архив)."""

    ff_request_id: int
    provider: str
    number: str | None
    warehouse_id: int
    warehouse_name: str | None
    stage_title: str | None
    status: str | None
    total_qty: int | None
    external_created_at: str | None  # ISO


class FboAnomalySupply(BaseModel):
    """Одна аномальная FBO-поставка (для разворота-списка с drill на /warehouse/fbo-supplies)."""

    supply_id: int
    wb_supply_id: str | None  # WB-I-xxxx — для deep-link и показа
    name: str | None
    warehouse_name: str | None  # склад ВБ (город сдачи)
    total_qty: int
    accepted_qty: int
    diff: int  # accepted_qty - total_qty (<0 — недоприёмка, >0 — излишек)
    planned_date: str | None  # ISO
    actual_date: str | None  # ISO
    assembly_request_number: str | None  # привязанная сборка, если есть


class FboAnomalyRollup(BaseModel):
    """Сводка аномалий FBO-поставок ВБ (drill-through на /warehouse/fbo-supplies)."""

    without_assembly_count: int  # ACCEPTED-поставка без нашей заявки на сборку
    under_accepted_count: int  # недоприёмка (необработанная)
    under_accepted_qty: int  # суммарно недопринято, шт
    excess_count: int  # излишек (необработанный)
    excess_qty: int  # суммарно излишек, шт
    # Списки самих поставок (cap 50/категория, новые сверху). Дефолты — на случай
    # записи в кэше от прошлой версии без этих ключей.
    without_assembly_supplies: list[FboAnomalySupply] = []
    under_accepted_supplies: list[FboAnomalySupply] = []
    excess_supplies: list[FboAnomalySupply] = []


class StockMismatchSkuRow(BaseModel):
    """Построчное расхождение остатка по SKU: наш склад vs ФФ-зеркало."""

    barcode: str
    article_seller: str | None
    brand: str | None
    name: str | None
    ff_good: int  # у ФФ (зеркало провайдера), штук россыпи
    our_quantity: int  # у нас годный (WarehouseStock.quantity)
    our_defect: int = 0  # у нас брак (учтён в diff только для migfull)
    diff: int  # ff_good − (our_quantity + our_defect для migfull); >0 — у ФФ больше


class StockMismatchWarehouseRow(BaseModel):
    """Расхождение остатка по ФФ-интегрированному складу (наш склад vs API-зеркало)."""

    warehouse_id: int
    warehouse_name: str | None
    provider: str | None  # провайдер ФФ-интеграции склада
    surplus_ff_qty: int  # суммарно у ФФ больше, штук
    surplus_ff_sku: int  # на скольких SKU у ФФ больше
    surplus_our_qty: int  # суммарно у нас больше, штук
    surplus_our_sku: int  # на скольких SKU у нас больше
    net_diff: int  # surplus_ff_qty - surplus_our_qty (нетто ФФ − наш)
    sku_total: int  # всего SKU с расхождением
    truncated: bool  # rows обрезаны до лимита (на складе больше расхождений)
    synced_at: str | None  # ISO — последний синк остатков ФФ
    rows: list[StockMismatchSkuRow]  # построчно, |diff| desc (cap)


class LinkAnomaliesResponse(BaseModel):
    ff_composition_mismatch: list[FfMismatchRow]
    assemblies_without_ff: list[UnlinkedAssemblyRow]
    ff_without_assembly: list[UnlinkedFfRow]
    fbo: FboAnomalyRollup
    # Расхождение остатков по складам с ФФ-интеграцией. Дефолт — на случай
    # записи в кэше от прошлой версии без этого ключа.
    stock_mismatch: list[StockMismatchWarehouseRow] = []


# ─── Распределение остатков (stock distribution) ───────────────────────────
# Вкладка «Распределение остатков»: «где сейчас товар» (100%). Зеркало TS-контракта:
# frontend-react/src/types/api.ts, блок «Распределение остатков сборки».


class StockDistributionBucket(BaseModel):
    """Где сейчас товар (шт + доля от итога). Сумма долей ≈ 100."""

    ff_stock: int  # на складе: ФФ-зеркало (qty_good−reserve, ≥0, короб→россыпь) либо
    #                наш WarehouseStock.quantity для складов без ФФ-зеркала
    in_assembly: int  # IN_PROGRESS («в сборке»)
    ready: int  # READY + VEHICLE_ASSIGNED («готово»)
    in_transit: int  # SHIPPED («в пути»)
    total: int  # сумма четырёх бакетов
    ff_stock_pct: float
    in_assembly_pct: float
    ready_pct: float
    in_transit_pct: float


class StockDistributionGroup(BaseModel):
    """Бакет с подписью группы — склад или статус товара."""

    key: str  # warehouse_id (str) либо ключ статуса (active/new/clearance/none)
    label: str  # имя склада / «Новинка» / «Без статуса» и т.п.
    bucket: StockDistributionBucket


class StockDistributionResponse(BaseModel):
    total: StockDistributionBucket
    by_warehouse: list[StockDistributionGroup]
    by_status: list[StockDistributionGroup]


class StockDistributionDailyStat(BaseModel):
    """Снимок 4 бакетов «где товар» за один день (шт)."""

    date: str  # ISO YYYY-MM-DD
    ff_stock: int
    in_assembly: int
    ready: int
    in_transit: int


class StockDistributionHistoryResponse(BaseModel):
    """Динамика распределения остатков по дням (накопительные снимки)."""

    daily: list[StockDistributionDailyStat]

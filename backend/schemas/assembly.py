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


# ─── Response schemas ───────────────────────────────────────────────────────


class AssemblyItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nomenclature_id: int
    barcode: str
    quantity: int
    product_name: str | None = None
    brand: str | None = None
    stock_quantity: int = 0


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
    total_weight_kg: Decimal | None = None  # computed: pallets x weight
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


class AssemblyListResponse(BaseModel):
    items: list[AssemblyRequestResponse]
    total: int


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


# ─── Logistics analytics ───────────────────────────────────────────────────


class LogisticsCostSummary(BaseModel):
    total_cost: Decimal
    avg_cost_per_pallet: Decimal
    total_pallets: int
    total_shipments: int


class LogisticsRouteStat(BaseModel):
    src_warehouse: str
    dest_warehouse: str
    avg_cost: Decimal
    shipments_count: int


class LogisticsDestStat(BaseModel):
    dest_warehouse: str
    avg_cost: Decimal
    total_cost: Decimal
    shipments_count: int


class LogisticsAnalyticsResponse(BaseModel):
    summary: LogisticsCostSummary
    by_destination: list[LogisticsDestStat]
    by_route: list[LogisticsRouteStat]


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

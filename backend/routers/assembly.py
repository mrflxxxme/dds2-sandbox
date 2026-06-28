# ruff: noqa: RUF002, RUF003
"""
Router: /warehouse/assembly — Assembly request CRUD + workflow transitions.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.fulfillment import FulfillmentRequest
from backend.models.warehouse import Warehouse
from backend.project_context import get_current_project
from backend.schemas.assembly import (
    AssemblyAttempt,
    AssemblyFlowAnalyticsResponse,
    AssemblyHistoryResponse,
    AssemblyListResponse,
    AssemblyRequestCreate,
    AssemblyRequestResponse,
    AssemblyRequestUpdate,
    AssignVehicle,
    AssignVehicleBulk,
    CostForecastResponse,
    CreatedGroupResponse,
    FfLinkInfo,
    FfReviewAction,
    JointSibling,
    LinkAnomaliesResponse,
    LogisticsAnalyticsResponse,
    LogisticsShipmentListResponse,
    RefreshFromFboResponse,
    ReturnToWarehouse,
    ShipBulk,
    StockDistributionHistoryResponse,
    StockDistributionResponse,
)
from backend.schemas.fulfillment import FfMismatchDetail
from backend.services import assembly_service, fulfillment_service
from backend.services.assembly.analytics import (
    get_assembly_flow_analytics,
    get_assembly_wb_warehouses,
    get_cost_forecast,
    get_logistics_shipments,
)
from backend.services.assembly.crud import review_ff_proposal
from backend.services.assembly.link_anomalies import get_link_anomalies
from backend.services.assembly.stock_distribution import get_stock_distribution, get_stock_distribution_history
from backend.utils.rate_limit import rate_limit_write

router = APIRouter(prefix="/warehouse/assembly", tags=["Assembly"])


def _parse_warehouse_ids(warehouse_ids: str | None) -> list[int] | None:
    """CSV warehouse_ids → list[int]; нечисловой ввод → 422 (не 500)."""
    if not warehouse_ids:
        return None
    try:
        return [int(x) for x in warehouse_ids.split(",") if x.strip()] or None
    except ValueError:
        raise HTTPException(status_code=422, detail="warehouse_ids: ожидаются целые через запятую") from None


# --- List -------------------------------------------------------------------


@router.get("", response_model=AssemblyListResponse)
async def list_assembly_requests(
    warehouse_id: int | None = Query(None),
    counterparty_id: int | None = Query(None, description="Filter by carrier counterparty"),
    draft_id: int | None = Query(None),
    status: str | None = Query(None),
    search: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    brand: str | None = Query(None),
    ff_link: str | None = Query(None, description='Фильтр привязки ФФ: "none" | "linked"'),
    joint_only: bool = Query(False, description="Только совместные сборки (≥2 на одну WB-поставку)"),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List assembly requests with filters and pagination."""
    items, total = await assembly_service.list_assembly_requests(
        db,
        project.id,
        warehouse_id=warehouse_id,
        counterparty_id=counterparty_id,
        draft_id=draft_id,
        status=status,
        search=search,
        date_from=date_from,
        date_to=date_to,
        brand=brand,
        ff_link=ff_link,
        joint_only=joint_only,
        limit=limit,
        offset=offset,
    )
    # Pre-fetch all per-row lookups (номенклатура/остатки/контрагенты/ФФ-правки)
    # одним набором батч-запросов → build-loop без N+1 (раньше на ~400 строк это
    # были сотни запросов через PgBouncer и тормозило список/фильтры/поиск).
    prefetch = await assembly_service.prefetch_list_maps(db, project.id, items)
    response_items = []
    for req in items:
        resp = await assembly_service._build_response(db, req, **prefetch)
        response_items.append(AssemblyRequestResponse.model_validate(resp))

    await _enrich_ff_links(db, project.id, items, response_items)
    await _enrich_joint(db, project.id, items, response_items)
    return AssemblyListResponse(items=response_items, total=total)


async def _enrich_ff_links(
    db: AsyncSession,
    project_id: int,
    items: list,
    response_items: list[AssemblyRequestResponse],
) -> None:
    """BATCH-обогащение списка полями привязанной ФФ-заявки (номер, этап, расхождение).

    Один индексированный запрос по `fulfillment_requests.assembly_request_id` —
    БЕЗ гейта на активность ключа интеграции: номер ФФ-заявки это историческая
    привязка и должен показываться даже при отключённом/ещё-не-активированном ключе
    (в т.ч. в локальной среде, где ключи неактивны). Деталочный эндпоинт делает то же
    per-row через fulfillment_service.get_ff_link_for_assembly — его НЕ трогаем.
    """
    if not items:
        return

    # ФФ-заявки, привязанные к заявкам страницы (один запрос по индексу
    # ix_fulfillment_requests_assembly_request_id).
    assembly_ids = [req.id for req in items]

    link_rows = await db.execute(
        select(
            FulfillmentRequest.assembly_request_id,
            FulfillmentRequest.id,
            FulfillmentRequest.number,
            FulfillmentRequest.external_id,
            FulfillmentRequest.stage_title,
            FulfillmentRequest.warehouse_id,
        )
        .where(
            FulfillmentRequest.project_id == project_id,
            FulfillmentRequest.assembly_request_id.in_(assembly_ids),
        )
        # стабильный порядок: первая привязка детерминирована (для ff_request_*)
        .order_by(FulfillmentRequest.assembly_request_id, FulfillmentRequest.id)
    )
    # У одной сборки может быть НЕСКОЛЬКО ФФ-заявок (migfull/«Натали», N:1).
    links: dict[int, list[FfLinkInfo]] = {}
    for row in link_rows.all():
        links.setdefault(row.assembly_request_id, []).append(
            FfLinkInfo(
                ff_request_id=row.id,
                ff_request_number=row.number or row.external_id,
                ff_stage_title=row.stage_title,
                ff_warehouse_id=row.warehouse_id,
            )
        )
    if not links:
        return

    # Расхождение наполнения сборки с привязанными заявками ФФ (по зеркалу, без HTTP)
    mismatch_map = await fulfillment_service.get_assembly_ff_mismatch_map(db, project_id, set(links))

    for resp in response_items:
        doc_links = links.get(resp.id)
        if doc_links:
            first = doc_links[0]
            resp.ff_request_id = first.ff_request_id
            resp.ff_request_number = first.ff_request_number
            resp.ff_stage_title = first.ff_stage_title
            resp.ff_warehouse_id = first.ff_warehouse_id
            resp.ff_links = doc_links
            resp.ff_mismatch = mismatch_map.get(resp.id)


async def _enrich_joint(
    db: AsyncSession,
    project_id: int,
    items: list,
    response_items: list[AssemblyRequestResponse],
) -> None:
    """BATCH-обогащение признаком «совместная поставка».

    Совместная = WB FBO-поставка несёт ≥2 сборок (по одной на ФФ-источник, напр.
    wms + wms2). Для таких ставим joint_supply=True и joint_siblings — ДРУГИЕ
    сборки той же поставки (тот же предикат, что у ix_assembly_requests_fbo_wh_unique:
    не удалена, не CANCELLED). Два запроса (сборки по поставкам + имена складов),
    без N+1.
    """
    supply_ids = {req.wb_fbo_supply_id for req in items if req.wb_fbo_supply_id is not None}
    if not supply_ids:
        return

    rows = (
        await db.execute(
            select(
                AssemblyRequest.id,
                AssemblyRequest.number,
                AssemblyRequest.warehouse_id,
                AssemblyRequest.status,
                AssemblyRequest.wb_fbo_supply_id,
                AssemblyRequest.pallets_count,
                AssemblyRequest.pallet_weight_kg,
            ).where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.wb_fbo_supply_id.in_(supply_ids),
                AssemblyRequest.is_deleted == False,  # noqa: E712
                AssemblyRequest.status != AssemblyStatus.CANCELLED,
            )
        )
    ).all()

    by_supply: dict[int, list] = {}
    for row in rows:
        by_supply.setdefault(row.wb_fbo_supply_id, []).append(row)

    # Имена складов-источников всех участников (один запрос).
    wh_ids = {row.warehouse_id for row in rows}
    wh_names: dict[int, str] = {}
    if wh_ids:
        wh_rows = (await db.execute(select(Warehouse.id, Warehouse.name).where(Warehouse.id.in_(wh_ids)))).all()
        wh_names = {wid: name for wid, name in wh_rows}

    # Внутренний номер ФФ-заявки каждой сборки-участника (первая привязка, как в
    # _enrich_ff_links). Один запрос по индексу ix_fulfillment_requests_assembly_request_id.
    all_ids = [row.id for row in rows]
    ff_num: dict[int, str | None] = {}
    if all_ids:
        ff_rows = await db.execute(
            select(
                FulfillmentRequest.assembly_request_id,
                FulfillmentRequest.number,
                FulfillmentRequest.external_id,
            )
            .where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.assembly_request_id.in_(all_ids),
            )
            .order_by(FulfillmentRequest.assembly_request_id, FulfillmentRequest.id)
        )
        for fr in ff_rows.all():
            ff_num.setdefault(fr.assembly_request_id, fr.number or fr.external_id)

    # «Ещё не готова» к логисту: поставку нельзя передавать/назначать машину.
    not_ready = {AssemblyStatus.PENDING, AssemblyStatus.IN_PROGRESS}

    for resp in response_items:
        sid = resp.wb_fbo_supply_id
        if sid is None:
            continue
        group = by_supply.get(sid, [])
        if len(group) < 2:
            continue
        resp.joint_supply = True
        # Готова к назначению машины, только когда ВСЕ сборки поставки готовы.
        resp.joint_ready = all(row.status not in not_ready for row in group)
        resp.joint_total_pallets = sum(int(row.pallets_count or 0) for row in group)
        # Вес = паллеты × вес паллеты (как total_weight_kg в _build_response), суммируем по сборкам.
        resp.joint_total_weight_kg = sum((int(row.pallets_count or 0) * (row.pallet_weight_kg or 0)) for row in group)
        resp.joint_siblings = [
            JointSibling(
                assembly_id=row.id,
                number=row.number,
                warehouse_id=row.warehouse_id,
                warehouse_name=wh_names.get(row.warehouse_id),
                status=row.status,
                pallets_count=row.pallets_count,
                pallet_weight_kg=row.pallet_weight_kg,
                ff_request_number=ff_num.get(row.id),
            )
            for row in group
            if row.id != resp.id
        ]


# --- Created groups (Предпросмотр созданных заявок) -------------------------


@router.get("/created-groups", response_model=list[CreatedGroupResponse])
async def get_created_groups(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Группы созданных заявок по черновику (source_draft_id), только активные
    (IN_PROGRESS) — для секции «Созданные партии / Предпросмотр»."""
    return await assembly_service.get_created_groups(db, project.id)


# --- WB warehouses ----------------------------------------------------------


@router.get("/wb-warehouses", response_model=list[str])
async def list_wb_warehouses(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get distinct WB warehouse names from assembly history and FBO supplies."""
    return await assembly_service.list_wb_warehouses(db, project.id)


# --- Shipment analytics ----------------------------------------------------


@router.get("/shipments/analytics", response_model=LogisticsAnalyticsResponse)
async def get_logistics_analytics(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    warehouse_ids: str | None = Query(None, description="Comma-separated warehouse IDs"),
    brands: str | None = Query(None, description="Comma-separated brand names"),
    carrier_id: int | None = Query(None, description="Filter by carrier counterparty id"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Logistics cost analytics: summary, by destination/route/carrier, pallet
    buckets, scatter points и аномальные отгрузки."""
    wh_ids = _parse_warehouse_ids(warehouse_ids)
    brand_list = [x.strip() for x in brands.split(",") if x.strip()] if brands else None
    return await assembly_service.get_logistics_analytics(
        db,
        project.id,
        date_from=date_from,
        date_to=date_to,
        warehouse_ids=wh_ids,
        brands=brand_list,
        carrier_id=carrier_id,
    )


@router.get("/shipments/list", response_model=LogisticsShipmentListResponse)
async def list_logistics_shipments(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    warehouse_ids: str | None = Query(None, description="Comma-separated warehouse IDs"),
    brands: str | None = Query(None, description="Comma-separated brand names"),
    carrier_id: int | None = Query(None, description="Filter by carrier counterparty id"),
    dest_warehouse: str | None = Query(None, description="Filter by WB destination warehouse name"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Построчная история отправок за период (для сортируемой таблицы).

    Возвращает ВСЕ строки периода (кап на сервере), чтобы клиентская сортировка
    шла по всему периоду, а не по одной странице.
    """
    wh_ids = _parse_warehouse_ids(warehouse_ids)
    brand_list = [x.strip() for x in brands.split(",") if x.strip()] if brands else None
    return await get_logistics_shipments(
        db,
        project.id,
        date_from=date_from,
        date_to=date_to,
        warehouse_ids=wh_ids,
        brands=brand_list,
        carrier_id=carrier_id,
        dest_warehouse=dest_warehouse,
    )


@router.get("/cost-forecast", response_model=CostForecastResponse)
async def cost_forecast(
    lookback_days: int | None = Query(None, ge=1, description="Окно истории в днях; пусто — вся история"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Прогнозная модель ₽/паллета по истории отгрузок — для неназначенных заявок.

    Фронт по (склад сдачи, кол-во паллет) выбирает уровень: склад+размер → склад →
    глобально, и умножает на число паллет заявки.
    """
    return await get_cost_forecast(db, project.id, lookback_days=lookback_days)


# --- Flow analytics ----------------------------------------------------------


@router.get("/flow-analytics", response_model=AssemblyFlowAnalyticsResponse)
async def get_flow_analytics(
    date_from: date | None = Query(None, description="Период по created_at заявки; пусто — всё время"),
    date_to: date | None = Query(None),
    warehouse_ids: str | None = Query(None, description="Comma-separated warehouse IDs"),
    categories: str | None = Query(None, description="Comma-separated категории (Nomenclature.subject)"),
    wb_warehouses: str | None = Query(None, description="Comma-separated города сдачи (целевой склад ВБ)"),
    assembly_threshold_days: int = Query(3, ge=1, le=365),
    ship_threshold_days: int = Query(2, ge=1, le=365),
    delivery_threshold_days: int = Query(7, ge=1, le=365),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Аналитика потока сборки: длительности этапов, матрица переходов, аномалии, дневная динамика."""
    wh_ids = _parse_warehouse_ids(warehouse_ids)
    cat_list = [x.strip() for x in categories.split(",") if x.strip()] if categories else None
    wb_list = [x.strip() for x in wb_warehouses.split(",") if x.strip()] if wb_warehouses else None
    return await get_assembly_flow_analytics(
        db,
        project.id,
        date_from=date_from,
        date_to=date_to,
        warehouse_ids=wh_ids,
        categories=cat_list,
        wb_warehouses=wb_list,
        assembly_threshold_days=assembly_threshold_days,
        ship_threshold_days=ship_threshold_days,
        delivery_threshold_days=delivery_threshold_days,
    )


# Путь намеренно отличается от /wb-warehouses: тот занят list_wb_warehouses
# (union всех manual-имён и поставок — автокомплит в new/edit). Здесь — только
# реальные «города сдачи» заявок (coalesce поставка → manual) для фильтра аналитики.
@router.get("/flow-analytics/wb-warehouses", response_model=list[str])
async def get_flow_wb_warehouses(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Distinct «города сдачи» (целевые склады ВБ) по заявкам — фильтр аналитики."""
    return await get_assembly_wb_warehouses(db, project.id)


@router.get("/flow-analytics/link-anomalies", response_model=LinkAnomaliesResponse)
async def get_flow_link_anomalies(
    warehouse_ids: str | None = Query(None, description="Comma-separated warehouse IDs"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Вкладка «Связи и расхождения»: расхождение наполнения, несвязанные заявки
    (с обеих сторон), сводка аномалий FBO-поставок. Read-only, по зеркалу."""
    wh_ids = _parse_warehouse_ids(warehouse_ids)
    return await get_link_anomalies(db, project.id, warehouse_ids=wh_ids)


@router.get("/flow-analytics/stock-distribution", response_model=StockDistributionResponse)
async def get_flow_stock_distribution(
    warehouse_ids: str | None = Query(None, description="Comma-separated warehouse IDs"),
    product_status: str | None = Query(None, description="Фильтр по статусу товара (active/new/clearance/none)"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Вкладка «Распределение остатков»: «где сейчас товар» (склад ФФ / в сборке /
    готово / в пути) — суммарно, по складам и по статусу товара."""
    wh_ids = _parse_warehouse_ids(warehouse_ids)
    return await get_stock_distribution(db, project.id, warehouse_ids=wh_ids, product_status=product_status)


@router.get("/flow-analytics/stock-distribution/history", response_model=StockDistributionHistoryResponse)
async def get_flow_stock_distribution_history(
    date_from: date | None = Query(None, description="Начало периода (snapshot_date)"),
    date_to: date | None = Query(None),
    warehouse_ids: str | None = Query(None, description="Comma-separated warehouse IDs"),
    product_status: str | None = Query(None, description="Фильтр по статусу товара (active/new/clearance/none)"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """История распределения остатков по дням (накопительные снимки). Пусто, пока
    ежедневная джоба не накопила данные — динамика копится вперёд, без бэкфилла."""
    wh_ids = _parse_warehouse_ids(warehouse_ids)
    return await get_stock_distribution_history(
        db, project.id, date_from=date_from, date_to=date_to, warehouse_ids=wh_ids, product_status=product_status
    )


# --- Create -----------------------------------------------------------------


@router.post("", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def create_assembly_request(
    payload: AssemblyRequestCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Create a new assembly request."""
    try:
        req = await assembly_service.create_assembly_request(db, project.id, payload)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# --- Get by ID --------------------------------------------------------------


@router.get("/{request_id}", response_model=AssemblyRequestResponse)
async def get_assembly_request(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get a single assembly request by ID (+ привязанная ФФ-заявка, если есть)."""
    req = await assembly_service.get_assembly_request(db, project.id, request_id)
    if not req:
        raise HTTPException(404, "Assembly request not found")
    data = await assembly_service._build_response(db, req)
    data.update(await fulfillment_service.get_ff_link_for_assembly(db, project.id, request_id) or {})
    resp = AssemblyRequestResponse.model_validate(data)
    await _enrich_joint(db, project.id, [req], [resp])
    return resp


@router.get("/{request_id}/ff-mismatch", response_model=FfMismatchDetail)
async def assembly_ff_mismatch(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Разбивка расхождения наполнения сборки с привязанными заявками ФФ (по позициям/итогам).

    Питает модалку «расхождение» на деталке/списке сборок и вкладке «ФФ сборка».
    live=True: migfull-отгрузка дотягивает неразрезолвленные ШК живой карточкой →
    сверка по позициям в штуках (а не фолбэк «по количеству» в коробах).
    """
    data = await fulfillment_service.get_assembly_ff_mismatch_detail(db, project.id, request_id, live=True)
    if data is None:
        raise HTTPException(404, "Assembly request not found")
    return data


@router.post(
    "/{request_id}/ff-review", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)]
)
async def review_ff_proposed_composition(
    request_id: int,
    payload: FfReviewAction,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Согласовать («approve» — применить) или отклонить («reject» — отбросить)
    предложенную ФФ-оператором правку состава сборки (ff_proposed_items).

    approve прогоняет канонический валидатор стока/резерва; дефицит → 400.
    Нет предложения на согласование → 400."""
    try:
        req = await review_ff_proposal(db, project.id, request_id, approve=payload.action == "approve")
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# --- Update -----------------------------------------------------------------


@router.put("/{request_id}", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def update_assembly_request(
    request_id: int,
    payload: AssemblyRequestUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Update an assembly request."""
    try:
        req = await assembly_service.update_assembly_request(db, project.id, request_id, payload)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# --- Status transitions -----------------------------------------------------


@router.post("/{request_id}/start", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def start_assembly(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """PENDING -> IN_PROGRESS."""
    try:
        req = await assembly_service.start_assembly(db, project.id, request_id)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/{request_id}/ready", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def mark_ready(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """IN_PROGRESS -> READY."""
    try:
        req = await assembly_service.mark_ready(db, project.id, request_id)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post(
    "/{request_id}/assign-vehicle", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)]
)
async def assign_vehicle(
    request_id: int,
    payload: AssignVehicle,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """READY -> VEHICLE_ASSIGNED."""
    try:
        req = await assembly_service.assign_vehicle(db, project.id, request_id, payload)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post(
    "/{request_id}/unassign-vehicle", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)]
)
async def unassign_vehicle(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """VEHICLE_ASSIGNED -> READY. Cancel vehicle assignment."""
    try:
        req = await assembly_service.unassign_vehicle(db, project.id, request_id)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/{request_id}/ship", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def ship_request(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """VEHICLE_ASSIGNED -> SHIPPED."""
    try:
        req = await assembly_service.ship_request(db, project.id, request_id)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/{request_id}/ship-joint", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def ship_joint_supply(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Отгрузить все назначенные сборки СОВМЕСТНОЙ поставки одним действием (одна машина)."""
    try:
        req = await assembly_service.ship_joint_supply(db, project.id, request_id)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/{request_id}/cancel", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def cancel_request(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Any status -> CANCELLED."""
    try:
        req = await assembly_service.cancel_request(db, project.id, request_id)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/{request_id}/return", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def return_to_warehouse(
    request_id: int,
    payload: ReturnToWarehouse | None = None,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """SHIPPED/DELIVERED -> RETURNED: WB не принял поставку, товар вернулся на склад
    (по умолчанию склад-источник; можно вернуть на другой). Дальше — переотгрузка
    (`/reopen`) или закрытие (`/close`)."""
    body = payload or ReturnToWarehouse()
    try:
        req = await assembly_service.return_to_warehouse(
            db,
            project.id,
            request_id,
            comment=body.comment,
            return_warehouse_id=body.return_warehouse_id,
        )
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/{request_id}/reopen", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def reopen_for_reship(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """RETURNED -> READY: переотгрузка (новая FBW-поставка + новый водитель)."""
    try:
        req = await assembly_service.reopen_for_reship(db, project.id, request_id)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/{request_id}/close", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def close_request(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """RETURNED/DELIVERED -> CLOSED: терминальное закрытие заявки."""
    try:
        req = await assembly_service.close_request(db, project.id, request_id)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/{request_id}/attempts", response_model=list[AssemblyAttempt])
async def get_assembly_attempts(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Цепочка попыток отгрузки заявки (водитель/FBW/склад WB/стоимость/исход по попыткам)."""
    try:
        attempts = await assembly_service.get_assembly_attempts(db, project.id, request_id)
        return [AssemblyAttempt.model_validate(a) for a in attempts]
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.delete("/{request_id}", status_code=204, dependencies=[Depends(rate_limit_write)])
async def delete_request(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a PENDING or CANCELLED assembly request."""
    try:
        await assembly_service.delete_request(db, project.id, request_id)
    except ValueError as e:
        msg = str(e)
        status = 404 if "not found" in msg.lower() else 400
        raise HTTPException(status, msg) from None


# --- Bulk operations --------------------------------------------------------


@router.post(
    "/assign-vehicle-bulk", response_model=list[AssemblyRequestResponse], dependencies=[Depends(rate_limit_write)]
)
async def assign_vehicle_bulk(
    payload: AssignVehicleBulk,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Bulk assign vehicle to multiple requests with per-request parameters."""
    try:
        results = []
        for item in payload.items:
            assign_payload = AssignVehicle(
                vehicle_info=payload.vehicle_info,
                vehicle_brand=payload.vehicle_brand,
                driver_phone=payload.driver_phone,
                pickup_date=item.pickup_date,
                pickup_time_slot=item.pickup_time_slot,
                pickup_cost=item.pickup_cost,
                delivery_date=item.delivery_date,
            )
            req = await assembly_service.assign_vehicle(db, project.id, item.request_id, assign_payload)
            results.append(req)
        response = []
        for req in results:
            resp = await assembly_service._build_response(db, req)
            response.append(AssemblyRequestResponse.model_validate(resp))
        return response
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/ship-bulk", response_model=list[AssemblyRequestResponse], dependencies=[Depends(rate_limit_write)])
async def ship_bulk(
    payload: ShipBulk,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Bulk ship multiple requests."""
    try:
        results = await assembly_service.ship_bulk(db, project.id, payload.ids)
        response = []
        for req in results:
            resp = await assembly_service._build_response(db, req)
            response.append(AssemblyRequestResponse.model_validate(resp))
        return response
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# --- History ----------------------------------------------------------------


@router.get("/{request_id}/history", response_model=list[AssemblyHistoryResponse])
async def get_assembly_history(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get status change history for an assembly request."""
    try:
        history = await assembly_service.get_assembly_history(db, project.id, request_id)
        return [AssemblyHistoryResponse.model_validate(h) for h in history]
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# --- FBO sync ---------------------------------------------------------------


@router.post(
    "/{request_id}/refresh-from-fbo", response_model=RefreshFromFboResponse, dependencies=[Depends(rate_limit_write)]
)
async def refresh_from_fbo(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Re-sync items from linked WbFboSupply."""
    try:
        return await assembly_service.refresh_from_fbo(db, project.id, request_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

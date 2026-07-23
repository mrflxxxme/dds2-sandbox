# ruff: noqa: RUF002, RUF003
"""
Router: /warehouse/assembly — Assembly request CRUD + workflow transitions.
"""

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from backend.auth import get_current_user
from backend.database import get_db
from backend.models import Project, User
from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.assembly_wb import AssemblyWbSupply, WbSupplySyncStatus
from backend.models.wb_fbo import WbFboSupply
from backend.services.wb_supply_service import fbo_adopted_supply_id, fbo_state_label
from backend.models.fulfillment import FulfillmentRequest
from backend.models.warehouse import Warehouse
from backend.project_context import get_current_project
from backend.schemas.assembly import (
    AssemblyAttempt,
    InTransitItem,
    InTransitResponse,
    AssemblyFlowAnalyticsResponse,
    AssemblyHistoryResponse,
    AssemblyListResponse,
    AssemblyMergeRequest,
    AssemblyRequestCreate,
    AssemblyRequestResponse,
    AssemblyRequestUpdate,
    AssignVehicle,
    AssignVehicleBulk,
    ApplyGoodsWeightBulkPayload,
    ApplyGoodsWeightBulkResult,
    ApplyGoodsWeightSkip,
    BulkDeleteResult,
    BulkStatusResult,
    CostForecastResponse,
    CreatedGroupResponse,
    DeleteBulk,
    FfLinkInfo,
    FfReviewAction,
    JointSibling,
    LinkAnomaliesResponse,
    LogisticsAnalyticsResponse,
    LogisticsShipmentListResponse,
    PreDistAdvanceResult,
    PreDistributionCreate,
    PreDistributionCreateResult,
    PalletManifest,
    PalletManifestUpdate,
    PreDistVehicle,
    WbSupplyStateBrief,
    PreDistVehiclePool,
    PrebookingCreate,
    PrebookingCreateResult,
    PickupCostHistoryResponse,
    RefreshFromFboResponse,
    SourceVehicleOption,
    ReturnToWarehouse,
    ShipBulk,
    StatusBulk,
    StockDistributionHistoryResponse,
    StockDistributionResponse,
    StockMismatchChangesResponse,
    StockMismatchHistoryResponse,
    StockMismatchSnapshotResponse,
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
from backend.services.assembly.stock_mismatch_history import (
    build_mismatch_changes_xlsx,
    get_changes as get_mismatch_changes,
    get_history as get_mismatch_history,
    msk_today,
    snapshot_project as snapshot_mismatch_project,
)
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
    view: str | None = Query(
        None, description='Вид списка: "active" (скрыть Принято ВБ/Закрыта/Отменена) | "archived" | "all"'
    ),
    search: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    brand: str | None = Query(None),
    ff_link: str | None = Query(None, description='Фильтр привязки ФФ: "none" | "linked"'),
    joint_only: bool = Query(False, description="Только совместные сборки (≥2 на одну WB-поставку)"),
    source: str | None = Query(
        None, description='Происхождение: "pre_dist" (из машины) | "prebooking" (🅿️ предзаявки) | "plain"'
    ),
    source_vehicle_id: int | None = Query(None, description="Только заявки этой машины (CostOrder.id)"),
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
        view=view,
        search=search,
        date_from=date_from,
        date_to=date_to,
        brand=brand,
        ff_link=ff_link,
        joint_only=joint_only,
        source=source,
        source_vehicle_id=source_vehicle_id,
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
    await _enrich_source_vehicle_order_no(db, project.id, items, response_items)
    await _enrich_joint(db, project.id, items, response_items)
    await _enrich_wb_supply(db, project.id, items, response_items)
    return AssemblyListResponse(items=response_items, total=total)


async def _enrich_wb_supply(
    db: AsyncSession,
    project_id: int,
    items: list,
    response_items: list[AssemblyRequestResponse],
) -> None:
    """BATCH-обогащение WB-сводкой поставки (реплей кабинета + адопция из FBO).

    Два индексированных батч-запроса (без per-row get_state):
      1) реплей-строки AssemblyWbSupply — если заявку заводили в WB (preorder/supply/
         статус ≠ NONE);
      2) для заявок БЕЗ реплей-строки, но с забронированной FBO-поставкой —
         «усыновление»: supply_id = FBO.wb_supply_id, статус BOOKED, живое
         состояние из FBO.wb_status (см. wb_supply_service.fbo_adopted_supply_id).
    """
    if not items:
        return
    assembly_ids = [req.id for req in items]
    rows = await db.execute(
        select(AssemblyWbSupply).where(
            AssemblyWbSupply.assembly_request_id.in_(assembly_ids),
            AssemblyWbSupply.project_id == project_id,
        )
    )
    by_assembly = {link.assembly_request_id: link for link in rows.scalars().all()}

    # Батч-загрузка FBO-поставок для адопции (без N+1).
    fbo_ids = {req.wb_fbo_supply_id for req in items if req.wb_fbo_supply_id is not None}
    by_fbo: dict[int, WbFboSupply] = {}
    if fbo_ids:
        fres = await db.execute(
            select(WbFboSupply).where(
                WbFboSupply.id.in_(fbo_ids),
                WbFboSupply.project_id == project_id,
            )
        )
        by_fbo = {f.id: f for f in fres.scalars().all()}
    req_by_id = {req.id: req for req in items}

    for resp in response_items:
        link = by_assembly.get(resp.id)
        is_real = link is not None and (
            link.preorder_id
            or link.supply_id
            or (link.sync_status and link.sync_status != WbSupplySyncStatus.NONE.value)
        )
        if is_real:
            resp.wb_supply = WbSupplyStateBrief.model_validate(link)
            continue
        # Адопция из FBO для заявок без реальной реплей-связи.
        req = req_by_id.get(resp.id)
        fbo = by_fbo.get(req.wb_fbo_supply_id) if req and req.wb_fbo_supply_id else None
        adopt = fbo_adopted_supply_id(fbo)
        if adopt and fbo is not None:
            resp.wb_supply = WbSupplyStateBrief(
                sync_status=WbSupplySyncStatus.BOOKED.value,
                wb_supply_state=fbo_state_label(fbo.wb_status),
                supply_id=adopt,
                preorder_id=None,
                pass_pallets=link.pass_pallets if link else None,
                pass_driver_first=link.pass_driver_first if link else None,
                pass_driver_last=link.pass_driver_last if link else None,
                pass_driver_phone=link.pass_driver_phone if link else None,
                pass_car_model=link.pass_car_model if link else None,
                pass_car_number=link.pass_car_number if link else None,
                supply_date=link.supply_date if link else None,
                wb_state_synced_at=None,
            )
        elif link is not None and _link_has_pass(link):
            # Голый локальный черновик пропуска (машину назначили → зеркалим её в
            # пропуск, но поставку в WB ещё не заводили): отдаём для префилла
            # модалки «Назначить машину» (F1). WB-статус остаётся «—» на фронте
            # (sync_status=NONE + нет supply/preorder).
            resp.wb_supply = WbSupplyStateBrief.model_validate(link)


def _link_has_pass(link: AssemblyWbSupply) -> bool:
    """У связи есть данные пропуска (для префилла модалки логиста)."""
    return any(
        (
            link.pass_driver_first,
            link.pass_driver_last,
            link.pass_driver_phone,
            link.pass_car_model,
            link.pass_car_number,
            link.pass_pallets,
        )
    )


async def _enrich_source_vehicle_order_no(
    db: AsyncSession,
    project_id: int,
    items: list,
    response_items: list[AssemblyRequestResponse],
) -> None:
    """Заполнить source_vehicle_order_no для предраспределённых заявок (бейдж «машина {order_no}»).

    Один батч-запрос по source_vehicle_id (без N+1). Только для is_pre_distribution-заявок.
    """
    from backend.models.cost import CostOrder

    vehicle_ids = {req.source_vehicle_id for req in items if req.source_vehicle_id is not None}
    if not vehicle_ids:
        return
    rows = await db.execute(
        select(CostOrder.id, CostOrder.order_no).where(
            CostOrder.id.in_(vehicle_ids),
            CostOrder.project_id == project_id,
        )
    )
    order_no_map = {vid: order_no for vid, order_no in rows.all()}
    resp_by_id = {resp.id: resp for resp in response_items}
    for req in items:
        vid = req.source_vehicle_id
        if vid is not None:
            resp = resp_by_id.get(req.id)
            if resp is not None:
                resp.source_vehicle_order_no = order_no_map.get(vid)


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


@router.get("/source-vehicles", response_model=list[SourceVehicleOption])
async def list_source_vehicles(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Машины с заявками сборки — опции фильтра «Источник» (свежие сверху)."""
    return await assembly_service.list_source_vehicles(db, project.id)


@router.get("/in-transit", response_model=InTransitResponse)
async def get_in_transit(
    nm_ids: str = Query(..., description="Comma-separated nm_id (article_wb)"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Что уже едет/зарезервировано на WB-склады активными заявками по SKU.

    Включая PRE_DISTRIBUTED (резерв под машину в пути). Источник «одного мира»
    для reconcile черновика распределения: строки, дублирующие уже едущее,
    урезаются на фронте при загрузке (прод-кейс «швабры апл» 2026-07-10).
    """
    from backend.services.cold_start_distribution_service import fetch_in_transit_by_nm

    try:
        ids = [int(x) for x in nm_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=422, detail="nm_ids: ожидаются числа через запятую")
    if not ids or len(ids) > 500:
        raise HTTPException(status_code=422, detail="nm_ids: от 1 до 500 значений")
    per_nm = await fetch_in_transit_by_nm(db, project.id, ids)
    items = [
        InTransitItem(nm_id=nm, warehouse_name=wh, quantity=qty)
        for nm, per_wh in per_nm.items()
        for wh, qty in per_wh.items()
        if qty > 0
    ]
    return InTransitResponse(items=items)


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


@router.get("/flow-analytics/mismatch-history", response_model=StockMismatchHistoryResponse)
async def get_flow_mismatch_history(
    days: int = Query(30, ge=1, le=90, description="Окно в днях"),
    warehouse_id: int | None = Query(None),
    category: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Вкладка «Динамика расхождения остатков»: суточные точки (склад×день) +
    справочники фильтров. Пусто, пока ежедневная джоба не накопила данные."""
    return await get_mismatch_history(db, project.id, days, warehouse_id=warehouse_id, category=category)


@router.get("/flow-analytics/mismatch-changes", response_model=StockMismatchChangesResponse)
async def get_flow_mismatch_changes(
    days: int = Query(30, ge=1, le=90, description="Окно в днях"),
    warehouse_id: int | None = Query(None),
    category: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Журнал изменений расхождения по SKU (appeared/resolved/grew/shrank/flipped),
    свежие дни сверху."""
    return await get_mismatch_changes(db, project.id, days, warehouse_id=warehouse_id, category=category)


@router.post(
    "/flow-analytics/mismatch-snapshot",
    response_model=StockMismatchSnapshotResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def post_flow_mismatch_snapshot(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """«Снять срез сейчас»: пересчитать сегодняшний снимок расхождения вручную
    (не дожидаясь ежедневной джобы) и вернуть точки пересчитанного дня."""
    today = msk_today()
    written = await snapshot_mismatch_project(db, project.id, day=today)
    hist = await get_mismatch_history(db, project.id, 1)
    return {
        "points": hist["points"],
        "warehouses": hist["warehouses"],
        "snapshot_date": today.isoformat() if written else None,
    }


@router.get("/flow-analytics/mismatch-changes.xlsx")
async def download_flow_mismatch_changes(
    days: int = Query(30, ge=1, le=90, description="Окно в днях"),
    warehouse_id: int | None = Query(None),
    category: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Выгрузить журнал изменений расхождения в Excel."""
    result = await get_mismatch_changes(db, project.id, days, warehouse_id=warehouse_id, category=category)
    data = build_mismatch_changes_xlsx(result["changes"])
    filename = f"mismatch_changes_{msk_today().isoformat()}.xlsx"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
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


# --- Pre-distribution (машина в пути) ───────────────────────────────────────
# Объявлены ДО `/{request_id}`, чтобы статический путь не перехватывался динамическим.


@router.get("/pre-distribution/vehicles", response_model=list[PreDistVehicle])
async def list_pre_distribution_vehicles(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Машины в пути (CUSTOMS/DISPATCHED), доступные для предраспределения."""
    return await assembly_service.get_pre_distribution_vehicles(db, project.id)


@router.get("/pre-distribution/vehicles/{vehicle_id}/pool", response_model=PreDistVehiclePool)
async def get_pre_distribution_pool(
    vehicle_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Пул машины: по каждому ШК — всего на машине, уже разнесено, доступно к раскладке."""
    try:
        return await assembly_service.get_vehicle_pre_dist_pool(db, project.id, vehicle_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post(
    "/pre-distribution",
    response_model=PreDistributionCreateResult,
    dependencies=[Depends(rate_limit_write)],
)
async def create_pre_distribution(
    payload: PreDistributionCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Создать заявки PRE_DISTRIBUTED из строк раскладки (без приёмки)."""
    try:
        return await assembly_service.create_pre_distribution(db, project.id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post(
    "/pre-distribution/vehicles/{vehicle_id}/advance",
    response_model=PreDistAdvanceResult,
    dependencies=[Depends(rate_limit_write)],
)
async def advance_pre_distribution(
    vehicle_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Ручной перевод PRE_DISTRIBUTED→IN_PROGRESS для машины (фолбэк, если авто-хук не сработал)."""
    try:
        advanced = await assembly_service.advance_pre_distribution_manual(db, project.id, vehicle_id)
        return PreDistAdvanceResult(advanced=advanced)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post(
    "/prebooking",
    response_model=PrebookingCreateResult,
    dependencies=[Depends(rate_limit_write)],
)
async def create_prebooking(
    payload: PrebookingCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Создать заявки-предзаявки на моно (is_prebooking=True) из строк предброни.

    Целые моно-паллеты на WB-склад без лимита приёмки (⌛) — сдаются предзаявкой.
    ⌛/whitelist проверяет фронт (там загружена приёмка); здесь — обычная валидация стока.
    """
    try:
        return await assembly_service.create_prebooking(db, project.id, payload)
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


def _actor_name(user: User) -> str:
    """Отображаемое имя автора правки для истории («кто поменял»)."""
    full = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return (full or user.username)[:50]


@router.put("/{request_id}", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def update_assembly_request(
    request_id: int,
    payload: AssemblyRequestUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update an assembly request."""
    try:
        req = await assembly_service.update_assembly_request(
            db, project.id, request_id, payload, changed_by=_actor_name(user)
        )
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# --- Pallet layout (раскладка по паллетам) ----------------------------------


@router.patch(
    "/{request_id}/pallet-manifest",
    response_model=AssemblyRequestResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def update_pallet_manifest(
    request_id: int,
    payload: PalletManifestUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Сохранить ручную раскладку коробов по паллетам или сбросить к «авто».

    pallets=null → сброс (поле очищается). Иначе строгий инвариант
    Σ(box_count·box_qty + loose_units) == quantity по каждой позиции; нарушение → 409.
    ШК не из состава заявки → 400."""
    manifest = None if payload.pallets is None else PalletManifest(pallets=payload.pallets)
    try:
        req = await assembly_service.update_pallet_manifest(db, project.id, request_id, manifest)
    except assembly_service.PalletManifestConflict as e:
        raise HTTPException(409, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))


@router.get("/{request_id}/pallet-layout.xlsx")
async def download_pallet_layout(
    request_id: int,
    format: str = Query("internal", pattern="^(internal|wb)$"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Выгрузить раскладку по паллетам в Excel.

    format=internal — покоробочно с паллетами (для кладовщика);
    format=wb — файл загрузки в WB (строка = короб; ШК короба и срок годности пусты)."""
    req = await assembly_service.get_assembly_request(db, project.id, request_id)
    if not req:
        raise HTTPException(404, "Assembly request not found")
    data = await assembly_service.build_pallet_layout_xlsx(db, project.id, req, fmt=format)
    suffix = "wb" if format == "wb" else "pallets"
    filename = f"{req.number}_{suffix}.xlsx"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
    )


@router.post(
    "/{request_id}/apply-goods-weight",
    response_model=AssemblyRequestResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def apply_goods_weight(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Проставить расчётный вес товаров в ручной вес паллеты (÷ кол-во паллет).

    Нет ни одной позиции с весом → 400 (заполнить справочник веса в настройках)."""
    try:
        req = await assembly_service.apply_goods_weight(db, project.id, request_id)
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


@router.post("/merge", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def merge_requests(
    payload: AssemblyMergeRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Объединить ≥2 сборки одного склада·направления·упаковки (статус «В сборке»)
    в одну: позиции суммируются, ФФ-связи переносятся, остальные — soft-delete.
    Возвращает survivor'а."""
    try:
        req = await assembly_service.merge_assembly_requests(db, project.id, payload.request_ids)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        msg = str(e)
        status = 404 if "не найден" in msg.lower() else 400
        raise HTTPException(status, msg) from None


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
                driver_first_name=payload.driver_first_name,
                driver_last_name=payload.driver_last_name,
                pickup_date=item.pickup_date,
                pickup_time_slot=item.pickup_time_slot,
                pickup_cost=item.pickup_cost,
                delivery_date=item.delivery_date,
                # Подрядчик приходит в bulk-payload, но раньше не прокидывался в
                # per-request AssignVehicle → при массовом назначении терялся.
                carrier_inn=payload.carrier_inn,
                carrier_name=payload.carrier_name,
            )
            req = await assembly_service.assign_vehicle(db, project.id, item.request_id, assign_payload)
            results.append(req)
        # via_gazelka одним батч-запросом на весь bulk (не N+1 через single-row fallback).
        gz_ids = await assembly_service._gazelka_linked_ids(db, project.id, [r.id for r in results])
        response = []
        for req in results:
            resp = await assembly_service._build_response(db, req, via_gazelka_ids=gz_ids)
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
        # via_gazelka одним батч-запросом на весь bulk (не N+1 через single-row fallback).
        gz_ids = await assembly_service._gazelka_linked_ids(db, project.id, [r.id for r in results])
        response = []
        for req in results:
            resp = await assembly_service._build_response(db, req, via_gazelka_ids=gz_ids)
            response.append(AssemblyRequestResponse.model_validate(resp))
        return response
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/delete-bulk", response_model=BulkDeleteResult, dependencies=[Depends(rate_limit_write)])
async def delete_bulk(
    payload: DeleteBulk,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Массово удалить заявки на сборку, ещё не отгруженные на WB.

    Отгруженные/на WB (SHIPPED/DELIVERED/RETURNED/CLOSED) пропускаются с причиной.
    """
    result = await assembly_service.delete_bulk(db, project.id, payload.ids)
    return BulkDeleteResult.model_validate(result)


@router.post("/status-bulk", response_model=BulkStatusResult, dependencies=[Depends(rate_limit_write)])
async def set_status_bulk(
    payload: StatusBulk,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Массово перевести заявки в статус (IN_PROGRESS | READY) одним запросом.

    Невалидные для перехода заявки пропускаются с причиной (partial success).
    """
    try:
        result = await assembly_service.set_status_bulk(db, project.id, payload.ids, payload.status)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    updated = []
    for req in result["updated"]:
        resp = await assembly_service._build_response(db, req)
        updated.append(AssemblyRequestResponse.model_validate(resp))
    return BulkStatusResult(updated=updated, skipped=result["skipped"])


@router.post(
    "/apply-goods-weight-bulk",
    response_model=ApplyGoodsWeightBulkResult,
    dependencies=[Depends(rate_limit_write)],
)
async def apply_goods_weight_bulk(
    payload: ApplyGoodsWeightBulkPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Массово проставить расчётный вес отгрузки (нетто + вес коробки × коробов) и
    авто-число паллет для набора заявок. Заявки без веса пропускаются с причиной."""
    applied, skipped = await assembly_service.apply_goods_weight_bulk(db, project.id, payload.ids)
    # Ответ собираем батч-путём (prefetch на весь набор), не per-row резолвом.
    prefetch = await assembly_service.prefetch_list_maps(db, project.id, applied) if applied else {}
    applied_resp = [
        AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req, **prefetch))
        for req in applied
    ]
    return ApplyGoodsWeightBulkResult(
        applied=applied_resp, skipped=[ApplyGoodsWeightSkip(**s) for s in skipped]
    )


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


@router.get("/{request_id}/pickup-cost-history", response_model=list[PickupCostHistoryResponse])
async def get_pickup_cost_history(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """История изменений стоимости перевозки заявки (старая→новая + автор, ASM-785)."""
    try:
        history = await assembly_service.get_pickup_cost_history(db, project.id, request_id)
        return [PickupCostHistoryResponse.model_validate(h) for h in history]
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

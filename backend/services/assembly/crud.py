# ruff: noqa: RUF001, RUF002, RUF003
"""
Assembly Request service — CRUD operations and helpers.

Part of the assembly service package. See backend/DOMAIN_ASSEMBLY.md for spec.
"""

import asyncio
import logging
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from backend.cache import invalidate_cache
from backend.models.assembly import (
    AssemblyDraft,
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
)
from backend.models.cost import Nomenclature
from backend.models.counterparty import Counterparty
from backend.models.fulfillment import FulfillmentRequest
from backend.models.warehouse import (
    InboundReceipt,
    OutboundShipment,
    Warehouse,
    WarehouseStock,
    WarehouseType,
)
from backend.models.wb_fbo import WbFboSupply
from backend.schemas.assembly import (
    AssemblyItemCreate,
    AssemblyRequestCreate,
    AssemblyRequestUpdate,
)
from backend.services.warehouse_service import (
    _next_number,
    get_warehouse,
)

from .status import _log_status_change

logger = logging.getLogger(__name__)


async def _try_force_enrich_supply(
    db: AsyncSession,
    project_id: int,
    supply: WbFboSupply,
    force: bool = False,
    with_goods: bool = False,
    api_client: Any = None,
    throttle: bool = False,
) -> None:
    """
    On-demand force-pull from WB when supply is linked to an assembly but periodic
    enrich hasn't caught up (race window между list-sync и enrich-джобой). Best-effort:
    любая ошибка глотается — caller продолжает, плановый enrich догонит позже.

    force=True — pull даже если warehouse_name уже задан (для «Обновить из WB»
    когда юзер мог сменить склад назначения в кабинете WB).

    with_goods=True — дополнительно перетянуть построчный состав (goods →
    WbFboSupplyItem). Detail-эндпоинт отдаёт только агрегаты; per-barcode состав —
    отдельный вызов. Без него зеркало item-ов застревает на старом наполнении, и
    кнопка «Из FBO» по непринятой поставке не видит новых ШК (расхождение с WB/ФФ).

    throttle=True — выдержать FBW_RATE_LIMIT_DELAY между detail и goods (оба
    эндпоинта лимитированы 6 req/min). Для ручной «Из FBO» (1 поставка) не нужен;
    для фоновой пачки (refresh_active_assemblies_from_fbo) — обязателен, иначе
    429-шторм. Зеркалит троттлинг планового enrich_fbo_supplies.

    api_client — инъекция для тестов; если None, строится из WB-ключа проекта.
    """
    if not force and supply.warehouse_name:
        return
    try:
        from backend.services.fbo_supply.mappers import (
            FBW_RATE_LIMIT_DELAY,
            _update_supply_from_fbw_detail,
            _upsert_supply_items_fbw,
        )

        client = api_client
        if client is None:
            from backend.integrations.wb_api import WBApiClient
            from backend.services.integrations_service import _get_wb_key

            try:
                _key, api_key = await _get_wb_key(db, project_id)
            except ValueError:
                return  # no WB key — silent skip
            client = WBApiClient(api_key, project_id=project_id)

        wb_id_int = int(supply.wb_supply_id)
        detail = await client.get_fbw_supply_detail(wb_id_int)
        if detail:
            _update_supply_from_fbw_detail(supply, detail)
        if with_goods:
            if throttle:
                await asyncio.sleep(FBW_RATE_LIMIT_DELAY)
            goods = await client.get_fbw_supply_goods(wb_id_int, limit=100, offset=0)
            if goods:
                await _upsert_supply_items_fbw(db, project_id, supply.id, wb_id_int, goods)
                supply.accepted_qty = sum(int(g.get("acceptedQuantity") or 0) for g in goods)
                supply.total_qty = sum(int(g.get("quantity") or 0) for g in goods)
        await db.flush()
    except Exception as e:
        logger.info(
            "assembly.force_enrich_skipped",
            extra={"project_id": project_id, "wb_supply_id": supply.wb_supply_id, "error": str(e)[:200]},
        )


# --- Helpers ----------------------------------------------------------------


async def _validate_stock_for_ship(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    items: list[AssemblyRequestItem],
) -> list[dict]:
    """
    Check stock availability for all items before shipping.
    Returns list of deficits with product names for readable error messages.
    Empty list = OK to ship.
    """
    nom_ids = [item.nomenclature_id for item in items if item.nomenclature_id]
    if not nom_ids:
        return []

    # Batch load stock quantities
    stock_result = await db.execute(
        select(WarehouseStock.nomenclature_id, WarehouseStock.quantity).where(
            WarehouseStock.project_id == project_id,
            WarehouseStock.warehouse_id == warehouse_id,
            WarehouseStock.nomenclature_id.in_(nom_ids),
        )
    )
    stock_map = {r.nomenclature_id: r.quantity for r in stock_result.all()}

    # Find deficit items
    deficit_items = [item for item in items if stock_map.get(item.nomenclature_id, 0) < item.quantity]
    if not deficit_items:
        return []

    # Batch load nomenclature names only for deficit items
    deficit_nom_ids = [item.nomenclature_id for item in deficit_items]
    nom_result = await db.execute(select(Nomenclature).where(Nomenclature.id.in_(deficit_nom_ids)))
    nom_map = {n.id: n for n in nom_result.scalars().all()}

    deficits: list[dict] = []
    for item in deficit_items:
        have = stock_map.get(item.nomenclature_id, 0)
        nom = nom_map.get(item.nomenclature_id)
        name = (nom.subject or nom.article_seller or item.barcode) if nom else item.barcode
        deficits.append(
            {
                "name": name,
                "barcode": item.barcode,
                "need": item.quantity,
                "have": have,
            }
        )
    return deficits


async def _validate_available_for_assembly(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    items: list[AssemblyRequestItem],
    exclude_request_id: int | None = None,
) -> list[dict]:
    """
    Check available stock = quantity - reserved by OTHER active assembly requests.
    Used at create / update / start_assembly to prevent over-commitment.

    exclude_request_id: исключить эту заявку из расчёта резерва (чтобы не вычитать саму себя
    при редактировании или старте).
    """
    nom_ids = [item.nomenclature_id for item in items if item.nomenclature_id]
    if not nom_ids:
        return []

    stock_result = await db.execute(
        select(WarehouseStock.nomenclature_id, WarehouseStock.quantity).where(
            WarehouseStock.project_id == project_id,
            WarehouseStock.warehouse_id == warehouse_id,
            WarehouseStock.nomenclature_id.in_(nom_ids),
        )
    )
    stock_map = {r.nomenclature_id: r.quantity for r in stock_result.all()}

    reserved_q = (
        select(
            AssemblyRequestItem.nomenclature_id,
            func.sum(AssemblyRequestItem.quantity).label("reserved"),
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.warehouse_id == warehouse_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status.in_(
                [
                    AssemblyStatus.PENDING,
                    AssemblyStatus.IN_PROGRESS,
                    AssemblyStatus.READY,
                    AssemblyStatus.VEHICLE_ASSIGNED,
                ]
            ),
            AssemblyRequestItem.nomenclature_id.in_(nom_ids),
        )
        .group_by(AssemblyRequestItem.nomenclature_id)
    )
    if exclude_request_id is not None:
        reserved_q = reserved_q.where(AssemblyRequest.id != exclude_request_id)
    reserved_result = await db.execute(reserved_q)
    reserved_map = {r.nomenclature_id: r.reserved for r in reserved_result.all()}

    needs: dict[int, int] = {}
    barcode_for_nom: dict[int, str] = {}
    for item in items:
        if item.nomenclature_id is None:
            continue
        needs[item.nomenclature_id] = needs.get(item.nomenclature_id, 0) + item.quantity
        barcode_for_nom.setdefault(item.nomenclature_id, item.barcode)

    deficit_nom_ids: list[int] = []
    for nom_id, need in needs.items():
        stock = stock_map.get(nom_id, 0)
        reserved = reserved_map.get(nom_id, 0)
        available = max(0, stock - reserved)
        if available < need:
            deficit_nom_ids.append(nom_id)

    if not deficit_nom_ids:
        return []

    nom_result = await db.execute(select(Nomenclature).where(Nomenclature.id.in_(deficit_nom_ids)))
    nom_map = {n.id: n for n in nom_result.scalars().all()}

    deficits: list[dict] = []
    for nom_id in deficit_nom_ids:
        nom = nom_map.get(nom_id)
        bc = barcode_for_nom[nom_id]
        name = (nom.subject or nom.article_seller or bc) if nom else bc
        stock = stock_map.get(nom_id, 0)
        reserved = reserved_map.get(nom_id, 0)
        available = max(0, stock - reserved)
        deficits.append(
            {
                "name": name,
                "barcode": bc,
                "need": needs[nom_id],
                "have": available,
                "stock": stock,
                "reserved": reserved,
            }
        )
    return deficits


def _format_deficit_error(deficits: list[dict]) -> str:
    """Format deficit list into a human-readable Russian error message."""
    lines = [
        f"  {d['name']} (ШК {d['barcode']}): нужно {d['need']}, доступно {d['have']} "
        f"(на складе {d['stock']}, в работе {d['reserved']})"
        for d in deficits
    ]
    return f"Недостаточно доступных остатков ({len(deficits)} поз.):\n" + "\n".join(lines)


async def _build_items_with_stock(
    db: AsyncSession,
    request: AssemblyRequest,
    *,
    nom_map: dict[int, Nomenclature] | None = None,
    stock_by_wh_nom: dict[tuple[int, int], int] | None = None,
) -> list[dict]:
    """Build items list with product_name and stock_quantity.

    Single-row callers (create/update/get) pass no maps → batch queries scoped
    to this one request. The list endpoint pre-fetches both maps across ALL
    requests (см. prefetch_list_maps) and passes them in, so the per-row loop
    issues zero DB round-trips — это и убирает N+1 на списке.
    """
    if not request.items:
        return []

    nom_ids = [item.nomenclature_id for item in request.items if item.nomenclature_id]

    # Nomenclature names — prefetched map (batch) or scoped query (single-row)
    if nom_map is None:
        nom_map = {}
        if nom_ids:
            nom_result = await db.execute(select(Nomenclature).where(Nomenclature.id.in_(nom_ids)))
            nom_map = {n.id: n for n in nom_result.scalars().all()}

    # Stock for THIS request's warehouse, keyed by nomenclature_id
    stock_map: dict[int, int] = {}
    if stock_by_wh_nom is not None:
        stock_map = {nid: stock_by_wh_nom.get((request.warehouse_id, nid), 0) for nid in nom_ids}
    elif nom_ids:
        stock_result = await db.execute(
            select(WarehouseStock.nomenclature_id, WarehouseStock.quantity).where(
                WarehouseStock.project_id == request.project_id,
                WarehouseStock.warehouse_id == request.warehouse_id,
                WarehouseStock.nomenclature_id.in_(nom_ids),
            )
        )
        for row in stock_result.all():
            stock_map[row.nomenclature_id] = stock_map.get(row.nomenclature_id, 0) + row.quantity

    items_out: list[dict] = []
    for item in request.items:
        nom = nom_map.get(item.nomenclature_id)
        product_name = (nom.subject or nom.article_seller or item.barcode) if nom else item.barcode

        items_out.append(
            {
                "id": item.id,
                "nomenclature_id": item.nomenclature_id,
                "barcode": item.barcode,
                "quantity": item.quantity,
                "product_name": product_name,
                "brand": nom.brand if nom else None,
                "stock_quantity": stock_map.get(item.nomenclature_id, 0),
            }
        )
    return items_out


async def _build_response(
    db: AsyncSession,
    request: AssemblyRequest,
    *,
    nom_map: dict[int, Nomenclature] | None = None,
    stock_by_wh_nom: dict[tuple[int, int], int] | None = None,
    cp_map: dict[int, Any] | None = None,
    prop_nom_map: dict[str, Nomenclature] | None = None,
) -> dict:
    """
    Build AssemblyRequestResponse dict from ORM model.
    Loads warehouse.name, wb_fbo_supply.name, wb_fbo_supply.warehouse_name.
    Computes total_weight_kg = pallets_count * pallet_weight_kg.

    Optional lookup maps (nom_map / stock_by_wh_nom / cp_map / prop_nom_map) are
    pre-fetched once for the whole list by prefetch_list_maps; when provided this
    function makes NO DB round-trips per row (kills the list N+1). Single-row
    callers omit them and each lookup falls back to a scoped query.
    """
    # Relationships are pre-loaded via selectinload in list/get queries.
    # Refresh only for direct calls (e.g. after create/update).
    try:
        _ = request.warehouse
    except Exception:
        await db.refresh(request, ["warehouse", "wb_fbo_supply", "items"])

    pallets = request.pallets_count or 0
    weight = request.pallet_weight_kg or Decimal("0")
    total_weight = Decimal(str(pallets)) * weight

    carrier_inn: str | None = None
    carrier_name: str | None = None
    if request.counterparty_id:
        if cp_map is not None:
            cp_row = cp_map.get(request.counterparty_id)
        else:
            cp_row = (
                await db.execute(
                    select(Counterparty.inn, Counterparty.name).where(Counterparty.id == request.counterparty_id)
                )
            ).first()
        if cp_row is not None:
            carrier_inn = cp_row.inn
            carrier_name = cp_row.name

    # Предложенная ФФ-оператором правка состава (ожидает согласования в DDS).
    # ff_proposed_items не None ⇒ pending; резолвим имя/артикул из номенклатуры
    # по ШК одним батч-запросом (как наполнение в _build_items_with_stock).
    ff_proposed_out: list[dict] | None = None
    proposal = request.ff_proposed_items
    if proposal is not None:
        proposal_barcodes = [str(i["barcode"]) for i in proposal if i.get("barcode")]
        if prop_nom_map is None:
            prop_nom_map = {}
            if proposal_barcodes:
                prop_nom_result = await db.execute(
                    select(Nomenclature).where(
                        Nomenclature.project_id == request.project_id,
                        Nomenclature.barcode.in_(proposal_barcodes),
                    )
                )
                prop_nom_map = {n.barcode: n for n in prop_nom_result.scalars().all()}
        ff_proposed_out = []
        for i in proposal:
            bc = str(i["barcode"])
            nom = prop_nom_map.get(bc)
            ff_proposed_out.append(
                {
                    "barcode": bc,
                    "quantity": int(i["quantity"]),
                    "product_name": (nom.subject or nom.article_seller or bc) if nom else None,
                    "article": nom.article_seller if nom else None,
                }
            )

    return {
        "id": request.id,
        "warehouse_id": request.warehouse_id,
        "warehouse_name": request.warehouse.name if request.warehouse else None,
        "number": request.number,
        "status": request.status,
        "wb_fbo_supply_id": request.wb_fbo_supply_id,
        "wb_supply_name": request.wb_fbo_supply.name if request.wb_fbo_supply else None,
        "wb_warehouse_name": request.wb_fbo_supply.warehouse_name if request.wb_fbo_supply else None,
        "wb_supply_id_wb": request.wb_fbo_supply.wb_supply_id if request.wb_fbo_supply else None,
        "wb_fbo_status": request.wb_fbo_supply.wb_status if request.wb_fbo_supply else None,
        "wb_fbo_planned_date": request.wb_fbo_supply.planned_date if request.wb_fbo_supply else None,
        "wb_fbo_actual_date": request.wb_fbo_supply.actual_date if request.wb_fbo_supply else None,
        "outbound_shipment_id": request.outbound_shipment_id,
        "estimated_ready_date": request.estimated_ready_date,
        "actual_ready_date": request.actual_ready_date,
        "pallets_count": request.pallets_count,
        "pallet_weight_kg": request.pallet_weight_kg,
        "total_weight_kg": total_weight,
        "vehicle_info": request.vehicle_info,
        "vehicle_brand": request.vehicle_brand,
        "driver_phone": request.driver_phone,
        "pickup_date": request.pickup_date,
        "pickup_time_slot": request.pickup_time_slot,
        "pickup_cost": request.pickup_cost,
        "delivery_date": request.delivery_date,
        "vehicle_assigned_at": request.vehicle_assigned_at,
        "shipped_at": request.shipped_at,
        "comment": request.comment,
        "wb_warehouse_name_manual": request.wb_warehouse_name_manual,
        "source_draft_id": request.source_draft_id,
        "effective_wb_warehouse": (
            (request.wb_fbo_supply.warehouse_name if request.wb_fbo_supply else None)
            or request.wb_warehouse_name_manual
        ),
        "counterparty_id": request.counterparty_id,
        "carrier_inn": carrier_inn,
        "carrier_name": carrier_name,
        "items": (
            items := await _build_items_with_stock(
                db, request, nom_map=nom_map, stock_by_wh_nom=stock_by_wh_nom
            )
        ),
        "brands": ", ".join(sorted({i["brand"] for i in items if i.get("brand")})) or None,
        "created_at": request.created_at,
        "updated_at": request.updated_at,
        "ff_review_pending": proposal is not None,
        "ff_proposed_items": ff_proposed_out,
        "ff_proposed_at": request.ff_proposed_at,
        "ff_proposed_by": request.ff_proposed_by,
    }


async def prefetch_list_maps(
    db: AsyncSession,
    project_id: int,
    requests: list[AssemblyRequest],
) -> dict[str, Any]:
    """Pre-fetch every per-row lookup the list response needs, in a fixed number
    of batch queries (independent of len(requests)).

    Returns kwargs for `_build_response(...)` so the per-row build loop makes
    zero DB round-trips. Без этого роутер делал на КАЖДУЮ сборку запросы
    контрагента + номенклатуры + остатков (N+1 на ~400 строк = сотни запросов
    через PgBouncer → тормоза списка/фильтров). `requests[*].items` уже
    eager-loaded через selectinload в list_assembly_requests.
    """
    nom_ids: set[int] = set()
    wh_ids: set[int] = set()
    cp_ids: set[int] = set()
    proposal_barcodes: set[str] = set()
    for req in requests:
        if req.warehouse_id:
            wh_ids.add(req.warehouse_id)
        if req.counterparty_id:
            cp_ids.add(req.counterparty_id)
        for item in req.items:
            if item.nomenclature_id:
                nom_ids.add(item.nomenclature_id)
        proposal = req.ff_proposed_items
        if proposal:
            for i in proposal:
                if i.get("barcode"):
                    proposal_barcodes.add(str(i["barcode"]))

    nom_map: dict[int, Nomenclature] = {}
    if nom_ids:
        res = await db.execute(select(Nomenclature).where(Nomenclature.id.in_(nom_ids)))
        nom_map = {n.id: n for n in res.scalars().all()}

    # Keyed by (warehouse_id, nomenclature_id): stock is per-warehouse, and rows
    # of the list may span several warehouses. Summed per key to mirror the
    # single-row query (multiple WarehouseStock rows per (wh, nom)).
    stock_by_wh_nom: dict[tuple[int, int], int] = {}
    if nom_ids and wh_ids:
        res = await db.execute(
            select(
                WarehouseStock.warehouse_id,
                WarehouseStock.nomenclature_id,
                WarehouseStock.quantity,
            ).where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id.in_(wh_ids),
                WarehouseStock.nomenclature_id.in_(nom_ids),
            )
        )
        for row in res.all():
            key = (row.warehouse_id, row.nomenclature_id)
            stock_by_wh_nom[key] = stock_by_wh_nom.get(key, 0) + row.quantity

    cp_map: dict[int, Any] = {}
    if cp_ids:
        res = await db.execute(
            select(Counterparty.id, Counterparty.inn, Counterparty.name).where(Counterparty.id.in_(cp_ids))
        )
        cp_map = {row.id: row for row in res.all()}

    prop_nom_map: dict[str, Nomenclature] = {}
    if proposal_barcodes:
        res = await db.execute(
            select(Nomenclature).where(
                Nomenclature.project_id == project_id,
                Nomenclature.barcode.in_(proposal_barcodes),
            )
        )
        prop_nom_map = {n.barcode: n for n in res.scalars().all()}

    return {
        "nom_map": nom_map,
        "stock_by_wh_nom": stock_by_wh_nom,
        "cp_map": cp_map,
        "prop_nom_map": prop_nom_map,
    }


# --- WB warehouses ----------------------------------------------------------


async def list_wb_warehouses(
    db: AsyncSession,
    project_id: int,
) -> list[str]:
    """
    Get distinct WB warehouse names from assembly requests and FBO supplies.
    Returns sorted list of unique non-null warehouse names.
    """
    # From assembly_requests.wb_warehouse_name_manual
    manual_q = (
        select(AssemblyRequest.wb_warehouse_name_manual)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted == False,  # noqa: E712
            AssemblyRequest.wb_warehouse_name_manual.isnot(None),
        )
        .distinct()
    )

    # From wb_fbo_supplies.warehouse_name
    fbo_q = (
        select(WbFboSupply.warehouse_name)
        .where(
            WbFboSupply.project_id == project_id,
            WbFboSupply.warehouse_name.isnot(None),
        )
        .distinct()
    )

    manual_result = await db.execute(manual_q)
    fbo_result = await db.execute(fbo_q)

    names: set[str] = set()
    for row in manual_result.scalars().all():
        if row:
            names.add(row)
    for row in fbo_result.scalars().all():
        if row:
            names.add(row)

    return sorted(names)


# --- CRUD -------------------------------------------------------------------


async def list_assembly_requests(
    db: AsyncSession,
    project_id: int,
    *,
    warehouse_id: int | None = None,
    counterparty_id: int | None = None,
    draft_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    brand: str | None = None,
    ff_link: str | None = None,
    joint_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AssemblyRequest], int]:
    """
    List assembly requests with filters, pagination.

    ff_link: "none" — только заявки БЕЗ привязанной ФФ-заявки; "linked" — только
    с привязанной; None — без фильтра. Привязка живёт в
    FulfillmentRequest.assembly_request_id (project-scoped).

    joint_only: True — только «совместные» сборки (делят WB FBO-поставку с ≥1
    другой активной сборкой, т.е. ≥2 сборок на одну поставку под тем же
    предикатом, что у partial-unique индекса: не удалена, не CANCELLED).
    """
    base = select(AssemblyRequest).where(
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.is_deleted == False,  # noqa: E712
    )

    if warehouse_id is not None:
        base = base.where(AssemblyRequest.warehouse_id == warehouse_id)
    if counterparty_id is not None:
        base = base.where(AssemblyRequest.counterparty_id == counterparty_id)
    if draft_id is not None:
        base = base.where(AssemblyRequest.source_draft_id == draft_id)
    if status is not None:
        statuses = [s.strip() for s in status.split(",")]
        if len(statuses) == 1:
            base = base.where(AssemblyRequest.status == statuses[0])
        else:
            base = base.where(AssemblyRequest.status.in_(statuses))
    if date_from is not None:
        base = base.where(AssemblyRequest.created_at >= date_from)
    if date_to is not None:
        end = datetime.combine(date_to, time.max)
        base = base.where(AssemblyRequest.created_at <= end)
    if search:
        # Escape % and _ in search string before ILIKE
        escaped = search.replace("%", r"\%").replace("_", r"\_")
        # Search by assembly number OR WB supply ID (e.g. FBW-37969677)
        supply_ids_q = select(WbFboSupply.id).where(
            WbFboSupply.project_id == project_id,
            WbFboSupply.wb_supply_id.ilike(f"%{escaped}%", escape="\\"),
        )
        base = base.where(
            or_(
                AssemblyRequest.number.ilike(f"%{escaped}%", escape="\\"),
                AssemblyRequest.wb_fbo_supply_id.in_(supply_ids_q),
            )
        )

    if brand:
        brand_noms = select(Nomenclature.id).where(
            Nomenclature.project_id == project_id,
            Nomenclature.brand == brand,
        )
        brand_requests = (
            select(AssemblyRequestItem.assembly_request_id)
            .where(
                AssemblyRequestItem.nomenclature_id.in_(brand_noms),
            )
            .distinct()
        )
        base = base.where(AssemblyRequest.id.in_(brand_requests))

    if ff_link in ("none", "linked"):
        ff_exists = (
            select(FulfillmentRequest.id)
            .where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.assembly_request_id == AssemblyRequest.id,
            )
            .exists()
        )
        base = base.where(ff_exists if ff_link == "linked" else ~ff_exists)

    if joint_only:
        # «Совместные»: на той же WB-поставке есть ≥2 активных сборок (та же
        # выборка, что у ix_assembly_requests_fbo_wh_unique). Коррелированный
        # COUNT по сёстрам-сборкам той же поставки.
        sib = aliased(AssemblyRequest)
        joint_cnt = (
            select(func.count(sib.id))
            .where(
                sib.project_id == project_id,
                sib.wb_fbo_supply_id == AssemblyRequest.wb_fbo_supply_id,
                sib.is_deleted == False,  # noqa: E712
                sib.status != AssemblyStatus.CANCELLED,
            )
            .correlate(AssemblyRequest)
            .scalar_subquery()
        )
        base = base.where(
            AssemblyRequest.wb_fbo_supply_id.is_not(None),
            joint_cnt >= 2,
        )

    # Total count
    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Items
    items_q = (
        base.options(
            selectinload(AssemblyRequest.items),
            selectinload(AssemblyRequest.warehouse),
            selectinload(AssemblyRequest.wb_fbo_supply),
        )
        .order_by(AssemblyRequest.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(items_q)
    items = list(result.scalars().all())

    return items, total


async def get_created_groups(db: AsyncSession, project_id: int) -> list[dict]:
    """Группы созданных заявок по source_draft_id («Предпросмотр созданных»).

    Берём только IN_PROGRESS-заявки, созданные из распределения (source_draft_id
    задан), ещё не ушедшие в работу/отгрузку — это то, что можно ревьюить и (в
    будущем) мёрджить. Имя черновика берём из assembly_drafts (включая
    soft-deleted — после полного коммита черновик удаляется). Кап — 50 групп.
    """
    # 1. Свежие source_draft_id с активными созданными заявками (кап групп).
    did_q = (
        select(AssemblyRequest.source_draft_id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted == False,  # noqa: E712
            AssemblyRequest.source_draft_id.isnot(None),
            AssemblyRequest.status == AssemblyStatus.IN_PROGRESS,
        )
        .group_by(AssemblyRequest.source_draft_id)
        .order_by(func.max(AssemblyRequest.created_at).desc())
        .limit(50)
    )
    draft_ids = [row[0] for row in (await db.execute(did_q)).all()]
    if not draft_ids:
        return []

    # 2. Заявки этих групп с позициями и складом-источником.
    result = await db.execute(
        select(AssemblyRequest)
        .options(
            selectinload(AssemblyRequest.items),
            selectinload(AssemblyRequest.warehouse),
        )
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted == False,  # noqa: E712
            AssemblyRequest.source_draft_id.in_(draft_ids),
            AssemblyRequest.status == AssemblyStatus.IN_PROGRESS,
        )
        .order_by(AssemblyRequest.created_at.desc())
    )
    requests = list(result.scalars().all())

    # 3. Имена черновиков (вкл. soft-deleted — фильтр project_id обязателен).
    name_rows = await db.execute(
        select(AssemblyDraft.id, AssemblyDraft.name).where(
            AssemblyDraft.project_id == project_id,
            AssemblyDraft.id.in_(draft_ids),
        )
    )
    draft_names = {row[0]: row[1] for row in name_rows.all()}

    # 4. Группировка в Python.
    groups: dict[int, dict] = {}
    for r in requests:
        did = r.source_draft_id
        if did is None:  # запрос фильтрует isnot(None), но mypy этого не знает
            continue
        g = groups.get(did)
        if g is None:
            g = {"draft_id": did, "draft_name": draft_names.get(did), "requests": [], "total_qty": 0, "_nm": set()}
            groups[did] = g
        qty = sum(it.quantity for it in r.items)
        nms = {it.nomenclature_id for it in r.items}
        g["requests"].append(
            {
                "id": r.id,
                "number": r.number,
                "ff_id": r.warehouse_id,
                "ff_name": r.warehouse.name if r.warehouse else f"Склад {r.warehouse_id}",
                "wb_name": r.wb_warehouse_name_manual,
                "package_type": r.package_type,
                "status": r.status,
                "qty": qty,
                "sku": len(nms),
            }
        )
        g["total_qty"] += qty
        g["_nm"].update(nms)

    # 5. Порядок групп — как в did_q (свежие сверху).
    out: list[dict] = []
    for did in draft_ids:
        g = groups.get(did)
        if g is None:
            continue
        out.append(
            {
                "draft_id": g["draft_id"],
                "draft_name": g["draft_name"],
                "request_count": len(g["requests"]),
                "total_qty": g["total_qty"],
                "total_sku": len(g["_nm"]),
                "requests": g["requests"],
            }
        )
    return out


async def get_assembly_request(
    db: AsyncSession,
    project_id: int,
    request_id: int,
) -> AssemblyRequest | None:
    """
    Get single assembly request with items loaded.
    """
    result = await db.execute(
        select(AssemblyRequest)
        .options(
            selectinload(AssemblyRequest.items),
            selectinload(AssemblyRequest.warehouse),
            selectinload(AssemblyRequest.wb_fbo_supply),
        )
        .where(
            AssemblyRequest.id == request_id,
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted == False,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


async def create_assembly_request(
    db: AsyncSession,
    project_id: int,
    payload: AssemblyRequestCreate,
) -> AssemblyRequest:
    """
    Create assembly request from payload.
    """
    # 1. Validate warehouse exists + type == FULFILLMENT
    wh = await get_warehouse(db, project_id, payload.warehouse_id)
    if not wh:
        raise ValueError("Warehouse not found")
    if wh.warehouse_type != WarehouseType.FULFILLMENT:
        raise ValueError("Assembly requests can only be created for FULFILLMENT warehouses")

    # 2. Validate FBO supply exists, is ACTIVE, has no active assembly request
    fbo_supply = None
    if payload.wb_fbo_supply_id is not None:
        fbo_result = await db.execute(
            select(WbFboSupply).where(
                WbFboSupply.id == payload.wb_fbo_supply_id,
                WbFboSupply.project_id == project_id,
            )
        )
        fbo_supply = fbo_result.scalar_one_or_none()
        if not fbo_supply:
            raise ValueError("FBO supply not found")
        # ACCEPTED is allowed: users need to retroactively register assembly
        # requests for supplies that WB has already accepted (late onboarding).
        # CANCELLED is still rejected — linking a cancelled supply makes no sense.
        if fbo_supply.wb_status not in ("ACTIVE", "ON_DELIVERY", "IN_PROGRESS", "ACCEPTED"):
            raise ValueError("FBO supply must be ACTIVE, ON_DELIVERY, IN_PROGRESS or ACCEPTED")

        # Совместная поставка: одна WB-поставка может нести несколько сборок —
        # по одной на ФФ-источник (warehouse_id). Блокируем только повтор С ТОГО ЖЕ
        # склада (это и гарантирует partial-unique ix_assembly_requests_fbo_wh_unique).
        existing_result = await db.execute(
            select(AssemblyRequest)
            .where(
                AssemblyRequest.wb_fbo_supply_id == payload.wb_fbo_supply_id,
                AssemblyRequest.warehouse_id == payload.warehouse_id,
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.is_deleted == False,  # noqa: E712
                AssemblyRequest.status != AssemblyStatus.CANCELLED,
            )
            .limit(1)
        )
        if existing_result.scalar_one_or_none():
            raise ValueError("На этой FBO-поставке уже есть активная сборка с этого склада-источника")

    # 3. Generate number
    number = await _next_number(db, project_id, "ASM", AssemblyRequest)

    # 4. Create request
    # Auto-set wb_warehouse_name_manual from FBO supply if linked.
    # If supply.warehouse_name is empty (race window between list-sync and enrich),
    # force-pull WB detail API now so the assembly gets the warehouse immediately.
    if payload.wb_fbo_supply_id is not None and fbo_supply and not fbo_supply.warehouse_name:
        await _try_force_enrich_supply(db, project_id, fbo_supply)

    wb_wh_manual = payload.wb_warehouse_name_manual
    if payload.wb_fbo_supply_id is not None and fbo_supply and not wb_wh_manual:
        wb_wh_manual = fbo_supply.warehouse_name

    assembly_req = AssemblyRequest(
        project_id=project_id,
        warehouse_id=payload.warehouse_id,
        number=number,
        status=AssemblyStatus.IN_PROGRESS,
        wb_fbo_supply_id=payload.wb_fbo_supply_id,
        estimated_ready_date=payload.estimated_ready_date,
        pallets_count=payload.pallets_count,
        pallet_weight_kg=payload.pallet_weight_kg,
        comment=payload.comment,
        wb_warehouse_name_manual=wb_wh_manual,
        package_type=getattr(payload, "package_type", None) or "BOX",
    )
    db.add(assembly_req)
    await db.flush()

    # 5. Resolve barcodes (batch) and create items
    barcodes = [item_data.barcode for item_data in payload.items]
    barcode_result = await db.execute(
        select(Nomenclature).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode.in_(barcodes),
        )
    )
    barcode_map = {n.barcode: n for n in barcode_result.scalars().all()}

    resolved_items: list[AssemblyRequestItem] = []
    for item_data in payload.items:
        nom = barcode_map.get(item_data.barcode)
        if not nom:
            raise ValueError(f"Barcode not found: {item_data.barcode}")
        item = AssemblyRequestItem(
            project_id=project_id,
            assembly_request_id=assembly_req.id,
            nomenclature_id=nom.id,
            barcode=item_data.barcode,
            quantity=item_data.quantity,
        )
        db.add(item)
        resolved_items.append(item)

    # 6. Validate AVAILABLE stock (= warehouse stock - reserved by other active assembly requests).
    # Заявка не должна резервировать больше чем доступно. Свою же заявку из reserved исключаем.
    await db.flush()
    deficits = await _validate_available_for_assembly(
        db, project_id, payload.warehouse_id, resolved_items, exclude_request_id=assembly_req.id
    )
    if deficits:
        raise ValueError(_format_deficit_error(deficits))

    await _log_status_change(db, project_id, assembly_req.id, None, AssemblyStatus.IN_PROGRESS, changed_by="user")

    await db.commit()
    await db.refresh(assembly_req)
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")

    # Best-effort: уведомить ФФ-оператора (портал, напр. Хамза) о новой сборке на
    # его складе. CancelledError (BaseException) пробрасывается, прочее — глушим.
    try:
        from backend.services import fulfillment_notify

        await fulfillment_notify.notify_new_ff_assembly(
            db,
            project_id,
            payload.warehouse_id,
            assembly_number=assembly_req.number,
            warehouse_name=wh.name,
            qty=sum(int(i.quantity) for i in resolved_items),
            wb_number=(fbo_supply.wb_supply_id if fbo_supply else None),
        )
    except Exception:
        logger.warning("new-ff-assembly notify failed", exc_info=True)
    return assembly_req


async def update_assembly_request(
    db: AsyncSession,
    project_id: int,
    request_id: int,
    payload: AssemblyRequestUpdate,
) -> AssemblyRequest:
    """
    Update editable fields.
    """
    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    if req.status == AssemblyStatus.CANCELLED:
        raise ValueError(f"Cannot edit in status {req.status}")

    # SHIPPED / DELIVERED / RETURNED / CLOSED — «закрытые» статусы: структурные поля
    # (склад, FBO-поставка, позиции) менять нельзя; мета (палеты, вес, даты, комментарий,
    # склад WB, упаковка) и логистика — можно (товар уже отгружён/возвращён).
    # Переотгрузка из RETURNED идёт через reopen_for_reship → READY, где FBO снова правится.
    _is_closed = req.status in (
        AssemblyStatus.SHIPPED,
        AssemblyStatus.DELIVERED,
        AssemblyStatus.RETURNED,
        AssemblyStatus.CLOSED,
    )

    # Update FBO supply link — only on actual change, and not in closed statuses.
    if payload.wb_fbo_supply_id is not None and payload.wb_fbo_supply_id != req.wb_fbo_supply_id:
        if _is_closed:
            raise ValueError("Cannot change FBO supply in status " + req.status)
        # Validate the FBO supply
        fbo_result = await db.execute(
            select(WbFboSupply).where(
                WbFboSupply.id == payload.wb_fbo_supply_id,
                WbFboSupply.project_id == project_id,
            )
        )
        fbo_supply = fbo_result.scalar_one_or_none()
        if not fbo_supply:
            raise ValueError("FBO supply not found")
        # Mirror create_assembly_request: ACCEPTED supplies may need late linking.
        if fbo_supply.wb_status not in ("ACTIVE", "ON_DELIVERY", "IN_PROGRESS", "ACCEPTED"):
            raise ValueError("FBO supply must be ACTIVE, ON_DELIVERY, IN_PROGRESS or ACCEPTED")

        # Совместная поставка: блокируем только повтор С ТОГО ЖЕ склада-источника
        # (см. create_assembly_request). Эффективный склад = новый из payload, если
        # его меняют тем же PATCH (warehouse_id ставится ниже), иначе текущий.
        effective_wh_id = payload.warehouse_id if payload.warehouse_id is not None else req.warehouse_id
        existing_result = await db.execute(
            select(AssemblyRequest)
            .where(
                AssemblyRequest.wb_fbo_supply_id == payload.wb_fbo_supply_id,
                AssemblyRequest.warehouse_id == effective_wh_id,
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.is_deleted == False,  # noqa: E712
                AssemblyRequest.status != AssemblyStatus.CANCELLED,
                AssemblyRequest.id != req.id,
            )
            .limit(1)
        )
        if existing_result.scalar_one_or_none():
            raise ValueError("На этой FBO-поставке уже есть активная сборка с этого склада-источника")

        req.wb_fbo_supply_id = payload.wb_fbo_supply_id
        # Force-pull WB detail if supply.warehouse_name is missing (race window).
        if not fbo_supply.warehouse_name:
            await _try_force_enrich_supply(db, project_id, fbo_supply)
        # Auto-set wb_warehouse_name_manual from FBO supply
        req.wb_warehouse_name_manual = fbo_supply.warehouse_name

    # Change source warehouse — structural, like items: only до отгрузки.
    # Ставим ДО блока позиций, чтобы их валидация стока шла по новому складу.
    warehouse_changed = False
    if payload.warehouse_id is not None and payload.warehouse_id != req.warehouse_id:
        if _is_closed:
            raise ValueError(f"Cannot change warehouse in status {req.status}")
        new_wh = await get_warehouse(db, project_id, payload.warehouse_id)
        if not new_wh:
            raise ValueError("Warehouse not found")
        if new_wh.warehouse_type != WarehouseType.FULFILLMENT:
            raise ValueError("Assembly requests can only be created for FULFILLMENT warehouses")
        req.warehouse_id = payload.warehouse_id
        warehouse_changed = True

    # Meta-поля (палеты, вес, даты, комментарий, склад WB, упаковка) — редактируемы
    # в любом не-CANCELLED статусе, включая SHIPPED/DELIVERED/CLOSED. Это не двигает
    # остатки, лишь правит сопроводительные данные уже отгруженной/возвращённой заявки.
    if payload.pallets_count is not None:
        req.pallets_count = payload.pallets_count
    if payload.pallet_weight_kg is not None:
        req.pallet_weight_kg = payload.pallet_weight_kg
    if payload.comment is not None:
        req.comment = payload.comment
    if payload.estimated_ready_date is not None:
        req.estimated_ready_date = payload.estimated_ready_date
    if payload.wb_warehouse_name_manual is not None:
        req.wb_warehouse_name_manual = payload.wb_warehouse_name_manual
    if payload.package_type is not None:
        req.package_type = payload.package_type

    # Vehicle & cost fields — editable in any non-cancelled status (including SHIPPED/DELIVERED)
    if payload.pickup_cost is not None:
        req.pickup_cost = payload.pickup_cost
    if payload.vehicle_info is not None:
        req.vehicle_info = payload.vehicle_info
    if payload.vehicle_brand is not None:
        req.vehicle_brand = payload.vehicle_brand
    if payload.driver_phone is not None:
        req.driver_phone = payload.driver_phone
    if payload.carrier_inn is not None:
        # carrier_inn == "" → unlink, non-empty → upsert & link
        from .status import _resolve_carrier

        cp_id = await _resolve_carrier(db, project_id, payload.carrier_inn, payload.carrier_name)
        req.counterparty_id = cp_id  # None if inn was empty

    # Зеркалим правки логистики на отгрузку ТЕКУЩЕЙ попытки, чтобы аналитика
    # логистики (сумма по OutboundShipment) учитывала постфактум-правки стоимости.
    if req.outbound_shipment_id and req.status in (AssemblyStatus.SHIPPED, AssemblyStatus.DELIVERED):
        cur_ship = (
            await db.execute(
                select(OutboundShipment).where(
                    OutboundShipment.id == req.outbound_shipment_id,
                    OutboundShipment.project_id == project_id,
                )
            )
        ).scalar_one_or_none()
        if cur_ship is not None:
            if payload.pickup_cost is not None:
                cur_ship.pickup_cost = req.pickup_cost
            if payload.vehicle_info is not None:
                cur_ship.vehicle_info = req.vehicle_info
            if payload.vehicle_brand is not None:
                cur_ship.vehicle_brand = req.vehicle_brand
            if payload.driver_phone is not None:
                cur_ship.driver_phone = req.driver_phone
            if payload.carrier_inn is not None:
                cur_ship.counterparty_id = req.counterparty_id
            if payload.pallets_count is not None:
                cur_ship.pallets_count = req.pallets_count
            if payload.pallet_weight_kg is not None:
                cur_ship.pallet_weight_kg = req.pallet_weight_kg

    # Update items: allowed everywhere except SHIPPED/DELIVERED/RETURNED/CANCELLED.
    # READY/VEHICLE_ASSIGNED — позволяем подправить кол-во под факт WB (например,
    # WB принял меньше заявленного — редактируем позиции до отгрузки).
    if payload.items is not None:
        if req.status in (
            AssemblyStatus.SHIPPED,
            AssemblyStatus.DELIVERED,
            AssemblyStatus.RETURNED,
            AssemblyStatus.CLOSED,
            AssemblyStatus.CANCELLED,
        ):
            raise ValueError(f"Items cannot be edited in status {req.status}")
        # Delete existing items
        await db.execute(delete(AssemblyRequestItem).where(AssemblyRequestItem.assembly_request_id == req.id))
        # Resolve barcodes (batch) and create new items
        barcodes = [item_data.barcode for item_data in payload.items]
        barcode_result = await db.execute(
            select(Nomenclature).where(
                Nomenclature.project_id == project_id,
                Nomenclature.barcode.in_(barcodes),
            )
        )
        barcode_map = {n.barcode: n for n in barcode_result.scalars().all()}

        new_items: list[AssemblyRequestItem] = []
        for item_data in payload.items:
            nom = barcode_map.get(item_data.barcode)
            if not nom:
                raise ValueError(f"Barcode not found: {item_data.barcode}")
            item = AssemblyRequestItem(
                project_id=project_id,
                assembly_request_id=req.id,
                nomenclature_id=nom.id,
                barcode=item_data.barcode,
                quantity=item_data.quantity,
            )
            db.add(item)
            new_items.append(item)

        # Validate available stock for active (pre-shipping) statuses.
        # READY/VEHICLE_ASSIGNED skip — там позиции подгоняют под факт WB (см. комментарий выше).
        if req.status in (AssemblyStatus.PENDING, AssemblyStatus.IN_PROGRESS):
            await db.flush()
            deficits = await _validate_available_for_assembly(
                db, project_id, req.warehouse_id, new_items, exclude_request_id=req.id
            )
            if deficits:
                raise ValueError(_format_deficit_error(deficits))

    # Warehouse changed without an items edit → re-validate existing items' available
    # stock on the NEW warehouse (same status gate as item edits: до READY).
    if (
        warehouse_changed
        and payload.items is None
        and req.status in (AssemblyStatus.PENDING, AssemblyStatus.IN_PROGRESS)
    ):
        existing_items = (
            (await db.execute(select(AssemblyRequestItem).where(AssemblyRequestItem.assembly_request_id == req.id)))
            .scalars()
            .all()
        )
        deficits = await _validate_available_for_assembly(
            db, project_id, req.warehouse_id, list(existing_items), exclude_request_id=req.id
        )
        if deficits:
            raise ValueError(_format_deficit_error(deficits))

    await db.commit()
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")
    # Expunge all cached objects so selectinload re-fetches fresh data from DB
    db.expunge_all()
    updated = await get_assembly_request(db, project_id, req.id)
    assert updated is not None, "Assembly request disappeared after update"
    return updated


async def review_ff_proposal(
    db: AsyncSession,
    project_id: int,
    request_id: int,
    approve: bool,
) -> AssemblyRequest:
    """Согласовать или отклонить предложенную ФФ-оператором правку состава.

    ФФ хранит правку как ПРЕДЛОЖЕНИЕ на сборке (ff_proposed_items), не применяя её.
    approve=True — применяем предложение через канонический update_assembly_request
    (тот же сток/резерв-валидатор: дефицит → ValueError → 400 у роутера), затем чистим
    ff_proposed_*. approve=False — просто чистим предложение, состав не трогаем.

    Нет предложения (ff_proposed_items is None) → ValueError (роутер → 400).
    """
    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")
    if req.ff_proposed_items is None:
        raise ValueError("Нет предложения ФФ на согласование")

    if approve:
        proposal = req.ff_proposed_items
        # Применяем как обычное редактирование состава: канонический путь с
        # валидацией доступного стока/резерва. Дефицит поднимет ValueError —
        # откатываем незакоммиченные DELETE/INSERT позиций, чтобы сессия осталась
        # чистой (предложение и старый состав не тронуты), и пробрасываем.
        try:
            await update_assembly_request(
                db,
                project_id,
                request_id,
                AssemblyRequestUpdate(
                    items=[
                        AssemblyItemCreate(barcode=str(i["barcode"]), quantity=int(i["quantity"]))
                        for i in proposal
                    ]
                ),
            )
        except ValueError:
            await db.rollback()
            raise
        # update_assembly_request делает expunge_all + commit; перечитываем заявку,
        # чтобы снять предложение на свежем объекте.
        req = await get_assembly_request(db, project_id, request_id)
        assert req is not None, "Assembly request disappeared after applying FF proposal"

    req.ff_proposed_items = None
    req.ff_proposed_at = None
    req.ff_proposed_by = None
    await db.commit()
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")

    db.expunge_all()
    updated = await get_assembly_request(db, project_id, request_id)
    assert updated is not None, "Assembly request disappeared after FF review"
    return updated


async def get_assembly_attempts(db: AsyncSession, project_id: int, request_id: int) -> list[dict[str, Any]]:
    """Цепочка попыток отгрузки заявки: по одной на OutboundShipment (+ её возврат).

    Снимок логистики берём с самой отгрузки (не с заявки — там лишь последняя попытка).
    Исход: rejected — есть приёмка-возврат для попытки; accepted — FBW принята / заявка
    DELIVERED; иначе in_transit. Возвраты сопоставляются по assembly_attempt_no
    (legacy-приёмки без номера попытки и единственная отгрузка → её возврат).
    """
    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    ship_rows = (
        await db.execute(
            select(
                OutboundShipment,
                WbFboSupply.name.label("supply_name"),
                WbFboSupply.wb_status.label("supply_status"),
                WbFboSupply.wb_supply_id.label("supply_wb_id"),
                Counterparty.inn.label("carrier_inn"),
                Counterparty.name.label("carrier_name"),
            )
            .outerjoin(WbFboSupply, OutboundShipment.wb_fbo_supply_id == WbFboSupply.id)
            .outerjoin(Counterparty, OutboundShipment.counterparty_id == Counterparty.id)
            .where(
                OutboundShipment.assembly_request_id == request_id,
                OutboundShipment.project_id == project_id,
                OutboundShipment.is_deleted == False,  # noqa: E712
            )
            .order_by(OutboundShipment.attempt_no.asc(), OutboundShipment.id.asc())
        )
    ).all()

    ret_rows = (
        await db.execute(
            select(InboundReceipt, Warehouse.name.label("wh_name"))
            .outerjoin(Warehouse, InboundReceipt.warehouse_id == Warehouse.id)
            .where(
                InboundReceipt.assembly_request_id == request_id,
                InboundReceipt.project_id == project_id,
                InboundReceipt.is_deleted == False,  # noqa: E712
            )
            .order_by(InboundReceipt.id.asc())
        )
    ).all()

    returns_by_attempt: dict[int, tuple] = {}
    legacy_returns: list[tuple] = []
    for receipt, wh_name in ret_rows:
        if receipt.assembly_attempt_no is not None:
            returns_by_attempt.setdefault(receipt.assembly_attempt_no, (receipt, wh_name))
        else:
            legacy_returns.append((receipt, wh_name))

    attempts: list[dict[str, Any]] = []
    for ship, supply_name, supply_status, supply_wb_id, carrier_inn, carrier_name in ship_rows:
        ret = returns_by_attempt.get(ship.attempt_no)
        if ret is None and legacy_returns and len(ship_rows) == 1:
            ret = legacy_returns[0]

        if ret is not None:
            outcome = "rejected"
            returned_to_warehouse_id = ret[0].warehouse_id
            returned_to_warehouse_name = ret[1]
            returned_at = ret[0].created_at
        elif supply_status == "ACCEPTED" or req.status == AssemblyStatus.DELIVERED:
            outcome = "accepted"
            returned_to_warehouse_id = None
            returned_to_warehouse_name = None
            returned_at = None
        else:
            outcome = "in_transit"
            returned_to_warehouse_id = None
            returned_to_warehouse_name = None
            returned_at = None

        attempts.append(
            {
                "attempt_no": ship.attempt_no,
                "shipment_id": ship.id,
                "shipment_number": ship.number,
                "shipped_at": ship.created_at,
                "wb_supply_id": ship.wb_supply_id or supply_wb_id,
                "wb_supply_name": supply_name,
                "wb_warehouse_name": ship.destination,
                "wb_fbo_status": supply_status,
                "vehicle_info": ship.vehicle_info,
                "vehicle_brand": ship.vehicle_brand,
                "driver_phone": ship.driver_phone,
                "carrier_inn": carrier_inn,
                "carrier_name": carrier_name,
                "pickup_cost": ship.pickup_cost,
                "pallets_count": ship.pallets_count,
                "pickup_date": ship.pickup_date,
                "delivery_date": ship.delivery_date,
                "outcome": outcome,
                "returned_to_warehouse_id": returned_to_warehouse_id,
                "returned_to_warehouse_name": returned_to_warehouse_name,
                "returned_at": returned_at,
            }
        )

    return attempts

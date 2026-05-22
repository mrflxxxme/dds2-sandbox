# ruff: noqa: RUF001, RUF002, RUF003
"""
Assembly Request service — CRUD operations and helpers.

Part of the assembly service package. See backend/DOMAIN_ASSEMBLY.md for spec.
"""

import logging
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.assembly import (
    AssemblyDraft,
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
)
from backend.models.cost import Nomenclature
from backend.models.warehouse import (
    WarehouseStock,
    WarehouseType,
)
from backend.models.wb_fbo import WbFboSupply
from backend.schemas.assembly import (
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
) -> None:
    """
    On-demand force-pull warehouse_name from WB detail API when supply is linked
    to an assembly but periodic enrich hasn't filled it yet (race window between
    list-sync creating supply and enrich job catching up). Best-effort: any error
    is swallowed — caller proceeds, scheduled enrich will catch up later.

    force=True — pull даже если warehouse_name уже задан (для «Обновить из WB»
    когда юзер мог сменить склад назначения в кабинете WB).
    """
    if not force and supply.warehouse_name:
        return
    try:
        from backend.integrations.wb_api import WBApiClient
        from backend.services.fbo_supply.mappers import _update_supply_from_fbw_detail
        from backend.services.integrations_service import _get_wb_key

        try:
            _key, api_key = await _get_wb_key(db, project_id)
        except ValueError:
            return  # no WB key — silent skip

        api_client = WBApiClient(api_key, project_id=project_id)
        wb_id_int = int(supply.wb_supply_id)
        detail = await api_client.get_fbw_supply_detail(wb_id_int)
        if detail:
            _update_supply_from_fbw_detail(supply, detail)
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
) -> list[dict]:
    """Build items list with product_name and stock_quantity.

    Uses batch queries instead of per-item N+1 queries.
    """
    if not request.items:
        return []

    nom_ids = [item.nomenclature_id for item in request.items if item.nomenclature_id]

    # Batch load nomenclature names
    nom_map: dict[int, Nomenclature] = {}
    if nom_ids:
        nom_result = await db.execute(select(Nomenclature).where(Nomenclature.id.in_(nom_ids)))
        nom_map = {n.id: n for n in nom_result.scalars().all()}

    # Batch load stock quantities
    stock_map: dict[int, int] = {}
    if nom_ids:
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
) -> dict:
    """
    Build AssemblyRequestResponse dict from ORM model.
    Loads warehouse.name, wb_fbo_supply.name, wb_fbo_supply.warehouse_name.
    Computes total_weight_kg = pallets_count * pallet_weight_kg.
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
        from backend.models.counterparty import Counterparty

        cp_row = (
            await db.execute(
                select(Counterparty.inn, Counterparty.name).where(Counterparty.id == request.counterparty_id)
            )
        ).first()
        if cp_row is not None:
            carrier_inn = cp_row.inn
            carrier_name = cp_row.name

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
        "items": (items := await _build_items_with_stock(db, request)),
        "brands": ", ".join(sorted({i["brand"] for i in items if i.get("brand")})) or None,
        "created_at": request.created_at,
        "updated_at": request.updated_at,
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
    draft_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    brand: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AssemblyRequest], int]:
    """
    List assembly requests with filters, pagination.
    """
    base = select(AssemblyRequest).where(
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.is_deleted == False,  # noqa: E712
    )

    if warehouse_id is not None:
        base = base.where(AssemblyRequest.warehouse_id == warehouse_id)
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

        # Check no active assembly request for this FBO supply
        existing_result = await db.execute(
            select(AssemblyRequest)
            .where(
                AssemblyRequest.wb_fbo_supply_id == payload.wb_fbo_supply_id,
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.is_deleted == False,  # noqa: E712
                AssemblyRequest.status != AssemblyStatus.CANCELLED,
            )
            .limit(1)
        )
        if existing_result.scalar_one_or_none():
            raise ValueError("FBO supply already has an active assembly request")

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

    # SHIPPED / DELIVERED — allow only cost, pallets and vehicle fields
    _is_closed = req.status in (AssemblyStatus.SHIPPED, AssemblyStatus.DELIVERED)

    # Update FBO supply link — not allowed in closed statuses
    if payload.wb_fbo_supply_id is not None:
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

        # Check no other active assembly request for this FBO supply (except current)
        existing_result = await db.execute(
            select(AssemblyRequest)
            .where(
                AssemblyRequest.wb_fbo_supply_id == payload.wb_fbo_supply_id,
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.is_deleted == False,  # noqa: E712
                AssemblyRequest.status != AssemblyStatus.CANCELLED,
                AssemblyRequest.id != req.id,
            )
            .limit(1)
        )
        if existing_result.scalar_one_or_none():
            raise ValueError("FBO supply already has an active assembly request")

        req.wb_fbo_supply_id = payload.wb_fbo_supply_id
        # Force-pull WB detail if supply.warehouse_name is missing (race window).
        if not fbo_supply.warehouse_name:
            await _try_force_enrich_supply(db, project_id, fbo_supply)
        # Auto-set wb_warehouse_name_manual from FBO supply
        req.wb_warehouse_name_manual = fbo_supply.warehouse_name

    # Update scalar fields — not allowed in closed statuses
    if not _is_closed:
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

    # Update items: allowed everywhere except SHIPPED/DELIVERED/CANCELLED.
    # READY/VEHICLE_ASSIGNED — позволяем подправить кол-во под факт WB (например,
    # WB принял меньше заявленного — редактируем позиции до отгрузки).
    if payload.items is not None:
        if req.status in (AssemblyStatus.SHIPPED, AssemblyStatus.DELIVERED, AssemblyStatus.CANCELLED):
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

    await db.commit()
    # Expunge all cached objects so selectinload re-fetches fresh data from DB
    db.expunge_all()
    updated = await get_assembly_request(db, project_id, req.id)
    assert updated is not None, "Assembly request disappeared after update"
    return updated

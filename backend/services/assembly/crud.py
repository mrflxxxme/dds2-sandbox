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
    AssemblyKind,
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
)
from backend.models.cost import CostOrder, Nomenclature
from backend.models.counterparty import Counterparty
from backend.models.fulfillment import FulfillmentRequest
from backend.models.gazelka import GazelkaOrder, GazelkaOrderStatus
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
from backend.services.assembly.weight import (
    compute_boxes_count,
    compute_goods_weight,
    compute_suggested_total_weight,
    resolve_box_qty_by_warehouse,
    resolve_unit_weights,
)
from backend.services.settings_service import get_box_weight_kg
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

# Статусы, в которых заявка удерживает резерв стока (учитываются как «в работе»
# при расчёте доступного остатка). SHIPPED резерв не держит — сток уже списан OUTBOUND.
_RESERVING_STATUSES = [
    AssemblyStatus.PENDING,
    AssemblyStatus.IN_PROGRESS,
    AssemblyStatus.READY,
    AssemblyStatus.VEHICLE_ASSIGNED,
]


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
    Check available stock = quantity - reserved by OTHER active assembly requests
    - открытые FBS-задания на этом складе.
    Used at create / update / start_assembly to prevent over-commitment.

    exclude_request_id: исключить эту заявку из расчёта резерва (чтобы не вычитать саму себя
    при редактировании или старте).

    Обратный гейт FBS: товар, проданный со склада продавца WB (`supplier_status`
    new/confirm), физически ещё лежит на нашем складе и НЕ списан из ledger'а —
    списание идёт только по `complete`. Без вычета его можно было бы второй раз
    набрать в сборку и увезти на FBO, а потом не собрать FBS-задание.
    Источник цифры — `wb_fbs.stock_service.get_open_fbs_qty` (та же функция кормит
    прямой расчёт FBS-остатка). Без FBS-заказов вычет = 0 и поведение прежнее.
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
            # kind=fbs резерв не держит — его держат открытые FBS-задания
            # (второе слагаемое ниже); включить = задвоить вычет.
            AssemblyRequest.kind != AssemblyKind.FBS.value,
            AssemblyRequest.status.in_(_RESERVING_STATUSES),
            AssemblyRequestItem.nomenclature_id.in_(nom_ids),
        )
        .group_by(AssemblyRequestItem.nomenclature_id)
    )
    if exclude_request_id is not None:
        reserved_q = reserved_q.where(AssemblyRequest.id != exclude_request_id)
    reserved_result = await db.execute(reserved_q)
    reserved_map = {r.nomenclature_id: r.reserved for r in reserved_result.all()}

    # Второе слагаемое резерва — открытые FBS-задания на этом складе (см. докстринг).
    from backend.services.warehouse_stock_engine import get_open_fbs_reserved

    fbs_map = await get_open_fbs_reserved(db, project_id, [warehouse_id])

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
        fbs_open = fbs_map.get(nom_id, 0)
        available = max(0, stock - reserved - fbs_open)
        if available < need:
            deficit_nom_ids.append(nom_id)

    if not deficit_nom_ids:
        return []

    nom_result = await db.execute(select(Nomenclature).where(Nomenclature.id.in_(deficit_nom_ids)))
    nom_map = {n.id: n for n in nom_result.scalars().all()}

    # Расшифровка «в работе»: какие заявки держат резерв по дефицитным позициям.
    # Без неё сообщение «на складе 88, в работе 132» вводит в заблуждение
    # (резерв может превышать остаток, напр. зависшая неотгруженная заявка).
    holders_q = (
        select(
            AssemblyRequestItem.nomenclature_id,
            AssemblyRequest.number,
            func.sum(AssemblyRequestItem.quantity).label("qty"),
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.warehouse_id == warehouse_id,
            AssemblyRequest.is_deleted.is_(False),
            # Зеркало reserved_q выше: kind=fbs резерв не держит.
            AssemblyRequest.kind != AssemblyKind.FBS.value,
            AssemblyRequest.status.in_(_RESERVING_STATUSES),
            AssemblyRequestItem.nomenclature_id.in_(deficit_nom_ids),
        )
        .group_by(AssemblyRequestItem.nomenclature_id, AssemblyRequest.number)
        .order_by(func.sum(AssemblyRequestItem.quantity).desc(), AssemblyRequest.number)
    )
    if exclude_request_id is not None:
        holders_q = holders_q.where(AssemblyRequest.id != exclude_request_id)
    holders_map: dict[int, list[tuple[str, int]]] = {}
    for r in (await db.execute(holders_q)).all():
        holders_map.setdefault(r.nomenclature_id, []).append((r.number, r.qty))

    deficits: list[dict] = []
    for nom_id in deficit_nom_ids:
        nom = nom_map.get(nom_id)
        bc = barcode_for_nom[nom_id]
        name = (nom.subject or nom.article_seller or bc) if nom else bc
        stock = stock_map.get(nom_id, 0)
        reserved = reserved_map.get(nom_id, 0)
        fbs_open = fbs_map.get(nom_id, 0)
        available = max(0, stock - reserved - fbs_open)
        deficits.append(
            {
                "name": name,
                "barcode": bc,
                "need": needs[nom_id],
                "have": available,
                "stock": stock,
                "reserved": reserved,
                "fbs_open": fbs_open,
                "holders": holders_map.get(nom_id, []),
            }
        )
    return deficits


def _format_deficit_error(deficits: list[dict]) -> str:
    """Format deficit list into a human-readable Russian error message."""
    lines = []
    for d in deficits:
        line = (
            f"  {d['name']} (ШК {d['barcode']}): нужно {d['need']}, доступно {d['have']} "
            f"(на складе {d['stock']}, в работе {d['reserved']}"
        )
        # Продано по FBS — только когда реально есть открытые задания, иначе
        # сообщение обязано остаться прежним (инвариант обратной совместимости).
        fbs_open = d.get("fbs_open") or 0
        if fbs_open:
            line += f", продано по FBS {fbs_open}"
        holders = d.get("holders") or []
        if holders:
            shown = holders[:6]
            line += " — " + ", ".join(f"{number}×{qty}" for number, qty in shown)
            if len(holders) > len(shown):
                line += f" и ещё {len(holders) - len(shown)}"
        line += ")"
        lines.append(line)
    return f"Недостаточно доступных остатков ({len(deficits)} поз.):\n" + "\n".join(lines)


async def _build_items_with_stock(
    db: AsyncSession,
    request: AssemblyRequest,
    *,
    nom_map: dict[int, Nomenclature] | None = None,
    stock_by_wh_nom: dict[tuple[int, int], int] | None = None,
    box_qty_by_wh_bc: dict[tuple[int, str], int | None] | None = None,
    machine_box_qty: dict[tuple[int, str], int] | None = None,
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

        # Кратность короба (склад заявки + ШК) → коробов на позицию = ⌈штук/кратность⌉
        # (зеркало compute_boxes_count: хвост < короба — отдельный неполный короб).
        # box_qty_by_wh_bc может отсутствовать (single-row без резолва) или не иметь
        # записи по ШК → box_qty/boxes = None («коробов» в листе пусто, не ложный 0).
        # Фолбэк для заявок ЕДУЩЕЙ машины: справочника склада ещё нет (заводится на
        # приёмке) → берём кратность со строк машины-источника (machine_box_qty).
        box_qty = box_qty_by_wh_bc.get((request.warehouse_id, item.barcode)) if box_qty_by_wh_bc else None
        if not box_qty and machine_box_qty and request.source_vehicle_id is not None:
            box_qty = machine_box_qty.get((request.source_vehicle_id, item.barcode))
        qty = int(item.quantity or 0)
        boxes = -(-qty // box_qty) if (box_qty and box_qty > 0 and qty > 0) else None

        items_out.append(
            {
                "id": item.id,
                "nomenclature_id": item.nomenclature_id,
                "barcode": item.barcode,
                "quantity": item.quantity,
                "product_name": product_name,
                "article": (nom.article_seller if nom else None),
                "brand": nom.brand if nom else None,
                "stock_quantity": stock_map.get(item.nomenclature_id, 0),
                "box_qty": box_qty,
                "boxes": boxes,
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
    via_gazelka_ids: set[int] | None = None,
    weight_by_barcode: dict[str, Decimal | None] | None = None,
    box_qty_by_wh_bc: dict[tuple[int, str], int | None] | None = None,
    machine_box_qty: dict[tuple[int, str], int] | None = None,
    box_weight_kg: Decimal | None = None,
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

    # Активная (SENT/MATCHED) отправка в Газельку по этой заявке → логистику ведёт
    # агрегатор, ручное «Назначить машину» блокируется. В списке берём из батч-набора
    # via_gazelka_ids (0 запросов на строку); одиночный вызов делает один
    # project-scoped indexed-запрос по FK.
    if via_gazelka_ids is not None:
        via_gazelka = request.id in via_gazelka_ids
    else:
        via_gazelka = (
            await db.execute(
                select(GazelkaOrder.id)
                .where(
                    GazelkaOrder.assembly_request_id == request.id,
                    GazelkaOrder.project_id == request.project_id,
                    GazelkaOrder.status.in_([GazelkaOrderStatus.SENT, GazelkaOrderStatus.MATCHED]),
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None

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

    # Расчётный вес товаров (нетто) + ШК без веса. Вес за 1 шт резолвится цепочкой
    # Nomenclature.weight_kg → машина (CostOrderItem). В list-контексте карта
    # weight_by_barcode приходит из prefetch (0 запросов на строку — паритет с
    # per-row); одиночный вызов резолвит цепочку сам (2 батч-запроса).
    goods_weight_kg, weight_missing_barcodes = await compute_goods_weight(
        db, request.project_id, request, weight_by_barcode=weight_by_barcode
    )

    # Число коробов + расчётный вес отгрузки (нетто + вес_коробки × коробов). Карта
    # кратности per (склад, ШК) и вес коробки приходят из prefetch (0 запросов на
    # строку); одиночный вызов резолвит для своего склада и читает настройку сам.
    single_row = box_qty_by_wh_bc is None  # детальный/одиночный путь (не список)
    if box_qty_by_wh_bc is None:
        box_qty_by_wh_bc = await resolve_box_qty_by_warehouse(
            db, request.project_id, {request.warehouse_id: [it.barcode for it in (request.items or [])]}
        )
        box_weight_kg = await get_box_weight_kg(db, request.project_id)
    # Фолбэк кратности со строк машины-источника (единичный путь; список — из prefetch).
    if machine_box_qty is None and request.source_vehicle_id is not None:
        from backend.services.box_multiplicity_service import resolve_source_machine_box_qty

        vehicle = await db.get(CostOrder, request.source_vehicle_id)
        if vehicle is not None and vehicle.project_id == request.project_id and not vehicle.is_deleted:
            machine_box_qty = await resolve_source_machine_box_qty(
                db, request.project_id, {request.source_vehicle_id: vehicle.order_no}
            )
    boxes_count = compute_boxes_count(request, box_qty_by_wh_bc)
    suggested_total_weight_kg = compute_suggested_total_weight(goods_weight_kg, boxes_count, box_weight_kg)

    # Геометрическая оценка числа паллет (footprint по коробам, БЕЗ веса) — только на
    # детальном пути, чтобы не плодить N+1 в списке (warehouse-имя + машинный
    # box_size + override). Кнопка «Авто» в раскладке проставляет её в pallets_count.
    suggested_pallets_count = None
    if single_row:
        from backend.services.assembly.pallets import _suggest_pallets_count

        suggested_pallets_count = await _suggest_pallets_count(
            db, request.project_id, request, box_qty_by_wh_bc
        )

    # Авто-вес: если ручной «Общий вес» не задан (0), показываем расчётный вес
    # отгрузки как значение (примерный, флаг weight_is_estimated) — без кнопок
    # «Указать»/«применить». Оператор при желании перебивает вручную; при переходе
    # в «Готово» расчёт сохраняется в реальный вес (mark_ready).
    weight_is_estimated = False
    if total_weight <= 0 and suggested_total_weight_kg is not None and suggested_total_weight_kg > 0:
        total_weight = suggested_total_weight_kg
        weight_is_estimated = True

    return {
        "id": request.id,
        "warehouse_id": request.warehouse_id,
        "warehouse_name": request.warehouse.name if request.warehouse else None,
        "number": request.number,
        "status": request.status,
        "kind": request.kind,
        "fbs_supply_id": request.fbs_supply_id,
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
        "shipped_as_boxes": request.shipped_as_boxes,
        "pallet_weight_kg": request.pallet_weight_kg,
        "total_weight_kg": total_weight,
        "weight_is_estimated": weight_is_estimated,
        "pallet_manifest": request.pallet_manifest,
        "goods_weight_kg": goods_weight_kg,
        "weight_missing_barcodes": weight_missing_barcodes,
        "boxes_count": boxes_count,
        "suggested_total_weight_kg": suggested_total_weight_kg,
        "suggested_pallets_count": suggested_pallets_count,
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
        "package_type": request.package_type,  # тип поставки (короб/моно/сейф) — колонка списка
        "source_draft_id": request.source_draft_id,
        "source_vehicle_id": request.source_vehicle_id,
        "is_pre_distribution": request.is_pre_distribution,
        "is_prebooking": request.is_prebooking,
        "effective_wb_warehouse": (
            (request.wb_fbo_supply.warehouse_name if request.wb_fbo_supply else None)
            or request.wb_warehouse_name_manual
        ),
        "counterparty_id": request.counterparty_id,
        "carrier_inn": carrier_inn,
        "carrier_name": carrier_name,
        "logistics_by_warehouse": request.logistics_by_warehouse,
        # Контрагент склада-источника — для UI-гейта чекбокса «логистику оказывает
        # склад». warehouse уже загружен (warehouse_name выше) — без лишнего запроса.
        "warehouse_counterparty_id": request.warehouse.counterparty_id if request.warehouse else None,
        "items": (
            items := await _build_items_with_stock(
                db, request, nom_map=nom_map, stock_by_wh_nom=stock_by_wh_nom,
                box_qty_by_wh_bc=box_qty_by_wh_bc, machine_box_qty=machine_box_qty,
            )
        ),
        "brands": ", ".join(sorted({i["brand"] for i in items if i.get("brand")})) or None,
        "created_at": request.created_at,
        "updated_at": request.updated_at,
        "ff_review_pending": proposal is not None,
        "ff_proposed_items": ff_proposed_out,
        "ff_proposed_at": request.ff_proposed_at,
        "ff_proposed_by": request.ff_proposed_by,
        "via_gazelka": via_gazelka,
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
    item_barcodes: set[str] = set()
    wh_to_barcodes: dict[int, set[str]] = {}  # склад → ШК (кратность резолвится per-склад)
    for req in requests:
        if req.warehouse_id:
            wh_ids.add(req.warehouse_id)
        if req.counterparty_id:
            cp_ids.add(req.counterparty_id)
        for item in req.items:
            if item.nomenclature_id:
                nom_ids.add(item.nomenclature_id)
            if item.barcode:
                item_barcodes.add(item.barcode)
                if req.warehouse_id:
                    wh_to_barcodes.setdefault(req.warehouse_id, set()).add(item.barcode)
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

    # Один запрос на всю страницу: id заявок с активной (SENT/MATCHED) отправкой в
    # Газельку. Ограничено IN (набор строк страницы) → без .limit().
    req_ids = [r.id for r in requests]
    via_gazelka_ids: set[int] = set()
    if req_ids:
        gz_result = await db.execute(
            select(GazelkaOrder.assembly_request_id).where(
                GazelkaOrder.project_id == project_id,
                GazelkaOrder.assembly_request_id.in_(req_ids),
                GazelkaOrder.status.in_([GazelkaOrderStatus.SENT, GazelkaOrderStatus.MATCHED]),
            )
        )
        for rid in gz_result.scalars().all():
            if rid is not None:
                via_gazelka_ids.add(rid)

    # Вес за 1 шт по цепочке Nomenclature.weight_kg → машина (CostOrderItem) —
    # один резолв на всю страницу (константа запросов), чтобы goods_weight в
    # списке считался без N+1 и совпадал с per-row сборкой.
    weight_by_barcode = await resolve_unit_weights(db, project_id, sorted(item_barcodes))

    # Кратность коробки per (склад, ШК) — один проход на склад (складов мало) +
    # вес пустой коробки (одно число на проект): расчётный вес отгрузки в списке
    # считается без N+1 и совпадает с per-row/детальной сборкой.
    box_qty_by_wh_bc = await resolve_box_qty_by_warehouse(
        db, project_id, {wh: list(bcs) for wh, bcs in wh_to_barcodes.items()}
    )
    box_weight_kg = await get_box_weight_kg(db, project_id)

    # Фолбэк кратности для заявок ЕДУЩЕЙ машины (справочника склада ещё нет): резолв
    # со строк машин-источников. Одним батчем на все машины страницы (не N+1).
    machine_box_qty: dict[tuple[int, str], int] = {}
    vehicle_ids = {req.source_vehicle_id for req in requests if req.source_vehicle_id is not None}
    if vehicle_ids:
        from backend.services.box_multiplicity_service import resolve_source_machine_box_qty

        veh_rows = await db.execute(
            select(CostOrder.id, CostOrder.order_no).where(
                CostOrder.project_id == project_id,
                CostOrder.id.in_(vehicle_ids),
                CostOrder.is_deleted == False,  # noqa: E712
            )
        )
        vid_to_order = {r.id: r.order_no for r in veh_rows.all()}
        if vid_to_order:
            machine_box_qty = await resolve_source_machine_box_qty(db, project_id, vid_to_order)

    return {
        "nom_map": nom_map,
        "stock_by_wh_nom": stock_by_wh_nom,
        "cp_map": cp_map,
        "prop_nom_map": prop_nom_map,
        "via_gazelka_ids": via_gazelka_ids,
        "weight_by_barcode": weight_by_barcode,
        "box_qty_by_wh_bc": box_qty_by_wh_bc,
        "machine_box_qty": machine_box_qty,
        "box_weight_kg": box_weight_kg,
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


async def list_source_vehicles(
    db: AsyncSession,
    project_id: int,
) -> list[dict[str, Any]]:
    """Машины (CostOrder), у которых есть не удалённые заявки сборки, — опции
    дропдауна «Источник» в списке сборок. Свежие сверху (по max id заявки)."""
    rows = (
        await db.execute(
            select(
                CostOrder.id,
                CostOrder.order_no,
                func.max(AssemblyRequest.id).label("last_req_id"),
            )
            .join(AssemblyRequest, AssemblyRequest.source_vehicle_id == CostOrder.id)
            .where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.is_deleted == False,  # noqa: E712
                CostOrder.project_id == project_id,
                CostOrder.is_deleted == False,  # noqa: E712
            )
            .group_by(CostOrder.id, CostOrder.order_no)
            .order_by(func.max(AssemblyRequest.id).desc())
            .limit(100)
        )
    ).all()
    return [{"id": r.id, "order_no": r.order_no} for r in rows]


# --- CRUD -------------------------------------------------------------------


# Статусы, при которых сборка авто-уходит в «Архив» основного списка заявок:
# «Принято ВБ» (DELIVERED) / «Возврат на склад» (RETURNED) / «Закрыта» (CLOSED) /
# «Отменена» (CANCELLED) — в активном списке они не нужны (зеркало
# _AUTO_ARCHIVE_STATUSES FF-портала + CLOSED/RETURNED).
# Переход в DELIVERED ставится автоматически при приёмке WB (fbo_supply/sync.py).
# RETURNED НЕтерминален (из него возможна переотгрузка через reopen_for_reship),
# но текущая отгрузка уже отработала — в активной работе такая заявка не нужна;
# найти её можно явным фильтром статуса «Возврат на склад» или видом «Архив»/«Все».
_LIST_ARCHIVED_STATUSES = (
    AssemblyStatus.DELIVERED.value,
    AssemblyStatus.RETURNED.value,
    AssemblyStatus.CLOSED.value,
    AssemblyStatus.CANCELLED.value,
)


async def list_assembly_requests(
    db: AsyncSession,
    project_id: int,
    *,
    warehouse_id: int | None = None,
    counterparty_id: int | None = None,
    draft_id: int | None = None,
    status: str | None = None,
    view: str | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    brand: str | None = None,
    ff_link: str | None = None,
    joint_only: bool = False,
    source: str | None = None,
    source_vehicle_id: int | None = None,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AssemblyRequest], int]:
    """
    List assembly requests with filters, pagination.

    ff_link: "none" — только заявки БЕЗ привязанной ФФ-заявки; "linked" — только
    с привязанной; None — без фильтра. Привязка живёт в
    FulfillmentRequest.assembly_request_id (project-scoped).

    view: "active" — скрыть авто-архивные статусы (Принято ВБ / Возврат на склад /
    Закрыта / Отменена); "archived" — только их; "all"/None — без фильтра.
    Явный `status` имеет приоритет над `view` (выбор конкретного статуса
    показывает его независимо от вида).

    joint_only: True — только «совместные» сборки (делят WB FBO-поставку с ≥1
    другой активной сборкой, т.е. ≥2 сборок на одну поставку под тем же
    предикатом, что у partial-unique индекса: не удалена, не CANCELLED).

    source: происхождение заявки. "pre_dist" — с меткой машины
    (source_vehicle_id, предраспределение И долив принятой машины);
    "prebooking" — 🅿️ предзаявки (is_prebooking); "plain" — без того и
    другого; None — без фильтра.

    source_vehicle_id: точечный фильтр «заявки ЭТОЙ машины» (уточняет
    source="pre_dist"; сам по себе тоже работает).

    kind: "fbo" — операционные заявки логиста; "fbs" — учётные зеркала поставок
    FBS (ведёт джоб); None — все. Валидация значения — на роутере
    (ALLOWED_ASSEMBLY_KINDS → 422).
    """
    base = select(AssemblyRequest).where(
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.is_deleted == False,  # noqa: E712
    )

    if kind is not None:
        base = base.where(AssemblyRequest.kind == kind)
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
    elif view == "active":
        base = base.where(AssemblyRequest.status.notin_(_LIST_ARCHIVED_STATUSES))
    elif view == "archived":
        base = base.where(AssemblyRequest.status.in_(_LIST_ARCHIVED_STATUSES))
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

    if source_vehicle_id is not None:
        base = base.where(AssemblyRequest.source_vehicle_id == source_vehicle_id)
    if source == "pre_dist":
        base = base.where(AssemblyRequest.source_vehicle_id.is_not(None))
    elif source == "prebooking":
        base = base.where(AssemblyRequest.is_prebooking == True)  # noqa: E712
    elif source == "plain":
        base = base.where(
            AssemblyRequest.source_vehicle_id.is_(None),
            AssemblyRequest.is_prebooking == False,  # noqa: E712
        )

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


async def merge_assembly_requests(
    db: AsyncSession,
    project_id: int,
    request_ids: list[int],
) -> AssemblyRequest:
    """Объединить N созданных сборок в одну.

    Требования (иначе ValueError → 400 у роутера):
      - все существуют, принадлежат проекту, не удалены;
      - статус ∈ {PENDING, IN_PROGRESS} («В сборке», сток ещё не двигался);
      - однородны по (склад-источник · направление · упаковка), где направление =
        wb_fbo_supply_id (если задан) иначе wb_warehouse_name_manual.

    Survivor — сборка с наибольшим числом позиций (tie-break — меньший id).
    Позиции losers суммируются в survivor по (nomenclature_id, barcode); строки
    losers удаляются. pallets_count = Σ; pallet_weight_kg = взвешенное среднее на
    паллету. ФФ-связи (fulfillment_requests.assembly_request_id) losers → survivor.
    Losers → soft_delete. Атомарно.

    Сток не валидируем: тот же склад, товар лишь консолидируется — суммарный
    резерв не меняется (losers уходят в soft-delete, их qty переходит в survivor).
    """
    result = await db.execute(
        select(AssemblyRequest)
        .options(selectinload(AssemblyRequest.items))
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.id.in_(request_ids),
            AssemblyRequest.is_deleted == False,  # noqa: E712
        )
    )
    requests = list(result.scalars().all())

    found_ids = {r.id for r in requests}
    missing = [i for i in request_ids if i not in found_ids]
    if missing:
        raise ValueError(f"Сборки не найдены: {missing}")
    if len(requests) < 2:
        raise ValueError("Нужно ≥2 сборки для объединения")

    # PRE_DISTRIBUTED («зарезервировано под машину в пути») тоже сливаем — это дубли
    # экрана «Распределить машину». Но НЕ смешиваем их с обычными «В сборке»: у
    # PRE_DISTRIBUTED нет реального стока (резерв под машину), слияние с IN_PROGRESS
    # раздуло бы реальный резерв склада.
    mergeable = {AssemblyStatus.PENDING, AssemblyStatus.IN_PROGRESS, AssemblyStatus.PRE_DISTRIBUTED}
    bad = [r.number for r in requests if AssemblyStatus(r.status) not in mergeable]
    if bad:
        raise ValueError(f"Объединять можно только сборки «В сборке»/«Распределено». Не подходят: {', '.join(bad)}")
    statuses = {AssemblyStatus(r.status) for r in requests}
    if AssemblyStatus.PRE_DISTRIBUTED in statuses and statuses != {AssemblyStatus.PRE_DISTRIBUTED}:
        raise ValueError("Нельзя смешивать сборки «Распределено под машину» с обычными «В сборке»")

    def _dir_key(r: AssemblyRequest) -> tuple:
        return (
            r.warehouse_id,
            r.package_type or "BOX",
            r.wb_fbo_supply_id,
            None if r.wb_fbo_supply_id is not None else (r.wb_warehouse_name_manual or "").strip(),
            # Пре-дистрибуции разных машин на одно направление НЕ сливаем (у обычных
            # сборок source_vehicle_id=None → не влияет).
            r.source_vehicle_id,
        )

    if len({_dir_key(r) for r in requests}) != 1:
        raise ValueError("Объединять можно только сборки одного склада-источника, направления и упаковки")

    # Survivor — больше всего позиций; tie-break — меньший id.
    survivor = max(requests, key=lambda r: (len(r.items), -r.id))
    losers = [r for r in requests if r.id != survivor.id]
    loser_ids = [r.id for r in losers]

    # Слить позиции по (nomenclature_id, barcode): совпадение → сумма qty, иначе новая строка.
    items_by_key: dict[tuple[int, str], AssemblyRequestItem] = {
        (it.nomenclature_id, it.barcode): it for it in survivor.items
    }
    for loser in losers:
        for it in loser.items:
            key = (it.nomenclature_id, it.barcode)
            existing = items_by_key.get(key)
            if existing is not None:
                existing.quantity += it.quantity
            else:
                new_it = AssemblyRequestItem(
                    project_id=project_id,
                    assembly_request_id=survivor.id,
                    nomenclature_id=it.nomenclature_id,
                    barcode=it.barcode,
                    quantity=it.quantity,
                )
                db.add(new_it)
                items_by_key[key] = new_it

    # Позиции losers полностью поглощены survivor — удаляем, чтобы не задвоить в сырых суммах.
    await db.execute(
        delete(AssemblyRequestItem).where(
            AssemblyRequestItem.project_id == project_id,
            AssemblyRequestItem.assembly_request_id.in_(loser_ids),
        )
    )

    # Паллеты — сумма; вес — взвешенное среднее на паллету (pallet_weight_kg = вес одной паллеты).
    total_pallets = sum(max(0, int(r.pallets_count or 0)) for r in requests)
    total_weight = sum(
        (Decimal(int(r.pallets_count or 0)) * (r.pallet_weight_kg or Decimal("0")) for r in requests),
        Decimal("0"),
    )
    survivor.pallets_count = max(1, total_pallets)
    survivor.pallet_weight_kg = (
        (total_weight / Decimal(total_pallets)).quantize(Decimal("0.01")) if total_pallets > 0 else Decimal("0")
    )

    # ФФ-связи losers → survivor (обратный аудит FF-портал → сборка не теряется).
    ff_links = (
        (
            await db.execute(
                select(FulfillmentRequest).where(
                    FulfillmentRequest.project_id == project_id,
                    FulfillmentRequest.assembly_request_id.in_(loser_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    for link in ff_links:
        link.assembly_request_id = survivor.id

    note = f"Объединено: {', '.join(r.number for r in losers)}"
    survivor.comment = f"{survivor.comment} | {note}" if survivor.comment else note

    for loser in losers:
        loser.soft_delete()

    await db.commit()
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")
    await invalidate_cache("reports:balance")
    db.expunge_all()
    merged = await get_assembly_request(db, project_id, survivor.id)
    assert merged is not None, "Survivor disappeared after merge"
    return merged


async def create_assembly_request(
    db: AsyncSession,
    project_id: int,
    payload: AssemblyRequestCreate,
    *,
    skip_stock_validation: bool = False,
    status_override: AssemblyStatus | None = None,
    source_vehicle_id: int | None = None,
    is_pre_distribution: bool = False,
    is_prebooking: bool = False,
) -> AssemblyRequest:
    """
    Create assembly request from payload.

    Pre-distribution params (машина в пути, см. pre_distribution.py):
      - skip_stock_validation: пропустить проверку доступного стока (товар ещё едет,
        реального стока на ФФ нет — резерв создаётся под входящую машину).
      - status_override: статус создаваемой заявки (по умолч. IN_PROGRESS);
        для предраспределения = PRE_DISTRIBUTED.
      - source_vehicle_id / is_pre_distribution: привязка к машине-источнику.
    Предзаявка (бронь) на моно, см. prebooking.py:
      - is_prebooking: пометка «предзаявка на моно» (целая моно-паллета на WB-склад
        без лимита приёмки). Обычная валидация стока (товар реально на ФФ), статус
        по умолчанию IN_PROGRESS.
    FF-type check, FBO-валидация и нумерация сохраняются во всех режимах.
    """
    effective_status = status_override or AssemblyStatus.IN_PROGRESS
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
        status=effective_status,
        wb_fbo_supply_id=payload.wb_fbo_supply_id,
        estimated_ready_date=payload.estimated_ready_date,
        pallets_count=payload.pallets_count,
        pallet_weight_kg=payload.pallet_weight_kg,
        shipped_as_boxes=getattr(payload, "shipped_as_boxes", False) or False,
        comment=payload.comment,
        wb_warehouse_name_manual=wb_wh_manual,
        package_type=getattr(payload, "package_type", None) or "BOX",
        source_vehicle_id=source_vehicle_id,
        is_pre_distribution=is_pre_distribution,
        is_prebooking=is_prebooking,
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
    # Предраспределение (skip_stock_validation): товар ещё едет на машине, реального
    # стока на ФФ нет — резерв создаётся под входящую машину, проверка стока неуместна.
    if not skip_stock_validation:
        await db.flush()
        deficits = await _validate_available_for_assembly(
            db, project_id, payload.warehouse_id, resolved_items, exclude_request_id=assembly_req.id
        )
        if deficits:
            raise ValueError(_format_deficit_error(deficits))
    else:
        await db.flush()

    await _log_status_change(db, project_id, assembly_req.id, None, effective_status, changed_by="user")

    await db.commit()
    await db.refresh(assembly_req)
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")
    await invalidate_cache("reports:warehouse_need")

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
    changed_by: str | None = None,
) -> AssemblyRequest:
    """
    Update editable fields.

    `changed_by` — отображаемое имя автора правки; пишется в историю
    стоимости перевозки при фактическом изменении pickup_cost (ASM-785).
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
    if payload.shipped_as_boxes is not None:
        req.shipped_as_boxes = payload.shipped_as_boxes
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
        # Логируем правку стоимости перевозки в историю (старая→новая + автор),
        # только когда цена реально изменилась (ASM-785).
        if payload.pickup_cost != req.pickup_cost:
            from .status import _log_pickup_cost_change

            await _log_pickup_cost_change(
                db,
                project_id,
                req.id,
                old_cost=req.pickup_cost,
                new_cost=payload.pickup_cost,
                changed_by=changed_by,
            )
        req.pickup_cost = payload.pickup_cost
    if payload.vehicle_info is not None:
        req.vehicle_info = payload.vehicle_info
    if payload.vehicle_brand is not None:
        req.vehicle_brand = payload.vehicle_brand
    if payload.driver_phone is not None:
        req.driver_phone = payload.driver_phone
    if payload.driver_first_name is not None:
        req.driver_first_name = payload.driver_first_name
    if payload.driver_last_name is not None:
        req.driver_last_name = payload.driver_last_name
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
            if payload.shipped_as_boxes is not None:
                # Единица (короб/паллета) — постфактум-правка заявки зеркалится на забор,
                # чтобы «Оплаты»/«История отправок» показывали ту же единицу, что в заявке.
                cur_ship.shipped_as_boxes = req.shipped_as_boxes

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

    # Ручная правка реквизитов машины в шапке заявки тоже тянется в WB-пропуск
    # (как и при assign_vehicle). Best-effort: сбой синка не роняет сохранение.
    vehicle_edited = (
        payload.vehicle_info is not None
        or payload.vehicle_brand is not None
        or payload.driver_phone is not None
        or payload.driver_first_name is not None
        or payload.driver_last_name is not None
    )
    if vehicle_edited:
        from backend.services import wb_supply_service

        try:
            await wb_supply_service.sync_pass_from_vehicle(db, project_id, req)
        except Exception:  # noqa: BLE001
            logger.warning("sync_pass_from_vehicle failed for request %s", req.id)

    await db.commit()
    # Правка реквизитов могла дозаполнить пропуск → пробуем авто-занос в WB
    # (если дата забронирована и пропуск полный). После commit, best-effort.
    if vehicle_edited:
        from backend.services import wb_supply_service

        try:
            await wb_supply_service.try_autopush_pass_by_assembly(db, project_id, req.id)
        except Exception:  # noqa: BLE001
            logger.warning("try_autopush_pass_by_assembly failed for request %s", req.id)
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")
    await invalidate_cache("reports:warehouse_need")
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
                "shipped_as_boxes": ship.shipped_as_boxes,
                "pickup_date": ship.pickup_date,
                "delivery_date": ship.delivery_date,
                "outcome": outcome,
                "returned_to_warehouse_id": returned_to_warehouse_id,
                "returned_to_warehouse_name": returned_to_warehouse_name,
                "returned_at": returned_at,
            }
        )

    return attempts

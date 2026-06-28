# ruff: noqa: RUF002, RUF003
"""
FF-портал — сервис чтения/скоупа для внешнего оператора (Хамза).

Кросс-проектные выборки по его складам: заявки на сборку, приёмки (вкл. возвраты WB),
остатки. Только «безопасные» поля (без себестоимости/перевозчика/аналитики). Мутации
(start/ready/ship/accept) делаются существующими сервисами — здесь только скоуп-гард.
"""

from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.ff_context import FfContext
from backend.models.assembly import (
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
    AssemblyStatusHistory,
)
from backend.models.cost import Nomenclature
from backend.models.counterparty import Counterparty
from backend.models.gazelka import GazelkaOrder, GazelkaOrderStatus
from backend.models.warehouse import InboundReceipt, WarehouseStock
from backend.schemas.ff_portal import (
    FfAcceptanceItem,
    FfAcceptanceRow,
    FfAssemblyDetail,
    FfAssemblyItem,
    FfAssemblyItemInput,
    FfAssemblyRow,
    FfStatusEvent,
    FfStockRow,
)
from backend.utils.time import utcnow

# Статусы, в которых оператор может править состав заявки (до начала сборки).
_EDITABLE_STATUSES = (AssemblyStatus.PENDING, AssemblyStatus.IN_PROGRESS)

# Статусы «до отгрузки» — пока товар физически не уехал, можно править паллеты/вес.
_PRE_SHIP_STATUSES = (
    AssemblyStatus.PENDING,
    AssemblyStatus.IN_PROGRESS,
    AssemblyStatus.READY,
    AssemblyStatus.VEHICLE_ASSIGNED,
)

# Терминальные статусы, при которых сборка АВТО-уходит в архив FF-портала
# («Принята WB» / «Отменена») — в активном списке оператору они не нужны.
_AUTO_ARCHIVE_STATUSES = (AssemblyStatus.DELIVERED.value, AssemblyStatus.CANCELLED.value)


def _assembly_archive_cond(archived: bool):  # type: ignore[no-untyped-def]
    """Предикат архива FF-списка сборок.

    Активные (archived=False): не в архиве И не терминальная (delivered/cancelled).
    Архив (archived=True): в архиве ИЛИ терминальная — delivered/cancelled авто-архивируются.
    """
    if archived:
        return or_(
            AssemblyRequest.is_archived.is_(True),
            AssemblyRequest.status.in_(_AUTO_ARCHIVE_STATUSES),
        )
    return and_(
        AssemblyRequest.is_archived.is_(False),
        AssemblyRequest.status.notin_(_AUTO_ARCHIVE_STATUSES),
    )

# Статусы, в которых позиции заявки держат резерв остатка (ещё не отгружены).
_ACTIVE_RESERVE_STATUSES = (
    AssemblyStatus.PENDING,
    AssemblyStatus.IN_PROGRESS,
    AssemblyStatus.READY,
    AssemblyStatus.VEHICLE_ASSIGNED,
)

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500


def _esc_like(s: str) -> str:
    """Escape % and _ for ILIKE (iron rule: экранировать пользовательский ввод)."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_status_filter(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    vals = [s.strip() for s in raw.split(",") if s.strip()]
    return vals or None


async def _meta_map(db: AsyncSession, nom_ids: set[int]) -> dict[int, tuple[str | None, str | None, str | None]]:
    """nomenclature_id -> (display_name, article_seller, brand). Batch, no N+1.

    display_name = subject → article_seller (fallback); article = article_seller as-is;
    brand = Nomenclature.brand as-is.
    """
    if not nom_ids:
        return {}
    rows = await db.execute(
        select(Nomenclature.id, Nomenclature.subject, Nomenclature.article_seller, Nomenclature.brand).where(
            Nomenclature.id.in_(nom_ids)
        )
    )
    return {nid: ((subject or article), article, brand) for nid, subject, article, brand in rows.all()}


# ─── Scope guards ────────────────────────────────────────────────────────────


async def get_scoped_assembly(db: AsyncSession, ctx: FfContext, assembly_id: int) -> AssemblyRequest:
    """Load an assembly request only if it belongs to one of the operator's FF warehouses."""
    req = (
        await db.execute(
            select(AssemblyRequest)
            .options(selectinload(AssemblyRequest.items), selectinload(AssemblyRequest.wb_fbo_supply))
            .where(
                AssemblyRequest.id == assembly_id,
                AssemblyRequest.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if not req or not ctx.allows(req.project_id, req.warehouse_id):
        raise HTTPException(404, "Заявка не найдена")
    return req


async def get_scoped_receipt(db: AsyncSession, ctx: FfContext, receipt_id: int) -> InboundReceipt:
    """Load an inbound receipt only if it belongs to one of the operator's FF warehouses."""
    receipt = (
        await db.execute(
            select(InboundReceipt)
            .options(selectinload(InboundReceipt.items))
            .where(
                InboundReceipt.id == receipt_id,
                InboundReceipt.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if not receipt or not ctx.allows(receipt.project_id, receipt.warehouse_id):
        raise HTTPException(404, "Приёмка не найдена")
    return receipt


# ─── Row builders ─────────────────────────────────────────────────────────────


def _build_assembly_row(
    ctx: FfContext,
    req: AssemblyRequest,
    meta: dict[int, tuple[str | None, str | None, str | None]],
    box_qty_map: dict[str, int | None] | None = None,
    stock_map: dict[str, int] | None = None,
) -> FfAssemblyRow:
    proj = ctx.projects.get(req.project_id)
    wh = ctx.warehouse_by_id.get(req.warehouse_id)
    wb_name = None
    if req.wb_fbo_supply is not None:
        wb_name = req.wb_fbo_supply.warehouse_name
    wb_name = wb_name or req.wb_warehouse_name_manual
    items = [
        FfAssemblyItem(
            barcode=it.barcode,
            article=meta.get(it.nomenclature_id, (None, None, None))[1],
            product_name=(meta.get(it.nomenclature_id, (None, None, None))[0]) or it.barcode,
            box_qty=box_qty_map.get(it.barcode) if box_qty_map else None,
            stock=stock_map.get(it.barcode, 0) if stock_map else 0,
            quantity=it.quantity,
        )
        for it in req.items
    ]
    # Уникальные непустые бренды позиций (из Nomenclature.brand) — через запятую.
    brand_set = {b for it in req.items if (b := meta.get(it.nomenclature_id, (None, None, None))[2])}
    brands = ", ".join(sorted(brand_set)) or None
    return FfAssemblyRow(
        id=req.id,
        project_slug=proj.slug if proj else "",
        project_name=proj.name if proj else "",
        warehouse_id=req.warehouse_id,
        warehouse_name=wh.name if wh else "",
        number=req.number,
        status=req.status,
        wb_warehouse_name=wb_name,
        fbo_supply_number=req.wb_fbo_supply.wb_supply_id if req.wb_fbo_supply is not None else None,
        brands=brands,
        package_type=req.package_type,
        pallets_count=req.pallets_count,
        pallet_weight_kg=req.pallet_weight_kg,
        estimated_ready_date=req.estimated_ready_date,
        actual_ready_date=req.actual_ready_date,
        vehicle_assigned=req.vehicle_assigned_at is not None,
        is_archived=req.is_archived,
        items=items,
        created_at=req.created_at,
    )


def _build_acceptance_row(
    ctx: FfContext,
    receipt: InboundReceipt,
    meta: dict[int, tuple[str | None, str | None, str | None]],
    user_id: int,
) -> FfAcceptanceRow:
    proj = ctx.projects.get(receipt.project_id)
    wh = ctx.warehouse_by_id.get(receipt.warehouse_id)
    items = [
        FfAcceptanceItem(
            item_id=it.id,
            barcode=it.barcode,
            article=meta.get(it.nomenclature_id, (None, None, None))[1],
            product_name=(meta.get(it.nomenclature_id, (None, None, None))[0]) or it.barcode,
            expected_qty=it.expected_qty,
            actual_qty=it.actual_qty,
            defect_qty=it.defect_qty,
        )
        for it in receipt.items
    ]
    return FfAcceptanceRow(
        id=receipt.id,
        project_slug=proj.slug if proj else "",
        project_name=proj.name if proj else "",
        warehouse_id=receipt.warehouse_id,
        warehouse_name=wh.name if wh else "",
        number=receipt.number,
        kind="return" if receipt.assembly_request_id else "inbound",
        status=receipt.status,
        in_work=receipt.work_started_at is not None,
        assigned_to_me=receipt.assigned_to_user_id == user_id,
        planned_date=receipt.planned_date,
        actual_date=receipt.actual_date,
        comment=receipt.comment,
        is_archived=receipt.is_archived,
        items=items,
        created_at=receipt.created_at,
    )


async def assembly_row(db: AsyncSession, ctx: FfContext, req: AssemblyRequest) -> FfAssemblyRow:
    """Build one assembly row (resolving names) — used for action responses."""
    meta = await _meta_map(db, {it.nomenclature_id for it in req.items})
    return _build_assembly_row(ctx, req, meta)


async def acceptance_row(db: AsyncSession, ctx: FfContext, receipt: InboundReceipt) -> FfAcceptanceRow:
    meta = await _meta_map(db, {it.nomenclature_id for it in receipt.items})
    return _build_acceptance_row(ctx, receipt, meta, ctx.user.id)


# ─── Detail builder (карточка заявки) ────────────────────────────────────────


async def _carrier_name(db: AsyncSession, req: AssemblyRequest) -> str | None:
    """Resolve carrier display name from req.counterparty_id (scoped, single query)."""
    if not req.counterparty_id:
        return None
    name = (
        await db.execute(
            select(Counterparty.name).where(
                Counterparty.id == req.counterparty_id,
                Counterparty.project_id == req.project_id,
            )
        )
    ).scalar_one_or_none()
    return name


async def _is_via_gazelka(db: AsyncSession, req: AssemblyRequest) -> bool:
    """True if a Газелька order was sent/matched for this assembly request (scoped)."""
    found = (
        await db.execute(
            select(GazelkaOrder.id)
            .where(
                GazelkaOrder.assembly_request_id == req.id,
                GazelkaOrder.project_id == req.project_id,
                GazelkaOrder.status.in_([GazelkaOrderStatus.SENT, GazelkaOrderStatus.MATCHED]),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return found is not None


async def _status_history(db: AsyncSession, req: AssemblyRequest) -> list[FfStatusEvent]:
    """Status timeline for this request (asc by changed_at), scoped to its project."""
    rows = (
        await db.execute(
            select(AssemblyStatusHistory)
            .where(
                AssemblyStatusHistory.assembly_request_id == req.id,
                AssemblyStatusHistory.project_id == req.project_id,
            )
            .order_by(AssemblyStatusHistory.changed_at.asc(), AssemblyStatusHistory.id.asc())
            .limit(MAX_LIST_LIMIT)
        )
    ).scalars().all()
    return [FfStatusEvent(status=h.new_status, changed_at=h.changed_at, comment=h.comment) for h in rows]


async def assembly_detail(db: AsyncSession, ctx: FfContext, req: AssemblyRequest) -> FfAssemblyDetail:
    """Full operator card: base row + delivery/vehicle + Газелька flag + status timeline.

    Себестоимость и стоимость перевозки НЕ отдаём (см. schemas.ff_portal).
    """
    # Detail-only: резолвим кратность коробки per barcode для склада заявки
    # (батч, не N+1) — в списках box_qty не показываем, поэтому строим row здесь.
    from backend.services.box_multiplicity_service import resolve_box_qty_map

    # Предложенный «на согласование» состав (если оператор его отправлял).
    proposed_raw: list[dict] = list(req.ff_proposed_items or [])
    proposed_barcodes = [str(p["barcode"]) for p in proposed_raw if p.get("barcode")]

    # Бар코ды текущих + предложенных позиций (для одного батча box_qty и meta).
    current_barcodes = [it.barcode for it in req.items]
    all_barcodes = list({*current_barcodes, *proposed_barcodes})
    box_qty_map = await resolve_box_qty_map(db, req.project_id, req.warehouse_id, all_barcodes)

    # nomenclature_id текущих позиций знаем напрямую; для предложенных — резолвим
    # barcode → nomenclature_id одним запросом (для name/article через _meta_map).
    nom_id_by_bc: dict[str, int] = {}
    if proposed_barcodes:
        rows = await db.execute(
            select(Nomenclature.barcode, Nomenclature.id).where(
                Nomenclature.project_id == req.project_id,
                Nomenclature.barcode.in_(proposed_barcodes),
            )
        )
        nom_id_by_bc = {bc: nid for bc, nid in rows.all()}

    nom_ids = {it.nomenclature_id for it in req.items} | set(nom_id_by_bc.values())
    meta = await _meta_map(db, nom_ids)

    # «На складе»: WarehouseStock.quantity на складе заявки per nomenclature_id
    # (один батч-запрос), затем → {barcode: stock} для текущих ∪ предложенных.
    bc_by_nom_id: dict[int, str] = {it.nomenclature_id: it.barcode for it in req.items}
    bc_by_nom_id.update({nid: bc for bc, nid in nom_id_by_bc.items()})
    stock_map: dict[str, int] = {}
    if nom_ids:
        stock_rows = await db.execute(
            select(WarehouseStock.nomenclature_id, WarehouseStock.quantity).where(
                WarehouseStock.project_id == req.project_id,
                WarehouseStock.warehouse_id == req.warehouse_id,
                WarehouseStock.nomenclature_id.in_(nom_ids),
            )
        )
        for nid, qty in stock_rows.all():
            bc = bc_by_nom_id.get(nid)
            if bc is not None:
                stock_map[bc] = stock_map.get(bc, 0) + int(qty or 0)

    base = _build_assembly_row(ctx, req, meta, box_qty_map, stock_map)

    def _proposed_item(bc: str, qty: int) -> FfAssemblyItem:
        nid = nom_id_by_bc.get(bc)
        name, article, _brand = meta.get(nid, (None, None, None)) if nid is not None else (None, None, None)
        return FfAssemblyItem(
            barcode=bc,
            article=article,
            product_name=name or bc,
            box_qty=box_qty_map.get(bc),
            stock=stock_map.get(bc, 0),
            quantity=qty,
        )

    proposed_items = [
        _proposed_item(str(p["barcode"]), int(p.get("quantity") or 0))
        for p in proposed_raw
        if p.get("barcode")
    ]

    carrier = await _carrier_name(db, req)
    via_gazelka = await _is_via_gazelka(db, req)
    history = await _status_history(db, req)
    # Связанная WB FBO-поставка (selectinload'нута в get_scoped_assembly).
    supply = req.wb_fbo_supply
    return FfAssemblyDetail(
        **base.model_dump(),
        editable=AssemblyStatus(req.status) in _EDITABLE_STATUSES,
        vehicle_info=req.vehicle_info,
        vehicle_brand=req.vehicle_brand,
        driver_phone=req.driver_phone,
        carrier_name=carrier,
        pickup_date=req.pickup_date,
        pickup_time_slot=req.pickup_time_slot,
        delivery_date=req.delivery_date,
        vehicle_assigned_at=req.vehicle_assigned_at,
        via_gazelka=via_gazelka,
        fbo_supply_name=supply.name if supply else None,
        fbo_supply_wb_id=supply.wb_supply_id if supply else None,
        fbo_supply_status=supply.wb_status if supply else None,
        fbo_supply_planned_date=supply.planned_date if supply else None,
        ff_review_pending=req.ff_proposed_items is not None,
        proposed_items=proposed_items,
        ff_proposed_at=req.ff_proposed_at,
        history=history,
    )


# ─── Edit наполнения (состав заявки) ─────────────────────────────────────────


async def update_assembly_items(
    db: AsyncSession, ctx: FfContext, req: AssemblyRequest, items: list[FfAssemblyItemInput]
) -> FfAssemblyDetail:
    """Предложить новый состав заявки «на согласование» (НЕ применять напрямую).

    Оператор ФФ только ПРЕДЛАГАЕТ состав — канонические `req.items` не трогаем,
    предложение кладём в `ff_proposed_*`. Применит/отклонит его DDS в основном
    приложении (там же и проверка доступного остатка). Здесь — только валидация
    формы (статус до сборки, непустой список, известные штрихкоды) и сохранение.
    """
    if AssemblyStatus(req.status) not in _EDITABLE_STATUSES:
        raise HTTPException(400, "Состав можно менять только до сборки")
    if not items:
        raise HTTPException(400, "Список позиций не может быть пустым")

    # Резолвим штрихкоды одним запросом в рамках проекта заявки (no N+1) — только
    # чтобы отвергнуть неизвестные. Дефицит остатка проверяется при согласовании.
    barcodes = {it.barcode for it in items}
    rows = await db.execute(
        select(Nomenclature.barcode).where(
            Nomenclature.project_id == req.project_id,
            Nomenclature.barcode.in_(barcodes),
        )
    )
    known = {bc for (bc,) in rows.all()}
    for it in items:
        if it.barcode not in known:
            raise HTTPException(400, f"Штрихкод {it.barcode} не найден в номенклатуре")

    # Сохраняем ПРЕДЛОЖЕНИЕ (canonical состав остаётся прежним до approve в DDS).
    req.ff_proposed_items = [{"barcode": it.barcode, "quantity": it.quantity} for it in items]
    req.ff_proposed_at = utcnow()
    req.ff_proposed_by = ctx.user.username
    await db.commit()
    return await assembly_detail(db, ctx, req)


# ─── Archive toggles ─────────────────────────────────────────────────────────


async def set_assembly_archived(
    db: AsyncSession, ctx: FfContext, req: AssemblyRequest, archived: bool
) -> FfAssemblyRow:
    """Toggle operator archive flag on an assembly request (hides from active list)."""
    req.is_archived = archived
    await db.commit()
    return await assembly_row(db, ctx, req)


async def set_acceptance_archived(
    db: AsyncSession, ctx: FfContext, receipt: InboundReceipt, archived: bool
) -> FfAcceptanceRow:
    """Toggle operator archive flag on an inbound receipt (hides from active list)."""
    receipt.is_archived = archived
    await db.commit()
    return await acceptance_row(db, ctx, receipt)


async def set_assembly_pallets(
    db: AsyncSession,
    ctx: FfContext,
    req: AssemblyRequest,
    pallets_count: int,
    pallet_weight_kg: Decimal,
    estimated_ready_date: date | None = None,
) -> FfAssemblyDetail:
    """Correct pallets count + weight + плановую готовность independently of «Готово».

    Простые скалярные поля (без влияния на остаток/резерв) — ставим прямо на заявку
    и коммитим (как set_assembly_archived). Разрешено только до отгрузки. ВНИМАНИЕ:
    estimated_ready_date выставляется всегда (None = очистить) — зовётся только из
    формы «Параметры», которая всегда передаёт текущее значение.
    """
    if AssemblyStatus(req.status) not in _PRE_SHIP_STATUSES:
        raise HTTPException(400, "Паллеты можно менять только до отгрузки")
    req.pallets_count = pallets_count
    req.pallet_weight_kg = pallet_weight_kg
    req.estimated_ready_date = estimated_ready_date
    await db.commit()
    return await assembly_detail(db, ctx, req)


# ─── List queries ──────────────────────────────────────────────────────────────


def _resolve_project_slug(ctx: FfContext, project_slug: str | None) -> tuple[list[int], bool]:
    """Map an optional project_slug to the project ids to query.

    Returns (pids, ok). When no slug → all operator projects, ok=True. When slug
    matches one of the operator's projects → just that one. When slug is unknown
    to the operator → ([], False): callers must return an empty list.
    """
    if not project_slug:
        return ctx.project_ids, True
    for pid, proj in ctx.projects.items():
        if proj.slug == project_slug:
            return [pid], True
    return [], False


async def list_assemblies(
    db: AsyncSession,
    ctx: FfContext,
    *,
    status: str | None = None,
    warehouse_id: int | None = None,
    archived: bool = False,
    project_slug: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> tuple[list[FfAssemblyRow], int]:
    pids, ok = _resolve_project_slug(ctx, project_slug)
    wh_ids = [warehouse_id] if warehouse_id is not None else ctx.warehouse_ids
    if warehouse_id is not None and not ctx.allows_any_project(warehouse_id):
        raise HTTPException(404, "Склад не найден")
    if not ok or not pids or not wh_ids:
        return [], 0

    conds = [
        AssemblyRequest.project_id.in_(pids),
        AssemblyRequest.warehouse_id.in_(wh_ids),
        AssemblyRequest.is_deleted == False,  # noqa: E712
        _assembly_archive_cond(archived),
    ]
    statuses = _parse_status_filter(status)
    if statuses:
        conds.append(AssemblyRequest.status.in_(statuses))

    total = (await db.execute(select(func.count()).select_from(AssemblyRequest).where(*conds))).scalar_one()

    limit = min(max(1, limit), MAX_LIST_LIMIT)
    rows = (
        await db.execute(
            select(AssemblyRequest)
            .options(selectinload(AssemblyRequest.items), selectinload(AssemblyRequest.wb_fbo_supply))
            .where(*conds)
            .order_by(AssemblyRequest.created_at.desc(), AssemblyRequest.id.desc())
            .limit(limit)
            .offset(max(0, offset))
        )
    ).scalars().all()

    nom_ids = {it.nomenclature_id for r in rows for it in r.items}
    meta = await _meta_map(db, nom_ids)
    return [_build_assembly_row(ctx, r, meta) for r in rows], int(total)


async def list_acceptances(
    db: AsyncSession,
    ctx: FfContext,
    *,
    status: str | None = None,
    warehouse_id: int | None = None,
    archived: bool = False,
    project_slug: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> tuple[list[FfAcceptanceRow], int]:
    from backend.models.warehouse import InboundStatus

    pids, ok = _resolve_project_slug(ctx, project_slug)
    wh_ids = [warehouse_id] if warehouse_id is not None else ctx.warehouse_ids
    if warehouse_id is not None and not ctx.allows_any_project(warehouse_id):
        raise HTTPException(404, "Склад не найден")
    if not ok or not pids or not wh_ids:
        return [], 0

    conds = [
        InboundReceipt.project_id.in_(pids),
        InboundReceipt.warehouse_id.in_(wh_ids),
        InboundReceipt.is_deleted == False,  # noqa: E712
        InboundReceipt.is_archived == archived,
        # PVZ-дефект-возвраты (is_defect) обрабатываются отдельным флоу — не показываем.
        InboundReceipt.is_defect == False,  # noqa: E712
    ]
    statuses = _parse_status_filter(status) or [InboundStatus.EXPECTED.value, InboundStatus.ACCEPTED.value]
    conds.append(InboundReceipt.status.in_(statuses))

    total = (await db.execute(select(func.count()).select_from(InboundReceipt).where(*conds))).scalar_one()

    limit = min(max(1, limit), MAX_LIST_LIMIT)
    rows = (
        await db.execute(
            select(InboundReceipt)
            .options(selectinload(InboundReceipt.items))
            .where(*conds)
            .order_by(InboundReceipt.id.desc())
            .limit(limit)
            .offset(max(0, offset))
        )
    ).scalars().all()

    nom_ids = {it.nomenclature_id for r in rows for it in r.items}
    meta = await _meta_map(db, nom_ids)
    return [_build_acceptance_row(ctx, r, meta, ctx.user.id) for r in rows], int(total)


async def _reserved_map(db: AsyncSession, pids: list[int], wh_ids: list[int]) -> dict[tuple[int, int], int]:
    """(warehouse_id, nomenclature_id) -> reserved qty from active assembly requests."""
    rows = await db.execute(
        select(
            AssemblyRequest.warehouse_id,
            AssemblyRequestItem.nomenclature_id,
            func.sum(AssemblyRequestItem.quantity),
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .where(
            AssemblyRequest.project_id.in_(pids),
            AssemblyRequest.warehouse_id.in_(wh_ids),
            AssemblyRequest.is_deleted == False,  # noqa: E712
            AssemblyRequest.status.in_([s.value for s in _ACTIVE_RESERVE_STATUSES]),
        )
        .group_by(AssemblyRequest.warehouse_id, AssemblyRequestItem.nomenclature_id)
    )
    return {(wid, nid): int(q or 0) for wid, nid, q in rows.all()}


async def list_stock(
    db: AsyncSession,
    ctx: FfContext,
    *,
    barcode: str | None = None,
    warehouse_id: int | None = None,
    project_slug: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[FfStockRow], int, int]:
    pids, ok = _resolve_project_slug(ctx, project_slug)
    wh_ids = [warehouse_id] if warehouse_id is not None else ctx.warehouse_ids
    if warehouse_id is not None and not ctx.allows_any_project(warehouse_id):
        raise HTTPException(404, "Склад не найден")
    if not ok or not pids or not wh_ids:
        return [], 0, 0

    conds = [
        WarehouseStock.project_id.in_(pids),
        WarehouseStock.warehouse_id.in_(wh_ids),
    ]
    if barcode and barcode.strip():
        conds.append(WarehouseStock.barcode.ilike(f"%{_esc_like(barcode.strip())}%", escape="\\"))

    total = (await db.execute(select(func.count()).select_from(WarehouseStock).where(*conds))).scalar_one()
    total_qty = (
        await db.execute(select(func.coalesce(func.sum(WarehouseStock.quantity), 0)).where(*conds))
    ).scalar_one()

    limit = min(max(1, limit), MAX_LIST_LIMIT)
    rows = (
        await db.execute(
            select(WarehouseStock)
            .where(*conds)
            .order_by(WarehouseStock.quantity.desc(), WarehouseStock.id.desc())
            .limit(limit)
            .offset(max(0, offset))
        )
    ).scalars().all()

    reserved = await _reserved_map(db, pids, wh_ids)
    meta = await _meta_map(db, {s.nomenclature_id for s in rows})

    out: list[FfStockRow] = []
    for s in rows:
        proj = ctx.projects.get(s.project_id)
        wh = ctx.warehouse_by_id.get(s.warehouse_id)
        res = reserved.get((s.warehouse_id, s.nomenclature_id), 0)
        name, article, _brand = meta.get(s.nomenclature_id, (None, None, None))
        out.append(
            FfStockRow(
                project_slug=proj.slug if proj else "",
                project_name=proj.name if proj else "",
                warehouse_id=s.warehouse_id,
                warehouse_name=wh.name if wh else "",
                barcode=s.barcode,
                article=article,
                product_name=name or s.barcode,
                quantity=s.quantity,
                defect_quantity=s.defect_quantity,
                reserved=res,
                available=max(0, s.quantity - res),
            )
        )
    return out, int(total), int(total_qty)

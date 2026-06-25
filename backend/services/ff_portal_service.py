# ruff: noqa: RUF002, RUF003
"""
FF-портал — сервис чтения/скоупа для внешнего оператора (Хамза).

Кросс-проектные выборки по его складам: заявки на сборку, приёмки (вкл. возвраты WB),
остатки. Только «безопасные» поля (без себестоимости/перевозчика/аналитики). Мутации
(start/ready/ship/accept) делаются существующими сервисами — здесь только скоуп-гард.
"""

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.ff_context import FfContext
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.cost import Nomenclature
from backend.models.warehouse import InboundReceipt, WarehouseStock
from backend.schemas.ff_portal import (
    FfAcceptanceItem,
    FfAcceptanceRow,
    FfAssemblyItem,
    FfAssemblyRow,
    FfStockRow,
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


async def _name_map(db: AsyncSession, nom_ids: set[int]) -> dict[int, str | None]:
    """nomenclature_id -> display name (subject → article_seller). Batch, no N+1."""
    if not nom_ids:
        return {}
    rows = await db.execute(
        select(Nomenclature.id, Nomenclature.subject, Nomenclature.article_seller).where(Nomenclature.id.in_(nom_ids))
    )
    return {nid: (subject or article) for nid, subject, article in rows.all()}


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


def _build_assembly_row(ctx: FfContext, req: AssemblyRequest, name_map: dict[int, str | None]) -> FfAssemblyRow:
    proj = ctx.projects.get(req.project_id)
    wh = ctx.warehouse_by_id.get(req.warehouse_id)
    wb_name = None
    if req.wb_fbo_supply is not None:
        wb_name = req.wb_fbo_supply.warehouse_name
    wb_name = wb_name or req.wb_warehouse_name_manual
    items = [
        FfAssemblyItem(
            barcode=it.barcode,
            product_name=name_map.get(it.nomenclature_id) or it.barcode,
            quantity=it.quantity,
        )
        for it in req.items
    ]
    return FfAssemblyRow(
        id=req.id,
        project_slug=proj.slug if proj else "",
        project_name=proj.name if proj else "",
        warehouse_id=req.warehouse_id,
        warehouse_name=wh.name if wh else "",
        number=req.number,
        status=req.status,
        wb_warehouse_name=wb_name,
        package_type=req.package_type,
        pallets_count=req.pallets_count,
        pallet_weight_kg=req.pallet_weight_kg,
        estimated_ready_date=req.estimated_ready_date,
        actual_ready_date=req.actual_ready_date,
        vehicle_assigned=req.vehicle_assigned_at is not None,
        items=items,
        created_at=req.created_at,
    )


def _build_acceptance_row(
    ctx: FfContext, receipt: InboundReceipt, name_map: dict[int, str | None], user_id: int
) -> FfAcceptanceRow:
    proj = ctx.projects.get(receipt.project_id)
    wh = ctx.warehouse_by_id.get(receipt.warehouse_id)
    items = [
        FfAcceptanceItem(
            item_id=it.id,
            barcode=it.barcode,
            product_name=name_map.get(it.nomenclature_id) or it.barcode,
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
        items=items,
        created_at=receipt.created_at,
    )


async def assembly_row(db: AsyncSession, ctx: FfContext, req: AssemblyRequest) -> FfAssemblyRow:
    """Build one assembly row (resolving names) — used for action responses."""
    names = await _name_map(db, {it.nomenclature_id for it in req.items})
    return _build_assembly_row(ctx, req, names)


async def acceptance_row(db: AsyncSession, ctx: FfContext, receipt: InboundReceipt) -> FfAcceptanceRow:
    names = await _name_map(db, {it.nomenclature_id for it in receipt.items})
    return _build_acceptance_row(ctx, receipt, names, ctx.user.id)


# ─── List queries ──────────────────────────────────────────────────────────────


async def list_assemblies(
    db: AsyncSession,
    ctx: FfContext,
    *,
    status: str | None = None,
    warehouse_id: int | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> tuple[list[FfAssemblyRow], int]:
    pids = ctx.project_ids
    wh_ids = [warehouse_id] if warehouse_id is not None else ctx.warehouse_ids
    if warehouse_id is not None and not ctx.allows_any_project(warehouse_id):
        raise HTTPException(404, "Склад не найден")
    if not pids or not wh_ids:
        return [], 0

    conds = [
        AssemblyRequest.project_id.in_(pids),
        AssemblyRequest.warehouse_id.in_(wh_ids),
        AssemblyRequest.is_deleted == False,  # noqa: E712
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
    names = await _name_map(db, nom_ids)
    return [_build_assembly_row(ctx, r, names) for r in rows], int(total)


async def list_acceptances(
    db: AsyncSession,
    ctx: FfContext,
    *,
    status: str | None = None,
    warehouse_id: int | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> tuple[list[FfAcceptanceRow], int]:
    from backend.models.warehouse import InboundStatus

    pids = ctx.project_ids
    wh_ids = [warehouse_id] if warehouse_id is not None else ctx.warehouse_ids
    if warehouse_id is not None and not ctx.allows_any_project(warehouse_id):
        raise HTTPException(404, "Склад не найден")
    if not pids or not wh_ids:
        return [], 0

    conds = [
        InboundReceipt.project_id.in_(pids),
        InboundReceipt.warehouse_id.in_(wh_ids),
        InboundReceipt.is_deleted == False,  # noqa: E712
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
    names = await _name_map(db, nom_ids)
    return [_build_acceptance_row(ctx, r, names, ctx.user.id) for r in rows], int(total)


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
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[FfStockRow], int, int]:
    pids = ctx.project_ids
    wh_ids = [warehouse_id] if warehouse_id is not None else ctx.warehouse_ids
    if warehouse_id is not None and not ctx.allows_any_project(warehouse_id):
        raise HTTPException(404, "Склад не найден")
    if not pids or not wh_ids:
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
    names = await _name_map(db, {s.nomenclature_id for s in rows})

    out: list[FfStockRow] = []
    for s in rows:
        proj = ctx.projects.get(s.project_id)
        wh = ctx.warehouse_by_id.get(s.warehouse_id)
        res = reserved.get((s.warehouse_id, s.nomenclature_id), 0)
        out.append(
            FfStockRow(
                project_slug=proj.slug if proj else "",
                project_name=proj.name if proj else "",
                warehouse_id=s.warehouse_id,
                warehouse_name=wh.name if wh else "",
                barcode=s.barcode,
                product_name=names.get(s.nomenclature_id) or s.barcode,
                quantity=s.quantity,
                defect_quantity=s.defect_quantity,
                reserved=res,
                available=max(0, s.quantity - res),
            )
        )
    return out, int(total), int(total_qty)

# ruff: noqa: RUF002, RUF003
"""
FF billing — ожидаемая стоимость услуг ФФ по заявке сборки.

Компоненты на дату shipped_at (не отгружена → сегодня), тарифы через
resolve_rates: PALLETIZING × pallets_count, BOX_PROCESSING × Σкоробов,
LOADING × (паллеты|короба по unit тарифа), CUSTOM = ff_custom_cost.
Паллеты заявки — ручное поле pallets_count (authoritative), НЕ вычисляются.
"""

import logging
from decimal import Decimal
from math import ceil

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem
from backend.models.ff_billing import FfServiceType, FfTariffUnit
from backend.schemas.ff_billing import (
    FfAssemblyExpectedCost,
    FfCustomCostPayload,
    FfExpectedComponent,
    FfServiceTypeLiteral,
)
from backend.services.box_multiplicity_service import resolve_box_qty_map
from backend.services.ff_billing.tariffs import resolve_rates
from backend.utils.time import utcnow

logger = logging.getLogger("dds.ff_billing")

_TWO_PLACES = Decimal("0.01")


async def _get_request(db: AsyncSession, project_id: int, request_id: int) -> AssemblyRequest:
    res = await db.execute(
        select(AssemblyRequest).where(
            AssemblyRequest.id == request_id,
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted == False,  # noqa: E712
        )
    )
    req = res.scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Заявка сборки не найдена")
    return req


async def compute_assembly_expected_cost(
    db: AsyncSession, project_id: int, request_id: int
) -> FfAssemblyExpectedCost:
    req = await _get_request(db, project_id, request_id)
    on_date = req.shipped_at.date() if req.shipped_at else utcnow().date()

    item_rows = (
        await db.execute(
            select(AssemblyRequestItem.barcode, func.sum(AssemblyRequestItem.quantity))
            .where(
                AssemblyRequestItem.project_id == project_id,
                AssemblyRequestItem.assembly_request_id == req.id,
            )
            .group_by(AssemblyRequestItem.barcode)
        )
    ).all()
    qty_by_bc = {bc: int(q or 0) for bc, q in item_rows if bc and int(q or 0) > 0}
    units = sum(qty_by_bc.values())

    box_qty_map = (
        await resolve_box_qty_map(db, project_id, req.warehouse_id, sorted(qty_by_bc))
        if qty_by_bc
        else {}
    )
    # box_qty None → короба=0 (штуки в units всё равно учтены).
    boxes = sum(
        ceil(qty / bq)
        for bc, qty in qty_by_bc.items()
        if (bq := box_qty_map.get(bc)) and bq > 0
    )
    pallets = int(req.pallets_count or 0)

    rates = await resolve_rates(db, project_id, req.warehouse_id, on_date)
    components: list[FfExpectedComponent] = []
    missing: list[str] = []

    def _component(service: FfServiceTypeLiteral, qty: int, unit: str | None) -> FfExpectedComponent:
        entry = rates.get(service)
        if entry is None:
            missing.append(service)
            return FfExpectedComponent(service_type=service, unit=unit, qty=qty, rate=None, cost=None)
        rate = entry[0]
        return FfExpectedComponent(
            service_type=service, unit=unit, qty=qty, rate=rate,
            cost=(rate * qty).quantize(_TWO_PLACES),
        )

    components.append(_component("PALLETIZING", pallets, "PALLET"))
    components.append(_component("BOX_PROCESSING", boxes, "BOX"))

    loading = rates.get(FfServiceType.LOADING.value)
    if loading is None:
        missing.append(FfServiceType.LOADING.value)
        components.append(
            FfExpectedComponent(service_type="LOADING", unit=None, qty=None, rate=None, cost=None)
        )
    else:
        rate, unit = loading
        qty = pallets if unit == FfTariffUnit.PALLET.value else boxes
        components.append(
            FfExpectedComponent(
                service_type="LOADING", unit=unit, qty=qty, rate=rate,
                cost=(rate * qty).quantize(_TWO_PLACES),
            )
        )

    if req.ff_custom_cost is not None:
        components.append(
            FfExpectedComponent(service_type="CUSTOM", unit=None, qty=None, rate=None, cost=req.ff_custom_cost)
        )

    costs = [c.cost for c in components if c.cost is not None]
    total = sum(costs, Decimal("0")).quantize(_TWO_PLACES) if costs else None

    return FfAssemblyExpectedCost(
        assembly_request_id=req.id,
        warehouse_id=req.warehouse_id,
        pallets=pallets,
        boxes=boxes,
        units=units,
        components=components,
        custom_cost=req.ff_custom_cost,
        custom_cost_comment=req.ff_custom_cost_comment,
        total=total,
        missing_tariffs=missing,
    )


async def set_assembly_custom_cost(
    db: AsyncSession, project_id: int, request_id: int, payload: FfCustomCostPayload
) -> FfAssemblyExpectedCost:
    """Ручная сумма доп-услуг ФФ по сборке. amount=null → очистить (вместе с комментарием)."""
    req = await _get_request(db, project_id, request_id)
    if payload.amount is None:
        req.ff_custom_cost = None
        req.ff_custom_cost_comment = None
    else:
        req.ff_custom_cost = payload.amount
        req.ff_custom_cost_comment = payload.comment
    await db.commit()
    await invalidate_cache("ff_billing:invoices")
    return await compute_assembly_expected_cost(db, project_id, request_id)

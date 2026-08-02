# ruff: noqa: RUF002, RUF003
"""
FF billing — ожидаемая стоимость услуг ФФ по заявке сборки и по переезду.

Заявка: компоненты на дату shipped_at (не отгружена → сегодня), тарифы через
resolve_rates: PALLETIZING × pallets_count, BOX_PROCESSING × Σкоробов,
LOADING × (паллеты|короба по unit тарифа), CUSTOM = ff_custom_cost.
Паллеты заявки — ручное поле pallets_count (authoritative), НЕ вычисляются.

Переезд: одна тарифицируемая услуга TRANSFER_ASSEMBLY у склада-ИСТОЧНИКА
(работу по сборке выполняет он) × pallets_count в единице переезда, плюс
CUSTOM. Обе стороны считают ЧЕСТНО: услуга без ставки не входит в total и
уезжает в missing_tariffs, а не в «ноль рублей».
"""

import logging
from datetime import date
from decimal import Decimal
from math import ceil

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem
from backend.models.ff_billing import FfServiceType, FfTariffUnit
from backend.models.warehouse import StockTransfer, Warehouse
from backend.schemas.ff_billing import (
    FfAssemblyExpectedCost,
    FfCustomCostPayload,
    FfExpectedComponent,
    FfServiceTypeLiteral,
    FfTariffUnitLiteral,
    FfTransferExpectedCost,
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


# ─── Переезд между складами ─────────────────────────────────────────────────


async def _get_transfer(db: AsyncSession, project_id: int, transfer_id: int) -> StockTransfer:
    res = await db.execute(
        select(StockTransfer).where(
            StockTransfer.id == transfer_id,
            StockTransfer.project_id == project_id,
            StockTransfer.is_deleted == False,  # noqa: E712
        )
    )
    tr = res.scalar_one_or_none()
    if tr is None:
        raise HTTPException(status_code=404, detail="Перемещение не найдено")
    return tr


def _transfer_rate_date(tr: StockTransfer) -> date:
    """Дата, на которую действует ставка: когда ФФ сделал работу.

    Приоритет — как у заявки, но с поправкой на то, что у переезда есть веха
    готовности: собрал (actual_ready_date) → уехал (shipped_at) → заведён
    (created_at). Черновик без вех считается по сегодняшней ставке.
    """
    if tr.actual_ready_date:
        return tr.actual_ready_date
    if tr.shipped_at:
        return tr.shipped_at.date()
    if tr.created_at:
        return tr.created_at.date()
    return utcnow().date()


async def compute_transfer_expected_cost(
    db: AsyncSession, project_id: int, transfer_id: int
) -> FfTransferExpectedCost:
    """Ожидаемая стоимость услуг ФФ по переезду (тариф склада-ИСТОЧНИКА).

    🔴 Единицу не конвертируем: если склад биллит паллеты, а переезд коробочный
    (или наоборот), компонент отдаётся с `unit_mismatch=True` и без суммы —
    выдуманный коэффициент «коробов в паллете» в деньгах хуже пустой строки.
    """
    tr = await _get_transfer(db, project_id, transfer_id)
    on_date = _transfer_rate_date(tr)
    # Литералы (а не .value енумов) — того же значения, но с типом схемы.
    unit: FfTariffUnitLiteral = "BOX" if tr.shipped_as_boxes else "PALLET"
    qty = int(tr.pallets_count or 0)

    from_name = (
        await db.execute(
            select(Warehouse.name).where(
                Warehouse.id == tr.from_warehouse_id,
                Warehouse.project_id == project_id,
                Warehouse.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()

    rates = await resolve_rates(db, project_id, tr.from_warehouse_id, on_date)
    service: FfServiceTypeLiteral = "TRANSFER_ASSEMBLY"
    entry = rates.get(FfServiceType.TRANSFER_ASSEMBLY.value)

    components: list[FfExpectedComponent] = []
    missing: list[str] = []
    unit_mismatch = False

    if entry is None:
        missing.append(service)
        components.append(
            FfExpectedComponent(service_type=service, unit=unit, qty=qty, rate=None, cost=None)
        )
    else:
        rate, tariff_unit = entry
        if tariff_unit != unit:
            # Ставка есть, но в другой единице (в т.ч. NULL у исторической
            # записи) — показываем её человеку, в деньги не переводим.
            unit_mismatch = True
            components.append(
                FfExpectedComponent(
                    service_type=service, unit=tariff_unit, qty=qty, rate=rate,
                    cost=None, unit_mismatch=True,
                )
            )
        else:
            components.append(
                FfExpectedComponent(
                    service_type=service, unit=unit, qty=qty, rate=rate,
                    cost=(rate * qty).quantize(_TWO_PLACES),
                )
            )

    if tr.ff_custom_cost is not None:
        components.append(
            FfExpectedComponent(
                service_type="CUSTOM", unit=None, qty=None, rate=None, cost=tr.ff_custom_cost
            )
        )

    costs = [c.cost for c in components if c.cost is not None]
    total = sum(costs, Decimal("0")).quantize(_TWO_PLACES) if costs else None

    return FfTransferExpectedCost(
        transfer_id=tr.id,
        transfer_number=tr.number,
        from_warehouse_id=tr.from_warehouse_id,
        from_warehouse_name=from_name,
        to_warehouse_id=tr.to_warehouse_id,
        unit=unit,
        qty=qty,
        on_date=on_date,
        components=components,
        custom_cost=tr.ff_custom_cost,
        custom_cost_comment=tr.ff_custom_cost_comment,
        total=total,
        missing_tariffs=missing,
        unit_mismatch=unit_mismatch,
    )


async def set_transfer_custom_cost(
    db: AsyncSession, project_id: int, transfer_id: int, payload: FfCustomCostPayload
) -> FfTransferExpectedCost:
    """Ручная сумма доп-услуг ФФ по переезду. amount=null → очистить (с комментарием)."""
    tr = await _get_transfer(db, project_id, transfer_id)
    if payload.amount is None:
        tr.ff_custom_cost = None
        tr.ff_custom_cost_comment = None
    else:
        tr.ff_custom_cost = payload.amount
        tr.ff_custom_cost_comment = payload.comment
    await db.commit()
    await invalidate_cache("ff_billing:invoices")
    return await compute_transfer_expected_cost(db, project_id, transfer_id)

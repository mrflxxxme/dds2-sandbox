# ruff: noqa: RUF002, RUF003
"""
FF billing — посуточное начисление хранения: снапшот (штуки→короба→паллеты)
и пересчёт стоимости по актуальным тарифам.

Штуки: склад «ФФ-зеркальный» (есть ≥1 строка FulfillmentStock) → ВЕСЬ физический
остаток зеркала (qty_good+qty_reserve+qty_defect, короб-строки нормализуются к
россыпи ×units_per_box); иначе → WarehouseStock (＋брак) — грабля «апл/хамза»:
ФФ-склады без API-зеркала нельзя терять.
"""

import logging
from collections import Counter
from datetime import date
from decimal import Decimal
from math import ceil

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached, invalidate_cache
from backend.models.cost import BoxQtyPerWarehouse
from backend.models.ff_billing import FfServiceType, FfStorageDaily, WarehouseTariff
from backend.models.fulfillment import FulfillmentStock
from backend.models.warehouse import WarehouseStock
from backend.schemas.ff_billing import FfStorageDailyRow
from backend.services.box_multiplicity_service import (
    _resolve_machine_box_qty,
    resolve_box_qty_map,
)
from backend.services.ff_billing.tariffs import _get_warehouse, resolve_rates
from backend.services.settings_service import _canon_box_size, get_pallet_boxes_by_size
from backend.utils.time import utcnow

logger = logging.getLogger("dds.ff_billing")

_STOCK_LIMIT = 20000
_DAYS_LIMIT = 1000


async def _units_by_barcode(db: AsyncSession, project_id: int, warehouse_id: int) -> dict[str, int]:
    """Физические штуки per barcode. ФФ-зеркало приоритетно; иначе WarehouseStock."""
    ff_rows = (
        await db.execute(
            select(
                FulfillmentStock.barcode,
                FulfillmentStock.base_barcode,
                FulfillmentStock.units_per_box,
                FulfillmentStock.qty_good,
                FulfillmentStock.qty_reserve,
                FulfillmentStock.qty_defect,
            )
            .where(
                FulfillmentStock.project_id == project_id,
                FulfillmentStock.warehouse_id == warehouse_id,
            )
            # Детерминированное усечение при переполнении лимита (биллинг!).
            .order_by(FulfillmentStock.barcode)
            .limit(_STOCK_LIMIT)
        )
    ).all()
    out: dict[str, int] = {}
    if ff_rows:
        # Зеркальный склад (даже если все qty нулевые — зеркало есть, его и берём).
        for bc, base_bc, upb, good, reserve, defect in ff_rows:
            units = (int(good or 0) + int(reserve or 0) + int(defect or 0)) * int(upb or 1)
            if units <= 0:
                continue
            key = base_bc or bc  # короб → россыпь
            out[key] = out.get(key, 0) + units
        return out

    ws_rows = (
        await db.execute(
            select(WarehouseStock.barcode, WarehouseStock.quantity, WarehouseStock.defect_quantity)
            .where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id == warehouse_id,
            )
            .order_by(WarehouseStock.barcode)
            .limit(_STOCK_LIMIT)
        )
    ).all()
    for bc, qty, defect in ws_rows:
        units = int(qty or 0) + int(defect or 0)
        if units > 0:
            out[bc] = out.get(bc, 0) + units
    return out


async def _box_size_by_barcode(
    db: AsyncSession, project_id: int, warehouse_id: int, barcodes: list[str]
) -> dict[str, str | None]:
    """Размер коробки per barcode: машинный резолв → BoxQtyPerWarehouse.box_size."""
    if not barcodes:
        return {}
    machine = await _resolve_machine_box_qty(db, project_id, sorted(barcodes))
    manual_rows = (
        await db.execute(
            select(BoxQtyPerWarehouse.barcode, BoxQtyPerWarehouse.box_size).where(
                BoxQtyPerWarehouse.project_id == project_id,
                BoxQtyPerWarehouse.warehouse_id == warehouse_id,
                BoxQtyPerWarehouse.barcode.in_(barcodes),
            )
        )
    ).all()
    manual = {bc: size for bc, size in manual_rows}
    out: dict[str, str | None] = {}
    for bc in barcodes:
        m = machine.get((bc, warehouse_id))
        out[bc] = (m.get("box_size") if m else None) or manual.get(bc)
    return out


def _compute_pallets(
    boxes_by_bc: dict[str, int],
    size_by_bc: dict[str, str | None],
    pallet_map: dict[str, int],
) -> int:
    """Паллеты = Σ по размерам ceil(коробов/ёмкость). Короба неизвестного размера —
    в группу самого частого (по коробам) известного размера; если известных нет —
    самая частая ёмкость маппинга. Маппинг пуст → 0 («не можем посчитать»)."""
    total_boxes = sum(boxes_by_bc.values())
    if not pallet_map or total_boxes <= 0:
        return 0

    by_size: dict[str | None, int] = {}
    for bc, boxes in boxes_by_bc.items():
        if boxes <= 0:
            continue
        raw = size_by_bc.get(bc)
        canon = _canon_box_size(raw) if raw else None
        key = canon if (canon and canon in pallet_map) else None
        by_size[key] = by_size.get(key, 0) + boxes

    unknown = by_size.pop(None, 0)
    if unknown:
        if by_size:
            host = max(by_size.items(), key=lambda kv: (kv[1], kv[0]))[0]
            by_size[host] = by_size[host] + unknown
        else:
            cnt = Counter(pallet_map.values())
            cap = max(cnt.items(), key=lambda kv: (kv[1], -kv[0]))[0]
            return ceil(unknown / cap)
    return sum(ceil(boxes / pallet_map[size]) for size, boxes in by_size.items() if size)


def _storage_cost(rate: Decimal | None, units: int, pallets: int) -> Decimal | None:
    """NULL = честно «не можем посчитать»: нет тарифа ИЛИ есть товар, но паллеты
    не вычислились (пустой маппинг / ни одной кратности)."""
    if rate is None or (units > 0 and pallets == 0):
        return None
    return (rate * pallets).quantize(Decimal("0.01"))


async def compute_storage_snapshot(
    db: AsyncSession, project_id: int, warehouse_id: int, snapshot_date: date
) -> None:
    """Снапшот хранения за день: UPSERT по (project, warehouse, date) — повторный
    запуск в тот же день перезаписывает. Commit — на вызывающем."""
    units_map = await _units_by_barcode(db, project_id, warehouse_id)
    barcodes = sorted(units_map)

    box_qty_map = (
        await resolve_box_qty_map(db, project_id, warehouse_id, barcodes) if barcodes else {}
    )
    boxes_by_bc: dict[str, int] = {}
    for bc in barcodes:
        bq = box_qty_map.get(bc)
        boxes_by_bc[bc] = ceil(units_map[bc] / bq) if bq and bq > 0 else 0

    size_by_bc = await _box_size_by_barcode(db, project_id, warehouse_id, barcodes)
    pallet_map = await get_pallet_boxes_by_size(db, project_id)
    pallets = _compute_pallets(boxes_by_bc, size_by_bc, pallet_map)

    units = sum(units_map.values())
    boxes = sum(boxes_by_bc.values())

    rates = await resolve_rates(db, project_id, warehouse_id, snapshot_date)
    storage = rates.get(FfServiceType.STORAGE.value)
    rate = storage[0] if storage else None
    cost = _storage_cost(rate, units, pallets)

    detail = {bc: {"units": units_map[bc], "boxes": boxes_by_bc.get(bc, 0)} for bc in barcodes}

    stmt = (
        pg_insert(FfStorageDaily)
        .values(
            project_id=project_id,
            warehouse_id=warehouse_id,
            snapshot_date=snapshot_date,
            units=units,
            boxes=boxes,
            pallets=pallets,
            storage_rate=rate,
            storage_cost=cost,
            detail=detail,
            created_at=utcnow(),
        )
        .on_conflict_do_update(
            constraint="uq_ff_storage_daily_day",
            set_={
                "units": units,
                "boxes": boxes,
                "pallets": pallets,
                "storage_rate": rate,
                "storage_cost": cost,
                "detail": detail,
                "created_at": utcnow(),
            },
        )
    )
    await db.execute(stmt)


@cached(prefix="ff_billing:storage", ttl=300)
async def list_storage_daily(
    db: AsyncSession, project_id: int, warehouse_id: int, date_from: date, date_to: date
) -> list[dict]:
    """JSON-сериализуемые строки (кэшируются) — роутер валидирует в FfStorageDailyRow."""
    await _get_warehouse(db, project_id, warehouse_id)
    rows = await db.execute(
        select(FfStorageDaily)
        .where(
            FfStorageDaily.project_id == project_id,
            FfStorageDaily.warehouse_id == warehouse_id,
            FfStorageDaily.snapshot_date >= date_from,
            FfStorageDaily.snapshot_date <= date_to,
        )
        .order_by(FfStorageDaily.snapshot_date.asc())
        .limit(_DAYS_LIMIT)
    )
    return [FfStorageDailyRow.model_validate(r).model_dump(mode="json") for r in rows.scalars().all()]


async def recompute_storage_costs(
    db: AsyncSession, project_id: int, warehouse_id: int, date_from: date, date_to: date
) -> int:
    """Пересчитать ТОЛЬКО storage_rate/storage_cost за диапазон по актуальным
    тарифам (после правки задним числом). units/boxes/pallets заморожены."""
    if date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from > date_to")
    await _get_warehouse(db, project_id, warehouse_id)

    tariffs = (
        await db.execute(
            select(WarehouseTariff)
            .where(
                WarehouseTariff.project_id == project_id,
                WarehouseTariff.warehouse_id == warehouse_id,
                WarehouseTariff.service_type == FfServiceType.STORAGE.value,
                WarehouseTariff.is_deleted == False,  # noqa: E712
            )
            .order_by(WarehouseTariff.valid_from.asc(), WarehouseTariff.id.asc())
            .limit(500)
        )
    ).scalars().all()

    def _rate_on(d: date) -> Decimal | None:
        rate: Decimal | None = None
        for t in tariffs:
            if t.valid_from <= d and (t.valid_to is None or t.valid_to >= d):
                rate = t.rate  # asc-порядок → max(valid_from) побеждает
        return rate

    rows = (
        await db.execute(
            select(FfStorageDaily)
            .where(
                FfStorageDaily.project_id == project_id,
                FfStorageDaily.warehouse_id == warehouse_id,
                FfStorageDaily.snapshot_date >= date_from,
                FfStorageDaily.snapshot_date <= date_to,
            )
            .order_by(FfStorageDaily.snapshot_date.asc())
            .limit(_DAYS_LIMIT)
        )
    ).scalars().all()

    for row in rows:
        rate = _rate_on(row.snapshot_date)
        row.storage_rate = rate
        row.storage_cost = _storage_cost(rate, row.units, row.pallets)

    await db.commit()
    await invalidate_cache("ff_billing:storage")
    return len(rows)

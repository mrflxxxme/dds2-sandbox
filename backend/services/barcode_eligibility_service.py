# ruff: noqa: RUF002, RUF003
"""Barcode eligibility service — per-barcode WB targets + free FF stock.

Compose-only: for a flat list of barcodes возвращает на каждый баркод
  1. eligible WB-склады (по acceptance options/лимитам приёмки), и
  2. свободный ФФ-остаток по складам-источникам (тип FULFILLMENT).

Никакого нового WB-plumbing: eligibility берётся через
`check_acceptance_and_redistribute` с пустым distribution на каждый баркод
(он же кэширует WB-вызов 5 мин и агрегирует coefficients в *_meta).
Свободный ФФ-остаток считается из `WarehouseStock` (авторитетный документный
ledger; `FulfillmentStock` пуст для складов без API-интеграции — см. learnings)
минус резерв активных сборок.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.schemas.warehouse import (
    BarcodeEligibilityItem,
    BarcodeEligibilityResponse,
    BarcodeEligibilityTarget,
    BarcodeFfStock,
)
from backend.services.warehouse_acceptance_service import (
    check_acceptance_and_redistribute,
)
from backend.utils.time import utcnow

logger = logging.getLogger("dds.barcode_eligibility")


def _normalize_barcodes(barcodes: list[str]) -> list[str]:
    """Trim + dedupe + drop empties, сохраняя порядок первого появления."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in barcodes:
        bc = (raw or "").strip()
        if not bc or bc in seen:
            continue
        seen.add(bc)
        out.append(bc)
    return out


def _flags_to_target(wb_name: str, flags: dict) -> BarcodeEligibilityTarget | None:
    """Map one availability entry (AcceptanceFlags-shaped) → target DTO.

    Eligible = любой can_X True. `no_limit` = eligible, но free_days_14 == 0 И
    paid_days_14 == 0 (публичный лимит закрыт → только предзаявка ⌛).
    free/paid_days_14 — лучшие (максимум) среди трёх типов упаковки.
    """
    can_box = bool(flags.get("can_box"))
    can_mono = bool(flags.get("can_monopallet"))
    can_super = bool(flags.get("can_supersafe"))
    if not (can_box or can_mono or can_super):
        return None

    free = 0
    paid = 0
    for meta_key in ("box_meta", "mono_meta", "super_meta"):
        meta = flags.get(meta_key)
        if not meta:
            continue
        free = max(free, int(meta.get("free_days_14", 0) or 0))
        paid = max(paid, int(meta.get("paid_days_14", 0) or 0))

    return BarcodeEligibilityTarget(
        wb_name=wb_name,
        can_box=can_box,
        can_monopallet=can_mono,
        can_supersafe=can_super,
        free_days_14=free,
        paid_days_14=paid,
        no_limit=(free == 0 and paid == 0),
    )


async def _resolve_barcodes(
    db: AsyncSession,
    project_id: int,
    barcodes: list[str],
) -> tuple[dict[str, dict], list[str]]:
    """Barcode → {nm_id, vendor_code, nomenclature_id} (batch). NON-raising.

    Баркоды без Nomenclature в проекте собираются в `unknown` (а не 400).
    """
    from backend.models.cost import Nomenclature

    result = await db.execute(
        select(
            Nomenclature.barcode,
            Nomenclature.id,
            Nomenclature.article_wb,
            Nomenclature.article_seller,
        ).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode.in_(barcodes),
        )
    )
    resolved: dict[str, dict] = {}
    for row in result:
        resolved[row.barcode] = {
            "nomenclature_id": row.id,
            "nm_id": int(row.article_wb) if row.article_wb is not None else 0,
            "vendor_code": row.article_seller or f"#{row.article_wb or row.id}",
        }
    unknown = [bc for bc in barcodes if bc not in resolved]
    return resolved, unknown


async def _free_ff_stock(
    db: AsyncSession,
    project_id: int,
    nomenclature_ids: list[int],
) -> dict[int, list[BarcodeFfStock]]:
    """Свободный ФФ-остаток per nomenclature_id per FF-склад.

    available = quantity − in_assembly_reserved. Источник остатка —
    `WarehouseStock` (документный ledger), а резерв — активные сборки
    (PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED) с этого ФФ-склада. Возвращаются
    только склады с available > 0. Зеркалит lean-логику warehouse_need_service.
    """
    from backend.models.assembly import (
        AssemblyRequest,
        AssemblyRequestItem,
        AssemblyStatus,
    )
    from backend.models.warehouse import Warehouse, WarehouseStock, WarehouseType

    if not nomenclature_ids:
        return {}

    # FF-склады проекта (активные, не удалённые).
    ff_result = await db.execute(
        select(Warehouse.id, Warehouse.name)
        .where(
            Warehouse.project_id == project_id,
            Warehouse.warehouse_type == WarehouseType.FULFILLMENT,
            Warehouse.is_deleted.is_(False),
            Warehouse.is_active.is_(True),
        )
        .order_by(Warehouse.sort_order)
    )
    ff_rows = ff_result.all()
    ff_ids = [r.id for r in ff_rows]
    ff_name_by_id = {r.id: r.name for r in ff_rows}
    if not ff_ids:
        return {}

    # Остаток per (nomenclature_id, warehouse_id) на FF-складах.
    stock_result = await db.execute(
        select(
            WarehouseStock.nomenclature_id,
            WarehouseStock.warehouse_id,
            func.sum(WarehouseStock.quantity).label("qty"),
        )
        .where(
            WarehouseStock.project_id == project_id,
            WarehouseStock.warehouse_id.in_(ff_ids),
            WarehouseStock.nomenclature_id.in_(nomenclature_ids),
        )
        .group_by(WarehouseStock.nomenclature_id, WarehouseStock.warehouse_id)
    )
    # {nomenclature_id: {warehouse_id: qty}}
    stock_map: dict[int, dict[int, int]] = {}
    for row in stock_result:
        stock_map.setdefault(row.nomenclature_id, {})[row.warehouse_id] = int(row.qty or 0)

    # Резерв активных сборок per (nomenclature_id, warehouse_id).
    active_statuses = [
        AssemblyStatus.PENDING,
        AssemblyStatus.IN_PROGRESS,
        AssemblyStatus.READY,
        AssemblyStatus.VEHICLE_ASSIGNED,
    ]
    asm_result = await db.execute(
        select(
            AssemblyRequestItem.nomenclature_id,
            AssemblyRequest.warehouse_id,
            func.sum(AssemblyRequestItem.quantity).label("qty"),
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status.in_(active_statuses),
            AssemblyRequest.warehouse_id.in_(ff_ids),
            AssemblyRequestItem.nomenclature_id.in_(nomenclature_ids),
        )
        .group_by(AssemblyRequestItem.nomenclature_id, AssemblyRequest.warehouse_id)
    )
    reserved_map: dict[int, dict[int, int]] = {}
    for row in asm_result:
        reserved_map.setdefault(row.nomenclature_id, {})[row.warehouse_id] = int(row.qty or 0)

    out: dict[int, list[BarcodeFfStock]] = {}
    for nom_id in nomenclature_ids:
        per_wh = stock_map.get(nom_id, {})
        rows: list[BarcodeFfStock] = []
        for wh_id in ff_ids:
            qty = per_wh.get(wh_id, 0)
            reserved = reserved_map.get(nom_id, {}).get(wh_id, 0)
            available = max(0, qty - reserved)
            if available > 0:
                rows.append(
                    BarcodeFfStock(
                        ff_id=wh_id,
                        ff_name=ff_name_by_id.get(wh_id, f"#{wh_id}"),
                        available=available,
                    )
                )
        if rows:
            out[nom_id] = rows
    return out


async def get_barcode_eligibility(
    db: AsyncSession,
    project_id: int,
    barcodes: list[str],
) -> BarcodeEligibilityResponse:
    """Per-barcode WB targets (acceptance) + свободный ФФ-остаток.

    Args:
        barcodes: плоский список баркодов (нормализуется/дедупится внутри).

    Raises ValueError если WB-ключ не настроен (наследуется от
    check_acceptance_and_redistribute) — роутер → 400.
    """
    norm = _normalize_barcodes(barcodes)
    if not norm:
        return BarcodeEligibilityResponse(items=[], unknown=[], checked_at=utcnow())

    resolved, unknown = await _resolve_barcodes(db, project_id, norm)
    if not resolved:
        return BarcodeEligibilityResponse(items=[], unknown=unknown, checked_at=utcnow())

    # Eligibility: один item на баркод с пустым distribution. Из ответа берём
    # только поле `availability` (полный набор eligible WB-складов с *_meta);
    # splits/moves не нужны (distribution пуст). Кэш WB переиспользуется.
    accept_items = [
        {"nm_id": info["nm_id"], "barcode": bc, "distribution": {}}
        for bc, info in resolved.items()
    ]
    accept_result = await check_acceptance_and_redistribute(db, project_id, accept_items)
    availability_by_barcode: dict[str, dict] = {
        it["barcode"]: it.get("availability") or {} for it in accept_result.get("items", [])
    }

    # Free FF stock per nomenclature_id (batch).
    nom_ids = [info["nomenclature_id"] for info in resolved.values()]
    ff_stock_by_nom = await _free_ff_stock(db, project_id, nom_ids)

    items: list[BarcodeEligibilityItem] = []
    for bc in norm:
        info = resolved.get(bc)
        if info is None:
            continue
        availability = availability_by_barcode.get(bc, {})
        targets: list[BarcodeEligibilityTarget] = []
        for wb_name, flags in availability.items():
            target = _flags_to_target(wb_name, flags)
            if target is not None:
                targets.append(target)
        targets.sort(key=lambda t: t.wb_name)

        items.append(
            BarcodeEligibilityItem(
                nm_id=info["nm_id"],
                vendor_code=info["vendor_code"],
                barcode=bc,
                targets=targets,
                ff_stock=ff_stock_by_nom.get(info["nomenclature_id"], []),
            )
        )

    return BarcodeEligibilityResponse(items=items, unknown=unknown, checked_at=utcnow())

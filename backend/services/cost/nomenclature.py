# ruff: noqa: RUF002, RUF003
"""
Cost — Nomenclature (get, upload Excel).
"""

import io
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.models import (
    CostOrder,
    CostOrderItem,
    DutyBasis,
    DutyException,
    DutyRule,
    FulfillmentStock,
    Nomenclature,
    VehicleStatus,
    WarehouseStock,
)
from backend.utils.time import utcnow

# Vehicle statuses that mean the goods are en route (shipped from factory, not yet a warehouse stock row).
_IN_TRANSIT_STATUSES = [VehicleStatus.SHIPPED.value, VehicleStatus.CUSTOMS.value, VehicleStatus.DISPATCHED.value]


async def get_nomenclature(db: AsyncSession, project_id: int, limit: int = 1000, offset: int = 0) -> list:  # type: ignore[type-arg]
    result = await db.execute(
        select(Nomenclature)
        .where(Nomenclature.project_id == project_id)
        .order_by(Nomenclature.subject, Nomenclature.article_seller)
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def _missing_metric_barcodes(  # type: ignore[type-arg]
    db: AsyncSession, project_id: int, *, basis: DutyBasis | None, nom_col: Any, item_col: Any
) -> list[dict]:
    """Barcodes/quantity in vehicles where the metric (area_m2 / weight_kg) is missing
    **everywhere** the calc looks.

    The calc takes the metric from the cost item (Excel), then from a sibling source of the
    same barcode in the same vehicle (one barcode may span several factory-order lines), and
    finally falls back to the Nomenclature reference. So a quantity is "missing" only when
    ALL of these are empty: this `item_col`, every sibling `item_col` in the same vehicle,
    AND `nom_col` (Nomenclature). Flagging on the Nomenclature reference alone over-counts
    massively — e.g. weight that is always present on items but never maintained as a
    reference. Ignoring siblings over-counts too — it would nag to fill a metric a sibling
    source already supplies.

    When `basis` is given (area), only barcodes whose *effective* duty basis equals it are
    returned — exception-aware: an article-level DutyException overrides the category
    DutyRule. When `basis` is None (weight), the duty basis is not considered: every barcode
    in a vehicle missing the metric is surfaced, because weight is a general SKU attribute
    needed beyond duty (e.g. auto-computing assembly weight from item weights), not only for
    weight-based duty.

    Vehicles (cost_orders) count in any status, just not deleted. total_qty is the count of
    units actually missing the metric.
    """
    # A sibling source of the same barcode in the same vehicle that carries the metric
    # supplies it to this row (see items.recalculate_order_items) → this row is not
    # really missing the metric. Mirror that fallback so the warning doesn't over-report.
    sib = aliased(CostOrderItem)
    sib_metric = getattr(sib, item_col.key)
    sibling_supplies_metric = (
        select(sib.id)
        .where(
            sib.barcode == CostOrderItem.barcode,
            sib.order_no == CostOrderItem.order_no,
            sib.project_id == CostOrderItem.project_id,
            sib.is_deleted == False,  # noqa: E712
            sib.id != CostOrderItem.id,
            sib_metric.isnot(None),
            sib_metric != 0,
        )
        .exists()
    )
    stmt = (
        select(
            Nomenclature.barcode,
            Nomenclature.subject,
            Nomenclature.article_seller,
            func.coalesce(func.sum(CostOrderItem.qty), 0).label("total_qty"),
            func.array_agg(CostOrder.order_no.distinct()).label("vehicles"),
        )
        .join(
            CostOrderItem,
            (CostOrderItem.barcode == Nomenclature.barcode)
            & (CostOrderItem.project_id == Nomenclature.project_id)
            & (CostOrderItem.is_deleted == False),  # noqa: E712
        )
        .join(
            CostOrder,
            (CostOrder.order_no == CostOrderItem.order_no)
            & (CostOrder.project_id == Nomenclature.project_id)
            & (CostOrder.is_deleted == False),  # noqa: E712
        )
    )
    conditions = [
        Nomenclature.project_id == project_id,
        # metric missing on the item (Excel) ...
        or_(item_col.is_(None), item_col == 0),
        # ... AND no sibling source of the same barcode/vehicle supplies it ...
        ~sibling_supplies_metric,
        # ... AND no reference value on Nomenclature to fall back to
        or_(nom_col.is_(None), nom_col == 0),
    ]
    if basis is not None:
        # Restrict to barcodes whose effective duty basis == `basis` (exception wins, else rule).
        exc = aliased(DutyException)
        rule = aliased(DutyRule)
        stmt = stmt.outerjoin(
            exc,
            (exc.article_seller == Nomenclature.article_seller)
            & (exc.project_id == Nomenclature.project_id)
            & (exc.is_deleted == False),  # noqa: E712
        ).outerjoin(
            rule,
            (rule.subject == Nomenclature.subject)
            & (rule.project_id == Nomenclature.project_id)
            & (rule.is_deleted == False),  # noqa: E712
        )
        conditions.append(or_(exc.basis == basis, and_(exc.id.is_(None), rule.basis == basis)))
    stmt = (
        stmt.where(*conditions)
        .group_by(Nomenclature.barcode, Nomenclature.subject, Nomenclature.article_seller)
        .order_by(Nomenclature.subject, Nomenclature.article_seller)
    )
    result = await db.execute(stmt)
    return [
        {
            "barcode": row.barcode,
            "subject": row.subject,
            "article_seller": row.article_seller,
            "total_qty": int(row.total_qty or 0),
            "vehicles": sorted({v for v in (row.vehicles or []) if v}),
        }
        for row in result.all()
    ]


async def get_missing_area_barcodes(db: AsyncSession, project_id: int) -> list[dict]:  # type: ignore[type-arg]
    """Barcodes in vehicles whose (effective) duty is area-based but area is missing both on the item and the reference."""
    return await _missing_metric_barcodes(
        db, project_id, basis=DutyBasis.AREA, nom_col=Nomenclature.area_m2, item_col=CostOrderItem.area_m2
    )


async def get_missing_weight_barcodes(db: AsyncSession, project_id: int) -> list[dict]:  # type: ignore[type-arg]
    """Products (Nomenclature) with no weight that have real inventory presence — good stock on
    a warehouse/FF, OR goods in transit in a vehicle (shipped/customs/dispatched).

    Weight here is a general SKU attribute (needed to auto-compute assembly weight and for
    weight-based duty), not scoped to weight-based duty. Newcomers that only sit in a not-yet-
    shipped (FORMING) order drop out for free: they have neither stock nor an in-transit vehicle.

    Missing = no weight anywhere the auto calc looks: Nomenclature.weight_kg is NULL/0 AND no
    vehicle line (CostOrderItem.weight_kg) carries a weight for that barcode. A weight already
    entered on a machine is used automatically, so it is not "missing". total_qty/vehicles are
    display-only: good stock (our + FF) plus in-transit quantity, and the in-transit vehicles.
    """
    has_wh_stock = (
        select(WarehouseStock.id)
        .where(
            WarehouseStock.project_id == project_id,
            WarehouseStock.barcode == Nomenclature.barcode,
            WarehouseStock.quantity > 0,
        )
        .exists()
    )
    has_ff_stock = (
        select(FulfillmentStock.id)
        .where(
            FulfillmentStock.project_id == project_id,
            FulfillmentStock.barcode == Nomenclature.barcode,
            FulfillmentStock.qty_good > 0,
        )
        .exists()
    )
    in_transit = (
        select(CostOrderItem.id)
        .join(
            CostOrder,
            (CostOrder.order_no == CostOrderItem.order_no)
            & (CostOrder.project_id == CostOrderItem.project_id)
            & (CostOrder.is_deleted == False),  # noqa: E712
        )
        .where(
            CostOrderItem.project_id == project_id,
            CostOrderItem.barcode == Nomenclature.barcode,
            CostOrderItem.is_deleted == False,  # noqa: E712
            CostOrder.status.in_(_IN_TRANSIT_STATUSES),
        )
        .exists()
    )
    # Weight already entered on a vehicle line (наполнение машины) counts as known — the auto
    # weight calc uses it as a fallback, so such a barcode is NOT missing and must not be listed.
    has_machine_weight = (
        select(CostOrderItem.id)
        .where(
            CostOrderItem.project_id == project_id,
            CostOrderItem.barcode == Nomenclature.barcode,
            CostOrderItem.is_deleted == False,  # noqa: E712
            CostOrderItem.weight_kg.isnot(None),
            CostOrderItem.weight_kg > 0,
        )
        .exists()
    )
    wh_qty = (
        select(func.coalesce(func.sum(WarehouseStock.quantity), 0))
        .where(WarehouseStock.project_id == project_id, WarehouseStock.barcode == Nomenclature.barcode)
        .correlate(Nomenclature)
        .scalar_subquery()
    )
    ff_qty = (
        select(func.coalesce(func.sum(FulfillmentStock.qty_good), 0))
        .where(FulfillmentStock.project_id == project_id, FulfillmentStock.barcode == Nomenclature.barcode)
        .correlate(Nomenclature)
        .scalar_subquery()
    )
    transit_qty = (
        select(func.coalesce(func.sum(CostOrderItem.qty), 0))
        .select_from(CostOrderItem)
        .join(
            CostOrder,
            (CostOrder.order_no == CostOrderItem.order_no)
            & (CostOrder.project_id == CostOrderItem.project_id)
            & (CostOrder.is_deleted == False),  # noqa: E712
        )
        .where(
            CostOrderItem.project_id == project_id,
            CostOrderItem.barcode == Nomenclature.barcode,
            CostOrderItem.is_deleted == False,  # noqa: E712
            CostOrder.status.in_(_IN_TRANSIT_STATUSES),
        )
        .correlate(Nomenclature)
        .scalar_subquery()
    )
    transit_vehicles = (
        select(func.array_agg(func.distinct(CostOrder.order_no)))
        .select_from(CostOrderItem)
        .join(
            CostOrder,
            (CostOrder.order_no == CostOrderItem.order_no)
            & (CostOrder.project_id == CostOrderItem.project_id)
            & (CostOrder.is_deleted == False),  # noqa: E712
        )
        .where(
            CostOrderItem.project_id == project_id,
            CostOrderItem.barcode == Nomenclature.barcode,
            CostOrderItem.is_deleted == False,  # noqa: E712
            CostOrder.status.in_(_IN_TRANSIT_STATUSES),
        )
        .correlate(Nomenclature)
        .scalar_subquery()
    )
    stmt = (
        select(
            Nomenclature.barcode,
            Nomenclature.subject,
            Nomenclature.article_seller,
            (wh_qty + ff_qty + transit_qty).label("total_qty"),
            transit_vehicles.label("vehicles"),
        )
        .where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode.isnot(None),
            or_(Nomenclature.weight_kg.is_(None), Nomenclature.weight_kg == 0),
            ~has_machine_weight,
            or_(has_wh_stock, has_ff_stock, in_transit),
        )
        .order_by(Nomenclature.subject, Nomenclature.article_seller)
    )
    result = await db.execute(stmt)
    return [
        {
            "barcode": row.barcode,
            "subject": row.subject,
            "article_seller": row.article_seller,
            "total_qty": int(row.total_qty or 0),
            "vehicles": sorted({v for v in (row.vehicles or []) if v}),
        }
        for row in result.all()
    ]


async def get_nomenclature_subjects(db: AsyncSession, project_id: int) -> list[str]:
    """Return distinct non-empty subjects for the project (for category dropdowns/duty rules)."""
    result = await db.execute(
        select(Nomenclature.subject)
        .where(Nomenclature.project_id == project_id, Nomenclature.subject.isnot(None))
        .distinct()
        .order_by(Nomenclature.subject)
    )
    return [s for s in result.scalars().all() if s]


async def upload_nomenclature(db: AsyncSession, project_id: int, data: bytes) -> tuple[int, int]:
    """Upload nomenclature from Excel data, returns (inserted, updated)."""
    df = pd.read_excel(io.BytesIO(data))

    col_map = {
        "Баркод": "barcode",
        "Бренд": "brand",
        "Предмет": "subject",
        "Артикул продавца": "article_seller",
        "Артикул WB": "article_wb",
        "Объем, л": "volume_l",
    }
    df = df.rename(columns=col_map)

    # Collect valid barcodes from DataFrame
    rows_by_barcode: dict[str, Any] = {}
    for _, row in df.iterrows():
        bc = str(row.get("barcode", "")).strip()
        if not bc or bc == "nan":
            continue
        rows_by_barcode[bc] = row

    if not rows_by_barcode:
        return 0, 0

    # Single batch SELECT instead of N queries
    existing_result = await db.execute(
        select(Nomenclature).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode.in_(list(rows_by_barcode.keys())),
        )
    )
    existing = {nom.barcode: nom for nom in existing_result.scalars().all()}

    inserted, updated = 0, 0
    for bc, row in rows_by_barcode.items():
        try:
            awb = int(row.get("article_wb")) if row.get("article_wb") else None
        except Exception:
            awb = None
        try:
            vol = Decimal(str(row.get("volume_l", 0) or 0))
        except Exception:
            vol = None

        nom = existing.get(bc)
        if nom:
            nom.brand = str(row.get("brand", "") or "").strip() or None
            nom.subject = str(row.get("subject", "") or "").strip() or None
            nom.article_seller = str(row.get("article_seller", "") or "").strip() or None
            nom.article_wb = awb
            nom.volume_l = vol
            nom.updated_at = utcnow()
            updated += 1
        else:
            nom = Nomenclature(
                project_id=project_id,
                barcode=bc,
                brand=str(row.get("brand", "") or "").strip() or None,
                subject=str(row.get("subject", "") or "").strip() or None,
                article_seller=str(row.get("article_seller", "") or "").strip() or None,
                article_wb=awb,
                volume_l=vol,
            )
            db.add(nom)
            inserted += 1

    await db.commit()
    return inserted, updated

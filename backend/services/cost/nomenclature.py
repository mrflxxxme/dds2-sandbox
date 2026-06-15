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

from backend.models import CostOrder, CostOrderItem, DutyBasis, DutyException, DutyRule, Nomenclature
from backend.utils.time import utcnow


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
    db: AsyncSession, project_id: int, *, basis: DutyBasis, nom_col: Any, item_col: Any
) -> list[dict]:
    """Barcodes/quantity in vehicles whose *effective* duty basis equals `basis`
    but the metric (area_m2 / weight_kg) is missing **everywhere** the calc looks.

    The duty calc takes the metric from the cost item (Excel), then from a sibling
    source of the same barcode in the same vehicle (one barcode may span several
    factory-order lines), and finally falls back to the Nomenclature reference. So a
    quantity is "missing" only when ALL of these are empty: this `item_col`, every
    sibling `item_col` in the same vehicle, AND `nom_col` (Nomenclature). Flagging on
    the Nomenclature reference alone over-counts massively — e.g. weight that is
    always present on items but never maintained as a reference. Ignoring siblings
    over-counts too — it would nag to fill a weight a sibling source already supplies
    (duty for that barcode is already correct).

    Effective basis is exception-aware: an article-level DutyException overrides
    the category DutyRule. Vehicles (cost_orders) count in any status, just not
    deleted. total_qty is the count of units actually missing the metric.
    """
    exc = aliased(DutyException)
    rule = aliased(DutyRule)
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
        .outerjoin(
            exc,
            (exc.article_seller == Nomenclature.article_seller)
            & (exc.project_id == Nomenclature.project_id)
            & (exc.is_deleted == False),  # noqa: E712
        )
        .outerjoin(
            rule,
            (rule.subject == Nomenclature.subject)
            & (rule.project_id == Nomenclature.project_id)
            & (rule.is_deleted == False),  # noqa: E712
        )
        .where(
            Nomenclature.project_id == project_id,
            # metric missing on the item (Excel) ...
            or_(item_col.is_(None), item_col == 0),
            # ... AND no sibling source of the same barcode/vehicle supplies it ...
            ~sibling_supplies_metric,
            # ... AND no reference value on Nomenclature to fall back to
            or_(nom_col.is_(None), nom_col == 0),
            # effective basis == `basis`: exception wins, else category rule
            or_(exc.basis == basis, and_(exc.id.is_(None), rule.basis == basis)),
        )
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
    """Barcodes in vehicles whose (effective) duty is weight-based but weight is missing both on the item and the reference."""
    return await _missing_metric_barcodes(
        db, project_id, basis=DutyBasis.WEIGHT, nom_col=Nomenclature.weight_kg, item_col=CostOrderItem.weight_kg
    )


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

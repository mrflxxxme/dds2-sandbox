# ruff: noqa: RUF002, RUF003
"""
Cost — Nomenclature (get, upload Excel).
"""

import io
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import CostOrder, CostOrderItem, DutyBasis, DutyRule, Nomenclature
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


async def get_missing_area_barcodes(db: AsyncSession, project_id: int) -> list[dict]:  # type: ignore[type-arg]
    """Barcodes in vehicles whose duty is area-based but `area_m2` is not set.

    Площадь нужна для пошлины только у категорий с базисом «За м²» (DutyBasis.AREA),
    поэтому отбираем баркоды, у которых subject имеет AREA-правило и площадь пустая.
    Машины (cost_orders) учитываются в любом статусе — лишь бы не удалены.
    Возвращает одну строку на баркод с суммарным кол-вом и списком машин (order_no).
    """
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
        .join(
            DutyRule,
            (DutyRule.subject == Nomenclature.subject)
            & (DutyRule.project_id == Nomenclature.project_id)
            & (DutyRule.is_deleted == False)  # noqa: E712
            & (DutyRule.basis == DutyBasis.AREA),
        )
        .where(
            Nomenclature.project_id == project_id,
            or_(Nomenclature.area_m2.is_(None), Nomenclature.area_m2 == 0),
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

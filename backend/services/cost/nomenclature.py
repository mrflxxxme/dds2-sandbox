"""
Cost — Nomenclature (get, upload Excel).
"""

import io
from decimal import Decimal

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Nomenclature
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

    inserted, updated = 0, 0
    for _, row in df.iterrows():
        bc = str(row.get("barcode", "")).strip()
        if not bc or bc == "nan":
            continue
        result = await db.execute(
            select(Nomenclature).where(
                Nomenclature.project_id == project_id,
                Nomenclature.barcode == bc,
            )
        )
        nom = result.scalar_one_or_none()
        if nom:
            nom.brand = str(row.get("brand", "") or "").strip() or None
            nom.subject = str(row.get("subject", "") or "").strip() or None
            nom.article_seller = str(row.get("article_seller", "") or "").strip() or None
            try:
                nom.article_wb = int(row.get("article_wb")) if row.get("article_wb") else None
            except Exception:
                nom.article_wb = None
            try:
                nom.volume_l = Decimal(str(row.get("volume_l", 0) or 0))
            except Exception:
                nom.volume_l = None
            nom.updated_at = utcnow()
            updated += 1
        else:
            try:
                vol = Decimal(str(row.get("volume_l", 0) or 0))
            except Exception:
                vol = None
            try:
                awb = int(row.get("article_wb")) if row.get("article_wb") else None
            except Exception:
                awb = None
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

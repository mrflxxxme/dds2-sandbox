"""
Service: tariff — WB commission tariffs CRUD + avg buyout map.

Tariffs are project-scoped, no expiry.
Loaded from xlsx, used by funnel profit formula.
"""

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached, invalidate_cache
from backend.etl.tariff_parser import parse_tariff_xlsx
from backend.models import WbFunnelDaily
from backend.models.wb_tariff import WbTariff

logger = logging.getLogger("dds.tariff")


async def list_tariffs(db: AsyncSession, project_id: int) -> list[WbTariff]:
    """List all active tariffs for a project."""
    result = await db.execute(
        select(WbTariff)
        .where(
            WbTariff.project_id == project_id,
            WbTariff.is_deleted == False,  # noqa: E712
        )
        .order_by(WbTariff.subject_name)
    )
    return list(result.scalars().all())


async def upload_tariffs(db: AsyncSession, project_id: int, data: bytes) -> dict:
    """Parse xlsx, soft-delete old tariffs, insert new ones.

    Returns {inserted: N, replaced: N}.
    """
    rows = parse_tariff_xlsx(data)

    # Count existing tariffs (for 'replaced' stat)
    existing = await list_tariffs(db, project_id)
    replaced = len(existing)

    # Soft-delete all existing tariffs for this project
    for t in existing:
        t.soft_delete()

    # Insert new tariffs
    for row in rows:
        tariff = WbTariff(
            project_id=project_id,
            subject_name=row["subject_name"],
            commission_rate=row["commission_rate"],
        )
        db.add(tariff)

    await db.commit()
    await invalidate_cache(f"funnel:project_id={project_id}")
    logger.info(f"Uploaded {len(rows)} tariffs for project {project_id} (replaced {replaced})")

    return {"inserted": len(rows), "replaced": replaced}


async def delete_tariff(db: AsyncSession, project_id: int, tariff_id: int) -> bool:
    """Soft-delete a single tariff."""
    result = await db.execute(
        select(WbTariff).where(
            WbTariff.id == tariff_id,
            WbTariff.project_id == project_id,
            WbTariff.is_deleted == False,  # noqa: E712
        )
    )
    tariff = result.scalar_one_or_none()
    if not tariff:
        return False

    tariff.soft_delete()
    await db.commit()
    await invalidate_cache(f"funnel:project_id={project_id}")
    return True


@cached(prefix="funnel:tariff_map", ttl=600)
async def get_tariff_map(db: AsyncSession, project_id: int) -> dict[str, float]:
    """Get tariff map {subject_name: commission_rate} for funnel queries.

    Cached for 10 min. Invalidated on tariff upload/delete.
    """
    tariffs = await list_tariffs(db, project_id)
    return {t.subject_name: float(t.commission_rate) for t in tariffs}


@cached(prefix="funnel:avg_buyout", ttl=600)
async def get_avg_buyout_map(db: AsyncSession, project_id: int) -> dict[int, float]:
    """Get {nm_id: avg_buyout_%} from last 7 days where buyout > 0.

    Used for revenue calculation: revenue = orders_sum * avg_buyout / 100.
    Fallback: 100% if product has no buyout history in last 7 days.
    Cached for 10 min. Invalidated by funnel sync (prefix 'funnel:*').
    """
    max_date_q = select(func.max(WbFunnelDaily.date)).where(WbFunnelDaily.project_id == project_id)
    max_date = (await db.execute(max_date_q)).scalar()
    if not max_date:
        return {}

    cutoff = max_date - timedelta(days=7)
    q = (
        select(
            WbFunnelDaily.nm_id,
            func.avg(WbFunnelDaily.buyout_percent).label("avg_buyout"),
        )
        .where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.buyout_percent > 0,
            WbFunnelDaily.date >= cutoff,
        )
        .group_by(WbFunnelDaily.nm_id)
    )

    rows = (await db.execute(q)).all()
    return {r.nm_id: float(r.avg_buyout) for r in rows}

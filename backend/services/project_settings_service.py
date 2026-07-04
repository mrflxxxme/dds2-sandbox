"""
Service: project-level rate settings (tax_rate, vat_rate).

Centralises mutations that were previously inlined in routers,
and ensures full cache invalidation after changes.
"""

import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.models import Project

logger = logging.getLogger("dds.project_settings")


async def set_tax_rate(db: AsyncSession, project: Project, tax_rate: Decimal) -> Decimal:
    """Update project tax rate and invalidate affected caches."""
    project.tax_rate = Decimal(str(tax_rate))
    await db.commit()

    pid = project.id
    await invalidate_cache(f"reports:wb_bdr:project_id={pid}")
    await invalidate_cache(f"reports:opiu:project_id={pid}")
    await invalidate_cache(f"reports:dashboard:project_id={pid}")
    await invalidate_cache(f"funnel:tariff_map:project_id={pid}")
    await invalidate_cache(f"funnel:avg_buyout:project_id={pid}")

    logger.info("tax_rate updated: project_id=%d, value=%s", pid, project.tax_rate)
    return project.tax_rate


async def set_vat_rate(db: AsyncSession, project: Project, vat_rate: Decimal) -> Decimal:
    """Update project VAT rate and invalidate affected caches."""
    project.vat_rate = Decimal(str(vat_rate))
    await db.commit()

    pid = project.id
    await invalidate_cache(f"cost:project_id={pid}")
    await invalidate_cache(f"reports:wb_bdr:project_id={pid}")
    await invalidate_cache(f"reports:opiu:project_id={pid}")
    await invalidate_cache(f"reports:dashboard:project_id={pid}")

    logger.info("vat_rate updated: project_id=%d, value=%s", pid, project.vat_rate)
    return project.vat_rate

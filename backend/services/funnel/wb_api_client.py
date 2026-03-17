"""
WB API client — backward-compatible re-export.

Split into:
- wb_funnel_api.py: sales funnel data (fetch_funnel, fetch_funnel_history)
- wb_advertising_api.py: ad campaigns and stats
- wb_supplier_api.py: supplier orders, warehouse stocks, acceptance
"""

# API key lookup stays here (tiny, used by all modules)
import logging
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import IntegrationKey
from backend.utils.crypto import decrypt

logger = logging.getLogger("dds.funnel")


async def get_wb_key(db: AsyncSession, project_id: int, service: str) -> Optional[str]:
    """Get decrypted WB API key by service label."""
    result = await db.execute(
        select(IntegrationKey).where(
            or_(
                IntegrationKey.project_id == project_id,
                IntegrationKey.project_id.is_(None),
            ),
            IntegrationKey.service == service,
            IntegrationKey.is_active == True,
            IntegrationKey.is_deleted == False,
        ).order_by(IntegrationKey.project_id.desc().nullslast()).limit(1)
    )
    key = result.scalar_one_or_none()
    if not key:
        return None
    return decrypt(key.encrypted_key)


# Re-export all API functions for backward compatibility
from backend.services.funnel.wb_funnel_api import (
    fetch_funnel,
    fetch_funnel_history,
)

from backend.services.funnel.wb_advertising_api import (
    fetch_ad_campaigns,
    fetch_ad_stats,
)

from backend.services.funnel.wb_supplier_api import (
    fetch_supplier_orders,
    fetch_acceptance_coefficients,
    fetch_wb_warehouses,
    fetch_acceptance_options,
    fetch_warehouse_stocks,
)

__all__ = [
    "get_wb_key",
    "fetch_funnel",
    "fetch_funnel_history",
    "fetch_ad_campaigns",
    "fetch_ad_stats",
    "fetch_supplier_orders",
    "fetch_acceptance_coefficients",
    "fetch_wb_warehouses",
    "fetch_acceptance_options",
    "fetch_warehouse_stocks",
]

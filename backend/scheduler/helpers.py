"""Scheduler helpers — shared utilities, project detection, missing date logic."""

import logging
import re
from collections import Counter
from datetime import date, timedelta

from backend.database import AsyncSessionLocal
from backend.models import IntegrationKey, Project, SyncLog, WbFunnelDaily

logger = logging.getLogger("dds.scheduler")

BACKFILL_DAYS = 90
MISSING_BATCH_SIZE = 10
MAX_DATE_FAILURES = 3


def split_into_windows(dates: list[str], max_window: int = 30) -> list[tuple[str, str]]:
    """Split date strings into windows of max `max_window` days."""
    if not dates:
        return []
    windows = []
    i = 0
    while i < len(dates):
        w_start = date.fromisoformat(dates[i])
        j = i
        while j < len(dates):
            d = date.fromisoformat(dates[j])
            if (d - w_start).days >= max_window:
                break
            j += 1
        windows.append((dates[i], dates[j - 1]))
        i = j
    return windows


async def get_sync_project_ids() -> list[int]:
    """Get project IDs that should be synced (global or per-project WB keys)."""
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        global_key = await db.execute(
            select(IntegrationKey.id)
            .where(
                IntegrationKey.service.in_(["wb", "wb_analytics"]),
                IntegrationKey.is_active == True,
                IntegrationKey.is_deleted == False,
                IntegrationKey.project_id.is_(None),
            )
            .limit(1)
        )
        has_global = global_key.scalar() is not None

        if has_global:
            result = await db.execute(select(Project.id))
            return [r[0] for r in result if r[0]]
        else:
            result = await db.execute(
                select(IntegrationKey.project_id)
                .where(
                    IntegrationKey.service.in_(["wb", "wb_analytics"]),
                    IntegrationKey.is_active == True,
                    IntegrationKey.is_deleted == False,
                    IntegrationKey.project_id.isnot(None),
                )
                .distinct()
            )
            return [r[0] for r in result if r[0]]


async def get_failed_dates(project_id: int) -> set[str]:
    """Find dates that have failed >= MAX_DATE_FAILURES times (skipped)."""
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SyncLog.sync_type, SyncLog.error_msg, SyncLog.status).where(
                SyncLog.service == "wb_funnel",
                SyncLog.sync_type.in_(["backfill", "ad_resync"]),
                SyncLog.status.in_(["TIMEOUT", "ERROR"]),
            )
        )
        date_failures: Counter = Counter()
        for row in result:
            msg = row.error_msg or ""
            dates_found = re.findall(r"\d{4}-\d{2}-\d{2}", msg)
            for d in dates_found:
                date_failures[d] += 1
        return {d for d, count in date_failures.items() if count >= MAX_DATE_FAILURES}


async def get_missing_dates(project_id: int, lookback_days: int = BACKFILL_DAYS) -> list[str]:
    """Find dates in the last `lookback_days` that have NO funnel data."""
    from sqlalchemy import select

    today = date.today()
    start = today - timedelta(days=lookback_days)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(WbFunnelDaily.date)
            .where(
                WbFunnelDaily.project_id == project_id,
                WbFunnelDaily.date >= start,
            )
            .distinct()
        )
        existing_dates = {r[0] for r in result}

    poisoned = await get_failed_dates(project_id)
    if poisoned:
        logger.info(f"Skipping {len(poisoned)} poisoned dates: {sorted(poisoned)[:5]}")

    all_dates = set()
    d = start
    while d < today:
        all_dates.add(d)
        d += timedelta(days=1)

    missing = sorted(all_dates - existing_dates)
    return [d.isoformat() for d in missing if d.isoformat() not in poisoned]


async def get_days_with_incomplete_ads(project_id: int, lookback_days: int = BACKFILL_DAYS) -> list[str]:
    """Find dates that have funnel data but ZERO ad data."""
    from sqlalchemy import func, select

    today = date.today()
    start = today - timedelta(days=lookback_days)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(
                WbFunnelDaily.date,
                func.sum(WbFunnelDaily.adv_sum).label("total_adv"),
            )
            .where(
                WbFunnelDaily.project_id == project_id,
                WbFunnelDaily.date >= start,
                WbFunnelDaily.date < today,
            )
            .group_by(WbFunnelDaily.date)
            .order_by(WbFunnelDaily.date)
        )
        rows = list(result)

    if not rows:
        return []

    zero_days = [r.date.isoformat() for r in rows if float(r.total_adv or 0) == 0]
    if zero_days:
        logger.info(f"Ad completeness check: project {project_id}, zero ad days: {len(zero_days)}")
    return zero_days[:30]

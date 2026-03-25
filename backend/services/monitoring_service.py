"""
Service: monitoring — sync health metrics, scheduler status.
"""

import logging
from datetime import timedelta

from sqlalchemy import case, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import IntegrationKey, SyncLog
from backend.utils.time import utcnow

logger = logging.getLogger("dds.monitoring")


async def get_sync_overview(db: AsyncSession, project_id: int) -> dict:
    """Get overview of all sync types with health metrics for last 24h."""
    now = utcnow()
    since_24h = now - timedelta(hours=24)

    # Get all integration key IDs for this project
    key_ids_q = select(IntegrationKey.id).where(
        IntegrationKey.project_id == project_id,
        IntegrationKey.is_deleted.is_(False),
    )

    # Aggregate stats per (service, sync_type) for last 24h
    stats_q = (
        select(
            SyncLog.service,
            SyncLog.sync_type,
            func.count().label("total"),
            func.count(case((SyncLog.status == "OK", 1))).label("ok_count"),
            func.count(case((SyncLog.status == "ERROR", 1))).label("error_count"),
            func.avg(extract("epoch", SyncLog.finished_at) - extract("epoch", SyncLog.started_at)).label(
                "avg_duration"
            ),
        )
        .where(
            SyncLog.integration_id.in_(key_ids_q),
            SyncLog.started_at >= since_24h,
        )
        .group_by(SyncLog.service, SyncLog.sync_type)
    )
    stats_result = await db.execute(stats_q)
    stats_rows = stats_result.all()

    # Get latest sync per (service, sync_type) — last OK and last ERROR
    # Using a subquery to get the latest entry per type
    latest_q = select(
        SyncLog.service,
        SyncLog.sync_type,
        SyncLog.status,
        SyncLog.started_at,
        SyncLog.finished_at,
        SyncLog.error_msg,
        func.row_number()
        .over(
            partition_by=[SyncLog.service, SyncLog.sync_type],
            order_by=SyncLog.started_at.desc(),
        )
        .label("rn"),
    ).where(SyncLog.integration_id.in_(key_ids_q))
    latest_sub = latest_q.subquery()
    latest_result = await db.execute(select(latest_sub).where(latest_sub.c.rn == 1))
    latest_rows = {(r.service, r.sync_type): r for r in latest_result.all()}

    # Get last OK per type
    last_ok_q = (
        select(
            SyncLog.service,
            SyncLog.sync_type,
            func.max(SyncLog.finished_at).label("last_ok_at"),
        )
        .where(
            SyncLog.integration_id.in_(key_ids_q),
            SyncLog.status == "OK",
        )
        .group_by(SyncLog.service, SyncLog.sync_type)
    )
    last_ok_result = await db.execute(last_ok_q)
    last_ok_map = {(r.service, r.sync_type): r.last_ok_at for r in last_ok_result.all()}

    # Get last ERROR per type
    last_err_q = (
        select(
            SyncLog.service,
            SyncLog.sync_type,
            func.max(SyncLog.started_at).label("last_error_at"),
        )
        .where(
            SyncLog.integration_id.in_(key_ids_q),
            SyncLog.status == "ERROR",
        )
        .group_by(SyncLog.service, SyncLog.sync_type)
    )
    last_err_result = await db.execute(last_err_q)
    last_err_map = {(r.service, r.sync_type): r.last_error_at for r in last_err_result.all()}

    # Check running syncs
    running_q = select(SyncLog.service, SyncLog.sync_type, SyncLog.started_at).where(
        SyncLog.integration_id.in_(key_ids_q),
        SyncLog.status == "RUNNING",
    )
    running_result = await db.execute(running_q)
    running_map = {(r.service, r.sync_type): r.started_at for r in running_result.all()}

    # Build sync_types list
    # Collect all known (service, sync_type) pairs
    all_keys: set[tuple[str, str]] = set()
    for r in stats_rows:
        all_keys.add((r.service, r.sync_type))
    for k in latest_rows:
        all_keys.add(k)
    for k in running_map:
        all_keys.add(k)

    sync_types = []
    total_24h = 0
    total_errors_24h = 0

    for service, sync_type in sorted(all_keys):
        # Stats from 24h window
        stat = next(
            (r for r in stats_rows if r.service == service and r.sync_type == sync_type),
            None,
        )
        total = stat.total if stat else 0
        ok_count = stat.ok_count if stat else 0
        error_count = stat.error_count if stat else 0
        avg_dur = float(stat.avg_duration) if stat and stat.avg_duration else None

        total_24h += total
        total_errors_24h += error_count

        latest = latest_rows.get((service, sync_type))
        last_error_msg = None
        if latest and latest.status == "ERROR":
            last_error_msg = latest.error_msg

        is_running = (service, sync_type) in running_map
        running_since = running_map.get((service, sync_type))

        sync_types.append(
            {
                "service": service,
                "sync_type": sync_type,
                "last_ok_at": last_ok_map.get((service, sync_type)),
                "last_error_at": last_err_map.get((service, sync_type)),
                "last_error_msg": last_error_msg,
                "last_status": latest.status if latest else None,
                "total_24h": total,
                "ok_24h": ok_count,
                "error_24h": error_count,
                "avg_duration_sec": round(avg_dur, 1) if avg_dur else None,
                "is_running": is_running,
                "running_since": running_since,
            }
        )

    return {
        "sync_types": sync_types,
        "total_syncs_24h": total_24h,
        "total_errors_24h": total_errors_24h,
    }


async def get_sync_log_filtered(
    db: AsyncSession,
    project_id: int,
    service: str | None = None,
    sync_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Get filtered sync log entries with duration."""
    key_ids_q = select(IntegrationKey.id).where(
        IntegrationKey.project_id == project_id,
        IntegrationKey.is_deleted.is_(False),
    )

    q = (
        select(
            SyncLog,
            (extract("epoch", SyncLog.finished_at) - extract("epoch", SyncLog.started_at)).label("duration_sec"),
        )
        .where(SyncLog.integration_id.in_(key_ids_q))
        .order_by(SyncLog.started_at.desc())
        .limit(limit)
        .offset(offset)
    )

    if service:
        q = q.where(SyncLog.service == service)
    if sync_type:
        q = q.where(SyncLog.sync_type == sync_type)
    if status:
        q = q.where(SyncLog.status == status)

    result = await db.execute(q)
    rows = result.all()

    return [
        {
            "id": row.SyncLog.id,
            "integration_id": row.SyncLog.integration_id,
            "service": row.SyncLog.service,
            "sync_type": row.SyncLog.sync_type,
            "started_at": row.SyncLog.started_at,
            "finished_at": row.SyncLog.finished_at,
            "status": row.SyncLog.status,
            "rows_fetched": row.SyncLog.rows_fetched,
            "rows_inserted": row.SyncLog.rows_inserted,
            "error_msg": row.SyncLog.error_msg,
            "duration_sec": round(float(row.duration_sec), 1) if row.duration_sec else None,
        }
        for row in rows
    ]

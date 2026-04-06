"""
FBO Supply sync — sync orchestration with DB + WB API.

Functions:
- sync_fbo_supplies: Full sync from FBW list API
- enrich_fbo_supplies: Background detail enrichment (rate-limited)
- sync_fbo_statuses: Status-only sync for active supplies
"""

import asyncio
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyRequest, AssemblyStatus, AssemblyStatusHistory
from backend.models.integrations import SyncLog
from backend.models.warehouse import OutboundShipment, OutboundStatus
from backend.models.wb_fbo import WbFboSupply, WbSupplyStatus
from backend.utils.time import utcnow

from .mappers import (
    FBW_RATE_LIMIT_DELAY,
    _create_supply_from_fbw_list,
    _update_supply_from_fbw_detail,
    _update_supply_from_fbw_list,
)

logger = logging.getLogger(__name__)


# ─── Sync: full ─────────────────────────────────────────────────────────────


async def sync_fbo_supplies(
    db: AsyncSession,
    project_id: int,
    api_client: Any,
    integration_id: int,
) -> dict[str, object]:
    """
    Fast sync: fetch all FBW supplies list (1 API call), upsert into DB, return immediately.
    Detail enrichment (warehouse, qty) runs in background via enrich_fbo_supplies().

    Returns: {synced, created, updated, errors, message}
    """
    sync_log = SyncLog(
        integration_id=integration_id,
        service="wildberries",
        sync_type="fbo_supplies",
        started_at=utcnow(),
        status="RUNNING",
    )
    db.add(sync_log)
    await db.flush()

    created = 0
    updated = 0
    errors = 0

    try:
        # 1. Fetch all supplies from FBW API (1 call, up to 1000 items per page)
        now = utcnow()
        date_from = (now - timedelta(days=365)).strftime("%Y-%m-%d")
        date_to = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        all_supplies: list[dict] = []
        offset = 0
        while True:
            batch = await api_client.get_fbw_supplies(
                date_from=date_from,
                date_to=date_to,
                status_ids=[1, 2, 3, 4, 5, 6],
                limit=1000,
                offset=offset,
            )
            if not batch:
                break
            all_supplies.extend(batch)
            if len(batch) < 1000:
                break
            offset += len(batch)

        logger.info(
            "fbo_sync.fbw_list_fetched",
            extra={"project_id": project_id, "total": len(all_supplies)},
        )

        # 2. Load existing supplies for this project (for upsert logic)
        result = await db.execute(select(WbFboSupply).where(WbFboSupply.project_id == project_id))
        existing_map: dict[str, WbFboSupply] = {s.wb_supply_id: s for s in result.scalars().all()}

        # 3. Upsert all supplies from list data (no detail call needed)
        for wb_supply in all_supplies:
            try:
                supply_id_raw = wb_supply.get("supplyID")
                if not supply_id_raw:
                    continue
                wb_supply_id = str(supply_id_raw)

                existing = existing_map.get(wb_supply_id)

                if existing:
                    _update_supply_from_fbw_list(existing, wb_supply)
                    updated += 1
                else:
                    supply = _create_supply_from_fbw_list(project_id, wb_supply)
                    db.add(supply)
                    await db.flush()
                    created += 1
                    existing_map[wb_supply_id] = supply

            except Exception as e:
                logger.warning(
                    "fbo_sync.supply_error",
                    extra={
                        "wb_supply_id": wb_supply.get("supplyID"),
                        "error": str(e),
                    },
                )
                errors += 1

        # 4. Auto-deliver linked shipments for ACCEPTED supplies
        for _wb_supply_id, supply in existing_map.items():
            if supply.wb_status == WbSupplyStatus.ACCEPTED and supply.outbound_shipment_id:
                await _auto_deliver_shipment(db, project_id, supply.outbound_shipment_id)
                await _auto_deliver_assembly(db, project_id, supply.id)

        await db.commit()

        sync_log.status = "OK"
        sync_log.rows_fetched = len(all_supplies)
        sync_log.rows_inserted = created
        sync_log.finished_at = utcnow()
        await db.commit()

        return {
            "synced": created + updated,
            "created": created,
            "updated": updated,
            "errors": errors,
            "message": (
                f"Загружено {created + updated} поставок "
                f"({created} новых, {updated} обновлено). "
                f"Детали загружаются в фоне."
            ),
        }

    except Exception as e:
        sync_log.status = "ERROR"
        sync_log.error_msg = str(e)[:500]
        sync_log.finished_at = utcnow()
        await db.commit()
        raise


async def enrich_fbo_supplies(
    db: AsyncSession,
    project_id: int,
    api_client: Any,
    max_calls: int = 30,
) -> dict[str, int]:
    """
    Background enrichment: fetch detail (warehouse, qty) for supplies missing it.
    Called after sync or on-demand. Rate-limited to max_calls per run.

    Returns: {enriched, errors}
    """
    # Find supplies needing detail (no warehouse_name OR CANCELLED with possible partial acceptance)
    result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.project_id == project_id,
            or_(
                WbFboSupply.warehouse_name.is_(None),
                WbFboSupply.wb_status == WbSupplyStatus.CANCELLED,
            ),
        )
    )
    supplies_needing_detail = result.scalars().all()

    if not supplies_needing_detail:
        return {"enriched": 0, "errors": 0}

    logger.info(
        "fbo_enrich.start",
        extra={
            "project_id": project_id,
            "need": len(supplies_needing_detail),
            "max": max_calls,
        },
    )

    enriched = 0
    errors = 0

    for supply in supplies_needing_detail[:max_calls]:
        try:
            if enriched > 0:
                await asyncio.sleep(FBW_RATE_LIMIT_DELAY)

            detail = await api_client.get_fbw_supply_detail(int(supply.wb_supply_id))
            if detail:
                _update_supply_from_fbw_detail(supply, detail)
            supply.synced_at = utcnow()
            await db.commit()
            enriched += 1

        except Exception as e:
            await db.rollback()
            logger.warning(
                "fbo_enrich.error",
                extra={"wb_supply_id": supply.wb_supply_id, "error": str(e)},
            )
            errors += 1

    logger.info(
        "fbo_enrich.done",
        extra={"enriched": enriched, "errors": errors},
    )

    return {"enriched": enriched, "errors": errors}


# ─── Sync: statuses only ───────────────────────────────────────────────────


async def sync_fbo_statuses(
    db: AsyncSession,
    project_id: int,
    api_client: Any,
    integration_id: int,
) -> dict[str, object]:
    """
    Status-only sync: fetch active FBW supplies from Suppliers API
    and update statuses + dates.

    Uses single POST /api/v1/supplies call with statusIDs=[1,2,3]
    to get all non-final supplies. No per-supply detail calls needed.

    Returns: {synced, updated, errors, message}
    """
    # Get our active supplies from DB
    result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.project_id == project_id,
            WbFboSupply.wb_status.notin_(
                [
                    WbSupplyStatus.ACCEPTED,
                    WbSupplyStatus.CANCELLED,
                ]
            ),
        )
    )
    active_supplies = result.scalars().all()

    if not active_supplies:
        return {
            "synced": 0,
            "updated": 0,
            "errors": 0,
            "message": "No active supplies to update",
        }

    # Build lookup by wb_supply_id
    supply_map: dict[str, WbFboSupply] = {s.wb_supply_id: s for s in active_supplies}

    updated = 0
    errors = 0

    try:
        # Fetch active supplies from FBW API (statuses 1-3 = non-final)
        now = utcnow()
        date_from = (now - timedelta(days=365)).strftime("%Y-%m-%d")
        date_to = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        wb_supplies = await api_client.get_fbw_supplies(
            date_from=date_from,
            date_to=date_to,
            status_ids=[1, 2, 3, 4, 5, 6],  # all statuses to catch transitions
            limit=1000,
            offset=0,
        )

        # Match and update our active supplies
        for wb_data in wb_supplies:
            supply_id_raw = wb_data.get("supplyID")
            if not supply_id_raw:
                continue
            wb_supply_id = str(supply_id_raw)

            supply = supply_map.get(wb_supply_id)
            if not supply:
                continue

            try:
                old_status = supply.wb_status
                _update_supply_from_fbw_list(supply, wb_data)
                supply.synced_at = utcnow()
                updated += 1

                # Auto-deliver linked shipment if status changed to ACCEPTED
                if (
                    old_status != WbSupplyStatus.ACCEPTED
                    and supply.wb_status == WbSupplyStatus.ACCEPTED
                    and supply.outbound_shipment_id
                ):
                    await _auto_deliver_shipment(db, project_id, supply.outbound_shipment_id)
                    await _auto_deliver_assembly(db, project_id, supply.id)

            except Exception as e:
                logger.warning(
                    "fbo_sync.status_error",
                    extra={
                        "supply_id": supply.id,
                        "error": str(e),
                    },
                )
                errors += 1

    except Exception as e:
        logger.warning(
            "fbo_sync.status_list_error",
            extra={"project_id": project_id, "error": str(e)},
        )
        errors += 1

    await db.commit()

    return {
        "synced": updated,
        "updated": updated,
        "errors": errors,
        "message": f"Updated {updated} supply statuses",
    }


# ─── Auto-deliver helpers ─────────────────────────────────────────────────


async def _auto_deliver_shipment(
    db: AsyncSession,
    project_id: int,
    shipment_id: int,
) -> None:
    """Auto-deliver OutboundShipment when linked WB supply is ACCEPTED."""
    result = await db.execute(
        select(OutboundShipment).where(
            OutboundShipment.id == shipment_id,
            OutboundShipment.project_id == project_id,
            OutboundShipment.is_deleted == False,  # noqa: E712
        )
    )
    shipment = result.scalar_one_or_none()
    if shipment and shipment.status == OutboundStatus.SHIPPED:
        shipment.status = OutboundStatus.DELIVERED
        logger.info(
            "fbo_sync.auto_deliver",
            extra={
                "shipment_id": shipment_id,
                "project_id": project_id,
            },
        )


async def _auto_deliver_assembly(
    db: AsyncSession,
    project_id: int,
    fbo_supply_id: int,
) -> None:
    """Auto-deliver AssemblyRequest when linked FBO supply is ACCEPTED."""
    result = await db.execute(
        select(AssemblyRequest).where(
            AssemblyRequest.wb_fbo_supply_id == fbo_supply_id,
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted == False,  # noqa: E712
            AssemblyRequest.status == AssemblyStatus.SHIPPED,
        )
    )
    assembly_req = result.scalar_one_or_none()
    if assembly_req:
        assembly_req.status = AssemblyStatus.DELIVERED
        history = AssemblyStatusHistory(
            project_id=project_id,
            assembly_request_id=assembly_req.id,
            old_status=AssemblyStatus.SHIPPED,
            new_status=AssemblyStatus.DELIVERED,
            changed_at=utcnow(),
            changed_by="system",
            comment="WB FBO ACCEPTED",
        )
        db.add(history)
        logger.info(
            "fbo_sync.auto_deliver_assembly",
            extra={"assembly_id": assembly_req.id, "project_id": project_id},
        )

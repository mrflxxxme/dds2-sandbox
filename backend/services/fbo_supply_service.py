"""
FBO Supply Service — sync WB Marketplace supplies, list, link to outbound shipments.

Responsibilities:
- Sync supplies from WB API (full + status-only)
- List supplies with search/filter/sort/pagination
- Get supply items
- Link/unlink supply ↔ OutboundShipment
- Auto-deliver shipment when supply is ACCEPTED
"""

import logging
from datetime import date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.integrations import SyncLog
from backend.models.warehouse import OutboundShipment, OutboundStatus
from backend.models.wb_fbo import WbFboSupply, WbFboSupplyItem, WbSupplyStatus
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)


# ─── Sync: full ─────────────────────────────────────────────────────────────


async def sync_fbo_supplies(
    db: AsyncSession,
    project_id: int,
    api_client,
    integration_id: int,
) -> dict:
    """
    Full sync: fetch all supplies from WB API (last 60 days),
    upsert into wb_fbo_supplies + wb_fbo_supply_items.
    Updates SyncLog.

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
        # 1. Fetch all supplies from WB API (paginated)
        all_supplies = []
        next_cursor = 0
        while True:
            response = await api_client.get_fbo_supplies(next=next_cursor, limit=1000)
            supplies_batch = response.get("supplies", [])
            if not supplies_batch:
                break
            all_supplies.extend(supplies_batch)
            next_cursor = response.get("next", 0)
            if next_cursor == 0:
                break

        # 2. Process all supplies (no date cutoff — WB API handles pagination)
        for wb_supply in all_supplies:
            try:
                wb_supply_id = str(wb_supply.get("id", ""))
                if not wb_supply_id:
                    continue

                # WB API v3 /supplies list doesn't return "status" field,
                # only "done" (bool) and "closedAt". Map to our status enum.
                if "status" not in wb_supply:
                    wb_supply["status"] = _map_wb_done_to_status(wb_supply)

                # 3. Upsert supply
                result = await db.execute(
                    select(WbFboSupply).where(
                        WbFboSupply.project_id == project_id,
                        WbFboSupply.wb_supply_id == wb_supply_id,
                    )
                )
                existing = result.scalar_one_or_none()

                if existing:
                    _update_supply_from_wb(existing, wb_supply)
                    updated += 1
                    supply = existing
                else:
                    supply = _create_supply_from_wb(project_id, wb_supply)
                    db.add(supply)
                    await db.flush()
                    created += 1

                # 4. Fetch and upsert items for this supply
                wb_orders = await api_client.get_fbo_supply_orders(wb_supply_id)
                await _upsert_supply_items(db, supply.id, wb_orders)

                # 5. Update qty totals
                supply.total_qty = sum(o.get("quantity", 1) for o in wb_orders) if wb_orders else 0
                supply.synced_at = utcnow()

                # 6. Auto-deliver linked shipment if supply ACCEPTED
                if supply.wb_status == WbSupplyStatus.ACCEPTED and supply.outbound_shipment_id:
                    await _auto_deliver_shipment(db, project_id, supply.outbound_shipment_id)

            except Exception as e:
                logger.warning(
                    "fbo_sync.supply_error",
                    extra={
                        "wb_supply_id": wb_supply.get("id"),
                        "error": str(e),
                    },
                )
                errors += 1

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
            "message": f"Synced {created + updated} supplies ({created} new, {updated} updated)",
        }

    except Exception as e:
        sync_log.status = "ERROR"
        sync_log.error_msg = str(e)[:500]
        sync_log.finished_at = utcnow()
        await db.commit()
        raise


# ─── Sync: statuses only ───────────────────────────────────────────────────


async def sync_fbo_statuses(
    db: AsyncSession,
    project_id: int,
    api_client,
    integration_id: int,
) -> dict:
    """
    Status-only sync: for supplies with status not in (ACCEPTED, CANCELLED),
    re-fetch from WB API and update status + dates.

    Returns: {synced, updated, errors, message}
    """
    # Get supplies that may have changed status
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

    updated = 0
    errors = 0

    for supply in active_supplies:
        try:
            # Fetch fresh data from WB
            wb_data = await api_client.get_fbo_supply(supply.wb_supply_id)
            if not wb_data:
                continue

            old_status = supply.wb_status
            _update_supply_from_wb(supply, wb_data)
            supply.synced_at = utcnow()
            updated += 1

            # Auto-deliver linked shipment if status changed to ACCEPTED
            if (
                old_status != WbSupplyStatus.ACCEPTED
                and supply.wb_status == WbSupplyStatus.ACCEPTED
                and supply.outbound_shipment_id
            ):
                await _auto_deliver_shipment(db, project_id, supply.outbound_shipment_id)

        except Exception as e:
            logger.warning(
                "fbo_sync.status_error",
                extra={
                    "supply_id": supply.id,
                    "error": str(e),
                },
            )
            errors += 1

    await db.commit()

    return {
        "synced": updated,
        "updated": updated,
        "errors": errors,
        "message": f"Updated {updated} supply statuses",
    }


# ─── List supplies (with search/filter/sort/pagination) ────────────────────


async def list_fbo_supplies(
    db: AsyncSession,
    project_id: int,
    *,
    search: str | None = None,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort_by: str = "created_at_wb",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[WbFboSupply], int]:
    """
    List FBO supplies with filtering, search, sorting, and pagination.

    Returns: (supplies, total_count)
    """
    base_query = select(WbFboSupply).where(
        WbFboSupply.project_id == project_id,
    )

    # Search by wb_supply_id or name
    if search:
        search_term = f"%{search}%"
        base_query = base_query.where(
            or_(
                WbFboSupply.wb_supply_id.ilike(search_term),
                WbFboSupply.name.ilike(search_term),
            )
        )

    # Filter by status
    if status:
        base_query = base_query.where(WbFboSupply.wb_status == status)

    # Filter by date range
    if date_from:
        base_query = base_query.where(
            func.date(WbFboSupply.created_at_wb) >= date_from,
        )
    if date_to:
        base_query = base_query.where(
            func.date(WbFboSupply.created_at_wb) <= date_to,
        )

    # Count total
    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Sort
    sort_column = getattr(WbFboSupply, sort_by, WbFboSupply.created_at_wb)
    if sort_order == "asc":
        base_query = base_query.order_by(sort_column.asc())
    else:
        base_query = base_query.order_by(sort_column.desc())

    # Pagination
    base_query = base_query.limit(limit).offset(offset)

    result = await db.execute(base_query)
    supplies = result.scalars().all()

    return supplies, total


# ─── Get supply items ───────────────────────────────────────────────────────


async def get_fbo_supply_items(
    db: AsyncSession,
    project_id: int,
    supply_id: int,
) -> list[WbFboSupplyItem]:
    """Get items for a specific FBO supply. Validates project_id ownership."""
    # Verify supply belongs to project
    result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.id == supply_id,
            WbFboSupply.project_id == project_id,
        )
    )
    supply = result.scalar_one_or_none()
    if not supply:
        raise ValueError("FBO Supply not found")

    result = await db.execute(
        select(WbFboSupplyItem)
        .where(
            WbFboSupplyItem.supply_id == supply_id,
        )
        .order_by(WbFboSupplyItem.id)
    )
    return result.scalars().all()


# ─── Link / Unlink supply ↔ OutboundShipment ───────────────────────────────


async def link_supply_to_shipment(
    db: AsyncSession,
    project_id: int,
    supply_id: int,
    outbound_shipment_id: int,
) -> WbFboSupply:
    """
    Link FBO supply to an OutboundShipment.
    Sets supply.outbound_shipment_id and shipment.wb_supply_id.
    """
    # Get supply
    result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.id == supply_id,
            WbFboSupply.project_id == project_id,
        )
    )
    supply = result.scalar_one_or_none()
    if not supply:
        raise ValueError("FBO Supply not found")

    # Get shipment
    result = await db.execute(
        select(OutboundShipment).where(
            OutboundShipment.id == outbound_shipment_id,
            OutboundShipment.project_id == project_id,
            OutboundShipment.is_deleted == False,  # noqa: E712
        )
    )
    shipment = result.scalar_one_or_none()
    if not shipment:
        raise ValueError("Outbound shipment not found")

    # Link both sides
    supply.outbound_shipment_id = outbound_shipment_id
    shipment.wb_supply_id = supply.wb_supply_id

    # Auto-deliver if supply already ACCEPTED
    if supply.wb_status == WbSupplyStatus.ACCEPTED:
        await _auto_deliver_shipment(db, project_id, outbound_shipment_id)

    await db.commit()
    await db.refresh(supply)
    return supply


async def unlink_supply_from_shipment(
    db: AsyncSession,
    project_id: int,
    supply_id: int,
) -> WbFboSupply:
    """Unlink FBO supply from its OutboundShipment."""
    result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.id == supply_id,
            WbFboSupply.project_id == project_id,
        )
    )
    supply = result.scalar_one_or_none()
    if not supply:
        raise ValueError("FBO Supply not found")

    if supply.outbound_shipment_id:
        # Clear shipment side
        result = await db.execute(
            select(OutboundShipment).where(
                OutboundShipment.id == supply.outbound_shipment_id,
                OutboundShipment.project_id == project_id,
            )
        )
        shipment = result.scalar_one_or_none()
        if shipment:
            shipment.wb_supply_id = None

    supply.outbound_shipment_id = None
    await db.commit()
    await db.refresh(supply)
    return supply


# ─── Private helpers ────────────────────────────────────────────────────────


def _map_wb_done_to_status(wb_supply: dict) -> str:
    """
    Map WB API v3 supply fields to status enum.
    WB list endpoint returns {done: bool, closedAt: str|null} instead of status.
    """
    done = wb_supply.get("done", False)
    closed_at = wb_supply.get("closedAt")
    if done and closed_at:
        return WbSupplyStatus.ACCEPTED
    if done and not closed_at:
        return WbSupplyStatus.CANCELLED
    return WbSupplyStatus.ACTIVE


def _parse_wb_datetime(value: str) -> datetime | None:
    """Parse WB API datetime string (ISO 8601)."""
    if not value:
        return None
    try:
        # WB returns ISO 8601: 2026-03-20T16:20:00Z or 2026-03-20T16:20:00+03:00
        value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _parse_wb_date(value: str) -> date | None:
    """Parse WB API date string."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def _create_supply_from_wb(project_id: int, wb_data: dict) -> WbFboSupply:
    """Create new WbFboSupply from WB API data."""
    return WbFboSupply(
        project_id=project_id,
        wb_supply_id=str(wb_data.get("id", "")),
        wb_status=wb_data.get("status", WbSupplyStatus.ACTIVE),
        name=wb_data.get("name"),
        created_at_wb=_parse_wb_datetime(wb_data.get("createdAt", "")) or utcnow(),
        planned_date=_parse_wb_date(wb_data.get("plannedDate")),
        actual_date=_parse_wb_date(wb_data.get("closedAt")),
        warehouse_name=wb_data.get("warehouseName"),
        synced_at=utcnow(),
    )


def _update_supply_from_wb(supply: WbFboSupply, wb_data: dict) -> None:
    """Update existing WbFboSupply from WB API data."""
    supply.wb_status = wb_data.get("status", supply.wb_status)
    supply.name = wb_data.get("name", supply.name)
    supply.planned_date = _parse_wb_date(wb_data.get("plannedDate")) or supply.planned_date
    supply.actual_date = _parse_wb_date(wb_data.get("closedAt")) or supply.actual_date
    supply.warehouse_name = wb_data.get("warehouseName", supply.warehouse_name)


async def _upsert_supply_items(
    db: AsyncSession,
    supply_id: int,
    wb_orders: list[dict],
) -> None:
    """Replace supply items with fresh data from WB API."""
    # Delete old items
    result = await db.execute(select(WbFboSupplyItem).where(WbFboSupplyItem.supply_id == supply_id))
    old_items = result.scalars().all()
    for item in old_items:
        await db.delete(item)
    await db.flush()

    # Insert new items
    for order in wb_orders:
        item = WbFboSupplyItem(
            supply_id=supply_id,
            wb_order_id=str(order.get("orderId", order.get("id", ""))),
            nm_id=order.get("nmId"),
            barcode=str(order.get("barcode", order.get("skus", [""])[0] if order.get("skus") else "")),
            article_seller=order.get("articleSeller", order.get("vendorCode")),
            product_name=order.get("productName", order.get("name")),
            quantity=order.get("quantity", 1),
            accepted_qty=order.get("acceptedQty", 0),
        )
        db.add(item)


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

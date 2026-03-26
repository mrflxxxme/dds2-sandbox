"""
FBO Supply Service — sync WB FBW supplies, list, link to outbound shipments.

Uses Supplies API (supplies-api.wildberries.ru) for FBW supplies:
- POST /api/v1/supplies — list supplies
- GET /api/v1/supplies/{id} — supply detail (warehouse, quantities)
- GET /api/v1/supplies/{id}/goods — supply items

Rate limit: 6 req/min for Suppliers API — must throttle detail/goods calls.

Responsibilities:
- Sync supplies from WB FBW API (full + status-only)
- List supplies with search/filter/sort/pagination
- Get supply items
- Link/unlink supply <-> OutboundShipment
- Auto-deliver shipment when supply is ACCEPTED
"""

import asyncio
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.integrations import SyncLog
from backend.models.warehouse import OutboundShipment, OutboundStatus
from backend.models.wb_fbo import WbFboSupply, WbFboSupplyItem, WbSupplyStatus
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

# FBW API rate limit: 6 req/min = 1 req per 10 seconds
FBW_RATE_LIMIT_DELAY = 11  # seconds between detail/goods API calls

# ─── FBW status/boxType mappings ─────────────────────────────────────────────

FBW_STATUS_MAP: dict[int, str] = {
    1: WbSupplyStatus.ACTIVE,  # Запланирована
    2: WbSupplyStatus.ON_DELIVERY,  # В пути  # noqa: RUF003
    3: WbSupplyStatus.IN_PROGRESS,  # Разгрузка
    4: WbSupplyStatus.ACCEPTED,  # Принята
    5: WbSupplyStatus.CANCELLED,  # Отменена
    6: WbSupplyStatus.ACCEPTED,  # Частично принята -> ACCEPTED
}

FBW_BOX_TYPE_MAP: dict[int, str] = {
    0: None,  # virtual (no physical box)
    1: "Короб",  # noqa: RUF001
    2: "Короб",  # noqa: RUF001
    5: "Монопаллет",
    6: "Суперсейф",
}


def _map_fbw_status(status_id: int, accepted_qty: int | None = None) -> str:
    """Map FBW statusID to WbSupplyStatus enum value.

    WB API returns statusID=5 (CANCELLED) even when items were partially accepted.
    If accepted_qty > 0 and statusID=5, treat as ACCEPTED (partially received).
    """
    if status_id == 5 and accepted_qty and accepted_qty > 0:
        return WbSupplyStatus.ACCEPTED
    return FBW_STATUS_MAP.get(status_id, WbSupplyStatus.ACTIVE)


def _map_fbw_box_type(box_type_id: int | None) -> str | None:
    """Map FBW boxTypeID to human-readable cargo type."""
    if box_type_id is None:
        return None
    return FBW_BOX_TYPE_MAP.get(box_type_id, f"Тип {box_type_id}")


# ─── Sync: full ─────────────────────────────────────────────────────────────


async def sync_fbo_supplies(
    db: AsyncSession,
    project_id: int,
    api_client,
    integration_id: int,
) -> dict:
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
    api_client,
    max_calls: int = 30,
) -> dict:
    """
    Background enrichment: fetch detail (warehouse, qty) for supplies missing it.
    Called after sync or on-demand. Rate-limited to max_calls per run.

    Returns: {enriched, errors}
    """
    # Find supplies needing detail (no warehouse_name)
    result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.project_id == project_id,
            WbFboSupply.warehouse_name.is_(None),
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
    api_client,
    integration_id: int,
) -> dict:
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


# ─── List supplies (with search/filter/sort/pagination) ────────────────────


async def list_warehouses(
    db: AsyncSession,
    project_id: int,
) -> list[str]:
    """Get unique warehouse names for filter dropdown."""
    result = await db.execute(
        select(WbFboSupply.warehouse_name)
        .where(
            WbFboSupply.project_id == project_id,
            WbFboSupply.warehouse_name.is_not(None),
        )
        .distinct()
        .order_by(WbFboSupply.warehouse_name)
    )
    return [row[0] for row in result.fetchall()]


async def list_fbo_supplies(
    db: AsyncSession,
    project_id: int,
    *,
    search: str | None = None,
    status: str | None = None,
    warehouse: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort_by: str = "created_at_wb",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    exclude_with_assembly: bool = False,
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

    # Filter by status (comma-separated for multi-status filter)
    if status:
        statuses = [s.strip() for s in status.split(",")]
        if len(statuses) == 1:
            base_query = base_query.where(WbFboSupply.wb_status == statuses[0])
        else:
            base_query = base_query.where(WbFboSupply.wb_status.in_(statuses))

    # Filter by warehouse
    if warehouse:
        base_query = base_query.where(WbFboSupply.warehouse_name == warehouse)

    # Filter by date range
    if date_from:
        base_query = base_query.where(
            func.date(WbFboSupply.created_at_wb) >= date_from,
        )
    if date_to:
        base_query = base_query.where(
            func.date(WbFboSupply.created_at_wb) <= date_to,
        )

    # Exclude supplies that already have an active assembly request
    if exclude_with_assembly:
        active_statuses = [
            AssemblyStatus.PENDING,
            AssemblyStatus.IN_PROGRESS,
            AssemblyStatus.READY,
            AssemblyStatus.VEHICLE_ASSIGNED,
            AssemblyStatus.SHIPPED,
        ]
        active_assembly_ids = select(AssemblyRequest.wb_fbo_supply_id).where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status.in_(active_statuses),
        )
        base_query = base_query.where(WbFboSupply.id.not_in(active_assembly_ids))

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

    # Enrich with assembly request info (single query for the page)
    supply_ids = [s.id for s in supplies]
    assembly_map: dict[int, tuple[int, str, str]] = {}
    if supply_ids:
        active_statuses = [
            AssemblyStatus.PENDING,
            AssemblyStatus.IN_PROGRESS,
            AssemblyStatus.READY,
            AssemblyStatus.VEHICLE_ASSIGNED,
            AssemblyStatus.SHIPPED,
        ]
        asm_result = await db.execute(
            select(
                AssemblyRequest.wb_fbo_supply_id,
                AssemblyRequest.id,
                AssemblyRequest.number,
                AssemblyRequest.status,
            ).where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.is_deleted.is_(False),
                AssemblyRequest.status.in_(active_statuses),
                AssemblyRequest.wb_fbo_supply_id.in_(supply_ids),
            )
        )
        for row in asm_result.all():
            assembly_map[row.wb_fbo_supply_id] = (row.id, row.number, row.status)

    # Build enriched dicts
    enriched = []
    for s in supplies:
        d = {c.key: getattr(s, c.key) for c in WbFboSupply.__table__.columns}
        asm = assembly_map.get(s.id)
        d["assembly_request_id"] = asm[0] if asm else None
        d["assembly_request_number"] = asm[1] if asm else None
        d["assembly_request_status"] = asm[2] if asm else None
        enriched.append(d)

    return enriched, total


# ─── Get supply items ───────────────────────────────────────────────────────


async def get_fbo_supply_items(
    db: AsyncSession,
    project_id: int,
    supply_id: int,
    api_client=None,
) -> list[WbFboSupplyItem]:
    """
    Get items for a specific FBO supply. Validates project_id ownership.
    Lazy-load: if no items in DB and api_client provided, fetch from WB API.
    """
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
    items = result.scalars().all()

    # Lazy-load from WB API if no items cached
    if not items and api_client and supply.wb_supply_id.isdigit():
        try:
            wb_id_int = int(supply.wb_supply_id)
            goods = await api_client.get_fbw_supply_goods(
                wb_id_int,
                limit=100,
                offset=0,
            )
            if goods:
                await _upsert_supply_items_fbw(db, supply.id, wb_id_int, goods)

            # Also fetch detail (warehouse_name, qty) if missing
            if not supply.warehouse_name:
                try:
                    detail = await api_client.get_fbw_supply_detail(wb_id_int)
                    if detail:
                        _update_supply_from_fbw_detail(supply, detail)
                except Exception as e:
                    logger.warning(
                        "fbo_items.detail_fetch_error",
                        extra={"supply_id": supply_id, "error": str(e)},
                    )

            await db.commit()
            # Re-fetch from DB
            result = await db.execute(
                select(WbFboSupplyItem).where(WbFboSupplyItem.supply_id == supply_id).order_by(WbFboSupplyItem.id)
            )
            items = result.scalars().all()
        except Exception as e:
            logger.warning(
                "fbo_items.lazy_load_error",
                extra={"supply_id": supply_id, "error": str(e)},
            )

    return items


# ─── Link / Unlink supply <-> OutboundShipment ───────────────────────────────


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


# ─── Private helpers: FBW field mapping ────────────────────────────────────


def _parse_wb_datetime(value: str) -> datetime | None:
    """Parse WB API datetime string (ISO 8601)."""
    if not value:
        return None
    try:
        value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _parse_wb_date(value: str) -> date | None:
    """Parse WB API date string (take first 10 chars as YYYY-MM-DD)."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def _create_supply_from_fbw_list(project_id: int, wb_data: dict) -> WbFboSupply:
    """
    Create new WbFboSupply from FBW list endpoint data.

    FBW list fields: supplyID, preorderID, createDate, supplyDate,
    factDate, updatedDate, statusID, boxTypeID, phone
    """
    status_id = wb_data.get("statusID", 1)
    return WbFboSupply(
        project_id=project_id,
        wb_supply_id=str(wb_data.get("supplyID", "")),
        wb_status=_map_fbw_status(status_id),
        name=f"FBW-{wb_data.get('supplyID', '')}",
        created_at_wb=_parse_wb_datetime(wb_data.get("createDate", "")) or utcnow(),
        planned_date=_parse_wb_date(wb_data.get("supplyDate")),
        actual_date=_parse_wb_date(wb_data.get("factDate")),
        cargo_type=_map_fbw_box_type(wb_data.get("boxTypeID")),
        synced_at=utcnow(),
    )


def _update_supply_from_fbw_list(supply: WbFboSupply, wb_data: dict) -> None:
    """
    Update existing WbFboSupply from FBW list endpoint data.
    Only updates fields available in the list response.
    """
    status_id = wb_data.get("statusID")
    if status_id is not None:
        # Use existing accepted_qty from DB to detect partial acceptance
        supply.wb_status = _map_fbw_status(status_id, supply.accepted_qty)

    planned = _parse_wb_date(wb_data.get("supplyDate"))
    if planned:
        supply.planned_date = planned

    actual = _parse_wb_date(wb_data.get("factDate"))
    if actual:
        supply.actual_date = actual

    box_type = _map_fbw_box_type(wb_data.get("boxTypeID"))
    if box_type:
        supply.cargo_type = box_type

    # Update total_qty if available in list response
    qty = wb_data.get("quantity") or wb_data.get("totalQuantity")
    if qty and int(qty) > 0:
        supply.total_qty = int(qty)


def _update_supply_from_fbw_detail(supply: WbFboSupply, detail: dict) -> None:
    """
    Update WbFboSupply with data from FBW detail endpoint.
    Detail adds: warehouseName, quantity, acceptedQuantity, acceptanceCost.
    """
    wh_name = detail.get("warehouseName")
    if wh_name:
        supply.warehouse_name = wh_name

    qty = detail.get("quantity")
    if qty is not None:
        supply.total_qty = qty

    accepted = detail.get("acceptedQuantity")
    if accepted is not None:
        supply.accepted_qty = accepted


async def _upsert_supply_items_fbw(
    db: AsyncSession,
    supply_id: int,
    wb_supply_id_int: int,
    goods: list[dict],
) -> None:
    """
    Replace supply items with fresh data from FBW goods endpoint.

    FBW goods fields: barcode, vendorCode, nmID, quantity,
    readyForSaleQuantity, acceptedQuantity, techSize, color
    """
    # Delete old items
    result = await db.execute(select(WbFboSupplyItem).where(WbFboSupplyItem.supply_id == supply_id))
    old_items = result.scalars().all()
    for item in old_items:
        await db.delete(item)
    await db.flush()

    # Insert new items from FBW goods
    for good in goods:
        barcode = str(good.get("barcode", ""))
        vendor_code = good.get("vendorCode", "")

        item = WbFboSupplyItem(
            supply_id=supply_id,
            # FBW has no order IDs — use composite key
            wb_order_id=f"{wb_supply_id_int}_{barcode}",
            nm_id=good.get("nmID"),
            barcode=barcode,
            article_seller=vendor_code,
            # FBW goods API has no product_name — use vendorCode as fallback
            product_name=vendor_code or None,
            quantity=good.get("quantity", 0),
            accepted_qty=good.get("acceptedQuantity") or 0,
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


async def _auto_deliver_assembly(
    db: AsyncSession,
    project_id: int,
    fbo_supply_id: int,
) -> None:
    """Auto-deliver AssemblyRequest when linked FBO supply is ACCEPTED."""
    from backend.models.assembly import AssemblyRequest, AssemblyStatus, AssemblyStatusHistory
    from backend.utils.time import utcnow as _utcnow

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
            assembly_request_id=assembly_req.id,
            old_status=AssemblyStatus.SHIPPED,
            new_status=AssemblyStatus.DELIVERED,
            changed_at=_utcnow(),
            changed_by="system",
            comment="WB FBO ACCEPTED",
        )
        db.add(history)
        logger.info(
            "fbo_sync.auto_deliver_assembly",
            extra={"assembly_id": assembly_req.id, "project_id": project_id},
        )

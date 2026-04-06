"""
FBO Supply mappers — pure transformations, no DB access.

Constants and helpers for mapping WB FBW API data to internal models.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.wb_fbo import WbFboSupply, WbFboSupplyItem, WbSupplyStatus
from backend.utils.time import utcnow

# FBW API rate limit: 6 req/min = 1 req per 10 seconds
FBW_RATE_LIMIT_DELAY = 11  # seconds between detail/goods API calls

# ─── FBW status/boxType mappings ─────────────────────────────────────────────

FBW_STATUS_MAP: dict[int, str] = {
    1: WbSupplyStatus.ACTIVE,  # Запланирована
    2: WbSupplyStatus.ON_DELIVERY,  # В пути  # noqa: RUF003
    3: WbSupplyStatus.IN_PROGRESS,  # Разгрузка
    4: WbSupplyStatus.ACCEPTED,  # Принята
    5: WbSupplyStatus.ACCEPTED,  # Принята (WB docs say "Отменена", but real data = accepted)
    6: WbSupplyStatus.ACCEPTED,  # Частично принята -> ACCEPTED
}

FBW_BOX_TYPE_MAP: dict[int, str | None] = {
    0: None,  # virtual (no physical box)
    1: "Короб",  # noqa: RUF001
    2: "Короб",  # noqa: RUF001
    5: "Монопаллет",
    6: "Суперсейф",
}


def _map_fbw_status(status_id: int, accepted_qty: int | None = None) -> str:
    """Map FBW statusID to WbSupplyStatus enum value."""
    return FBW_STATUS_MAP.get(status_id, WbSupplyStatus.ACTIVE)


def _map_fbw_box_type(box_type_id: int | None) -> str | None:
    """Map FBW boxTypeID to human-readable cargo type."""
    if box_type_id is None:
        return None
    return FBW_BOX_TYPE_MAP.get(box_type_id, f"Тип {box_type_id}")


# ─── Parse helpers ───────────────────────────────────────────────────────────


def _parse_wb_datetime(value: str | None) -> datetime | None:
    """Parse WB API datetime string (ISO 8601)."""
    if not value:
        return None
    try:
        value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _parse_wb_date(value: str | None) -> date | None:
    """Parse WB API date string (take first 10 chars as YYYY-MM-DD)."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


# ─── Create/update from API data ─────────────────────────────────────────────


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

    # Recalculate status using detail's statusID with updated accepted_qty
    detail_status_id = detail.get("statusID")
    if detail_status_id is not None:
        supply.wb_status = _map_fbw_status(detail_status_id, supply.accepted_qty)
    elif accepted is not None and accepted > 0 and supply.wb_status == WbSupplyStatus.CANCELLED:
        # Fallback: no statusID in detail, but accepted_qty > 0 means partial acceptance
        supply.wb_status = WbSupplyStatus.ACCEPTED


async def _upsert_supply_items_fbw(
    db: AsyncSession,
    project_id: int,
    supply_id: int,
    wb_supply_id_int: int,
    goods: list[dict],
) -> None:
    """
    Replace supply items with fresh data from FBW goods endpoint.

    FBW goods fields: barcode, vendorCode, nmID, quantity,
    readyForSaleQuantity, acceptedQuantity, techSize, color
    """
    from sqlalchemy import select

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
            project_id=project_id,
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

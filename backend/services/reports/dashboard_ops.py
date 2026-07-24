"""
Reports — operational dashboard (cross-domain «action-center» counters).

Powers the main dashboard «Требуют внимания» block: pending payment approvals,
FBO orphan/partial acceptances, WB returns awaiting receipt, sync errors,
unlinked FF requests and supply-chain vehicles in transit. Each source is
best-effort — one failing domain degrades to 0 instead of breaking the whole
dashboard (and the session is rolled back so a poisoned transaction does not
cascade into the next source).

All queries share a single AsyncSession, so they run SEQUENTIALLY — a session is
not safe for concurrent operations (no asyncio.gather here).

Short TTL (60s): action items must stay fresh; the key carries only project_id so
prefix-invalidation may not pinpoint it, and TTL bounds staleness instead.
"""

import logging
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.models.enums import VehicleStatus

logger = logging.getLogger("dds.dashboard_ops")

_VEHICLE_IN_TRANSIT = (
    VehicleStatus.SHIPPED.value,
    VehicleStatus.CUSTOMS.value,
    VehicleStatus.DISPATCHED.value,
)


async def _safe(db: AsyncSession, factory: Callable[[], Awaitable[Any]], label: str) -> Optional[Any]:
    """Run one domain source; on failure log, roll back the session, return None."""
    try:
        return await factory()
    except Exception:
        logger.exception("dashboard_ops: %s failed", label)
        try:
            await db.rollback()
        except Exception:
            logger.exception("dashboard_ops: rollback after %s failed", label)
        return None


@cached(prefix="reports:dashboard_ops", ttl=60)
async def get_dashboard_operations(db: AsyncSession, project_id: int) -> dict:
    """Action-center counters for the main dashboard. Every domain is best-effort."""
    out: dict[str, Any] = {
        "payments_pending": 0,
        "fbo_orphans": 0,
        "fbo_partial": 0,
        "fbo_excess": 0,
        "returns_pending": 0,
        "returns_soon_expire": 0,
        "sync_errors_24h": 0,
        "ff_unlinked": 0,
        "vehicles_in_transit": 0,
        "vehicles_forming": 0,
        "supply_items_in_transit": 0,
        "supply_amount_cny": 0.0,
    }

    # 1. Pending payment approvals (PENDING_REVIEW; «общие» project_id IS NULL included).
    async def _payments() -> int:
        from backend.models.payment_request import PaymentRequestStatus
        from backend.services.payment_request_service import PaymentRequestService

        _, total, _, _ = await PaymentRequestService(db).list_requests(
            project_id, status=PaymentRequestStatus.PENDING_REVIEW.value, limit=1
        )
        return int(total)

    pending = await _safe(db, _payments, "payments_pending")
    if pending is not None:
        out["payments_pending"] = pending

    # 2. FBO supplies — orphan ACCEPTED (no assembly link) + partial / excess acceptance.
    async def _fbo() -> dict:
        from backend.services.fbo_supply import get_fbo_summary

        return await get_fbo_summary(db, project_id)

    fbo = await _safe(db, _fbo, "fbo")
    if fbo:
        out["fbo_orphans"] = int(fbo.get("accepted_without_assembly", 0))
        out["fbo_partial"] = int(fbo.get("accepted_partial", 0))
        out["fbo_excess"] = int(fbo.get("accepted_excess", 0))

    # 3. WB returns awaiting receipt / expiring soon.
    async def _returns() -> dict:
        from backend.services import wb_returns_service

        return await wb_returns_service.get_summary(db, project_id)

    ret = await _safe(db, _returns, "returns")
    if ret:
        out["returns_pending"] = int(ret.get("picked_up_pending_receipt", 0))
        out["returns_soon_expire"] = int(ret.get("soon_expires", 0))

    # 4. Sync errors over the last 24h.
    async def _sync() -> dict:
        from backend.services import monitoring_service

        return await monitoring_service.get_sync_overview(db, project_id)

    mon = await _safe(db, _sync, "monitoring")
    if mon:
        out["sync_errors_24h"] = int(mon.get("total_errors_24h", 0))

    # 5. Unlinked FF (assembly) requests across integrated warehouses.
    async def _ff() -> dict:
        from backend.services import fulfillment_service

        return await fulfillment_service.get_overview(db, project_id)

    ff = await _safe(db, _ff, "fulfillment")
    if ff:
        out["ff_unlinked"] = int(sum(w.get("requests_unlinked", 0) for w in ff.get("warehouses", [])))

    # 6. Supply-chain vehicles in transit / forming.
    async def _supply() -> dict:
        from backend.services.supply_chain import vehicle_delivery

        return await vehicle_delivery.get_supply_chain_overview(db, project_id)

    sc = await _safe(db, _supply, "supply_chain")
    if sc:
        by_status = sc.get("vehicles_by_status", {}) or {}
        out["vehicles_in_transit"] = int(sum(by_status.get(s, 0) for s in _VEHICLE_IN_TRANSIT))
        out["vehicles_forming"] = int(by_status.get(VehicleStatus.FORMING.value, 0))
        out["supply_items_in_transit"] = int(sc.get("total_items", 0))
        out["supply_amount_cny"] = float(sc.get("total_amount_cny", 0.0))

    return out

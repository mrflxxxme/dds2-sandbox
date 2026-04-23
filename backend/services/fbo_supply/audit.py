"""
FBO Supply audit trail — append-only log of user actions on WbFboSupply.

Records ARCHIVE / UNARCHIVE / RETURN / EXCESS / REASSIGN (user-initiated)
so the user can review a supply's history and revert reversible ones.

Only ARCHIVE/UNARCHIVE/REASSIGN are safely revertible — RETURN/EXCESS
touch physical stock (InboundReceipt/OutboundShipment) and would require
compensating receipts, which is better handled manually to keep the
audit unambiguous.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.wb_fbo import FboAuditAction, FboSupplyAudit, WbFboSupply
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

# Actions the UI is allowed to revert (safely undoable — no physical stock).
REVERTIBLE_ACTIONS = {
    FboAuditAction.ARCHIVE.value,
    FboAuditAction.UNARCHIVE.value,
    FboAuditAction.REASSIGN.value,
}


async def log_action(
    db: AsyncSession,
    *,
    project_id: int,
    supply_id: int,
    action: FboAuditAction | str,
    payload: dict[str, Any] | None = None,
    user_id: int | None = None,
    reverted_audit_id: int | None = None,
) -> FboSupplyAudit:
    """Append a row to the audit log. Caller is responsible for db.commit()."""
    row = FboSupplyAudit(
        project_id=project_id,
        supply_id=supply_id,
        action=action.value if isinstance(action, FboAuditAction) else action,
        payload=payload or {},
        user_id=user_id,
        reverted_audit_id=reverted_audit_id,
    )
    db.add(row)
    await db.flush()
    return row


async def get_audit_trail(
    db: AsyncSession,
    project_id: int,
    supply_id: int,
) -> list[dict[str, Any]]:
    """History for a single supply — newest first."""
    result = await db.execute(
        select(FboSupplyAudit)
        .where(
            FboSupplyAudit.project_id == project_id,
            FboSupplyAudit.supply_id == supply_id,
        )
        .order_by(FboSupplyAudit.created_at.desc(), FboSupplyAudit.id.desc())
    )
    rows = list(result.scalars().all())
    return [
        {
            "id": r.id,
            "supply_id": r.supply_id,
            "action": r.action,
            "payload": r.payload or {},
            "user_id": r.user_id,
            "created_at": r.created_at,
            "reverted_audit_id": r.reverted_audit_id,
            "reverted_at": r.reverted_at,
            "revertible": r.action in REVERTIBLE_ACTIONS and r.reverted_at is None and r.reverted_audit_id is None,
        }
        for r in rows
    ]


async def get_audit_list(
    db: AsyncSession,
    project_id: int,
    *,
    action: str | None = None,
    supply_wb_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """
    Global audit feed across all supplies in the project — newest first.
    Includes supply.wb_supply_id so the UI can link to the source supply.
    """
    from backend.models.auth import User
    from backend.models.wb_fbo import WbFboSupply

    base_query = (
        select(
            FboSupplyAudit,
            WbFboSupply.wb_supply_id,
            WbFboSupply.warehouse_name,
            User.username,
        )
        .join(WbFboSupply, WbFboSupply.id == FboSupplyAudit.supply_id)
        .outerjoin(User, User.id == FboSupplyAudit.user_id)
        .where(FboSupplyAudit.project_id == project_id)
    )

    if action:
        base_query = base_query.where(FboSupplyAudit.action == action)
    if supply_wb_id:
        base_query = base_query.where(WbFboSupply.wb_supply_id == supply_wb_id)

    from sqlalchemy import func

    count_q = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    base_query = (
        base_query.order_by(FboSupplyAudit.created_at.desc(), FboSupplyAudit.id.desc()).limit(limit).offset(offset)
    )
    result = await db.execute(base_query)

    entries = []
    for row in result.all():
        r: FboSupplyAudit = row[0]
        entries.append(
            {
                "id": r.id,
                "supply_id": r.supply_id,
                "supply_wb_id": row[1],
                "warehouse_name": row[2],
                "user_id": r.user_id,
                "username": row[3],
                "action": r.action,
                "payload": r.payload or {},
                "created_at": r.created_at,
                "reverted_audit_id": r.reverted_audit_id,
                "reverted_at": r.reverted_at,
                "revertible": r.action in REVERTIBLE_ACTIONS and r.reverted_at is None and r.reverted_audit_id is None,
            }
        )
    return entries, int(total)


async def revert_audit_entry(
    db: AsyncSession,
    project_id: int,
    audit_id: int,
    user_id: int | None = None,
) -> dict[str, Any]:
    """
    Revert a prior audit row. Only ARCHIVE/UNARCHIVE/REASSIGN actions are
    supported — RETURN/EXCESS are rejected because they touched physical stock.

    Marks the original row as reverted and creates a new REVERT row pointing
    back to it (append-only log).
    """
    original_result = await db.execute(
        select(FboSupplyAudit).where(
            FboSupplyAudit.id == audit_id,
            FboSupplyAudit.project_id == project_id,
        )
    )
    original = original_result.scalar_one_or_none()
    if original is None:
        raise ValueError("Audit entry not found")
    if original.reverted_at is not None:
        raise ValueError("Audit entry is already reverted")
    if original.action == FboAuditAction.REVERT.value:
        raise ValueError("Cannot revert a revert — redo a prior action instead")
    if original.action not in REVERTIBLE_ACTIONS:
        raise ValueError(f"Action {original.action} is not revertible — handle manually")

    supply_result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.id == original.supply_id,
            WbFboSupply.project_id == project_id,
        )
    )
    supply = supply_result.scalar_one_or_none()
    if supply is None:
        raise ValueError("Supply not found")

    payload = original.payload or {}

    # Inverse logic per action
    if original.action == FboAuditAction.ARCHIVE.value:
        # Payload: {"new": True | False | None, "old": ...}
        supply.is_archived = payload.get("old")
    elif original.action == FboAuditAction.UNARCHIVE.value:
        supply.is_archived = payload.get("old")
    elif original.action == FboAuditAction.REASSIGN.value:
        # Payload: {"target_supply_id": N, "reassigned_qty": Q, "target_items": [(item_id, delta)]}
        target_id = payload.get("target_supply_id")
        qty = int(payload.get("reassigned_qty") or 0)
        item_deltas = payload.get("target_items") or []
        if target_id is None or qty <= 0:
            raise ValueError("Reassign payload is incomplete — cannot revert")
        target_result = await db.execute(
            select(WbFboSupply).where(
                WbFboSupply.id == target_id,
                WbFboSupply.project_id == project_id,
            )
        )
        target = target_result.scalar_one_or_none()
        if target is None:
            raise ValueError("Target supply no longer exists — cannot revert reassign")
        # Roll back target counters + items
        target.accepted_qty = max(0, int(target.accepted_qty or 0) - qty)
        from backend.models.wb_fbo import WbFboSupplyItem

        for item_id, delta in item_deltas:
            item_result = await db.execute(
                select(WbFboSupplyItem).where(
                    WbFboSupplyItem.id == item_id,
                    WbFboSupplyItem.project_id == project_id,
                )
            )
            item = item_result.scalar_one_or_none()
            if item is not None:
                item.accepted_qty = max(0, int(item.accepted_qty or 0) - int(delta))
        # Return source to its prior archive state (None/True/False) — reassign
        # always set is_archived=True on source, so we must restore the snapshot
        # instead of hard-setting False (otherwise an originally-archived supply
        # pops back to the active list after revert).
        supply.reassigned_to_supply_id = None
        if "old_is_archived" in payload:
            supply.is_archived = payload.get("old_is_archived")
        else:
            # Legacy rows (before old_is_archived was logged) — safe default
            supply.is_archived = None

    original.reverted_at = utcnow()
    revert_row = await log_action(
        db,
        project_id=project_id,
        supply_id=original.supply_id,
        action=FboAuditAction.REVERT,
        payload={"action": original.action, "original_audit_id": original.id},
        user_id=user_id,
        reverted_audit_id=original.id,
    )
    await db.commit()
    return {
        "audit_id": original.id,
        "revert_id": revert_row.id,
        "action": original.action,
    }

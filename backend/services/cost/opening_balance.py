# ruff: noqa: RUF002, RUF003
"""
Opening-balance CRUD + valuation-facing analytics wrappers for the cost page.

Thin service layer over :mod:`backend.services.cost.valuation` (the heavy,
already-tested engine — never edited here). The two ``get_*`` analytics
wrappers are ``@cached`` so the router stays a pure HTTP layer; the CRUD
helpers mutate ``CostOpeningBalance`` / ``CostOrder`` and leave cache
invalidation to the router (iron rule #7).

SKU key everywhere = ``lower(article_seller)`` — the same key the valuation
engine uses. Opening balances are keyed by ``barcode`` (unique per project).
"""

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy import func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.bdr_loaders import SKU_TRIM_CHARS
from backend.cache import cached
from backend.models.cost import CostOpeningBalance, CostOrder, Nomenclature
from backend.services.cost.valuation import (
    compute_project_valuation,
    slice_window,
)
from backend.services.settings_service import get_valuation_method
from backend.utils.time import utcnow

logger = logging.getLogger("dds.cost.opening_balance")

ZERO = Decimal("0")
# Project-wide distortion summary is capped to keep the payload (and the FE
# table) bounded; the rest of the long tail carries negligible distortion.
_SUMMARY_CAP = 200


def _f2(x: "Decimal | float | int | None") -> float:
    """Money → JSON number, rounded to 2dp. The engine keeps full-precision
    Decimal for the consumers; the analytics API serialises rounded floats so
    the frontend ``formatNumber`` (ru-RU) renders them — mirrors cost_history."""
    return round(float(x or 0), 2)


# ── opening balance CRUD ─────────────────────────────────────────────────────


async def get_opening_balances(db: AsyncSession, pid: int) -> list[dict]:
    """Current opening balances for a project, newest barcode first."""
    rows = await db.execute(
        select(CostOpeningBalance)
        .where(CostOpeningBalance.project_id == pid)
        .order_by(CostOpeningBalance.barcode)
    )
    out: list[dict] = []
    for ob in rows.scalars():
        out.append(
            {
                "id": ob.id,
                "barcode": ob.barcode,
                "qty": ob.qty,
                "unit_cost": ob.unit_cost,
                "as_of_date": ob.as_of_date.isoformat() if ob.as_of_date else None,
                "note": ob.note,
            }
        )
    return out


async def upsert_opening_balances(db: AsyncSession, pid: int, items: list) -> int:
    """Upsert opening-balance rows (unique on project_id+barcode).

    ``items`` is a list of OpeningBalanceItem (pydantic) or dicts with keys
    ``barcode, qty, unit_cost, as_of_date, note``. Returns the count upserted.
    """
    # Normalize input to plain dicts and dedup by barcode (last wins) — avoids
    # touching the same (project_id, barcode) slot twice in one transaction.
    by_barcode: dict[str, dict] = {}
    for it in items:
        d = it if isinstance(it, dict) else it.model_dump()
        bc = (d.get("barcode") or "").strip()
        if not bc:
            continue
        by_barcode[bc] = d

    if not by_barcode:
        return 0

    existing = await db.execute(
        select(CostOpeningBalance).where(
            CostOpeningBalance.project_id == pid,
            CostOpeningBalance.barcode.in_(list(by_barcode.keys())),
        )
    )
    existing_by_bc = {ob.barcode: ob for ob in existing.scalars()}

    count = 0
    for bc, d in by_barcode.items():
        qty = int(d.get("qty") or 0)
        unit_cost = Decimal(str(d.get("unit_cost") or 0))
        as_of_date = d.get("as_of_date")
        note = d.get("note")
        row = existing_by_bc.get(bc)
        if row is not None:
            row.qty = qty
            row.unit_cost = unit_cost
            row.as_of_date = as_of_date
            row.note = note
            row.updated_at = utcnow()
        else:
            db.add(
                CostOpeningBalance(
                    project_id=pid,
                    barcode=bc,
                    qty=qty,
                    unit_cost=unit_cost,
                    as_of_date=as_of_date,
                    note=note,
                )
            )
        count += 1

    await db.commit()
    logger.info("Upserted %d opening balances for project %s", count, pid)
    return count


async def update_order_arrival_date(
    db: AsyncSession, pid: int, order_no: str, d: date | None
) -> bool:
    """Set ``actual_arrival_date`` on a cost order. Returns False if not found."""
    row = await db.execute(
        select(CostOrder).where(
            CostOrder.project_id == pid,
            CostOrder.order_no == order_no,
            CostOrder.is_deleted == False,  # noqa: E712
        )
    )
    order = row.scalar_one_or_none()
    if order is None:
        return False
    order.actual_arrival_date = d
    await db.commit()
    logger.info("Set actual_arrival_date=%s on order %s (project %s)", d, order_no, pid)
    return True


# ── valuation-facing analytics (cached) ──────────────────────────────────────


def _v_to_sku_dict(v: dict) -> dict:
    """Map an engine V-entry to the SkuValuationSchema-shaped dict. Money is
    rounded to 2dp floats (the engine's Decimals serialise as strings, which the
    frontend ``formatNumber`` can't format)."""
    monthly = []
    for mk in sorted(v.get("monthly", {})):
        b = v["monthly"][mk]
        sold = int(b.get("sold", 0))
        returned = int(b.get("returned", 0))
        monthly.append(
            {
                "month": mk,
                "qty": sold - returned,
                "sold": sold,
                "returned": returned,
                "cogs_fifo": _f2(b.get("cogs_fifo", 0)),
                "cogs_avg": _f2(b.get("cogs_avg", 0)),
                "cogs_moving": _f2(b.get("cogs_moving", 0)),
            }
        )
    ledger = [{**lay, "unit_cost": _f2(lay.get("unit_cost", 0))} for lay in v.get("ledger", [])]
    return {
        "sku": v["sku"],
        "barcode": v.get("barcode"),
        "article_wb": v.get("article_wb"),
        "brand": v.get("brand", ""),
        "subject": v.get("subject", ""),
        "lifetime_avg": _f2(v.get("lifetime_avg", 0)),
        "eff_now": {k: _f2(x) for k, x in v.get("eff_now", {}).items()},
        "on_hand_qty": int(v.get("on_hand_qty", 0)),
        "on_hand_value": {k: _f2(x) for k, x in v.get("on_hand_value", {}).items()},
        "total_received": int(v.get("total_received", 0)),
        "total_sold": int(v.get("total_sold", 0)),
        "is_estimated": bool(v.get("is_estimated", False)),
        "warnings": v.get("warnings", []),
        "ledger": ledger,
        "monthly": monthly,
    }


async def _resolve_sku_for_barcode(db: AsyncSession, pid: int, barcode: str) -> str | None:
    """lower(article_seller) for a project barcode, or None if unknown."""
    row = await db.execute(
        select(sa_func.lower(sa_func.btrim(Nomenclature.article_seller, SKU_TRIM_CHARS)))
        .where(
            Nomenclature.project_id == pid,
            Nomenclature.barcode == barcode,
            Nomenclature.article_seller.isnot(None),
        )
        .limit(1)
    )
    return row.scalar_one_or_none()


@cached(prefix="reports:cost_valuation", ttl=300)
async def get_sku_valuation(
    db: AsyncSession,
    project_id: int,
    barcode: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict | None:
    """Full per-SKU valuation analytics for one barcode (drill-down).

    ``date_from``/``date_to`` participate in the @cached key only — the full
    lifetime ledger/monthly is returned regardless (the FE slices client-side).
    Returns a SkuValuationSchema-shaped dict, or ``None`` if the barcode is
    unknown / the SKU has no valuation data.
    """
    sku = await _resolve_sku_for_barcode(db, project_id, barcode)
    if not sku:
        return None
    valuation = await compute_project_valuation(db, project_id, skus={sku})
    v = valuation.get(sku)
    if v is None:
        return None
    return _v_to_sku_dict(v)


@cached(prefix="reports:cost_valuation", ttl=300)
async def get_valuation_summary(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
) -> list[dict]:
    """Project-wide distortion summary for [date_from, date_to].

    Per SKU: window COGS under the project's configured method vs. under FIFO;
    ``distortion = cogs_fifo − cogs_current``. Sorted by |distortion| desc,
    capped to the top ``_SUMMARY_CAP`` rows (logged if truncated).
    """
    method = await get_valuation_method(db, project_id)
    valuation = await compute_project_valuation(db, project_id)

    current = slice_window(valuation, date_from, date_to, method)
    fifo = slice_window(valuation, date_from, date_to, "fifo")

    rows: list[dict] = []
    for sku, cur in current.items():
        v = valuation.get(sku, {})
        cogs_current = cur["cogs"]
        cogs_fifo = fifo.get(sku, {}).get("cogs", ZERO)
        distortion = cogs_fifo - cogs_current
        rows.append(
            {
                "sku": sku,
                "barcode": v.get("barcode"),
                "article_wb": v.get("article_wb"),
                "brand": v.get("brand", ""),
                "subject": v.get("subject", ""),
                "qty": int(cur["qty"]),
                "cogs_current": _f2(cogs_current),
                "cogs_fifo": _f2(cogs_fifo),
                "distortion": _f2(distortion),
                "is_estimated": bool(v.get("is_estimated", False)),
            }
        )

    rows.sort(key=lambda r: abs(r["distortion"]), reverse=True)
    if len(rows) > _SUMMARY_CAP:
        logger.info(
            "Valuation summary truncated for project %s: %d rows -> top %d",
            project_id,
            len(rows),
            _SUMMARY_CAP,
        )
        rows = rows[:_SUMMARY_CAP]
    return rows

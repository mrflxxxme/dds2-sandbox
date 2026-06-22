# ruff: noqa: RUF002, RUF003
"""
Cost valuation engine — period- and stock-aware COGS.

Replaces the global lifetime weighted-average (``bdr_loaders.load_avg_costs``)
with a path-dependent valuation that respects batch arrival order and stock
depletion. Three methods, selected per project (``ProjectSetting``
``valuation_method``):

  lifetime_avg — current behaviour: one global Σ(total_rub·qty)/Σqty per SKU,
                 applied to every period. Kept for zero-regression parity.
  fifo         — sales consume the oldest available batch first (FIFO queue);
                 period COGS = cost of the specific layers actually sold.
  moving_avg   — running weighted average of units on hand, recomputed on each
                 receipt; sales costed at the running average.

FIFO/moving_avg are PATH-DEPENDENT: a period's COGS depends on the full sales
history before it. The engine therefore replays the whole timeline per SKU from
inception (or from the opening balance), then slices to the requested window.

Physical depletion (which batch is sold / still on shelf — the ledger) is ALWAYS
FIFO and method-independent; the *method* only changes the money attached to
each consumed/remaining unit. All three method COGS are computed in a single
pass so the UI can toggle between them with no re-query.

SKU key = ``lower(article_seller)`` — the same key the legacy avg uses, so
``lifetime_avg`` reproduces (FORMING-excluded) ``load_avg_costs`` bit-for-bit.
``barcode``/``nm_id`` are carried only as display metadata.
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy import func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.cost import CostOpeningBalance, CostOrder, CostOrderItem, Nomenclature
from backend.models.enums import VehicleStatus
from backend.models.integrations import WbCostOverride
from backend.models.wb_finance import WbFinanceRow

logger = logging.getLogger("dds.cost_valuation")

ZERO = Decimal("0")
VALUATION_METHODS = ("lifetime_avg", "fifo", "moving_avg")
DEFAULT_METHOD = "lifetime_avg"

_SALE = "Продажа"
_RETURN = "Возврат"


def _mk(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _D(x: object) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x or 0))


# ── data structures ──────────────────────────────────────────────────────────


@dataclass
class _Batch:
    """One inventory layer (a cost_order_items row, opening balance, or a
    restored return). ``remaining``/``consumed`` mutate during the FIFO walk."""

    order_no: str
    avail_date: date
    qty: int
    unit_cost: Decimal
    arrival_known: bool
    remaining: int = 0
    consumed: int = 0


@dataclass
class _SkuRaw:
    batches: list[_Batch] = field(default_factory=list)
    sales: list[tuple[date, int]] = field(default_factory=list)  # +qty sale, magnitude
    returns: list[tuple[date, int]] = field(default_factory=list)  # +qty return, magnitude


# ── loaders ──────────────────────────────────────────────────────────────────


async def _load_batches(db: AsyncSession, pid: int) -> dict[str, list[_Batch]]:
    """Active, non-FORMING cost_order_items as inventory layers, keyed by
    lower(article_seller). avail_date = actual_arrival_date → ship_date →
    created_at (arrival_known False on the latter two)."""
    rows = await db.execute(
        select(
            sa_func.lower(CostOrderItem.article_seller).label("sku"),
            CostOrderItem.qty,
            CostOrderItem.total_rub,
            CostOrder.order_no,
            CostOrder.actual_arrival_date,
            CostOrder.ship_date,
            CostOrder.created_at,
        )
        .join(CostOrder, CostOrderItem.order_no == CostOrder.order_no)
        .where(
            CostOrder.project_id == pid,
            CostOrder.is_deleted == False,  # noqa: E712
            CostOrderItem.is_deleted == False,  # noqa: E712
            CostOrderItem.article_seller.isnot(None),
            CostOrderItem.total_rub.isnot(None),
            CostOrderItem.qty > 0,
            or_(CostOrder.status.is_(None), CostOrder.status != VehicleStatus.FORMING),
        )
    )
    out: dict[str, list[_Batch]] = {}
    for r in rows:
        if not r.sku:
            continue
        arrival_known = r.actual_arrival_date is not None
        avail = r.actual_arrival_date or r.ship_date or (r.created_at.date() if r.created_at else date.min)
        out.setdefault(r.sku, []).append(
            _Batch(
                order_no=r.order_no,
                avail_date=avail,
                qty=int(r.qty),
                unit_cost=_D(r.total_rub),
                arrival_known=arrival_known,
            )
        )
    return out


async def _load_sales(db: AsyncSession, pid: int, sales_from: date | None = None) -> dict[str, _SkuRaw]:
    """Sales/returns from wb_finance_rows, keyed by lower(sa_name).

    ``sales_from`` drops events before that date — used when early periods have
    incomplete data (e.g. Dec 2025): those sales must not deplete the FIFO queue.
    """
    rows = await db.execute(
        select(
            sa_func.lower(WbFinanceRow.sa_name).label("sku"),
            sa_func.coalesce(WbFinanceRow.sale_dt, WbFinanceRow.rr_dt, WbFinanceRow.date_from).label("d"),
            WbFinanceRow.doc_type_name,
            WbFinanceRow.quantity,
        ).where(
            WbFinanceRow.project_id == pid,
            WbFinanceRow.doc_type_name.in_((_SALE, _RETURN)),
            WbFinanceRow.supplier_oper_name.in_((_SALE, _RETURN)),
            WbFinanceRow.sa_name.isnot(None),
        )
    )
    out: dict[str, _SkuRaw] = {}
    for r in rows:
        if not r.sku or not r.d or not r.quantity:
            continue
        if sales_from is not None and r.d < sales_from:
            continue
        raw = out.setdefault(r.sku, _SkuRaw())
        q = int(r.quantity)
        if q <= 0:
            continue
        if r.doc_type_name == _SALE:
            raw.sales.append((r.d, q))
        else:
            raw.returns.append((r.d, q))
    return out


async def _load_meta(db: AsyncSession, pid: int) -> dict[str, dict]:
    """Per-sku display metadata + override seed, from nomenclature joined to
    WbCostOverride. Keyed by lower(article_seller)."""
    rows = await db.execute(
        select(
            sa_func.lower(Nomenclature.article_seller).label("sku"),
            Nomenclature.barcode,
            Nomenclature.article_wb,
            Nomenclature.brand,
            Nomenclature.subject,
            WbCostOverride.cost_price,
        )
        .outerjoin(
            WbCostOverride,
            (WbCostOverride.nm_id == Nomenclature.article_wb) & (WbCostOverride.project_id == pid),
        )
        .where(Nomenclature.project_id == pid, Nomenclature.article_seller.isnot(None))
    )
    out: dict[str, dict] = {}
    for r in rows:
        if not r.sku or r.sku in out:
            continue
        out[r.sku] = {
            "barcode": r.barcode,
            "article_wb": r.article_wb,
            "brand": r.brand or "",
            "subject": r.subject or "",
            "override": _D(r.cost_price) if r.cost_price else ZERO,
        }
    return out


async def _load_opening(db: AsyncSession, pid: int, bc_to_sku: dict[str, str]) -> dict[str, _Batch]:
    """Opening balance per sku (resolved barcode→sku)."""
    rows = await db.execute(
        select(CostOpeningBalance).where(
            CostOpeningBalance.project_id == pid,
            CostOpeningBalance.qty > 0,
        )
    )
    out: dict[str, _Batch] = {}
    for ob in rows.scalars():
        sku = bc_to_sku.get(ob.barcode)
        if not sku:
            continue
        out[sku] = _Batch(
            order_no="OPENING",
            avail_date=ob.as_of_date or date.min,
            qty=int(ob.qty),
            unit_cost=_D(ob.unit_cost),
            arrival_known=True,
        )
    return out


# ── the walk ─────────────────────────────────────────────────────────────────


def _walk(batches: list[_Batch], raw: _SkuRaw, global_avg: Decimal, seed: Decimal) -> dict:
    """Pure-sequence FIFO with lazy receipt. Physical depletion is FIFO and
    method-independent; COGS for all three methods is computed in one pass.

    Batches are ordered by arrival; sales (and returns) are processed in DATE
    order for monthly bucketing, but a sale consumes the oldest unsold batch
    regardless of that batch's arrival date — the standard accounting FIFO,
    robust to missing/unreliable ``actual_arrival_date`` and to sales that
    predate the first recorded batch. Batches are pulled into the queue
    on-demand to cover cumulative sales (the moving-average pool fills the same
    way), so leftover batches stay valued at their own cost.
    """
    batches = sorted(batches, key=lambda b: (b.avail_date, b.order_no))
    for b in batches:
        b.remaining = b.qty
        b.consumed = 0

    events: list[tuple[date, int, int]] = [(d, 1, q) for d, q in raw.sales]
    events += [(d, 2, q) for d, q in raw.returns]
    events.sort(key=lambda e: (e[0], e[1]))

    monthly: dict[str, dict] = {}

    def bucket(mk: str) -> dict:
        return monthly.setdefault(
            mk, {"sold": 0, "returned": 0, "cogs_fifo": ZERO, "cogs_avg": ZERO, "cogs_moving": ZERO}
        )

    queue: deque[_Batch] = deque()
    consumed_stack: list[list] = []  # [batch_ref, qty, unit_cost] — for return restore
    extra_batches: list[_Batch] = []  # synthetic return layers w/o matching consumption

    p = 0  # next un-pulled batch index
    avail = 0  # units currently in the FIFO queue
    mv_qty = 0
    mv_cost = ZERO
    warnings: list[str] = []
    is_estimated = False

    avg_unit = global_avg if global_avg > 0 else seed
    if any(not b.arrival_known for b in batches):
        warnings.append("Часть партий без даты прихода — порядок по дате отгрузки")

    def pull_to_cover(need_units: int) -> None:
        """Receive batches (in arrival order) until the queue can cover need_units.
        Updates the moving-average pool as each batch is received."""
        nonlocal p, avail, mv_qty, mv_cost
        while avail < need_units and p < len(batches):
            b = batches[p]
            p += 1
            queue.append(b)
            avail += b.remaining
            new_qty = mv_qty + b.remaining
            if new_qty > 0:
                mv_cost = (mv_qty * mv_cost + b.remaining * b.unit_cost) / Decimal(new_qty)
            mv_qty = new_qty

    for _d, kind, payload in events:
        mk = _mk(_d)
        bk = bucket(mk)
        if kind == 1:  # sale
            need = int(payload)
            pull_to_cover(need)
            bk["sold"] += need
            bk["cogs_avg"] += Decimal(need) * avg_unit
            bk["cogs_moving"] += Decimal(need) * (mv_cost if mv_cost > 0 else seed)
            mv_qty -= need
            while need > 0 and queue:
                layer = queue[0]
                take = min(need, layer.remaining)
                bk["cogs_fifo"] += Decimal(take) * layer.unit_cost
                consumed_stack.append([layer, take, layer.unit_cost])
                layer.remaining -= take
                layer.consumed += take
                avail -= take
                need -= take
                if layer.remaining == 0:
                    queue.popleft()
            if need > 0:  # oversold beyond all recorded batches — value at seed
                bk["cogs_fifo"] += Decimal(need) * seed
                is_estimated = True
        else:  # return
            give = int(payload)
            bk["returned"] += give
            bk["cogs_avg"] -= Decimal(give) * avg_unit
            bk["cogs_moving"] -= Decimal(give) * (mv_cost if mv_cost > 0 else seed)
            mv_qty += give
            while give > 0 and consumed_stack:
                ref, cq, cc = consumed_stack[-1]
                take = min(give, cq)
                bk["cogs_fifo"] -= Decimal(take) * cc
                was_zero = ref.remaining == 0
                ref.remaining += take
                ref.consumed -= take
                avail += take
                if was_zero:  # had been fully consumed/popped — back to the front
                    queue.appendleft(ref)
                consumed_stack[-1][1] -= take
                give -= take
                if consumed_stack[-1][1] == 0:
                    consumed_stack.pop()
            if give > 0:  # return with no matching consumption — restore at seed
                synth = _Batch("RETURN", _d, give, seed, True, remaining=give)
                extra_batches.append(synth)
                queue.appendleft(synth)
                avail += give
                bk["cogs_fifo"] -= Decimal(give) * seed
                is_estimated = True

    # Pull any remaining batches into the moving pool so ending inventory is
    # valued at the final running average (standard moving-average close).
    while p < len(batches):
        b = batches[p]
        p += 1
        new_qty = mv_qty + b.remaining
        if new_qty > 0:
            mv_cost = (mv_qty * mv_cost + b.remaining * b.unit_cost) / Decimal(new_qty)
        mv_qty = new_qty

    all_batches = batches + extra_batches
    on_hand_qty = sum(b.remaining for b in all_batches)
    fifo_on_hand = sum((Decimal(b.remaining) * b.unit_cost for b in all_batches), ZERO)
    eff_fifo = next((b.unit_cost for b in all_batches if b.remaining > 0), avg_unit)

    return {
        "monthly": monthly,
        "ledger": [
            {
                "order_no": b.order_no,
                "avail_date": b.avail_date.isoformat() if b.avail_date != date.min else None,
                "qty": b.qty,
                "remaining": b.remaining,
                "consumed": b.qty - b.remaining,
                "unit_cost": b.unit_cost,
                "arrival_known": b.arrival_known,
            }
            for b in all_batches
        ],
        "on_hand_qty": on_hand_qty,
        "on_hand_value": {
            "fifo": fifo_on_hand,
            "moving_avg": Decimal(on_hand_qty) * mv_cost,
            "lifetime_avg": Decimal(on_hand_qty) * avg_unit,
        },
        "eff_now": {"fifo": eff_fifo, "moving_avg": mv_cost or seed, "lifetime_avg": avg_unit},
        "is_estimated": is_estimated,
        "warnings": warnings,
    }


# ── public API ───────────────────────────────────────────────────────────────


def _cogs_key(method: str) -> str:
    return {"fifo": "cogs_fifo", "moving_avg": "cogs_moving", "lifetime_avg": "cogs_avg"}[method]


async def compute_project_valuation(
    db: AsyncSession, pid: int, skus: set[str] | None = None, sales_from: date | None = None
) -> dict[str, dict]:
    """Full per-SKU valuation (all three methods) for the project. Heavy — cache
    at the caller. ``skus`` (lowercased article_seller) narrows the work.

    ``sales_from`` (or, when None, the project's ``valuation_start_date`` setting)
    drops sales before that date so incomplete early periods (e.g. Dec 2025) do
    not skew COGS or deplete the FIFO queue.
    """
    if sales_from is None:
        from backend.services.settings_service import get_valuation_start_date

        sales_from = await get_valuation_start_date(db, pid)
    batches_by_sku = await _load_batches(db, pid)
    sales_by_sku = await _load_sales(db, pid, sales_from)
    meta = await _load_meta(db, pid)
    bc_to_sku = {m["barcode"]: sku for sku, m in meta.items() if m.get("barcode")}
    opening = await _load_opening(db, pid, bc_to_sku)

    keys = set(batches_by_sku) | set(sales_by_sku) | set(opening)
    if skus is not None:
        keys &= {s.lower() for s in skus}

    result: dict[str, dict] = {}
    for sku in keys:
        batches = list(batches_by_sku.get(sku, []))
        if sku in opening:
            batches.append(opening[sku])
        raw = sales_by_sku.get(sku, _SkuRaw())
        recv_qty = sum(b.qty for b in batches)
        recv_sum = sum((Decimal(b.qty) * b.unit_cost for b in batches), ZERO)
        global_avg = (recv_sum / Decimal(recv_qty)) if recv_qty else ZERO
        m = meta.get(sku, {})
        # Fallback cost for shortfall units (sold but no recorded batch) — SAME
        # priority as the reports' lifetime path (load_avg_costs → WbCostOverride):
        # the batch-derived average wins when batches exist, so untraceable units
        # degrade to the lifetime basis and don't fabricate FIFO "distortion".
        # The manual override only applies when there are no batches at all.
        seed = global_avg if global_avg > 0 else (m.get("override") or ZERO)
        walked = _walk(batches, raw, global_avg, seed)
        sold = sum(b["sold"] for b in walked["monthly"].values()) - sum(
            b["returned"] for b in walked["monthly"].values()
        )
        walked.update(
            {
                "sku": sku,
                "barcode": m.get("barcode"),
                "article_wb": m.get("article_wb"),
                "brand": m.get("brand", ""),
                "subject": m.get("subject", ""),
                "lifetime_avg": global_avg,
                "total_received": recv_qty,
                "total_sold": sold,
            }
        )
        if walked["is_estimated"] and sold > recv_qty:
            walked["warnings"].append(
                f"Заведено приходов {recv_qty} шт при {sold} проданных — {sold - recv_qty} шт "
                "оценены по средней (нет данных о закупке). Заведите приходы или опенинг-баланс."
            )
        if sales_from is not None:
            walked["warnings"].append(
                f"Продажи учитываются с {sales_from.isoformat()} — остаток может быть завышен "
                "(ранние продажи исключены, опенинг-баланс не задан)"
            )
        result[sku] = walked
    return result


def slice_window(valuation: dict[str, dict], d_from: date, d_to: date, method: str) -> dict[str, dict]:
    """Aggregate per-SKU COGS for the [d_from, d_to] window under ``method``.

    Returns ``{sku: {qty, cogs, eff_cost, monthly: {mk: {qty, cogs}}}}`` where
    qty is net (sold − returned) — same contract OPIU/BDR expect.
    """
    ck = _cogs_key(method)
    lo, hi = _mk(d_from), _mk(d_to)
    out: dict[str, dict] = {}
    for sku, v in valuation.items():
        net_qty = 0
        cogs = ZERO
        months: dict[str, dict] = {}
        for mk, b in v["monthly"].items():
            if mk < lo or mk > hi:
                continue
            nq = b["sold"] - b["returned"]
            net_qty += nq
            cogs += b[ck]
            months[mk] = {"qty": nq, "cogs": b[ck]}
        if not months:
            continue
        eff = (cogs / Decimal(net_qty)) if net_qty else ZERO
        out[sku] = {"qty": net_qty, "cogs": cogs, "eff_cost": eff, "monthly": months}
    return out

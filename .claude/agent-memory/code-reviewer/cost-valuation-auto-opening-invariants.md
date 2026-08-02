---
name: cost-valuation-auto-opening-invariants
description: Two-pass auto-opening in services/cost/valuation.py — which money contours are provably invariant, which shift, and the zero-cost/first-batch traps to re-check on every edit
metadata:
  type: project
---

`_walk_auto_opening` (added 2026-07-28, feat branch `feat/main-tree-port`) replays `_walk` twice:
pass 1 measures `oversold_units`, pass 2 re-runs with a synthetic `AUTO-OPENING` layer
(`qty=shortfall`, `unit_cost=first batch`, `avail_date=date.min`) at the head of the queue.

**Verified by fuzz (4000 random cases: date.min ties, zero-cost batches, returns before/after the
deficit) — these hold and are worth re-checking after any edit to `_walk`:**
- `oversold` in `_walk` == max prefix deficit `max_t(sales_cum − returns_cum − Q)`; therefore
  pass 2 ALWAYS converges to `oversold_units == 0` (no third pass, no residual).
- `on_hand_qty` is identical in both passes (`Q + R − S + oversold` — the shortfall cancels), so
  warehouse/stock quantities never move because of auto-opening.
- `cogs_avg` (lifetime contour) and per-month `sold`/`returned` are bit-identical across passes.
- `cogs_fifo` and the whole moving-average contour DO shift by
  `shortfall × (seed − first_batch_cost)` — that is the entire money delta into БДР/ОПиУ.
- Tie at `date.min`: `"AUTO-OPENING" < "OPENING"`, so a real `CostOpeningBalance` layer never
  outranks the synthetic one; a real batch whose `order_no` sorts before `AUTO-` (e.g. `AA-1`) does
  — harmless, since the synthetic layer is priced at exactly that batch's cost.

**Traps this design leaves open (check them in any follow-up review):**
- `min(batches, …).unit_cost` has no `> 0` guard, unlike every other zero-check in the file
  (`global_avg if global_avg > 0 else …`, `mv_cost if mv_cost > 0 else seed`). A zero-cost first
  layer is reachable: the FE opening-balance editor sends `Number(cost) || 0` and
  `CostOpeningBalance.unit_cost` is `default=0`; `_load_opening` filters only `qty > 0`.
- Auto-opening fires on ANY deficit, including "purchase order not entered yet" (deficit at the END
  of the timeline), always pricing at the OLDEST cost → systematically lower COGS / higher profit.
- Consumers of `eff_now[method]` (`warehouse_stock_engine`, `funnel/stock_costs`, `funnel/sync`)
  drop entries `<= 0`, so a 0 price degrades to override→stock rather than zeroing stock value;
  БДР/ОПиУ use `cost_with_fallback`, which only rescues a FULL zero, not an understated price.

**Why:** money-critical path — COGS feeds БДР/ОПиУ, `eff_now` prices warehouse stock. The engine
only runs for projects whose `valuation_method != lifetime_avg`, which bounds the blast radius.
**How to apply:** when reviewing `valuation.py`, don't re-derive the two-pass algebra — check the
five invariants above still hold and that the traps are still (un)addressed.

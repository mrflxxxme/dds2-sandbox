---
name: pre-dist-overcommit-invariant
description: Why pre-distribution ("Распределить машину") over-commit guard is vehicle-pool-based, not WarehouseStock-based — key to reviewing dedup/fold correctness
metadata:
  type: project
---

Pre-distribution (`backend/services/assembly/pre_distribution.py`, `create_pre_distribution`) over-commit guard is scoped to the **vehicle pool**, not real `WarehouseStock`.

- `gross` = `_vehicle_gross_by_barcode` (Σ qty on the CostOrder / vehicle).
- `reserved` = `_reserved_by_barcode` = Σ over ALL non-CANCELLED сборки with `source_vehicle_id == vehicle_id` (includes both PRE_DISTRIBUTED and IN_PROGRESS — after receipt acceptance the flip PRE_DISTRIBUTED→IN_PROGRESS keeps `source_vehicle_id`).
- Guard: `req_qty <= gross - reserved`.

**Why:** PRE_DISTRIBUTED сборки reserve against goods on a vehicle in transit — there is no real stock yet. Real `WarehouseStock` is only deducted at ship-time (`_validate_stock_for_ship` in `crud.py`), not at IN_PROGRESS. The PRE_DISTRIBUTED→IN_PROGRESS flip (`_advance_pre_distribution_assemblies`) is a pure status+history change, no stock mutation.

**How to apply:** When reviewing dedup/fold changes here, folding items into an IN_PROGRESS сборки does NOT over-commit real stock — the guard runs against the vehicle pool and `reserved` already counts the IN_PROGRESS items, so a fold is arithmetically identical to a fresh create. The IN_PROGRESS-fold path IS reachable (vehicle can sit in CUSTOMS — a valid `PRE_DIST_VEHICLE_STATUSES` — while its goods are physically received and accepted, flipping its сборки to IN_PROGRESS; a later pre-dist pass on the still-CUSTOMS vehicle then folds into IN_PROGRESS). See `warehouse_inbound.py` ~line 379: the flip hooks on `cost_order_id` receipt acceptance, deliberately NOT on DISPATCHED→DELIVERED.

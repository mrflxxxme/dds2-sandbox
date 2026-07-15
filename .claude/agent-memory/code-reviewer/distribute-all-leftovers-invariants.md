---
name: distribute-all-leftovers-invariants
description: Where the real over-commit/idempotency guards live for the "Распределить все остатки" button (pre-dist + draft matrix) — check these layers, not the button arithmetic
metadata:
  type: project
---

"Распределить все остатки" exists in TWO screens, both delegating to `frontend-react/src/lib/assembly/leftoverAlloc.ts` (`buildLeftoverWeights` → need>0 else stock+asm+transit presence, ⛔-closed filtered via isOpen; `allocateWholeBoxes` = largest-remainder / Hamilton, tie-break frac→weight→name-`localeCompare('ru')`).

**The button-level arithmetic is NOT the authoritative over-commit guard — the pipeline caps are:**
- Pre-dist (`pre-dist/page.tsx` `distributeAllLeftovers`): pins into `cellEdits`; the real cap is `buildPinnedRows` (`preDistribution.ts` ~L313): `cap = floor(available_qty)`, per-wh `units = min(boxes*ppb, floor(cap/ppb)*ppb)`, `cap -= units`. So Σpins×ppb ≤ floor(avail) regardless of seed. Base pin = existing pin `prev` OR `plan.cellByBc` floored to boxes; `manualTopUp` is deleted for pinned barcodes AND `autoTopUp` filters out any barcode in `cellEdits` → dozabor cannot resurrect.
- Draft (`DraftMatrixView.tsx` `distributeAllLeftovers`): the real cap is `applyDraftCellEdit` (`draftDistribution.ts` L302): `if (total > prevTotal && total > availForBox) return null` where `availForBox = ΣffAvail − keptUnits`. One-pass accumulation is safe because it reassigns `rows`/`prebook` per SKU and `applyDraftCellEdit` always returns `prebook: []` (prebook folded into the merged BOX row); `shipNow` is read from the current-cycle rows+prebook.

**Idempotency is by REPLACEMENT, not incremental mutation.** Both handlers recompute the full allocation from a captured base state (`draftRows`/`cellEdits`+`plan` from render scope) and `setState` the result. A rapid double-click (stale closure) recomputes the identical result → same setState. After re-render the free leftover is 0 (all distributed, only sub-box remainder < ppb) → `boxes=0` → "нечего распределять". So no accumulation on repeat.

**How to apply:** When reviewing changes to this feature, verify the two cap layers above still hold and that base-pin seeding still avoids double-counting `manualTopUp`/prebook. `allocateWholeBoxes` Σ==boxes exactly (remainder = Σfrac is integer < entries; loop drives `used` to `boxes`, no wrap, no infinite loop). Growth-aware speed (`warehouse_need_service._growth_factor`, g≥1) only ever raises demand toward recent windows — conservative, never rations. See [[pre-dist-overcommit-invariant]] for the backend vehicle-pool guard.

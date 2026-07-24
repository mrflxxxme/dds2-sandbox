---
name: assembly-draft-two-gates
description: How the two server-side gates in update_draft (subtract_in_transit + clamp_to_ff_capacity) interact — needed to review assembly_draft_service without re-deriving
metadata:
  type: project
---

`update_draft` in `backend/services/assembly_draft_service.py` runs TWO independent server-side gates, in order, on every autosave PUT:

1. `_subtract_in_transit` — DELTA gate, **target axis** (nm_id × WB-destination). Subtracts active-request transit only from the plan's GROWTH vs baseline (already-saved plan). Has baseline precisely to avoid eroding saved plans on repeated autosaves. Demotes cut rows to prebook (`_demote_directions_to_prebook`) to preserve "rows = whole pallets".
2. `_clamp_to_ff_capacity` — CAPACITY gate, **source axis** (barcode × FF-warehouse). Clamps Σ plan src to `cap = max(0, WarehouseStock − active-requests-in-assembly − get_drafts_reserved(other drafts))`. Deliberately NO baseline (kills the "храповик"/ratchet: a phantom SKU that dropped out of the frontend calc). Cuts prebook→rows→as_is/manual in that order. Does NOT demote to prebook.

**Why:** These operate on DIFFERENT axes (dest vs source), so they do not double-cut: clamp reads the post-subtract distribution, and subtract can only shrink the plan. Confirmed sound. Capacity semantics mirror `warehouse_need_service` (`rf_avail = max(0, stock − in_assembly)`); statuses PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED (no SHIPPED, no PRE_DISTRIBUTED for the source-side in_assembly). `WarehouseStock`/`Nomenclature` have NO SoftDeleteMixin — no is_deleted filter needed (not an iron-rule miss).

**How to apply:** When reviewing changes here, check (a) the two gates stay axis-disjoint, (b) clamp's `cap` still mirrors need-calc semantics, (c) the no-baseline choice on clamp is intentional (silent truncation of over-capacity plans is the feature, not a bug). Residual risks worth re-flagging if touched: clamp runs `get_drafts_reserved` (loads ≤500 drafts' JSONB) on every autosave; and clamp truncates already-saved plans with only a server log, no draft-history event.

---
name: warehouse-need-invariants
description: warehouse_need_service.py load-bearing invariants — total_need mode-invariance (HIGH-2), eff-maps vs raw-maps, three demand horizons
metadata:
  type: project
---

`backend/services/warehouse_need_service.py` (`get_warehouse_need`) carries three
subtle, load-bearing invariants a reviewer must re-check on any edit:

**Fact 1 — HIGH-2: `total_need` must be mode-invariant** (actual vs
localization_optimized vs hypothetical). Procurement KPI cannot depend on display
mode. Preserved by: summing all orders per-nm regardless of warehouse mapping
(`raw_total_by_nm` = Σ wh_orders_map + unmapped_demand), and computing delivery
lead weights from the **raw physical** `warehouseName` (`raw_wh_orders_for_lead`),
not the mode-dependent mapped warehouse.
**Why:** breaking it makes the same SKU show different buy-quantities per mode.
**How to apply:** any new term entering `total_need` must be built from
mode-invariant sources. Watch the leak: `lead_by_nm` resolves lead via
`lead_time_per_wb`, whose key set is `wh_names_to_show` — and that IS
mode-dependent (actual = stock warehouses only; loc = mapped-order ∪ stock). So
`lead_by_nm` is only mode-invariant for raw warehouses that carry stock; a
stockless-but-loc-mapping-target warehouse leaks mode-variance into `total_need`.

**Fact 2 — eff-maps (raw×growth) vs raw-maps: demand QUANTITY must use eff,
membership/ranking must use raw.** `wh_orders_eff`/`unmapped_eff` (raw × per-nm
growth factor g≥1) feed every demand-quantity site (per-cell need, only_available
cap, greedy 4.6 gross_wh, unmapped_gross). Raw maps stay for: nm/warehouse-set
membership, main-warehouse ranking (`wh_total_orders`), `avg_daily_base` reporting,
lead weights.
**Why:** flat window average under-provisions growing SKUs up to 2× (prod
2026-07-14, ШК 2043788816553).
**How to apply:** grep `wh_orders_map`/`unmapped_demand` on any edit — a demand
computation reading the raw map is a bug; a membership/ranking read is correct.

**Fact 3 — THREE distinct demand horizons, do not conflate:**
- per-cell need & greedy gross_wh: `supply_days + lead_time_per_wb[wh]` (per-wh lead)
- global cap / total_need: `supply_days + lead_by_nm[nm]` (demand-weighted lead)
- greedy 4.6 localization BASE (`demand_by_okrug`) was historically `supply_days`
  only ("база локализации, а не план поставки") — but as of commit d226a2d the
  code switched gross_wh to include lead while the comment (~lines 976-979) still
  says supply-only. Contradiction unresolved; verify intent before trusting either.

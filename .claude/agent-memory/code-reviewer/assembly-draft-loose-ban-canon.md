---
name: assembly-draft-loose-ban-canon
description: Assembly-draft normalizer "россыпь запрещена всем" canon (2026-07-08) + why the trim newcomer-whole-box branch is currently unwired end-to-end
metadata:
  type: project
---

Canon (user, 2026-07-08): draft→shipment ships ONLY whole boxes — россыпь (loose, sub-box) forbidden for EVERYONE, newcomers included. `NormalizeDraftCtx.isNewcomer` was removed; `normalizeDraft` no longer special-cases newcomers. A row without kratnost (neither global `ppbOf` nor per-FF `ppbAt`) is dropped to `dropped`→prebook, not shipped. `as_is` ("Оставить так") bypasses only the pallet-trim and only for rows WITH known kratnost (`hasAnyPpb` gate in `normalizeDraft.ts`).

**Non-obvious wiring gap (verify before trusting the canon line):** `trimLinesToWholePallets` (`assemblyPreview.ts`) has an `isNew`+`boxOf` branch that keeps whole boxes for a geometry-less newcomer (floor to FF box multiple). The canon/learnings claim "новинка без габаритов С кратностью едет целыми коробами". But **no production path exercises it**:
- `normalizeDraft` and `consolidatePrebookWholePallets` call `buildPreviewLines(rows, new Set())` → `isNew` always false → branch never keeps.
- `DraftPreview.tsx` passes a real `newcomerNmIds` to `buildPreviewLines` but calls `trimLinesToWholePallets(rawLines, uppForCell)` WITHOUT `boxOf`, and discards the trim result (`wholeOnly=false`).
- Only `assemblyPreviewTrim.test.ts` passes both boxOf + newcomer lines → branch is unit-tested in isolation only.

Net effect: a newcomer with ppb but no box dimensions is DROPPED to prebook (same as before this change — not a regression; normalizeDraft always dropped it). Conservation (Σkept+Σdropped==Σin) and idempotency hold. This "capability exists in trim but isn't threaded through the authoritative normalizer" pattern is the thing to flag when reviewing this area.

**How to apply:** When reviewing changes here, trace the `newcomerSet` arg to `buildPreviewLines` AND the `boxOf` arg to `trimLinesToWholePallets` together — the newcomer-whole-box behavior only manifests when BOTH are supplied by the same caller. See also [[pre-dist-overcommit-invariant]] for the backend guard on the same feature.

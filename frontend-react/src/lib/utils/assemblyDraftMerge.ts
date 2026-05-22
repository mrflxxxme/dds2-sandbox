/**
 * assemblyDraftMerge — helpers for detecting duplicate source→target lanes
 * across open AssemblyDrafts.
 *
 * A draft "covers" lane (ff_id, wb_name) when ∃ row with src[ff_id]>0 AND
 * tgt[wb_name]>0.  Lanes appearing in ≥2 drafts are "duplicates" — they would
 * produce separate AssemblyRequests on commit that could be consolidated.
 *
 * pieces is a best-effort total: Σ tgt[wb] across rows where src[ff]>0.
 * For single-source drafts (the typical case) this is exact; for multi-source
 * drafts it may slightly over-count.  The preview page shows exact numbers
 * after merge.
 */

import type { AssemblyDraft } from '@/types/api';

export interface DuplicateLane {
  ffId: number;
  ffName: string;
  wbName: string;
  /** IDs of drafts that cover this lane. */
  draftIds: number[];
  /** Best-effort piece count (Σ tgt[wb] over involved drafts). */
  pieces: number;
  /** Per-draft best-effort piece count: draftId → pieces. */
  piecesPerDraft: Record<number, number>;
}

/**
 * Detect source→target lanes covered by ≥2 drafts.
 *
 * @param drafts         Open (non-deleted) drafts to scan.
 * @param nameById       Map from FF-warehouse id → display name.
 */
export function findDuplicateLanes(
  drafts: AssemblyDraft[],
  nameById: (id: number) => string,
): DuplicateLane[] {
  if (drafts.length < 2) return [];

  type LaneAccum = { ffId: number; wbName: string; draftIds: number[]; piecesPerDraft: Map<number, number> };
  const laneMap = new Map<string, LaneAccum>();

  for (const draft of drafts) {
    const rows = draft.distribution.rows ?? [];
    // Collect lanes active in this draft with best-effort piece counts
    const draftLanes = new Map<string, { ffId: number; wbName: string; pcs: number }>();

    for (const row of rows) {
      const src = row.src ?? {};
      const tgt = row.tgt ?? {};
      for (const [ffStr, srcQty] of Object.entries(src)) {
        if ((srcQty ?? 0) <= 0) continue;
        for (const [wb, tgtQty] of Object.entries(tgt)) {
          if ((tgtQty ?? 0) <= 0) continue;
          const key = `${ffStr}→${wb}`;
          const existing = draftLanes.get(key);
          if (existing) {
            existing.pcs += tgtQty;
          } else {
            draftLanes.set(key, { ffId: parseInt(ffStr, 10), wbName: wb, pcs: tgtQty });
          }
        }
      }
    }

    for (const [laneKey, { ffId, wbName, pcs }] of draftLanes) {
      const accum = laneMap.get(laneKey);
      if (accum) {
        accum.draftIds.push(draft.id);
        accum.piecesPerDraft.set(draft.id, pcs);
      } else {
        const piecesPerDraft = new Map<number, number>();
        piecesPerDraft.set(draft.id, pcs);
        laneMap.set(laneKey, { ffId, wbName, draftIds: [draft.id], piecesPerDraft });
      }
    }
  }

  const result: DuplicateLane[] = [];
  for (const { ffId, wbName, draftIds, piecesPerDraft } of laneMap.values()) {
    if (draftIds.length < 2) continue;
    const pieces = [...piecesPerDraft.values()].reduce((a, b) => a + b, 0);
    result.push({
      ffId,
      ffName: nameById(ffId),
      wbName,
      draftIds,
      pieces,
      piecesPerDraft: Object.fromEntries(piecesPerDraft),
    });
  }

  return result;
}

/**
 * Given a set of selected draft ids and detected duplicate lanes, return the
 * lanes that will actually consolidate (both draft ids in the selection).
 */
export function consolidatingLanes(
  selected: Set<number>,
  duplicates: DuplicateLane[],
): DuplicateLane[] {
  return duplicates.filter(lane => lane.draftIds.filter(id => selected.has(id)).length >= 2);
}

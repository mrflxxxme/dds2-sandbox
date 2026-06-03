/**
 * assemblyDraftReconcile — guard against an autosave resurrecting committed rows.
 *
 * After a partial commit, `commit_draft` removes the committed rows from the
 * draft. A stale distribute page (restored from bfcache, browser-back, or a
 * second tab) may still hold those rows in React state; its debounced autosave
 * would PUT them back and resurrect committed lanes → duplicate shipment.
 *
 * Reconciling the local rows against the authoritative server draft before
 * saving drops the rows that no longer exist server-side, while keeping local
 * edits (src/tgt values) on the rows that survive.
 */

import type { AssemblyDraftRow } from '@/types/api';

/** Identity of a distribute row: (nm_id, package_type). Mirrors `commit_draft`
 *  grouping and the page's dedupe; an unset package_type defaults to BOX. */
export function draftRowKey(r: Pick<AssemblyDraftRow, 'nm_id' | 'package_type'>): string {
    return `${r.nm_id}-${r.package_type || 'BOX'}`;
}

/**
 * Drop locally-held rows that the server draft no longer contains.
 *
 * Rows are matched by (nm_id, package_type). Surviving rows are returned as-is
 * (local edits preserved), order preserved. An empty `serverRows` (e.g. after a
 * full commit) drops everything — nothing to resurrect.
 */
export function dropCommittedRows(
    local: AssemblyDraftRow[],
    serverRows: AssemblyDraftRow[],
): AssemblyDraftRow[] {
    const live = new Set(serverRows.map(draftRowKey));
    return local.filter(r => live.has(draftRowKey(r)));
}

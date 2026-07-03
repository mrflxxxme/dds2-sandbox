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

/** Identity of a distribute row: (nm_id, package_type, barcode). Mirrors the
 *  backend `_dedupe_rows`/`_merge_rows` 3-tuple key and the page's dedupe; an
 *  unset package_type defaults to BOX. Barcode MUST be in the key — one nm_id can
 *  carry several barcodes (size variants; all article-less cards share nm_id=0),
 *  and matching by nm_id alone would drop a live barcode on reconcile. */
export function draftRowKey(
    r: Pick<AssemblyDraftRow, 'nm_id' | 'package_type'> & { barcode?: string | null },
): string {
    return `${r.nm_id}-${r.package_type || 'BOX'}-${r.barcode || ''}`;
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

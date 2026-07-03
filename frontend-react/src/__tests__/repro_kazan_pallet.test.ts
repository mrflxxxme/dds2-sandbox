/**
 * РЕПРО (временный диагностический тест, живые фикстуры из стенда 2026-07-03):
 * «✂ На ФФ» / «Предзаявка» одной моно-паллеты направления Казань×ФФ1 сметает
 * ВСЁ направление. Симулируем конвейер consolidation-эффекта distribute/page.tsx
 * дословно и смотрим, какой шаг теряет остальные паллеты.
 */
import { describe, it, expect } from 'vitest';

import { normalizeDraft, consolidatePrebookWholePallets, type NormalizeDraftCtx } from '@/lib/utils/normalizeDraft';
import { topUpPrebookLooseBoxes, releaseUnfixableLooseBoxes } from '@/lib/utils/assemblyRoundBoxes';
import { allocatePairs } from '@/lib/utils/assemblyPreview';
import type { AssemblyDraftRow, PackageType, AcceptanceCheckPerItem } from '@/types/api';

/* eslint-disable @typescript-eslint/no-explicit-any */
import draftJson from './__fixtures__/draft.json';
import boxmultJson from './__fixtures__/boxmult.json';
import overridesJson from './__fixtures__/overrides.json';
import stockneedJson from './__fixtures__/stockneed.json';
import acceptanceJson from './__fixtures__/acceptance.json';

const draft = draftJson as any;
const boxmult = boxmultJson as any;
const overrides = overridesJson as Record<string, number>;
const stockneed = stockneedJson as any;
const acceptance = acceptanceJson as any;

const rows0: AssemblyDraftRow[] = draft.distribution.rows ?? [];
const prebook0: AssemblyDraftRow[] = draft.distribution.prebook ?? [];

// ── loadGeometry (page.tsx:210-249) ──
const nmPpb = new Map<number, number | null>();
const nmPpbByWh = new Map<number, Record<number, number>>();
const nmBoxSize = new Map<number, string | null>();
for (const r of boxmult.items) {
    let ppb: number | null = null;
    if (r.box_qty_override && r.box_qty_override > 0 && r.use_box_multiplicity) {
        ppb = r.box_qty_override;
    } else {
        let best = 0;
        let perWh: Record<number, number> | null = null;
        for (const p of r.per_warehouse) {
            if (p.box_qty && p.box_qty > 0 && p.use_box_multiplicity) {
                if (best === 0 || p.box_qty < best) best = p.box_qty;
                (perWh ??= {})[p.warehouse_id] = p.box_qty;
            }
        }
        ppb = best > 0 ? best : null;
        if (perWh) nmPpbByWh.set(r.nm_id, perWh);
    }
    let boxSize: string | null = null;
    let bestStock = -1;
    for (const p of r.per_warehouse) {
        if (!p.box_size) continue;
        if (p.rf_stock > bestStock) { boxSize = p.box_size; bestStock = p.rf_stock; }
    }
    nmPpb.set(r.nm_id, ppb);
    nmBoxSize.set(r.nm_id, boxSize);
}

// ── buildNormalizeCtx (page.tsx:~290-317) ──
function buildNormalizeCtx(involved: AssemblyDraftRow[]): NormalizeDraftCtx {
    const inDraft: Record<number, Record<number, number>> = {};
    for (const r of involved) {
        const m = (inDraft[r.nm_id] ??= {});
        for (const [ff, q] of Object.entries(r.src)) m[Number(ff)] = (m[Number(ff)] || 0) + (q || 0);
    }
    const freeByNm: Record<number, Record<number, number>> = {};
    for (const a of stockneed.articles ?? []) {
        const pool: Record<number, number> = {};
        for (const [ff, st] of Object.entries<any>(a.rf_stocks || {})) {
            const free = (st.available || 0) - (inDraft[a.nm_id]?.[Number(ff)] || 0);
            if (free > 0) pool[Number(ff)] = free;
        }
        if (Object.keys(pool).length) freeByNm[a.nm_id] = pool;
    }
    return {
        ppbOf: (nm) => nmPpb.get(nm),
        ppbAt: (nm, ffId) => nmPpbByWh.get(nm)?.[ffId] ?? null,
        boxSizeOf: (nm) => nmBoxSize.get(nm) ?? null,
        overrides,
        isNewcomer: () => false, // newcomerNmIds на стенде пуст для этого направления
        freeByNm,
    };
}

// ── readyPkgWbs / checkedPkgWbs (page.tsx:826-853) ──
const days = (m?: { free_days_14?: number; paid_days_14?: number } | null) =>
    (m?.free_days_14 ?? 0) + (m?.paid_days_14 ?? 0);
const readyPkgWbs = new Set<string>();
const checkedPkgWbs = new Set<string>();
for (const per of acceptance.items as AcceptanceCheckPerItem[]) {
    for (const [wb, f] of Object.entries(per.availability || {})) {
        checkedPkgWbs.add(`BOX::${wb}`); checkedPkgWbs.add(`MONOPALLET::${wb}`); checkedPkgWbs.add(`SUPERSAFE::${wb}`);
        if (f.can_box && days(f.box_meta) > 0) readyPkgWbs.add(`BOX::${wb}`);
        if (f.can_monopallet && days(f.mono_meta) > 0) readyPkgWbs.add(`MONOPALLET::${wb}`);
        if (f.can_supersafe && days(f.super_meta) > 0) readyPkgWbs.add(`SUPERSAFE::${wb}`);
    }
}

// ── mergeDraftRows (page.tsx:41-55) ──
function mergeDraftRows(rs: AssemblyDraftRow[]): AssemblyDraftRow[] {
    const m = new Map<string, AssemblyDraftRow>();
    for (const r of rs) {
        const k = `${r.nm_id}::${r.barcode}::${r.package_type || 'BOX'}::${r.as_is ? 1 : 0}`;
        let e = m.get(k);
        if (!e) {
            e = { nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: {}, tgt: {}, package_type: r.package_type || 'BOX' };
            if (r.as_is) e.as_is = true;
            m.set(k, e);
        }
        for (const [ff, q] of Object.entries(r.src)) e.src[ff] = (e.src[ff] || 0) + (q || 0);
        for (const [w2, q] of Object.entries(r.tgt)) e.tgt[w2] = (e.tgt[w2] || 0) + (q || 0);
    }
    return [...m.values()];
}

// ── консолидация (page.tsx:883-944, дословно) ──
function runConsolidation(rows: AssemblyDraftRow[], prebook: AssemblyDraftRow[]) {
    const keepRows: AssemblyDraftRow[] = [];
    const pool: AssemblyDraftRow[] = [];
    for (const r of rows) {
        const pkg = (r.package_type || 'BOX') as PackageType;
        if (r.as_is) { keepRows.push(r); continue; }
        const checkedWbs = Object.keys(r.tgt).filter(wb => checkedPkgWbs.has(`${pkg}::${wb}`));
        if (checkedWbs.length === 0) { keepRows.push(r); continue; }
        const cur = { ...r, src: { ...r.src }, tgt: { ...r.tgt } };
        for (const wb of checkedWbs) {
            for (const [pairKey, q] of allocatePairs(cur.src, cur.tgt)) {
                if ((q || 0) <= 0 || pairKey.slice(pairKey.indexOf('::') + 2) !== wb) continue;
                const ff = pairKey.slice(0, pairKey.indexOf('::'));
                pool.push({ nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [ff]: q }, tgt: { [wb]: q }, package_type: pkg });
                cur.tgt[wb] = Math.max(0, (cur.tgt[wb] || 0) - q);
                cur.src[ff] = Math.max(0, (cur.src[ff] || 0) - q);
            }
            if ((cur.tgt[wb] || 0) <= 0) delete cur.tgt[wb];
        }
        for (const ff of Object.keys(cur.src)) if (cur.src[ff] <= 0) delete cur.src[ff];
        if (Object.keys(cur.tgt).length > 0) keepRows.push(cur);
    }
    const fullPool = mergeDraftRows([...pool, ...prebook]);
    const keepInPrebook = (_nm: number, wb: string, pkg: PackageType) => !readyPkgWbs.has(`${pkg}::${wb}`);
    const ctx = buildNormalizeCtx([...keepRows, ...fullPool]);
    const topPool = topUpPrebookLooseBoxes(fullPool, ctx.ppbOf, ctx.freeByNm ?? {}, ctx.ppbAt);
    const relPool = releaseUnfixableLooseBoxes(topPool.changed ? topPool.rows : fullPool, ctx.ppbOf, ctx.freeByNm ?? {}, ctx.ppbAt);
    const packInput = relPool.changed ? relPool.rows : (topPool.changed ? topPool.rows : fullPool);
    const cons = consolidatePrebookWholePallets(packInput, ctx, keepInPrebook);
    const extractedRows = mergeDraftRows([...keepRows, ...cons.toDraft]);
    const topTail = topUpPrebookLooseBoxes(cons.prebook, ctx.ppbOf, ctx.freeByNm ?? {}, ctx.ppbAt);
    const release = releaseUnfixableLooseBoxes(topTail.changed ? topTail.rows : cons.prebook, ctx.ppbOf, ctx.freeByNm ?? {}, ctx.ppbAt);
    const tailPrebook = release.changed ? release.rows : (topTail.changed ? topTail.rows : cons.prebook);
    const verify = normalizeDraft(extractedRows, buildNormalizeCtx([...extractedRows, ...tailPrebook]));
    const newRows = verify.changed ? verify.rows : extractedRows;
    const newPrebook = verify.dropped.length ? mergeDraftRows([...tailPrebook, ...verify.dropped]) : tailPrebook;
    return {
        newRows, newPrebook,
        diag: {
            poolUnits: sumAll(pool), topPoolFilled: topPool.filledUp, relPoolReleased: relPool.releasedUnits,
            consExtracted: cons.extractedUnits, topTailFilled: topTail.filledUp, tailReleased: release.releasedUnits,
            verifyChanged: verify.changed, verifyDropped: sumAll(verify.dropped), verifyReleased: verify.releasedUnits,
        },
    };
}

const sumAll = (rs: AssemblyDraftRow[]) => rs.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
// Порция направления (wb × ff) в наборе строк.
function dirUnits(rs: AssemblyDraftRow[], wb: string, ffId: number, pkg: PackageType): number {
    let total = 0;
    for (const r of rs) {
        if ((r.package_type || 'BOX') !== pkg) continue;
        total += allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
    }
    return total;
}

// ── stripPalletsFromPrebook (page.tsx:1518-1536): Паллета 1 = BZ-FG12 44шт + винтажромбсиний 20шт ──
function stripPallet(prebook: AssemblyDraftRow[], wb: string, ffId: number, units: Record<number, number>): AssemblyDraftRow[] {
    const chosenKey = String(ffId);
    const need = new Map<number, number>(Object.entries(units).map(([nm, u]) => [Number(nm), u]));
    return prebook
        .map(r => {
            if ((r.package_type || 'BOX') !== 'MONOPALLET' || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) return r;
            const want = need.get(r.nm_id) || 0;
            if (want <= 0) return r;
            const portion = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
            const take = Math.min(portion, want);
            if (take <= 0) return r;
            need.set(r.nm_id, want - take);
            const tgt = { ...r.tgt }; tgt[wb] = Math.max(0, (tgt[wb] || 0) - take); if (tgt[wb] <= 0) delete tgt[wb];
            const src = { ...r.src }; src[chosenKey] = Math.max(0, (src[chosenKey] || 0) - take); if (src[chosenKey] <= 0) delete src[chosenKey];
            return { ...r, tgt, src };
        })
        .filter(r => Object.keys(r.tgt).length > 0);
}

describe('репро: попаллетное действие моно Казань×ФФ1', () => {
    const WB = 'Казань';
    const FF = 1;

    it('baseline: конвейер стабилен на текущем состоянии (fix-point)', () => {
        const before = dirUnits(prebook0, WB, FF, 'MONOPALLET');
        console.log('MONOPALLET::Казань ready?', readyPkgWbs.has('MONOPALLET::Казань'),
            '| checked?', checkedPkgWbs.has('MONOPALLET::Казань'), '| direction units:', before);
        const r1 = runConsolidation(rows0, prebook0);
        const afterPb = dirUnits(r1.newPrebook, WB, FF, 'MONOPALLET');
        const afterRows = dirUnits(r1.newRows, WB, FF, 'MONOPALLET');
        console.log('baseline diag:', JSON.stringify(r1.diag));
        console.log(`baseline: prebook ${before} -> prebook ${afterPb} + rows(mono) ${afterRows}`);
        expect(afterPb + afterRows).toBe(before);
    });

    it('strip Паллета 1 (64 шт) → остальное направление сохраняется', () => {
        const before = dirUnits(prebook0, WB, FF, 'MONOPALLET');
        const stripped = stripPallet(prebook0, WB, FF, { 937180959: 44, 917772919: 20 });
        const afterStrip = dirUnits(stripped, WB, FF, 'MONOPALLET');
        console.log(`strip: ${before} -> ${afterStrip} (снято ${before - afterStrip})`);
        expect(afterStrip).toBe(before - 64);

        const r2 = runConsolidation(rows0, stripped);
        const afterPb = dirUnits(r2.newPrebook, WB, FF, 'MONOPALLET');
        const afterRows = dirUnits(r2.newRows, WB, FF, 'MONOPALLET');
        console.log('post-strip diag:', JSON.stringify(r2.diag));
        console.log(`post-strip: prebook ${afterPb} + rows(mono) ${afterRows} (ожидалось Σ=${afterStrip})`);
        expect(afterPb + afterRows).toBe(afterStrip);
    });
});

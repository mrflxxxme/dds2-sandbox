/**
 * reconcileInTransit — «один мир» черновика и реальных заявок.
 *
 * Черновик распределения — персистентный план. Заявки могут создаваться и
 * другими путями (предраспределение машины, ручные) — тогда план черновика
 * становится дублем уже едущего: `dropCommittedRows` ловит только заявки,
 * закоммиченные ИЗ ЭТОГО черновика, а внешние — нет (прод-кейс «швабры апл»
 * 2026-07-10: 54 шт ехали заявками pre-dist, черновик предлагал их снова).
 *
 * Здесь строки черновика урезаются на «уже едет/зарезервировано» per
 * (nm_id, WB-склад) из GET /warehouse/assembly/in-transit (вкл. PRE_DISTRIBUTED).
 * Источник src урезается соразмерно (Σsrc == Σtgt), пустые строки выкидываются.
 * Имена складов с обеих сторон — канон ACCEPTANCE_TO_STOCK_NAME, сравнение прямое.
 */

import type { AssemblyDraftRow, InTransitItem } from '@/types/api';

export interface ReconcileResult {
    rows: AssemblyDraftRow[];
    /** Σ вычтенных штук по всем строкам. */
    subtractedUnits: number;
    /** Полностью удалённые строки (весь план уже едет). */
    removedRows: number;
    /** Затронутые nm_id (для тоста). */
    touchedNm: Set<number>;
    changed: boolean;
    /** Остаток transit после расхода — для последовательной сверки (rows → prebook). */
    remainingTransit: Map<number, Record<string, number>>;
}

/** items API → {nm → {wh → qty}} (mutable-копия для последовательного расхода). */
export function inTransitMap(items: InTransitItem[]): Map<number, Record<string, number>> {
    const m = new Map<number, Record<string, number>>();
    for (const it of items) {
        if (!it || (it.quantity || 0) <= 0) continue;
        const per = m.get(it.nm_id) ?? {};
        per[it.warehouse_name] = (per[it.warehouse_name] || 0) + it.quantity;
        m.set(it.nm_id, per);
    }
    return m;
}

const sumRec = (r: Record<string | number, number>) => Object.values(r).reduce((s, v) => s + (v || 0), 0);

/**
 * Вычесть «уже едет» из tgt строк (greedy по порядку строк), src ужать до Σtgt
 * (снимаем с крупнейших источников). transit расходуется между строками одного
 * nm последовательно — двойного вычета нет.
 */
export function subtractInTransitFromRows(
    rows: AssemblyDraftRow[],
    transit: Map<number, Record<string, number>>,
): ReconcileResult {
    const remaining = new Map<number, Record<string, number>>();
    for (const [nm, per] of transit) remaining.set(nm, { ...per });

    const out: AssemblyDraftRow[] = [];
    let subtracted = 0;
    let removed = 0;
    const touched = new Set<number>();

    for (const r of rows) {
        const per = remaining.get(r.nm_id);
        if (!per) { out.push(r); continue; }

        const tgt: Record<string, number> = { ...r.tgt };
        let rowSub = 0;
        for (const [wh, q] of Object.entries(tgt)) {
            const avail = per[wh] || 0;
            if (avail <= 0 || (q || 0) <= 0) continue;
            const take = Math.min(q, avail);
            tgt[wh] = q - take;
            per[wh] = avail - take;
            rowSub += take;
        }
        if (rowSub === 0) { out.push(r); continue; }

        subtracted += rowSub;
        touched.add(r.nm_id);
        const tgtTotal = sumRec(tgt);
        if (tgtTotal <= 0) { removed += 1; continue; }

        // src ужимаем до Σtgt: снимаем с крупнейших ФФ-источников (carve, Σsrc==Σtgt).
        const src: Record<string, number> = { ...r.src };
        let excess = sumRec(src) - tgtTotal;
        const byDesc = Object.entries(src).sort((a, b) => (b[1] || 0) - (a[1] || 0));
        for (const [ff] of byDesc) {
            if (excess <= 0) break;
            const cut = Math.min(src[ff] || 0, excess);
            src[ff] = (src[ff] || 0) - cut;
            excess -= cut;
        }
        for (const k of Object.keys(tgt)) if ((tgt[k] || 0) <= 0) delete tgt[k];
        for (const k of Object.keys(src)) if ((src[k] || 0) <= 0) delete src[k];
        out.push({ ...r, tgt, src });
    }

    // Пустые остатки чистим — потребителю (prebook-проход) нужен только живой transit.
    for (const [nm, per] of [...remaining]) {
        for (const k of Object.keys(per)) if ((per[k] || 0) <= 0) delete per[k];
        if (Object.keys(per).length === 0) remaining.delete(nm);
    }
    return {
        rows: out,
        subtractedUnits: subtracted,
        removedRows: removed,
        touchedNm: touched,
        changed: subtracted > 0,
        remainingTransit: remaining,
    };
}

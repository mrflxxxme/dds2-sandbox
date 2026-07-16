/**
 * «Распределить все остатки» — раздача остатка SKU (машины или свободного ФФ)
 * ЦЕЛЫМИ коробами по WB-складам. Чистые функции, общие для экрана
 * «Распределение машины» (пины cellEdits) и матрицы черновика (applyDraftCellEdit).
 *
 * Куда раздаём (buildLeftoverWeights, фолбэк-цепочка):
 *   1) склады с остаточной потребностью (need > 0) — пропорционально need;
 *   2) иначе — куда товар уже лежит/едет (stock + asm + transit): там он продаётся;
 *   3) пусто → SKU пропускается (некуда — решает человек).
 * Закрытые по приёмке склады (⛔) выфильтровываются на любом шаге.
 */

export interface LeftoverWhSignal {
    need: number;
    /** Сырой (до greedy-налива) остаточный дефицит клетки — предпочтительный вес:
     *  в only_available-ответе need = уже налитое, а не дефицит. */
    needRaw?: number;
    stock: number;
    asm: number;
    transit: number;
}

/** Веса раздачи остатка по складам SKU. Пустая Map — раздать некуда. */
export function buildLeftoverWeights(
    byWh: Record<string, LeftoverWhSignal> | undefined,
    isOpen: (wh: string) => boolean,
): Map<string, number> {
    const out = new Map<string, number>();
    if (!byWh) return out;
    const open = Object.entries(byWh).filter(([wh]) => isOpen(wh));
    for (const [wh, s] of open) { const w = s.needRaw ?? s.need ?? 0; if (w > 0) out.set(wh, w); }
    if (out.size > 0) return out;
    for (const [wh, s] of open) {
        const presence = (s.stock || 0) + (s.asm || 0) + (s.transit || 0);
        if (presence > 0) out.set(wh, presence);
    }
    return out;
}

/**
 * Раздать `boxes` целых коробов по весам (largest remainder / метод Гамильтона).
 * Детерминизм: остаточные коробы — по наибольшей дробной части, тай-брейк
 * больший вес → имя склада. Σ результата === boxes (при непустых весах).
 */
export function allocateWholeBoxes(boxes: number, weights: Map<string, number>): Map<string, number> {
    const out = new Map<string, number>();
    if (boxes <= 0 || weights.size === 0) return out;
    const totalW = [...weights.values()].reduce((s, v) => s + v, 0);
    if (totalW <= 0) return out;
    let used = 0;
    const fracs: { wh: string; frac: number; w: number }[] = [];
    for (const [wh, w] of weights) {
        const exact = (boxes * w) / totalW;
        const base = Math.floor(exact);
        if (base > 0) out.set(wh, base);
        used += base;
        fracs.push({ wh, frac: exact - base, w });
    }
    fracs.sort((a, b) => b.frac - a.frac || b.w - a.w || a.wh.localeCompare(b.wh, 'ru'));
    for (let i = 0; used < boxes && fracs.length > 0; i = (i + 1) % fracs.length) {
        const f = fracs[i];
        out.set(f.wh, (out.get(f.wh) ?? 0) + 1);
        used++;
    }
    return out;
}

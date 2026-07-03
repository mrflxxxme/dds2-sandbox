/**
 * Засев новинок (cold-start) ЦЕЛЫМИ коробами по главным складам округов — с учётом
 * УЖЕ имеющегося покрытия (остаток на WB + в сборке + в пути), чтобы не перетарить.
 *
 * По требованию пользователя: новинку лучше разложить целыми коробами по топ-складам
 * округов (по доле спроса), чем держать на ФФ — НО если на складе уже лежит/едет
 * примерно столько же, туда слать не нужно. Логика:
 *   1. idealShare_i = доля_округа × остаток_к_засеву  — сколько пришлось бы на склад;
 *   2. shortfall_i = max(0, idealShare_i − уже_на_складе_i)  — за вычетом покрытия
 *      (остаток WB + в сборке + в пути); перетаренный склад → 0 (не слать);
 *   3. режем Σshortfall в целые коробы `ppb` и раскладываем по складам пропорционально
 *      shortfall (largest-remainder).
 * Хвост < короба и доля перетаренных складов остаются на ФФ.
 *
 * Используется в «Потребности по складам» (новинки) и в предраспределении машины.
 * Чистая функция — полностью юнит-тестируется.
 */

export interface SeedAnchor {
    /** Имя WB-склада-анкера округа. */
    warehouse: string;
    /** Доля спроса округа (%). Может быть 0. */
    share_pct: number;
    /** Уже на этом складе/едет/в сборке (остаток WB + assembly + transit). Вычитается. */
    existing?: number;
}

export function seedNewcomerWholeBoxes(
    totalQty: number,
    ppb: number | null | undefined,
    anchors: SeedAnchor[],
): Record<string, number> {
    const k = ppb && ppb > 0 ? Math.floor(ppb) : 0;
    const qty = Math.max(0, Math.floor(Number(totalQty) || 0));
    if (k <= 0 || qty < k) return {};

    // Уникальные анкеры; доли/покрытие коэрсим в число.
    const seen = new Set<string>();
    const open: { warehouse: string; share: number; existing: number }[] = [];
    for (const a of anchors) {
        if (!a.warehouse || seen.has(a.warehouse)) continue;
        seen.add(a.warehouse);
        open.push({
            warehouse: a.warehouse,
            share: Math.max(0, Number(a.share_pct) || 0),
            existing: Math.max(0, Math.floor(Number(a.existing) || 0)),
        });
    }
    if (open.length === 0) return {};

    const shareSum = open.reduce((s, a) => s + a.share, 0);

    // Сколько из НАШЕГО остатка пришлось бы на склад по доле спроса, МИНУС уже
    // имеющееся там (остаток WB + едет + в сборке). Перетаренный склад (existing ≥
    // его доли) → 0 (туда не слать). Хвост остаётся на ФФ.
    const shortfalls = open.map(a => {
        const idealShare = shareSum > 0 ? qty * (a.share / shareSum) : qty / open.length;
        return { wh: a.warehouse, sf: Math.max(0, idealShare - a.existing) };
    });
    const sfSum = shortfalls.reduce((s, x) => s + x.sf, 0);
    if (sfSum <= 0) return {}; // всё покрыто → ничего не слать (не перетаривать)

    const shipTotal = Math.min(qty, sfSum);
    // +eps гасит float-дребезг суммы долей (Σ idealShare = qty математически, но в
    // double может дать qty−1e-13 → floor срезал бы целый короб).
    const totalBoxes = Math.floor(shipTotal / k + 1e-6);
    if (totalBoxes <= 0) return {};

    // Целые коробы по складам пропорционально shortfall (largest-remainder).
    const ranked = [...shortfalls].sort((a, b) => b.sf - a.sf);
    const ideal = ranked.map(x => {
        const want = totalBoxes * (x.sf / sfSum);
        return { wh: x.wh, base: Math.floor(want), rem: want - Math.floor(want) };
    });
    const boxesByWh = new Map<string, number>();
    let assigned = 0;
    for (const w of ideal) { boxesByWh.set(w.wh, w.base); assigned += w.base; }
    let leftover = totalBoxes - assigned;
    const byRem = [...ideal].sort((a, b) => b.rem - a.rem);
    for (let i = 0; leftover > 0 && i < byRem.length; i++, leftover--) {
        boxesByWh.set(byRem[i].wh, (boxesByWh.get(byRem[i].wh) || 0) + 1);
    }
    for (let i = 0; leftover > 0; i++, leftover--) {
        const wh = ranked[i % ranked.length].wh;
        boxesByWh.set(wh, (boxesByWh.get(wh) || 0) + 1);
    }

    const out: Record<string, number> = {};
    for (const [wh, b] of boxesByWh) if (b > 0) out[wh] = b * k;
    return out;
}

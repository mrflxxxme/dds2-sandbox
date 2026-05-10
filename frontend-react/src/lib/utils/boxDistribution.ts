/**
 * Распределение qty по WB-складам кратно `ppb` (pcs-per-box).
 *
 * Правила:
 *  1. **Минимум 1 коробка каждому складу с need>0**, пока хватает товара.
 *     Приоритет получения «первой коробки» — у склада с большей потребностью.
 *  2. Остаток коробок раздаётся жадно: каждая следующая коробка — складу с
 *     максимальной незакрытой потребностью (`need - already_given >= ppb`).
 *  3. Хвост `< ppb` остаётся неотправленным (floor-стратегия).
 *
 * Возвращает `Record<warehouseName, qty>` где qty всегда кратно ppb.
 * Пустой объект если ppb≤0, totalCanSend<ppb или нет складов с need>0.
 */
export function distributeByBoxMultiple(
    needs: ReadonlyArray<{ name: string; need: number }>,
    totalCanSend: number,
    ppb: number,
): Record<string, number> {
    if (ppb <= 0 || totalCanSend < ppb) return {};
    const sorted = [...needs]
        .filter(w => w.need > 0)
        .sort((a, b) => b.need - a.need);
    if (sorted.length === 0) return {};

    const tgt: Record<string, number> = {};
    let remainingBoxes = Math.floor(totalCanSend / ppb);

    // Pass 1: минимум по 1 коробке, приоритет — самым нуждающимся.
    for (const wh of sorted) {
        if (remainingBoxes <= 0) break;
        tgt[wh.name] = ppb;
        remainingBoxes--;
    }

    // Pass 2: доразать остаток по убыванию незакрытой потребности.
    while (remainingBoxes > 0) {
        let bestName: string | null = null;
        let bestUnmet = 0;
        for (const wh of sorted) {
            const unmet = wh.need - (tgt[wh.name] || 0);
            if (unmet >= ppb && unmet > bestUnmet) {
                bestName = wh.name;
                bestUnmet = unmet;
            }
        }
        if (!bestName) break;
        tgt[bestName] = (tgt[bestName] || 0) + ppb;
        remainingBoxes--;
    }

    return tgt;
}

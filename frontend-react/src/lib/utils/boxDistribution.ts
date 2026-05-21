/**
 * Распределение qty по WB-складам ТОЛЬКО целыми коробками `ppb` (pcs-per-box).
 *
 * Правила:
 *  1. **Только целые коробки.** Сначала минимум 1 коробка каждому складу с
 *     need>0 (приоритет «первой коробки» — у склада с большей потребностью), затем
 *     остаток коробок раздаётся жадно складу с максимальной незакрытой потребностью.
 *  2. **Остаток < короба остаётся на источнике.** Хвост, не собравшийся в целую
 *     коробку, в WB НЕ уходит — кратность важнее, чем «отгрузить всё до штуки».
 *     Он остаётся на ФФ и уедет в следующую поставку.
 *  3. Суммарно раздаётся целое число коробок:
 *     `⌊min(totalCanSend, Σ need) / ppb⌋ × ppb` — сверх потребности не отгружаем.
 *
 * Возвращает `Record<warehouseName, qty>`. Пустой объект если ppb≤0,
 * totalCanSend≤0, нет складов с need>0 или доступного меньше одной коробки.
 */
export function distributeByBoxMultiple(
    needs: ReadonlyArray<{ name: string; need: number }>,
    totalCanSend: number,
    ppb: number,
): Record<string, number> {
    if (ppb <= 0 || totalCanSend <= 0) return {};
    const sorted = [...needs]
        .filter(w => w.need > 0)
        .sort((a, b) => b.need - a.need);
    if (sorted.length === 0) return {};

    const totalNeed = sorted.reduce((s, w) => s + w.need, 0);
    const tgt: Record<string, number> = {};
    // Только целые коробки: бюджет округляем вниз до кратного ppb, остаток < короба
    // остаётся на ФФ (в WB россыпью не уходит). Сверх потребности не отгружаем.
    let remaining = Math.floor(Math.min(totalCanSend, totalNeed) / ppb) * ppb;

    // Pass 1: минимум по 1 коробке, приоритет — самым нуждающимся.
    for (const wh of sorted) {
        if (remaining < ppb) break;
        tgt[wh.name] = ppb;
        remaining -= ppb;
    }

    // Pass 2: доразать целые коробки по убыванию незакрытой потребности.
    while (remaining >= ppb) {
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
        remaining -= ppb;
    }

    return tgt;
}

/**
 * Распределение `qty` целыми коробками `ppb` ПРОПОРЦИОНАЛЬНО весам (`weight`),
 * а не абсолютной потребности. Используется для cold-start новинок, где «вес» —
 * это `share_pct` округа/склада из бенчмарка (своей истории продаж нет).
 *
 * Вес проецируется в эквивалентную потребность `qty × weight / Σweight`, после
 * чего применяется та же строгая box-логика {@link distributeByBoxMultiple}:
 * только целые коробки, минимум по одной самым «весомым» складам, хвост < короба
 * остаётся на ФФ. Без целой коробки (`qty < ppb`) — пустой объект.
 */
export function distributeByBoxMultipleWeighted(
    weights: ReadonlyArray<{ name: string; weight: number }>,
    qty: number,
    ppb: number,
): Record<string, number> {
    if (ppb <= 0 || qty <= 0) return {};
    const positive = weights.filter(w => w.weight > 0);
    const totalWeight = positive.reduce((s, w) => s + w.weight, 0);
    if (totalWeight <= 0) return {};
    const needs = positive.map(w => ({ name: w.name, need: (qty * w.weight) / totalWeight }));
    return distributeByBoxMultiple(needs, qty, ppb);
}

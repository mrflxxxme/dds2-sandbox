/**
 * Распределение qty по WB-складам целыми коробками `ppb` (pcs-per-box), с опцией
 * досыла хвоста россыпью.
 *
 * Правила:
 *  1. **Короба — по потребности.** Сначала минимум 1 коробка каждому складу с
 *     need>0 (приоритет «первой коробки» — у склада с большей потребностью), затем
 *     остаток коробок раздаётся жадно складу с максимальной незакрытой потребностью.
 *  2. **Хвост < короба** (`looseTail`):
 *     - `true` (дефолт, обычные SKU) — досылается россыпью по убыванию незакрытой
 *       потребности (cap на need — без overshoot); ничего не оседает на ФФ.
 *     - `false` (строгий режим, новинки cold-start) — хвост НЕ уходит, остаётся
 *       на ФФ до накопления целого короба. Только целые коробки `ppb`.
 *  3. Суммарно отгружается `min(totalCanSend, Σ need)` (looseTail) либо округлённое
 *     вниз до короба (строгий) — сверх потребности не шлём.
 *
 * Возвращает `Record<warehouseName, qty>`. Пустой объект если ppb≤0,
 * totalCanSend≤0, нет складов с need>0 (или в строгом режиме — меньше короба).
 */
export function distributeByBoxMultiple(
    needs: ReadonlyArray<{ name: string; need: number }>,
    totalCanSend: number,
    ppb: number,
    looseTail = true,
    /** «Не менее 1 короба на склад»: бюджет = весь доступный сток (не капим суммарной
     *  потребностью), чтобы КАЖДЫЙ склад с потребностью получил ≥1 целый короб, даже если
     *  это перебор над его потребностью. Кап — только физический сток (totalCanSend);
     *  приоритет тот же (по убыванию потребности = speed-локализация). Для pre-dist-матрицы. */
    minOneBoxPerWh = false,
): Record<string, number> {
    if (ppb <= 0 || totalCanSend <= 0) return {};
    const sorted = [...needs]
        .filter(w => w.need > 0)
        .sort((a, b) => b.need - a.need);
    if (sorted.length === 0) return {};

    const totalNeed = sorted.reduce((s, w) => s + w.need, 0);
    const tgt: Record<string, number> = {};
    // Бюджет: обычно = потребность (сверх потребности не шлём); в режиме «≥1 короб на
    // склад» = весь сток (перебор допускается ради минимального короба каждому складу).
    const budget = minOneBoxPerWh ? totalCanSend : Math.min(totalCanSend, totalNeed);
    let remaining = Math.floor(budget / ppb) * ppb; // целые коробки

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

    // Pass 3 (только looseTail): хвост (budget − короба) — россыпью по убыванию
    // незакрытой потребности, cap на need (budget ≤ Σneed → влезает без overshoot).
    // Строгий режим (новинки) хвост НЕ досылает — он остаётся на ФФ.
    if (looseTail) {
        let loose = budget - sorted.reduce((s, wh) => s + (tgt[wh.name] || 0), 0);
        if (loose > 0) {
            const byUnmet = [...sorted].sort(
                (a, b) => (b.need - (tgt[b.name] || 0)) - (a.need - (tgt[a.name] || 0)),
            );
            for (const wh of byUnmet) {
                if (loose <= 0) break;
                const unmet = wh.need - (tgt[wh.name] || 0);
                if (unmet <= 0) continue;
                const give = Math.min(loose, unmet);
                tgt[wh.name] = (tgt[wh.name] || 0) + give;
                loose -= give;
            }
        }
    }

    return tgt;
}

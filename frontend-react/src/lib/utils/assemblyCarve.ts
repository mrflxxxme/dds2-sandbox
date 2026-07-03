/**
 * Урезание src-распределения строки черновика (ФФ → units) до целевой суммы.
 *
 * Общий помощник палет-консолидаторов (короб/моно): когда tgt пересобран и его сумма
 * уменьшилась (хвосты сняты на ФФ), src нужно урезать ровно до новой Σtgt, сохранив
 * целые числа и баланс Σsrc==Σtgt. Распределяем пропорционально (largest-remainder);
 * снятые штуки остаются на ФФ. Чистая функция.
 */
export function carveSrcToTarget(
    src: Record<string, number>,
    target: number,
): Record<string, number> {
    const entries = Object.entries(src).filter(([, v]) => v > 0);
    const total = entries.reduce((s, [, v]) => s + v, 0);
    if (target >= total) {
        const out: Record<string, number> = {};
        for (const [k, v] of entries) out[k] = v;
        return out;
    }
    if (target <= 0) return {};
    const raw = entries.map(([ff, v]) => {
        const exact = (v * target) / total;
        const fl = Math.floor(exact);
        return { ff, fl, rem: exact - fl };
    });
    const remainder = target - raw.reduce((s, r) => s + r.fl, 0);
    raw.sort((a, b) => b.rem - a.rem || (a.ff < b.ff ? -1 : a.ff > b.ff ? 1 : 0));
    const out: Record<string, number> = {};
    raw.forEach((r, i) => { const q = r.fl + (i < remainder ? 1 : 0); if (q > 0) out[r.ff] = q; });
    return out;
}

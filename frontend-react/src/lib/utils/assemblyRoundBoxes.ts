/**
 * Приведение строк черновика сборки к ЦЕЛЫМ КОРОБАМ (правило «всегда целые коробы»).
 *
 * Две фазы на каждую BOX-строку (ключ строки = nm × barcode, tgt — по WB-складам):
 *  A. Добор НА МЕСТЕ: неполный короб склада (`qty % ppb ≠ 0`) добивается ВВЕРХ из
 *     свободного остатка ФФ (`freeByNm[nm][ff]`), если на короб хватает. Σsrc/Σtgt
 *     растут поровну (новые штуки берутся с ФФ). Региональная раскладка сохраняется.
 *  B. КОНСОЛИДАЦИЯ хвостов: оставшиеся «огрызки» (< короба, на которые ФФ не хватило)
 *     по всем складам строки собираются в общий пул, переупаковываются в ЦЕЛЫЕ коробы
 *     и кладутся на склады с наибольшими хвостами; последний неполный короб добивается
 *     из ФФ, если возможно. Неустранимый остаток (`R % ppb` без ФФ) остаётся россыпью
 *     на ОДНОМ складе — штуки НЕ теряем и в ноль не дропаем.
 *
 * Итог: для каждого SKU остаётся максимум один склад с россыпью (и только когда ФФ
 * физически не хватает на короб). Σsrc==Σtgt держится всегда. `freeByNm` мутируется
 * (общий пул ФФ SKU расходуется). Новинки и обычные обрабатываются одинаково —
 * разница лишь в объёме доступного ФФ. SKU без кратности (`ppbOf`→null/0) и не-BOX
 * (моно/сейф) не трогаются: cold-start без кратности едет россыпью, моно — по-SKU.
 */
import type { AssemblyDraftRow } from '@/types/api';

export interface RoundBoxesResult {
    rows: AssemblyDraftRow[];
    changed: number;       // сколько строк изменено
    filledUp: number;      // добито вверх из свободного ФФ (шт)
    trimmedDown: number;   // срезано вниз (шт) — сохранено для совместимости, теперь всегда 0 (штуки не теряем)
    consolidated: number;  // россыпь, переупакованная в целые коробы (шт)
    looseLeft: number;     // неустранимый остаток, оставшийся россыпью (шт)
}

const sameMap = (a: Record<string, number>, b: Record<string, number>): boolean => {
    const ka = Object.keys(a), kb = Object.keys(b);
    if (ka.length !== kb.length) return false;
    for (const k of ka) if (a[k] !== b[k]) return false;
    return true;
};

export function roundDraftRowsToWholeBoxes(
    rows: AssemblyDraftRow[],
    ppbOf: (nmId: number) => number | null | undefined,
    freeByNm: Record<number, Record<number, number>>,
    /** SKU, которым НЕЛЬЗЯ срезать вниз (новинки cold-start). Сейчас вниз не режем
     *  никого (штуки не теряем) — флаг оставлен для явности намерения вызова. */
    noTrimDown?: (nmId: number) => boolean,
): RoundBoxesResult {
    void noTrimDown;
    let changed = 0;
    let filledUp = 0;
    const trimmedDown = 0;
    let consolidated = 0;
    let looseLeft = 0;

    const out = rows.map((r) => {
        const ppb = ppbOf(r.nm_id);
        if ((r.package_type || 'BOX') !== 'BOX' || !ppb || ppb <= 0) return r;

        const pool = (freeByNm[r.nm_id] ??= {});
        const tgt: Record<string, number> = { ...r.tgt };
        const src: Record<string, number> = { ...r.src };

        const spare = () => Object.values(pool).reduce((s, v) => s + (v || 0), 0);
        // Снять n штук со свободного ФФ в src (с самого ёмкого ФФ). Возвращает фактически снятое.
        const takeFromPool = (n: number): number => {
            let add = n;
            const ids = Object.keys(pool).map(Number).sort((a, b) => (pool[b] || 0) - (pool[a] || 0));
            for (const id of ids) {
                if (add <= 0) break;
                const take = Math.min(add, pool[id] || 0);
                if (take <= 0) continue;
                src[String(id)] = (src[String(id)] || 0) + take;
                pool[id] -= take;
                add -= take;
            }
            return n - add;
        };

        // Фаза A: добор каждого неполного короба НА МЕСТЕ (если хватает ФФ на короб).
        for (const wb of Object.keys(tgt)) {
            const rem = tgt[wb] % ppb;
            if (rem === 0) continue;
            const gap = ppb - rem;
            if (spare() >= gap) {
                tgt[wb] += takeFromPool(gap);
                filledUp += gap;
            }
        }

        // Фаза B: консолидация оставшихся хвостов в целые коробы по складам строки.
        const tails = Object.keys(tgt)
            .map((wb) => ({ wb, tail: tgt[wb] % ppb }))
            .filter((t) => t.tail > 0);
        if (tails.length) {
            // Снять хвосты, оставив только целые коробы на местах.
            for (const t of tails) {
                tgt[t.wb] -= t.tail;
                if (tgt[t.wb] <= 0) delete tgt[t.wb];
            }
            let R = tails.reduce((s, t) => s + t.tail, 0);
            let boxes = Math.floor(R / ppb);
            let rem = R - boxes * ppb;
            // Добить последний неполный короб из ФФ, если хватает.
            if (rem > 0) {
                const gap = ppb - rem;
                if (spare() >= gap) {
                    const got = takeFromPool(gap);
                    R += got;
                    filledUp += got;
                    boxes += 1;
                    rem = 0;
                }
            }
            consolidated += boxes * ppb;
            // Целые коробы — на склады с наибольшими исходными хвостами.
            const order = [...tails].sort((a, b) => b.tail - a.tail).map((t) => t.wb);
            for (let k = 0; k < boxes; k++) {
                const wb = order[k % order.length];
                tgt[wb] = (tgt[wb] || 0) + ppb;
            }
            // Неустранимый остаток — россыпью на склад с наибольшим хвостом (штуки не теряем).
            if (rem > 0) {
                const wb = order[0];
                tgt[wb] = (tgt[wb] || 0) + rem;
                looseLeft += rem;
            }
        }

        if (sameMap(tgt, r.tgt) && sameMap(src, r.src)) return r;
        changed++;
        return { ...r, src, tgt };
    });

    return {
        rows: out.filter((r) => Object.keys(r.tgt).length > 0 && Object.keys(r.src).length > 0),
        changed,
        filledUp,
        trimmedDown,
        consolidated,
        looseLeft,
    };
}

/**
 * Округление МОНО-строк до ЦЕЛЫХ КОРОБОВ (вариант A). Для каждого WB-склада строки:
 *  • неполный короб (`qty % ppb ≠ 0`) добивается ВВЕРХ из свободного ФФ (`freeByNm`),
 *    если на короб хватает — `qty → следующий кратный ppb` (Σsrc/Σtgt растут поровну);
 *  • не хватает ФФ на короб — под-коробочный остаток СРЕЗАЕТСЯ вниз и остаётся на ФФ
 *    (не едет россыпью).
 * Так моно едет ТОЛЬКО целыми коробами. Не-моно — не трогаем.
 * `skipLoose(nm)` — оставить строку как есть (россыпь): новинка cold-start БЕЗ кратности
 * короба. Новинка С кратностью НЕ попадает сюда (skipLoose=false) → едет целыми коробами,
 * как обычный моно-SKU.
 * `freeByNm` мутируется (общий пул ФФ SKU расходуется — зови ПОСЛЕ box-округления, чтобы
 * короб и моно делили один остаток). Σsrc==Σtgt держится. Чистая функция.
 */
export function roundMonoToWholeBoxes(
    rows: AssemblyDraftRow[],
    ppbOf: (nmId: number) => number | null | undefined,
    freeByNm: Record<number, Record<number, number>>,
    skipLoose?: (nmId: number) => boolean,
): AssemblyDraftRow[] {
    const out = rows.map((r) => {
        if ((r.package_type || 'BOX') !== 'MONOPALLET') return r;
        if (skipLoose?.(r.nm_id)) return r;
        const ppb = ppbOf(r.nm_id);
        if (!ppb || ppb <= 0) return r;

        const pool = (freeByNm[r.nm_id] ??= {});
        const tgt: Record<string, number> = { ...r.tgt };
        const src: Record<string, number> = { ...r.src };
        const spare = () => Object.values(pool).reduce((s, v) => s + (v || 0), 0);
        // Снять n штук со свободного ФФ в src (с самого ёмкого). Возвращает фактически снятое.
        const takeFromPool = (n: number): number => {
            let add = n;
            for (const id of Object.keys(pool).map(Number).sort((a, b) => (pool[b] || 0) - (pool[a] || 0))) {
                if (add <= 0) break;
                const take = Math.min(add, pool[id] || 0);
                if (take <= 0) continue;
                src[String(id)] = (src[String(id)] || 0) + take;
                pool[id] -= take;
                add -= take;
            }
            return n - add;
        };
        // Вернуть n штук из src обратно в ФФ-пул (с наименее объёмного ФФ).
        const trimSrc = (n: number): void => {
            let rem = n;
            for (const id of Object.keys(src).sort((a, b) => src[a] - src[b])) {
                if (rem <= 0) break;
                const drop = Math.min(src[id], rem);
                src[id] -= drop;
                pool[Number(id)] = (pool[Number(id)] || 0) + drop;
                rem -= drop;
                if (src[id] <= 0) delete src[id];
            }
        };

        for (const wb of Object.keys(tgt)) {
            const rem = tgt[wb] % ppb;
            if (rem === 0) continue;
            const gap = ppb - rem;
            if (spare() >= gap) {
                tgt[wb] += takeFromPool(gap);    // добить короб вверх из ФФ
            } else {
                tgt[wb] -= rem;                   // срезать под-коробочный остаток на ФФ
                if (tgt[wb] <= 0) delete tgt[wb];
                trimSrc(rem);
            }
        }
        for (const k of Object.keys(src)) if (src[k] <= 0) delete src[k];
        return { ...r, tgt, src };
    });
    // строки, обнулённые срезом (всё < короба, ФФ не хватило) — выбрасываем.
    return out.filter((r) => Object.keys(r.tgt).length > 0 && Object.keys(r.src).length > 0);
}

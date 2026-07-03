/**
 * Приведение строк черновика сборки к ЦЕЛЫМ КОРОБАМ — СТРОГИЙ РЕЖИМ (решение юзера
 * 2026-07-02: «неполный короб в черновике не учитываем; некратное не добавляем»).
 *
 * Работа идёт ПО ПОРЦИЯМ (ФФ→WB, тот же `allocatePairs`, что показ и коммит) с
 * кратностью КОНКРЕТНОГО ФФ (`ppbAt`; фолбэк — глобальная `ppbOf`). Кратность у SKU
 * может отличаться по складам (80х160_синий: 22 на Хамзе, 30 на Газпроме) — глобальный
 * min резал бы физически целые коробы. Три фазы на BOX-строку:
 *  1. Добор порции ВВЕРХ строго с ЕЁ ФФ (короб пакуется на одном складе): свободного
 *     хватает до кратного — добираем, Σsrc/Σtgt растут поровну.
 *  2. Недобитые хвосты СНИМАЮТСЯ с порций (некратное в черновик не едет).
 *  3. Снятые хвосты ОДНОГО ФФ консолидируются в целые коробы (последний добивается из
 *     свободного ФФ, если хватает) и возвращаются на склады с наибольшими исходными
 *     хвостами. Неустранимый остаток УХОДИТ в свободный ФФ (`freeByNm` += rem) —
 *     НЕ едет и НЕ резервируется.
 *
 * Итог: все порции строки кратны коробу своего ФФ; россыпи в черновике нет (кроме
 * `noTrimDown`-SKU). Σsrc==Σtgt держится. `freeByNm` мутируется в обе стороны
 * (добор расходует, снятое возвращается). SKU без кратности (`ppbOf`→null/0) и
 * не-BOX (моно/сейф) не трогаются: cold-start без кратности едет россыпью, моно —
 * своим упаковщиком.
 */
import type { AssemblyDraftRow } from '@/types/api';
import { allocatePairs } from './assemblyPreview';

export interface RoundBoxesResult {
    rows: AssemblyDraftRow[];
    changed: number;       // сколько строк изменено
    filledUp: number;      // добито вверх из свободного ФФ (шт)
    trimmedDown: number;   // снято некратного на ФФ БЕЗ резерва (шт) — строгий режим
    consolidated: number;  // хвосты, переупакованные в целые коробы (шт)
    looseLeft: number;     // россыпь, оставшаяся в строках (только noTrimDown-SKU)
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
    /** SKU, которым НЕЛЬЗЯ срезать вниз (новинки cold-start): их россыпь остаётся. */
    noTrimDown?: (nmId: number) => boolean,
    /** Кратность короба КОНКРЕТНОГО ФФ (может отличаться по складам); фолбэк — ppbOf. */
    ppbAt?: (nmId: number, ffId: number) => number | null | undefined,
): RoundBoxesResult {
    let changed = 0;
    let filledUp = 0;
    let trimmedDown = 0;
    let consolidated = 0;
    let looseLeft = 0;

    const out = rows.map((r) => {
        // Кратность резолвим ПО-ПОРЦИОННО (ppbAt с фолбэком на global): у части SKU
        // короб задан ТОЛЬКО по складу (напр. Хамза), global пуст. Прежний гейт бракова́л
        // всю строку по пустой global → россыпь ОСТАВАЛАСЬ в черновике, хотя показ и
        // паллет-срез эту по-складскую кратность видят (баг «1 кор + N шт» в черновике).
        if ((r.package_type || 'BOX') !== 'BOX') return r;
        const gppb = ppbOf(r.nm_id);

        const pool = (freeByNm[r.nm_id] ??= {});
        const keepLoose = noTrimDown?.(r.nm_id) === true;

        // Порции (ФФ→WB) — та же раскладка, что показ карточек и коммит; кратность —
        // короба ФФ порции (глобальный min резал бы физически целый короб чужого ФФ).
        type Portion = { ff: number; wb: string; qty: number; p: number };
        const portions: Portion[] = [];
        for (const [pairKey, q] of allocatePairs(r.src, r.tgt)) {
            if ((q || 0) <= 0) continue;
            const sep = pairKey.indexOf('::');
            const ff = Number(pairKey.slice(0, sep));
            const wb = pairKey.slice(sep + 2);
            const pw = ppbAt?.(r.nm_id, ff);
            const p = pw && pw > 0 ? pw : (gppb && gppb > 0 ? gppb : 0);
            portions.push({ ff, wb, qty: q, p });
        }
        // Ни у одной порции нет кратности короба (ни по складу, ни global) → округлять
        // нечем, строка едет как есть (SKU без ppb / cold-start-россыпь).
        if (!portions.some((pt) => pt.p > 0)) return r;

        // Фаза 1: добор порции ВВЕРХ строго с ЕЁ ФФ (короб пакуется на одном складе).
        for (const pt of portions) {
            if (pt.p <= 0) continue;
            const rem = pt.qty % pt.p;
            if (rem === 0) continue;
            const gap = pt.p - rem;
            if ((pool[pt.ff] || 0) >= gap) {
                pool[pt.ff] -= gap;
                pt.qty += gap;
                filledUp += gap;
            }
        }

        // Фаза 2: недобитые хвосты СНИМАЮТСЯ (строгий режим — некратное не едет);
        // снятое копится per-ФФ для консолидации. noTrimDown-SKU оставляют россыпь.
        const tails = new Map<number, { units: number; p: number; wbs: { wb: string; rem: number }[] }>();
        for (const pt of portions) {
            const rem = pt.qty % pt.p;
            if (rem === 0) continue;
            if (keepLoose) { looseLeft += rem; continue; }
            pt.qty -= rem;
            let t = tails.get(pt.ff);
            if (!t) { t = { units: 0, p: pt.p, wbs: [] }; tails.set(pt.ff, t); }
            t.units += rem;
            t.wbs.push({ wb: pt.wb, rem });
        }

        // Фаза 3: консолидация снятых хвостов ОДНОГО ФФ в целые коробы (кратность у
        // порций одного ФФ одна); последний короб добивается из свободного ФФ, если
        // хватает. Коробы — на склады с наибольшими исходными хвостами. Неустранимый
        // остаток УХОДИТ в свободный ФФ — не едет и не резервируется (решение юзера).
        for (const [ff, t] of tails) {
            let boxes = Math.floor(t.units / t.p);
            let rem = t.units - boxes * t.p;
            if (rem > 0 && (pool[ff] || 0) >= t.p - rem) {
                pool[ff] -= t.p - rem;
                filledUp += t.p - rem;
                boxes += 1;
                rem = 0;
            }
            if (boxes > 0) {
                consolidated += boxes * t.p;
                const order = [...t.wbs].sort((a, b) => b.rem - a.rem).map((x) => x.wb);
                for (let k = 0; k < boxes; k++) {
                    const wb = order[k % order.length];
                    const pt = portions.find((x) => x.ff === ff && x.wb === wb);
                    if (pt) pt.qty += t.p;
                    else portions.push({ ff, wb, qty: t.p, p: t.p });
                }
            }
            if (rem > 0) {
                pool[ff] = (pool[ff] || 0) + rem;
                trimmedDown += rem;
            }
        }

        // Пересборка src/tgt из порций (Σsrc==Σtgt by construction).
        const src: Record<string, number> = {};
        const tgt: Record<string, number> = {};
        for (const pt of portions) {
            if (pt.qty <= 0) continue;
            src[String(pt.ff)] = (src[String(pt.ff)] || 0) + pt.qty;
            tgt[pt.wb] = (tgt[pt.wb] || 0) + pt.qty;
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
    /** Кратность короба КОНКРЕТНОГО ФФ (может отличаться по складам); фолбэк — ppbOf. */
    ppbAt?: (nmId: number, ffId: number) => number | null | undefined,
): AssemblyDraftRow[] {
    const out = rows.map((r) => {
        if ((r.package_type || 'BOX') !== 'MONOPALLET') return r;
        if (skipLoose?.(r.nm_id)) return r;
        const gppb = ppbOf(r.nm_id);
        if (!gppb || gppb <= 0) return r;

        const pool = (freeByNm[r.nm_id] ??= {});
        // ПОРЦИОННО (ФФ→WB) и СТРОГО с ФФ порции: кросс-ФФ добор рождал микро-порцию
        // чужого склада, которую попаллетный трим гарантированно ронял → строка
        // возвращалась некратной и следующий прогон резал её снова (неидемпотентность,
        // ловили фаззингом). Недобитый остаток срезается в пул ЕГО ФФ (на ФФ, без
        // резерва) — моно едет только целыми коробами.
        type Portion = { ff: number; wb: string; qty: number; p: number };
        const portions: Portion[] = [];
        for (const [pairKey, q] of allocatePairs(r.src, r.tgt)) {
            if ((q || 0) <= 0) continue;
            const sep = pairKey.indexOf('::');
            const ff = Number(pairKey.slice(0, sep));
            const wb = pairKey.slice(sep + 2);
            const pw = ppbAt?.(r.nm_id, ff);
            portions.push({ ff, wb, qty: q, p: pw && pw > 0 ? pw : gppb });
        }
        for (const pt of portions) {
            const rem = pt.qty % pt.p;
            if (rem === 0) continue;
            const gap = pt.p - rem;
            if ((pool[pt.ff] || 0) >= gap) {
                pool[pt.ff] -= gap;
                pt.qty += gap;                    // добить короб вверх со СВОЕГО ФФ
            } else {
                pt.qty -= rem;                    // срезать под-коробочный остаток на ФФ
                pool[pt.ff] = (pool[pt.ff] || 0) + rem;
            }
        }
        const src: Record<string, number> = {};
        const tgt: Record<string, number> = {};
        for (const pt of portions) {
            if (pt.qty <= 0) continue;
            src[String(pt.ff)] = (src[String(pt.ff)] || 0) + pt.qty;
            tgt[pt.wb] = (tgt[pt.wb] || 0) + pt.qty;
        }
        return { ...r, tgt, src };
    });
    // строки, обнулённые срезом (всё < короба, ФФ не хватило) — выбрасываем.
    return out.filter((r) => Object.keys(r.tgt).length > 0 && Object.keys(r.src).length > 0);
}

export interface TopUpPrebookResult {
    rows: AssemblyDraftRow[];
    /** Штук добито из свободного ФФ до целых коробов. */
    filledUp: number;
    changed: boolean;
}

/**
 * Добивка ПРЕДБРОНЬ-хвостов до ЦЕЛОГО КОРОБА из свободного остатка ТОГО ЖЕ ФФ.
 *
 * Правило: россыпь в предброни легитимна ТОЛЬКО когда на её ФФ свободного не осталось.
 * Фаза-A добора (`roundDraftRowsToWholeBoxes`) гоняется по строкам ЧЕРНОВИКА — хвост,
 * уже лежащий в предброни, повторно через неё не проходит и застревает как «0 кор»,
 * даже когда сток ФФ подрос. Этот шаг зовётся из normalizeAndSave ПОСЛЕ нормализации
 * rows с ТЕМ ЖЕ пулом `freeByNm` (остаток после rows-добора) → овербукинга нет.
 *
 * В отличие от фазы-A добор идёт СТРОГО с ФФ порции (не с самого ёмкого): короб
 * пакуется физически на одном ФФ, чужой ФФ хвост не спасает. Порции ФФ→WB — тот же
 * `allocatePairs`, что и показ карточек/коммит. МОНО (свой добор паллетами), SKU без
 * кратности (россыпь-новинки) и `as_is` (юзер зафиксировал частичную) не трогаются.
 * `freeByNm` мутируется. Σsrc==Σtgt держится (gap кладётся в обе стороны). Идемпотентна.
 */
export function topUpPrebookLooseBoxes(
    prebook: AssemblyDraftRow[],
    ppbOf: (nmId: number) => number | null | undefined,
    freeByNm: Record<number, Record<number, number>>,
    /** Кратность короба КОНКРЕТНОГО ФФ (может отличаться по складам); фолбэк — ppbOf. */
    ppbAt?: (nmId: number, ffId: number) => number | null | undefined,
): TopUpPrebookResult {
    let filledUp = 0;
    const out = prebook.map((r) => {
        if (r.as_is === true) return r;
        if ((r.package_type || 'BOX') !== 'BOX') return r;
        const gppb = ppbOf(r.nm_id);
        if (!gppb || gppb <= 0) return r;
        const pool = freeByNm[r.nm_id];
        if (!pool) return r;

        const src: Record<string, number> = { ...r.src };
        const tgt: Record<string, number> = { ...r.tgt };
        let rowFilled = 0;
        for (const [pairKey, portion] of allocatePairs(r.src, r.tgt)) {
            if (portion <= 0) continue;
            // ffId числовой → ПЕРВЫЙ '::' всегда разделитель (имя склада может быть любым).
            const sep = pairKey.indexOf('::');
            const ffStr = pairKey.slice(0, sep);
            const wb = pairKey.slice(sep + 2);
            const ff = Number(ffStr);
            const pw = ppbAt?.(r.nm_id, ff);
            const ppb = pw && pw > 0 ? pw : gppb;
            const rem = portion % ppb;
            if (rem === 0) continue;
            const gap = ppb - rem;
            if ((pool[ff] || 0) < gap) continue;   // на ЭТОМ ФФ добить нечем — россыпь легитимна
            pool[ff] -= gap;
            src[ffStr] = (src[ffStr] || 0) + gap;
            tgt[wb] = (tgt[wb] || 0) + gap;
            rowFilled += gap;
        }
        if (rowFilled === 0) return r;
        filledUp += rowFilled;
        return { ...r, src, tgt };
    });
    return { rows: out, filledUp, changed: filledUp > 0 };
}

export interface ReleaseLooseResult {
    rows: AssemblyDraftRow[];
    /** Штук россыпи, снятых с резерва (вернулись в свободный ФФ). */
    releasedUnits: number;
    changed: boolean;
}

/**
 * Снять с резерва предброни РОССЫПЬ, которую ДОБИТЬ НЕЧЕМ (решение юзера 2026-07-02:
 * «вообще не резервировать» — такие хвосты не едут, «Дозабить»/«Оставить так» их не
 * берут, строки 0/0 в карточках только путают).
 *
 * Зеркало-дополнение `topUpPrebookLooseBoxes`: зови ПОСЛЕ него с тем же пулом.
 * Порция (ФФ→WB) с суб-коробочным остатком, для которого свободного стока на ЭТОМ ФФ
 * меньше гэпа до целого короба, теряет россыпь (units снимаются с src/tgt), целые
 * коробы порции ОСТАЮТСЯ в резерве (ждут паллету/консолидацию). Снятое возвращается
 * в `freeByNm` (мутируется) — может разблокировать добор другого хвоста того же SKU
 * на следующем прогоне (self-heal/конс-эффект бегут на каждый PUT — сходится).
 * МОНО (свои частичные паллеты/предзаявка), SKU без кратности и `as_is` не трогаются.
 */
export function releaseUnfixableLooseBoxes(
    prebook: AssemblyDraftRow[],
    ppbOf: (nmId: number) => number | null | undefined,
    freeByNm: Record<number, Record<number, number>>,
    /** Кратность короба КОНКРЕТНОГО ФФ (может отличаться по складам); фолбэк — ppbOf. */
    ppbAt?: (nmId: number, ffId: number) => number | null | undefined,
): ReleaseLooseResult {
    let releasedUnits = 0;
    const out = prebook
        .map((r) => {
            if (r.as_is === true) return r;
            if ((r.package_type || 'BOX') !== 'BOX') return r;
            const gppb = ppbOf(r.nm_id);
            if (!gppb || gppb <= 0) return r;

            const src: Record<string, number> = { ...r.src };
            const tgt: Record<string, number> = { ...r.tgt };
            let rowReleased = 0;
            for (const [pairKey, portion] of allocatePairs(r.src, r.tgt)) {
                if (portion <= 0) continue;
                const sep = pairKey.indexOf('::');
                const ffStr = pairKey.slice(0, sep);
                const wb = pairKey.slice(sep + 2);
                const ff = Number(ffStr);
                const pw = ppbAt?.(r.nm_id, ff);
                const ppb = pw && pw > 0 ? pw : gppb;
                const rem = portion % ppb;
                if (rem === 0) continue;
                const pool = (freeByNm[r.nm_id] ??= {});
                if ((pool[ff] || 0) >= ppb - rem) continue;   // добиваемый — оставляем topUp'у
                src[ffStr] = Math.max(0, (src[ffStr] || 0) - rem);
                if (src[ffStr] <= 0) delete src[ffStr];
                tgt[wb] = Math.max(0, (tgt[wb] || 0) - rem);
                if (tgt[wb] <= 0) delete tgt[wb];
                pool[ff] = (pool[ff] || 0) + rem;
                rowReleased += rem;
            }
            if (rowReleased === 0) return r;
            releasedUnits += rowReleased;
            return { ...r, src, tgt };
        })
        .filter((r) => Object.keys(r.tgt).length > 0 && Object.keys(r.src).length > 0);
    return { rows: out, releasedUnits, changed: releasedUnits > 0 };
}

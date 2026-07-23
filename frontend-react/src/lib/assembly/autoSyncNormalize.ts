/**
 * Нормализация смёрженного плана авто-синка — ДВУСТОРОННИЙ клапан.
 *
 * До 2026-07-22 синк был односторонним: демоция (rows → предбронь при ⌛/закрытой
 * приёмке) выполнялась и расчётом, и ре-нормализацией, а ОБРАТНОЙ промоции у
 * фоновых акторов не было — собравшиеся в предброни целые паллеты направления
 * с уже ОТКРЫВШИМСЯ приёмом застревали там навсегда (прод: 5 целых паллет
 * апл→Екатеринбург при «приём открыт · 14 дн»); единственный промоутер —
 * страничный эффект консолидации — скипался собственным гейтом после синк-PUT.
 *
 * Здесь: полный scopedNormalizeDraft (с РЕАЛЬНЫМ пулом свободного ФФ — без него
 * releaseUnfixableLooseBoxes считал любой некратный хвост «добить нечем» и тихо
 * снимал его с резерва) + consolidatePrebookWholePallets по живой приёмке:
 * целые паллеты ГОТОВЫХ направлений (тип принимается И лимит > 0) уезжают в rows,
 * «нет данных ≠ готов» — без данных приёмки направление держится в предброни.
 * Идемпотентно (после выноса группа < 1 паллеты) — PUT-цикла нет.
 */
import { consolidatePrebookWholePallets, type NormalizeDraftCtx } from '@/lib/utils/normalizeDraft';
import { mergeDraftRows } from '@/lib/utils/scopedNormalizeDraft';
import { scopedNormalizeDraft } from '@/lib/utils/scopedNormalizeDraft';
import type { AcceptanceFlags, AssemblyDraftRow, StockNeedArticle } from '@/types/api';

/** Пул свободного ФФ per nm: available минус занятое ПЕРЕДАННЫМ планом
 *  (зеркало пула netOutInTransit — резерв других черновиков вычтен вызывающим). */
export function buildFreePoolByNm(
    articles: StockNeedArticle[],
    rows: AssemblyDraftRow[],
    prebook: AssemblyDraftRow[],
): Record<number, Record<number, number>> {
    const inDraft: Record<number, Record<number, number>> = {};
    for (const r of [...rows, ...prebook]) {
        const m = (inDraft[r.nm_id] ??= {});
        for (const [ff, q] of Object.entries(r.src || {})) m[Number(ff)] = (m[Number(ff)] || 0) + (q || 0);
    }
    const freeByNm: Record<number, Record<number, number>> = {};
    for (const a of articles) {
        const pool: Record<number, number> = {};
        for (const [ff, st] of Object.entries(a.rf_stocks || {})) {
            const free = (st.available || 0) - (inDraft[a.nm_id]?.[Number(ff)] || 0);
            if (free > 0) pool[Number(ff)] = free;
        }
        if (Object.keys(pool).length) freeByNm[a.nm_id] = pool;
    }
    return freeByNm;
}

/** Направления (`${pkg}::${wb}`), ГОТОВЫЕ к сдаче по живой приёмке: тип принимается
 *  И есть лимит (свободные ИЛИ платные дни > 0). Зеркало readyPkgWbs страницы. */
export function readyPkgWbsFromAvailability(
    perItemAvailability: Iterable<Record<string, AcceptanceFlags>>,
): Set<string> {
    const out = new Set<string>();
    const days = (m?: { free_days_14?: number; paid_days_14?: number } | null) =>
        (m?.free_days_14 ?? 0) + (m?.paid_days_14 ?? 0);
    for (const per of perItemAvailability) {
        for (const [wb, f] of Object.entries(per || {})) {
            if (f.can_box && days(f.box_meta) > 0) out.add(`BOX::${wb}`);
            if (f.can_monopallet && days(f.mono_meta) > 0) out.add(`MONOPALLET::${wb}`);
            if (f.can_supersafe && days(f.super_meta) > 0) out.add(`SUPERSAFE::${wb}`);
        }
    }
    return out;
}

/** normalizePair для `buildAutoSyncPlan` (страничный раннер и матрица).
 *  КОНТРАКТ: геометрия `baseCtx` (ppbOf/ppbAt/boxSizeOf) обязана покрывать ВСЕ nm
 *  пары rows+prebook (карты getBoxMultiplicity/getPalletBoxesBySize — полные,
 *  НЕ срез articles): промоция уже-целых паллет работает и для SKU вне articles
 *  (✋/unowned/чужая категория — прод: швабра 59 кор). `articles` задаёт ТОЛЬКО
 *  пул свободного ФФ — добор хвостов чужим SKU не положен, без геометрии
 *  SKU не трогается (fail-closed). Закреплено тестами autoSyncNormalize.test.ts. */
export function makeAutoSyncNormalizePair(
    baseCtx: NormalizeDraftCtx,
    articles: StockNeedArticle[],
    readyPkgWbs: Set<string>,
): (rows: AssemblyDraftRow[], prebook: AssemblyDraftRow[]) => { rows: AssemblyDraftRow[]; prebook: AssemblyDraftRow[] } {
    return (rs, pb) => {
        const ctx: NormalizeDraftCtx = { ...baseCtx, freeByNm: buildFreePoolByNm(articles, rs, pb) };
        const n = scopedNormalizeDraft(rs, pb, ctx, undefined);
        const cons = consolidatePrebookWholePallets(
            n.prebook,
            ctx,
            (_nm, wb, pkg) => !readyPkgWbs.has(`${pkg}::${wb}`),
        );
        return cons.changed
            ? { rows: mergeDraftRows([...n.rows, ...cons.toDraft]), prebook: cons.prebook }
            : { rows: n.rows, prebook: n.prebook };
    };
}

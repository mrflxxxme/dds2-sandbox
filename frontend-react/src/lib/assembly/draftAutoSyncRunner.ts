/**
 * Headless-раннер авто-синка черновиков — ТА ЖЕ расчётная цепочка, что у
 * «Ручной раскладки» (DraftMatrixView.computeDistribution → buildAutoSyncPlan),
 * но без монтирования матрицы: страница зовёт его при заходе и раз в час для
 * КАЖДОГО черновика (категорийные по очереди → главный), резерв перечитывается
 * свежим GET между шагами — то, что записал предыдущий черновик, честно
 * вычитается у следующего.
 *
 * Инварианты прохода:
 *  • ✋ manual_nms и prebook_origin-ячейки строк священны (buildAutoSyncPlan);
 *  • предбронь пересобирается расчётом целиком (решение юзера 2026-07-19);
 *  • проверка приёмки упала → черновик НЕ персистится (fail-open как в матрице);
 *  • PUT под CAS (base_updated_at) — конкурентную запись не перекрываем, шаг
 *    просто пропускается (следующий тик догонит);
 *  • событие AUTO_SYNC пишется только при реальной дельте (null-гейт плана).
 */
import { api } from '@/lib/api';
import type {
    AcceptanceSplitMap,
    DraftDistInput,
} from '@/lib/assembly/draftDistribution';
import {
    applyAcceptanceSplits,
    buildAutoSyncPlan,
    buildDraftSkus,
    finalizeDraftDistribution,
    splitByKratnost,
} from '@/lib/assembly/draftDistribution';
import { subtractReserveFromArticles, restrictArticlesToFf } from '@/lib/assembly/draftReserve';
import { inScopeOf, type CategoryOf } from '@/lib/assembly/categoryCompat';
import { allocatePairs } from '@/lib/utils/assemblyPreview';
import { parseBoxSize } from '@/lib/utils/boxPallet';
import { NEED_ANALYSIS_DAYS, NEED_SUPPLY_DAYS } from '@/lib/assembly/needParams';
import { formatNumber } from '@/lib/utils';
import type {
    AssemblyDraft,
    AssemblyDraftRow,
    StockNeedArticle,
    StockNeedResponse,
} from '@/types/api';

/** Возраст остатков WB, после которого синк пропускается: расчёт от протухшего
 *  зеркала прилежно синкал бы черновики к неправде (зеркало уже замирало на
 *  4 дня 15–17.07 — синк обязан останавливаться и звать, а не работать). */
export const WB_STOCKS_STALE_HOURS = 6;

/** Общие данные одного прохода — грузятся ОДИН раз на все черновики. */
export interface AutoSyncSharedData {
    stockNeed: StockNeedResponse;
    nmPpb: Map<number, number | null>;
    nmPpbByWh: Map<number, Record<number, number>>;
    nmBoxSize: Map<number, string | null>;
    palletOverrides: Record<string, number>;
    newcomerSet: Set<number>;
    newcomerAlloc: Map<number, Record<string, number>>;
    guardedNms: Set<number>;
    /** Возраст зеркала остатков WB в часах; null — бэк поле ещё не отдаёт. */
    wbStocksAgeHours: number | null;
}

export type DraftSyncStatus = 'synced' | 'no-delta' | 'skipped';
export interface DraftSyncOutcome {
    draftId: number;
    name: string;
    status: DraftSyncStatus;
    reason?: 'no-articles' | 'acceptance-failed' | 'version-conflict' | 'error';
    before?: number;
    after?: number;
    error?: string;
    /** Свежий черновик после успешного PUT — для applyDraft текущего на странице. */
    updated?: AssemblyDraft;
}

/** Маркер последнего ЗАВЕРШЁННОГО прохода — модульный: переживает навигацию по
 *  SPA. Гейтит и «заход» (каждый вход на страницу = полный проход с PUT'ами
 *  выедал write-бакет → 429 на входном запросе страницы, прод 2026-07-20). */
let lastPassAt = 0;
export const getLastAutoSyncPassAt = (): number => lastPassAt;
export const markAutoSyncPass = (): void => { lastPassAt = Date.now(); };

/** Данные, которые страница уже загрузила сама — раннер их НЕ перекачивает
 *  (дубль тяжёлых GET на каждый заход тоже давил rate-limit). */
export interface AutoSyncReuse {
    stockNeed?: StockNeedResponse | null;
    geometry?: {
        nmPpb: Map<number, number | null>;
        nmPpbByWh: Map<number, Record<number, number>>;
        nmBoxSize: Map<number, string | null>;
        palletOverrides: Record<string, number>;
    } | null;
}

/** Загрузка общих данных прохода (зеркало load-эффекта матрицы, без display-only). */
export async function loadAutoSyncSharedData(reuse?: AutoSyncReuse): Promise<AutoSyncSharedData> {
    let csp: { minPack?: number; shipPct?: number; floor?: number } = {};
    try { csp = JSON.parse(localStorage.getItem('dds.coldStartParams') || '{}'); } catch { /* дефолты */ }
    const [need, cold, boxMult, palletOv] = await Promise.all([
        reuse?.stockNeed
            ? Promise.resolve(reuse.stockNeed)
            : api.getStockNeed(NEED_SUPPLY_DAYS, NEED_ANALYSIS_DAYS, 'actual', true, true, 0) as Promise<StockNeedResponse>,
        api.getColdStartTable(14, csp.minPack ?? 5, csp.shipPct ?? 55, csp.floor ?? 50).catch(() => null),
        reuse?.geometry ? Promise.resolve(null) : api.getBoxMultiplicity().catch(() => null),
        reuse?.geometry ? Promise.resolve({} as Record<string, number>) : api.getPalletBoxesBySize().catch(() => ({} as Record<string, number>)),
    ]);
    const newcomerSet = new Set<number>();
    const newcomerAlloc = new Map<number, Record<string, number>>();
    const guardedNms = new Set<number>();
    for (const r of cold?.rows ?? []) {
        if (r.is_newcomer) newcomerSet.add(r.nm_id);
        if (r.oversort_guard) { guardedNms.add(r.nm_id); continue; }
        if (r.allocations && Object.keys(r.allocations).length > 0) newcomerAlloc.set(r.nm_id, r.allocations);
    }
    const nmPpb = new Map<number, number | null>();
    const nmPpbByWh = new Map<number, Record<number, number>>();
    const nmBoxSize = new Map<number, string | null>();
    for (const r of boxMult?.items ?? []) {
        let ppb: number | null = null;
        if (r.box_qty_override && r.box_qty_override > 0 && r.use_box_multiplicity) {
            ppb = r.box_qty_override;
        } else {
            let best = 0;
            let perWh: Record<number, number> | null = null;
            for (const p of r.per_warehouse) {
                if (p.box_qty && p.box_qty > 0 && p.use_box_multiplicity) {
                    if (best === 0 || p.box_qty < best) best = p.box_qty;
                    (perWh ??= {})[p.warehouse_id] = p.box_qty;
                }
            }
            ppb = best > 0 ? best : null;
            if (perWh) nmPpbByWh.set(r.nm_id, perWh);
        }
        let boxSize: string | null = null;
        let bestStock = -1;
        for (const p of r.per_warehouse) {
            if (!p.box_size || !parseBoxSize(p.box_size)) continue;
            if (p.rf_stock > bestStock) { boxSize = p.box_size; bestStock = p.rf_stock; }
        }
        nmPpb.set(r.nm_id, ppb);
        nmBoxSize.set(r.nm_id, boxSize);
    }
    if (reuse?.geometry) {
        // Карты страницы уже готовы (loadGeometry) — не дублируем разбор.
        for (const [k, v] of reuse.geometry.nmPpb) nmPpb.set(k, v);
        for (const [k, v] of reuse.geometry.nmPpbByWh) nmPpbByWh.set(k, v);
        for (const [k, v] of reuse.geometry.nmBoxSize) nmBoxSize.set(k, v);
    }
    const stampRaw = need.summary?.wb_stocks_updated_at ?? null;
    let wbStocksAgeHours: number | null = null;
    if (stampRaw) {
        const t = Date.parse(stampRaw.endsWith('Z') || stampRaw.includes('+') ? stampRaw : `${stampRaw}Z`);
        if (Number.isFinite(t)) wbStocksAgeHours = Math.max(0, (Date.now() - t) / 3_600_000);
    }
    return { stockNeed: need, nmPpb, nmPpbByWh, nmBoxSize, palletOverrides: reuse?.geometry ? reuse.geometry.palletOverrides : (palletOv || {}), newcomerSet, newcomerAlloc, guardedNms, wbStocksAgeHours };
}

const sumUnits = (rows: AssemblyDraftRow[]): number =>
    rows.reduce((s, r) => s + Object.values(r.tgt || {}).reduce((a, v) => a + (v || 0), 0), 0);

/**
 * Синк ОДНОГО черновика к живому расчёту. Резерв других черновиков читается
 * внутри (свежий GET) — вызывающий обязан идти последовательно.
 * `categoryOf`/`classOf` — те же мемо, что у страницы (паритет скоупов/классов).
 */
export async function runDraftAutoSync(
    draft: AssemblyDraft,
    shared: AutoSyncSharedData,
    categoryOf: CategoryOf,
    classOf: (nm: number) => string,
): Promise<DraftSyncOutcome> {
    const base: DraftSyncOutcome = { draftId: draft.id, name: draft.name, status: 'skipped' };
    try {
        const reservedResp = await api.getDraftsReserved(draft.id);
        const reserved = reservedResp.reserved || {};
        const scope = draft.distribution.category_scope ?? null;
        const scopeFfId = draft.distribution.scope_ff_id ?? null;
        let articles: StockNeedArticle[] = subtractReserveFromArticles(shared.stockNeed.articles ?? [], reserved);
        articles = restrictArticlesToFf(articles, scopeFfId);
        if (scope?.length) {
            const inSc = inScopeOf(scope, categoryOf);
            articles = articles.filter((a) => inSc(a.nm_id));
        }
        articles = articles.filter((a) =>
            Object.values(a.rf_stocks || {}).reduce((s, st) => s + Math.max(0, Number(st?.available) || 0), 0) > 0);
        if (articles.length === 0) return { ...base, reason: 'no-articles' };

        const priorityByWh: Record<string, number> = {};
        for (const w of shared.stockNeed.warehouses ?? []) if (w.priority_weight != null) priorityByWh[w.name] = Number(w.priority_weight) || 0;
        const distInput: DraftDistInput = {
            articles,
            stockNeed: shared.stockNeed,
            nmPpb: shared.nmPpb,
            nmBoxSize: shared.nmBoxSize,
            palletOverrides: shared.palletOverrides,
            newcomerSet: shared.newcomerSet,
            newcomerAlloc: shared.newcomerAlloc,
            ppbAt: (nm, ffId) => shared.nmPpbByWh.get(nm)?.[ffId] ?? null,
            priorityByWh,
            classOf,
        };
        const { skus: allSkus } = buildDraftSkus(distInput);
        const { shippable: autoSkus } = splitByKratnost(allSkus, (nm) => shared.nmPpb.get(nm));
        // Приёмка — живой батч КАЖДЫЙ проход (без модульного кэша: он вечно блокировал
        // синк после одного сбоя и переиспользовал устаревшие лимиты между тиками —
        // ревью MEDIUM×2). Квоту бережёт пер-баркод кэш бэка (10 мин), тик — часовой.
        let splitMap: AcceptanceSplitMap = new Map();
        if (autoSkus.length > 0) {
            try {
                const resp = await api.checkWbAcceptance({
                    items: autoSkus.map((s) => ({ nm_id: s.nm_id, barcode: s.barcode, distribution: s.target })),
                });
                for (const it of resp.items) {
                    const splits = it.splits?.length
                        ? it.splits.map((sp) => ({ package_type: sp.package_type, distribution: sp.distribution }))
                        : [{ package_type: it.package_type, distribution: it.distribution }];
                    splitMap.set(`${it.nm_id}::${it.barcode}`, splits);
                }
            } catch {
                // Fail-open: без живой приёмки план не персистим — иначе синканули бы
                // непроверенный план на закрытые склады. Следующий тик честно ретраит.
                return { ...base, reason: 'acceptance-failed' };
            }
        }
        const effective = applyAcceptanceSplits(autoSkus, splitMap);
        const whole = finalizeDraftDistribution(effective, distInput, true, [], true);
        // Предбронь расчёта — атомарные направления per ФФ×WB (зеркало effPrebook матрицы).
        const effPrebook: AssemblyDraftRow[] = [];
        for (const r of whole.prebook) {
            const pkg = r.package_type ?? 'BOX';
            for (const [pair, q] of allocatePairs(r.src, r.tgt)) {
                if ((q || 0) <= 0) continue;
                const [ff, wb] = pair.split('::');
                effPrebook.push({ nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [ff]: q }, tgt: { [wb]: q }, package_type: pkg });
            }
        }
        // База плана — СВЕЖИЙ GET (не снапшот вызова): между расчётом и записью
        // черновик могли тронуть; CAS ниже отсекает и эту гонку.
        const cur = await api.getAssemblyDraft(draft.id);
        const dist = cur.distribution ?? {};
        const curManual = new Set<number>(dist.manual_nms ?? []);
        const preserve = new Set<string>(dist.prebook_origin ?? []);
        const plan = buildAutoSyncPlan(dist, whole.rows, effPrebook, shared.guardedNms, curManual, preserve);
        if (!plan) return { ...base, status: 'no-delta' };
        const before = sumUnits(dist.rows ?? []) + sumUnits(dist.prebook ?? []);
        const after = sumUnits(plan.rows) + sumUnits(plan.prebook);
        try {
            const updated = await api.updateAssemblyDraft(draft.id, {
                distribution: { ...dist, ...plan },
                event: {
                    event_type: 'AUTO_SYNC',
                    summary: `Авто-синк с расчётом (страница): ${formatNumber(before, 0)} → ${formatNumber(after, 0)} шт`
                        + (curManual.size > 0 ? ` (✋ ручных не тронуто: ${curManual.size})` : ''),
                },
                base_updated_at: cur.updated_at,
            });
            return { ...base, status: 'synced', before, after, updated };
        } catch (e) {
            if (e instanceof Error && e.message.includes('DRAFT_VERSION_CONFLICT')) {
                return { ...base, reason: 'version-conflict' };
            }
            throw e;
        }
    } catch (e) {
        return { ...base, reason: 'error', error: e instanceof Error ? e.message : String(e) };
    }
}

/**
 * Раскладка ЧЕРНОВИКА по WB-складам — детерминированный движок ручного
 * распределения. Зеркало `preDistribution.ts` (машина), но ИСТОЧНИК —
 * свободный ФФ-сток проекта (`StockNeedArticle.rf_stocks`, мульти-склад), а
 * не пул одной машины. Единственная развилка черновик↔машина в общем движке
 * `buildAssemblyDistribution` — `availabilityOf(nm, barcode)`:
 *   • черновик: availabilityOf(nm,bc) = article.rf_stocks[ffId].available (мульти-ФФ)
 *   • машина:   availabilityOf(nm,bc) = { [targetWarehouseId]: pool.available_qty }
 *
 * Отличие от машины: остаток баркода размазан по НЕСКОЛЬКИМ ФФ, поэтому `src`
 * ручных строк (пины/дозабор) раскладывается по складам-источникам
 * (`allocSrcAcrossFf`, крупнейший остаток первым), а не сидит на одном ФФ.
 * Авто-путь мульти-ФФ `src` уже умеет через `buildDraftRows`/`finalizeDistribution`.
 *
 * Чистая (без React/IO): acceptance-check вызывает компонент и передаёт сюда
 * `applyAcceptanceSplits` уже применёнными скусами — полностью юнит-тестируется.
 */
import type {
    AssemblyDraftRow,
    PackageType,
    StockNeedArticle,
    StockNeedResponse,
} from '@/types/api';
import { type DraftSkuInput } from '@/lib/assembly/buildDraftRows';
import {
    applyAcceptanceSplits,
    buildDistributionSkus,
    finalizeDistribution,
    type AcceptanceSplitMap,
    type AvailabilityOf,
    type DistSku,
    type DistributionGeom,
} from '@/lib/assembly/buildAssemblyDistribution';
// Generic-хелперы ручного режима (источник-агностичные) — общий модуль с машиной.
import {
    applyCellBoxDelta,
    rowsToPreDistRows,
    splitByKratnost,
    type BoxTopUpCandidate,
    type CellEdits,
    type EnrichedSku,
    type PinnedPkgOf,
} from '@/lib/assembly/preDistribution';
import { allocatePairs } from '@/lib/utils/assemblyPreview';

// Приёмка/сплиты и generic-хелперы — реэкспорт для call-site черновика.
export { applyAcceptanceSplits, applyCellBoxDelta, rowsToPreDistRows, splitByKratnost };
export type { AcceptanceSplitMap, BoxTopUpCandidate, CellEdits, EnrichedSku, PinnedPkgOf };

export interface DraftDistInput {
    /** Артикулы «Потребности по складам» (`getStockNeed`) — источник ФФ-стока и спроса. */
    articles: StockNeedArticle[];
    /** Полный ответ потребности (per-WB спрос) — тот же объект. */
    stockNeed: StockNeedResponse | null;
    /** Кратность короба per nm_id (шт/короб). */
    nmPpb: Map<number, number | null>;
    /** Габариты короба «ДxШxВ» per nm_id. */
    nmBoxSize: Map<number, string | null>;
    /** Override «коробок на паллету» по канон-размеру короба. */
    palletOverrides: Record<string, number>;
    /** Новинки (cold-start) — засеваются отдельно, в base-skus помечаются is_newcomer. */
    newcomerSet: Set<number>;
    /** Авто-раздача новинок из backend cold-start (`allocations` per nm_id) — канал
     *  новинок ручной раскладки. Гвард пересорта уже применён на бэке (guarded SKU
     *  приходит с пустым alloc → новинка остаётся с нулями, причина — в guard_reason
     *  строки cold-start). Не задан → новинки без авто-раскладки (как раньше). */
    newcomerAlloc?: Map<number, Record<string, number>>;
}

/** Геометрия движка из DraftDistInput (кратность/габарит per nm + паллет-override). */
function draftGeom(input: DraftDistInput): DistributionGeom {
    return {
        ppbOf: (nm) => input.nmPpb.get(nm),
        boxSizeOf: (nm) => input.nmBoxSize.get(nm) ?? null,
        palletOverrides: input.palletOverrides,
    };
}

/** Свободный ФФ-остаток артикула (мульти-склад): { ffId → available }, только >0. */
function ffAvailOf(a: StockNeedArticle): Record<number, number> {
    const rec: Record<number, number> = {};
    for (const [ff, st] of Object.entries(a.rf_stocks || {})) {
        const av = Math.max(0, Math.floor(Number(st?.available) || 0));
        if (av > 0) rec[Number(ff)] = av;
    }
    return rec;
}

/** Источник доступности ЧЕРНОВИКА: свободный ФФ-сток per nm (мульти-склад). */
function draftAvailabilityOf(articles: StockNeedArticle[]): AvailabilityOf {
    const byNm = new Map<number, Record<number, number>>();
    for (const a of articles) byNm.set(a.nm_id, ffAvailOf(a));
    return (nm) => byNm.get(nm) ?? {};
}

/** Артикулы как кандидаты движка (обычные + пометка новинок для отдельного засева). */
function articlesToDistSkus(
    articles: StockNeedArticle[],
    newcomerSet: Set<number>,
): { skus: DistSku[]; nmByBarcode: Map<string, number> } {
    const skus: DistSku[] = [];
    const nmByBarcode = new Map<string, number>();
    for (const a of articles) {
        const avail = Object.values(ffAvailOf(a)).reduce((s, v) => s + v, 0);
        nmByBarcode.set(a.barcode, a.nm_id);
        skus.push({
            nm_id: a.nm_id,
            barcode: a.barcode,
            vendor_code: a.vendor_code || a.barcode,
            is_newcomer: newcomerSet.has(a.nm_id),
            available: avail,
        });
    }
    return { skus, nmByBarcode };
}

/** Базовые `DraftSkuInput` ДО приёмки: потребность × ФФ-сток (мульти-склад).
 *  Тонкий адаптер над общим движком `buildDistributionSkus`. */
export function buildDraftSkus(input: DraftDistInput): {
    skus: DraftSkuInput[];
    /** barcode → nm_id (для матрицы/обратного маппинга). */
    nmByBarcode: Map<string, number>;
} {
    const { skus: distSkus, nmByBarcode } = articlesToDistSkus(input.articles, input.newcomerSet);
    const skus = buildDistributionSkus(distSkus, input.stockNeed, draftAvailabilityOf(input.articles), draftGeom(input));
    // Канал новинок: движок исключает is_newcomer из need-канала (потребность от
    // продаж у новинки ~всегда 0) — target берём из backend cold-start `allocations`
    // (там уже вычтены WB-сток/сборки, применены концентрация, ship-кап и гвард
    // пересорта). Дальше новинка идёт общим конвейером: приёмка → целые
    // коробы/паллеты → предбронь.
    if (input.newcomerAlloc && input.newcomerAlloc.size > 0) {
        const geom = draftGeom(input);
        for (const a of input.articles) {
            if (!input.newcomerSet.has(a.nm_id)) continue;
            const alloc = input.newcomerAlloc.get(a.nm_id);
            if (!alloc) continue;
            const target = Object.fromEntries(Object.entries(alloc).filter(([, q]) => (q || 0) > 0));
            if (Object.keys(target).length === 0) continue;
            const avail = ffAvailOf(a);
            if (Object.values(avail).reduce((s, v) => s + v, 0) <= 0) continue;
            skus.push({
                nm_id: a.nm_id,
                barcode: a.barcode,
                vendor_code: a.vendor_code || a.barcode,
                target,
                ffStock: avail,
                ppb: geom.ppbOf(a.nm_id),
                box_size: geom.boxSizeOf(a.nm_id) ?? null,
                packageType: 'BOX',
            });
        }
    }
    return { skus, nmByBarcode };
}

/**
 * Финальная раскладка черновика — тонкий адаптер над общим движком
 * `finalizeDistribution`. Источник (`ffStock`) — мульти-ФФ из `buildDraftSkus`.
 * `wholePallets=true` — строго целые паллеты (хвост < паллеты → предбронь);
 * `false` — только целые коробы (частичные паллеты ок → матрица покрытия).
 */
export function finalizeDraftDistribution(
    effectiveSkus: DraftSkuInput[],
    input: DraftDistInput,
    wholePallets = true,
    extraRows: AssemblyDraftRow[] = [],
    /** «Не менее 1 короба на нуждающийся склад» — для матрицы. */
    minOneBoxPerWh = false,
): { rows: AssemblyDraftRow[]; prebook: AssemblyDraftRow[] } {
    return finalizeDistribution(effectiveSkus, draftGeom(input), wholePallets, extraRows, minOneBoxPerWh);
}

/**
 * Разложить `total` штук по ФФ-складам-источникам (крупнейший остаток первым),
 * с учётом уже занятого (`reserved` per ff). Возвращает `src` (ffId → штук).
 * Σ(src) === min(total, Σ свободного) — вызывающий гарантирует total ≤ свободного.
 * Чистая.
 */
export function allocSrcAcrossFf(
    total: number,
    ffAvail: Record<number, number>,
    reserved: Record<number, number> = {},
): Record<string, number> {
    const src: Record<string, number> = {};
    if (total <= 0) return src;
    const entries = Object.entries(ffAvail)
        .map(([ff, av]) => ({ ff: Number(ff), free: Math.max(0, (av || 0) - (reserved[Number(ff)] || 0)) }))
        .filter((x) => x.free > 0)
        .sort((a, b) => b.free - a.free || a.ff - b.ff);
    let need = total;
    for (const e of entries) {
        if (need <= 0) break;
        const take = Math.min(need, e.free);
        if (take <= 0) continue;
        src[String(e.ff)] = take;
        need -= take;
    }
    return src;
}

/** Ключ строки распределения для замены по SKU (nm × баркод × упаковка × as_is) —
 *  зеркало бэкенд-ключа `_merge_rows`/`_dedupe_rows`. */
export const keyOfDraftRow = (r: AssemblyDraftRow): string =>
    `${r.nm_id}::${r.barcode || ''}::${r.package_type || 'BOX'}::${r.as_is ? 1 : 0}`;

/**
 * Итог «Пересчитать от потребности → в черновик» — ЗАМЕНА по SKU:
 * строки/предбронь SKU, которыми владеет расчёт (дал отгрузку ИЛИ предбронь),
 * заменяются его результатом; SKU из `purgeNms` (новинки под гвардом пересорта —
 * посев лежит на WB без продаж, расчёт им даёт 0) вычищаются ЦЕЛИКОМ, иначе их
 * старый план (особенно предбронь) висел бы в черновике вечно; прочие SKU не
 * трогаются. Списки складов пересчитываются из новых строк. Чистая.
 */
export function buildWriteDistribution(
    dist: { rows?: AssemblyDraftRow[] | null; prebook?: AssemblyDraftRow[] | null },
    shipRows: AssemblyDraftRow[],
    effPrebook: AssemblyDraftRow[],
    purgeNms: Set<number> = new Set(),
): { rows: AssemblyDraftRow[]; prebook: AssemblyDraftRow[]; source_warehouse_ids: number[]; target_warehouse_names: string[] } {
    const owned = new Set([...shipRows, ...effPrebook].map(keyOfDraftRow));
    const keep = (r: AssemblyDraftRow) => !owned.has(keyOfDraftRow(r)) && !purgeNms.has(r.nm_id);
    const rows = [...(dist.rows ?? []).filter(keep), ...shipRows];
    const prebook = [...(dist.prebook ?? []).filter(keep), ...effPrebook];
    const srcIds = new Set<number>();
    const tgtNames = new Set<string>();
    for (const r of [...rows, ...prebook]) {
        for (const ff of Object.keys(r.src || {})) srcIds.add(Number(ff));
        for (const wb of Object.keys(r.tgt || {})) tgtNames.add(wb);
    }
    return { rows, prebook, source_warehouse_ids: [...srcIds], target_warehouse_names: [...tgtNames] };
}

/**
 * Правка ячейки ЧЕРНОВИКА (матрица = редактор черновика): изменить план SKU на
 * складе `wh` на `deltaBoxes` целых коробов. BOX-строки и ВСЯ предбронь этого
 * SKU сливаются в одну строку rows (ручная правка = точный план юзера, «хвост в
 * предбронь» — только для авто-пересчёта); строки других типов упаковки
 * (MONOPALLET/SUPERSAFE) не трогаются. Σtgt капится свободным ФФ-остатком SKU
 * (минус то, что уже занято нетронутыми строками); при превышении — null
 * (вызывающий игнорирует клик). src пересобирается по ФФ (крупнейший первым).
 * Возвращает новые строки ТОЛЬКО этого SKU: rows-замена и prebook=[] (поглощена).
 * Чистая — юнит-тестируется.
 */
export function applyDraftCellEdit(
    skuRows: AssemblyDraftRow[],
    skuPrebook: AssemblyDraftRow[],
    article: StockNeedArticle,
    wh: string,
    deltaBoxes: number,
    ppb: number | null | undefined,
): { rows: AssemblyDraftRow[]; prebook: AssemblyDraftRow[] } | null {
    if (!ppb || ppb <= 0 || !deltaBoxes) return null;
    const isBox = (r: AssemblyDraftRow) => (r.package_type ?? 'BOX') === 'BOX';
    const kept = skuRows.filter((r) => !isBox(r)); // MONO/SUPERSAFE остаются как есть
    const merged = [...skuRows.filter(isBox), ...skuPrebook];

    const tgt: Record<string, number> = {};
    for (const r of merged) {
        for (const [w, q] of Object.entries(r.tgt || {})) if ((q || 0) > 0) tgt[w] = (tgt[w] || 0) + (q || 0);
    }
    tgt[wh] = Math.max(0, (tgt[wh] || 0) + deltaBoxes * ppb);
    for (const k of Object.keys(tgt)) if ((tgt[k] || 0) <= 0) delete tgt[k];

    const ffAvail = ffAvailOf(article);
    const keptUnits = kept.reduce((s, r) => s + Object.values(r.tgt || {}).reduce((a, v) => a + (v || 0), 0), 0);
    const availForBox = Math.max(0, Object.values(ffAvail).reduce((s, v) => s + v, 0) - keptUnits);
    const total = Object.values(tgt).reduce((s, v) => s + v, 0);
    if (total > availForBox) return null; // не хватает свободного ФФ — клик игнорируется

    if (total === 0) return { rows: kept, prebook: [] };
    const base = merged[0] ?? kept[0];
    const row: AssemblyDraftRow = {
        nm_id: article.nm_id,
        barcode: article.barcode,
        vendor_code: article.vendor_code || base?.vendor_code || String(article.nm_id),
        src: allocSrcAcrossFf(total, ffAvail),
        tgt,
        package_type: 'BOX',
    };
    return { rows: [...kept, row], prebook: [] };
}

/**
 * Отредактированные вручную ячейки матрицы (пины) → строки ЦЕЛЫХ коробов для
 * общего движка (`finalizeDistribution` как `extraRows`). Отличие от машины:
 * `src` РАСКЛАДЫВАЕТСЯ по нескольким ФФ (`allocSrcAcrossFf`) — остаток баркода
 * размазан по складам, а не сидит на одном ФФ разгрузки.
 *
 * Каждый пин (barcode × WB) = `boxes × ppb`; тип упаковки — из приёмки
 * (`pinnedPkgOf`). Σ по баркоду капится суммарным свободным ФФ-остатком. Одна
 * строка на (barcode × package_type), Σsrc==Σtgt. Чистая — юнит-тестируется.
 */
export function buildPinnedRowsFf(
    cellEdits: CellEdits,
    articles: StockNeedArticle[],
    nmPpb: Map<number, number | null>,
    pinnedPkgOf: PinnedPkgOf,
): AssemblyDraftRow[] {
    if (cellEdits.size === 0) return [];
    const byBc = new Map<string, StockNeedArticle>();
    for (const a of articles) byBc.set(a.barcode, a);
    const rows: AssemblyDraftRow[] = [];
    for (const [bc, whBoxes] of cellEdits) {
        const a = byBc.get(bc);
        if (!a || !a.nm_id) continue;
        const ppb = nmPpb.get(a.nm_id) || 0;
        if (ppb <= 0) continue;
        const ffAvail = ffAvailOf(a);
        let cap = Object.values(ffAvail).reduce((s, v) => s + v, 0);
        const byPkg = new Map<PackageType, Record<string, number>>();
        for (const wb of Object.keys(whBoxes).sort()) {
            const boxes = Math.max(0, Math.floor(whBoxes[wb] || 0));
            if (boxes <= 0) continue;
            const units = Math.min(boxes * ppb, Math.floor(cap / ppb) * ppb);
            if (units <= 0) continue;
            cap -= units;
            const pkg = pinnedPkgOf(a.nm_id, wb);
            const rec = byPkg.get(pkg) ?? {};
            rec[wb] = (rec[wb] ?? 0) + units;
            byPkg.set(pkg, rec);
        }
        // src делим по ФФ; reserved держит уже разложенное между pkg одного баркода.
        const reserved: Record<number, number> = {};
        for (const [pkg, tgt] of byPkg) {
            const total = Object.values(tgt).reduce((s, v) => s + v, 0);
            if (total <= 0) continue;
            const src = allocSrcAcrossFf(total, ffAvail, reserved);
            for (const [ff, q] of Object.entries(src)) reserved[Number(ff)] = (reserved[Number(ff)] || 0) + q;
            rows.push({ nm_id: a.nm_id, barcode: bc, vendor_code: a.vendor_code || bc, src, tgt, package_type: pkg });
        }
    }
    return rows;
}

/**
 * Разложить дозабор BOX-направления (кандидаты nm+коробы) на баркоды свободного
 * ФФ-стока, ЦЕЛЫМИ коробами. Артикул черновика = один баркод на nm; кап по
 * суммарному свободному остатку (available − уже разложено, `allocByBc`).
 * Возвращает добавки (barcode→wb→штук) для накопления в manualTopUp. Чистая.
 */
export function planBoxTopUpFf(
    candidates: BoxTopUpCandidate[],
    wb: string,
    articles: StockNeedArticle[],
    allocByBc: Map<string, number>,
    nmPpb: Map<number, number | null>,
): { barcode: string; wb: string; units: number }[] {
    const byNm = new Map<number, StockNeedArticle>();
    for (const a of articles) if (!byNm.has(a.nm_id)) byNm.set(a.nm_id, a);
    const out: { barcode: string; wb: string; units: number }[] = [];
    for (const c of candidates) {
        if (c.nmId == null) continue;
        const a = byNm.get(c.nmId);
        if (!a) continue;
        const ppb = nmPpb.get(c.nmId) || 0;
        if (ppb <= 0 || c.boxes <= 0) continue;
        const availSum = Object.values(ffAvailOf(a)).reduce((s, v) => s + v, 0);
        const free = Math.max(0, availSum - (allocByBc.get(a.barcode) ?? 0));
        const want = c.boxes * ppb;
        const take = Math.min(want, Math.floor(free / ppb) * ppb);
        if (take > 0) out.push({ barcode: a.barcode, wb, units: take });
    }
    return out;
}

/**
 * Ручной дозабор (barcode→wb→штук) → строки ЦЕЛЫХ коробов (extraRows). `src`
 * раскладывается по ФФ (`allocSrcAcrossFf`). Кап по суммарному свободному
 * остатку баркода минус `reservedByBc` (потребность/пины) — Σsrc ≤ наличие
 * даже при гонке двойного клика. Чистая.
 */
export function buildTopUpRowsFf(
    manualTopUp: Map<string, Record<string, number>>,
    articles: StockNeedArticle[],
    nmPpb: Map<number, number | null>,
    reservedByBc?: Map<string, number>,
): AssemblyDraftRow[] {
    if (manualTopUp.size === 0) return [];
    const byBc = new Map<string, StockNeedArticle>();
    for (const a of articles) byBc.set(a.barcode, a);
    const rows: AssemblyDraftRow[] = [];
    for (const [bc, wbMap] of manualTopUp) {
        const a = byBc.get(bc);
        if (!a || !a.nm_id) continue;
        const ppb = nmPpb.get(a.nm_id) || 0;
        if (ppb <= 0) continue;
        const ffAvail = ffAvailOf(a);
        const availSum = Object.values(ffAvail).reduce((s, v) => s + v, 0);
        let cap = Math.max(0, availSum - (reservedByBc?.get(bc) ?? 0));
        const tgt: Record<string, number> = {};
        let total = 0;
        for (const [wb, q] of Object.entries(wbMap)) {
            const units = Math.min(Math.floor((q || 0) / ppb) * ppb, Math.floor(cap / ppb) * ppb);
            if (units <= 0) continue;
            tgt[wb] = (tgt[wb] ?? 0) + units;
            total += units;
            cap -= units;
        }
        if (total <= 0) continue;
        const src = allocSrcAcrossFf(total, ffAvail);
        rows.push({ nm_id: a.nm_id, barcode: bc, vendor_code: a.vendor_code || bc, src, tgt, package_type: 'BOX' });
    }
    return rows;
}

/**
 * Срез строк раскладки по ОДНОМУ ФФ-источнику: из каждой строки берётся только
 * доля, физически забираемая с `ffId`. Пары ФФ→WB — тот же `allocatePairs`, что
 * карточки черновика, экспорт и создание заявок → числа сходятся между экранами.
 * Для лупы «Склад забора» в матрице — это ОТОБРАЖЕНИЕ, раскладку не мутирует.
 * Чистая.
 */
export function sliceRowsByFf(rows: AssemblyDraftRow[], ffId: number): AssemblyDraftRow[] {
    const key = String(ffId);
    const out: AssemblyDraftRow[] = [];
    for (const r of rows) {
        if ((r.src?.[key] || 0) <= 0) continue;
        const tgt: Record<string, number> = {};
        let total = 0;
        for (const [pair, q] of allocatePairs(r.src, r.tgt)) {
            if ((q || 0) <= 0) continue;
            const sep = pair.indexOf('::');
            if (pair.slice(0, sep) !== key) continue;
            const wb = pair.slice(sep + 2);
            tgt[wb] = (tgt[wb] || 0) + q;
            total += q;
        }
        if (total <= 0) continue;
        out.push({ ...r, src: { [key]: total }, tgt });
    }
    return out;
}

/** Обогащение артикула данными «Потребности по складам» (для матрицы черновика):
 *  в сборке / на WB-остатке / новинка + per-WB срезы. Зеркало `enrichPoolRows`
 *  машины, но источник строк — сами артикулы (не пул). Матч по nm_id. */
export function enrichArticles(
    articles: StockNeedArticle[],
    stockNeed: StockNeedResponse | null,
    coldStartSet: Set<number>,
): Map<number, EnrichedSku> {
    // per-WB-склад потребность+остаток из warehouses[].articles[nm].
    const whCells = new Map<number, Record<string, { need: number; stock: number }>>();
    for (const w of stockNeed?.warehouses ?? []) {
        for (const [nmStr, cell] of Object.entries(w.articles ?? {})) {
            const nm = Number(nmStr);
            const m = whCells.get(nm) ?? {};
            m[w.name] = { need: Number(cell?.need) || 0, stock: Number(cell?.stock) || 0 };
            whCells.set(nm, m);
        }
    }
    const out = new Map<number, EnrichedSku>();
    for (const a of articles) {
        const nm = a.nm_id;
        if (!nm || out.has(nm)) continue;
        const cells = whCells.get(nm) ?? {};
        const names = new Set<string>([
            ...Object.keys(cells),
            ...Object.keys(a.asm_by_warehouse ?? {}),
            ...Object.keys(a.transit_by_warehouse ?? {}),
        ]);
        const byWh: Record<string, { need: number; stock: number; asm: number; transit: number }> = {};
        for (const name of names) {
            byWh[name] = {
                need: cells[name]?.need ?? 0,
                stock: cells[name]?.stock ?? 0,
                asm: Number(a.asm_by_warehouse?.[name]) || 0,
                transit: Number(a.transit_by_warehouse?.[name]) || 0,
            };
        }
        out.set(nm, {
            nm_id: nm,
            inAssembly: Number(a.in_assembly) || 0,
            inTransit: Number(a.in_transit) || 0,
            stocksWb: Number(a.stocks_wb) || 0,
            isNew: coldStartSet.has(nm),
            byWh,
        });
    }
    return out;
}

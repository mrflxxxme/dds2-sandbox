/**
 * Раскладка пула машины в пути по WB-складам — детерминированный движок
 * предраспределения. Делает ровно то же, что «Потребность по складам»
 * (`AddFromNeedPanel` + `WarehouseNeedView`), но ИСТОЧНИК — остатки конкретной
 * машины (пул), а не свободный ФФ-сток:
 *   1. потребность по WB-складам (из `getStockNeed`, per nm_id);
 *   2. кап «сколько можно отправить» = доступно в пуле машины (per barcode);
 *   3. целые коробы (`ppb`) — `buildDraftRows` (тот же, что у потребности);
 *   4. проверка приёмки WB — вызывающий передаёт результат acceptance-check;
 *   5. целые паллеты (per-shipment ФФ→WB) — `normalizeDraft`.
 *
 * Источник у всех строк ОДИН — ФФ-склад разгрузки машины (`targetWarehouseId`):
 * `src = { [targetWarehouseId]: qty }`. Σsrc===Σtgt держится (инвариант
 * `buildDraftRows`/`normalizeDraft`).
 *
 * Чистая (без React/IO): acceptance-check вызывает компонент и передаёт сюда
 * `applyAcceptanceSplits` уже применёнными скусами — полностью юнит-тестируется.
 */
import type {
    AssemblyDraftRow,
    PackageType,
    PreDistPoolRow,
    StockNeedResponse,
} from '@/types/api';
import { buildDraftRows, type DraftSkuInput } from '@/lib/assembly/buildDraftRows';
import { roundDraftRowsToWholeBoxes } from '@/lib/utils/assemblyRoundBoxes';
import { normalizeDraft, type NormalizeDraftCtx } from '@/lib/utils/normalizeDraft';

export interface PoolDistInput {
    /** Пул машины: товар + доступно к раздаче (per barcode). */
    poolRows: PreDistPoolRow[];
    /** ФФ-склад разгрузки машины — единственный источник всех строк. */
    targetWarehouseId: number;
    /** Потребность WB по складам (per nm_id) — `api.getStockNeed`. */
    stockNeed: StockNeedResponse | null;
    /** Кратность короба per nm_id (шт/короб). */
    nmPpb: Map<number, number | null>;
    /** Габариты короба «ДxШxВ» per nm_id. */
    nmBoxSize: Map<number, string | null>;
    /** Override «коробок на паллету» по канон-размеру короба. */
    palletOverrides: Record<string, number>;
}

/** Одна WB-целевая позиция к проверке приёмки. */
export interface PoolAcceptanceItem {
    nm_id: number;
    barcode: string;
    distribution: Record<string, number>;
}

/** Применённый результат приёмки: per (nm_id::barcode) → набор сплитов. */
export type AcceptanceSplitMap = Map<
    string,
    { package_type: PackageType; distribution: Record<string, number> }[]
>;

/** Базовые `DraftSkuInput` ДО приёмки: пул × потребность (источник = пул машины). */
export function buildPoolSkus(input: PoolDistInput): {
    skus: DraftSkuInput[];
    /** barcode → nm_id (для матрицы/обратного маппинга). */
    nmByBarcode: Map<string, number>;
} {
    const { poolRows, targetWarehouseId, stockNeed } = input;
    const skus: DraftSkuInput[] = [];
    const nmByBarcode = new Map<string, number>();

    // Потребность per nm_id per WB-склад: warehouses[].articles[nm].need.
    const needByNm = new Map<number, Record<string, number>>();
    for (const w of stockNeed?.warehouses ?? []) {
        for (const [nmStr, cell] of Object.entries(w.articles ?? {})) {
            const nm = Number(nmStr);
            const need = Number(cell?.need) || 0;  // Decimal сериализуется строкой → Number до арифметики
            if (need <= 0) continue;
            const t = needByNm.get(nm) ?? {};
            t[w.name] = (t[w.name] || 0) + need;
            needByNm.set(nm, t);
        }
    }
    // vendor_code per nm_id (для подписи строки).
    const vendorByNm = new Map<number, string>();
    for (const a of stockNeed?.articles ?? []) vendorByNm.set(a.nm_id, a.vendor_code);

    for (const row of poolRows) {
        const avail = Math.max(0, Math.floor(Number(row.available_qty) || 0));
        if (avail <= 0) continue;
        const nm = row.article_wb ? Number(row.article_wb) : 0;
        nmByBarcode.set(row.barcode, nm);
        const target = nm ? needByNm.get(nm) : undefined;
        if (!target || Object.keys(target).length === 0) continue; // нет потребности → на хранение
        skus.push({
            nm_id: nm,
            barcode: row.barcode,
            vendor_code: row.article_seller || vendorByNm.get(nm) || row.barcode,
            target: { ...target },
            ffStock: { [targetWarehouseId]: avail },
            ppb: input.nmPpb.get(nm),
            box_size: input.nmBoxSize.get(nm) ?? null,
            packageType: 'BOX',
        });
    }
    return { skus, nmByBarcode };
}

/** Применить сплиты приёмки к базовым скусам (closed→open + тип упаковки per WB). */
export function applyAcceptanceSplits(
    skus: DraftSkuInput[],
    splitMap: AcceptanceSplitMap | null,
): DraftSkuInput[] {
    if (!splitMap) return skus;
    const out: DraftSkuInput[] = [];
    for (const s of skus) {
        const splits = splitMap.get(`${s.nm_id}::${s.barcode}`);
        if (!splits || splits.length === 0) {
            out.push(s);
            continue;
        }
        for (const sp of splits) {
            const target = Object.fromEntries(
                Object.entries(sp.distribution || {}).filter(([, q]) => (q || 0) > 0),
            );
            if (Object.keys(target).length === 0) continue;
            out.push({ ...s, target, packageType: sp.package_type });
        }
    }
    return out;
}

/**
 * Финальная раскладка: buildDraftRows (целые коробы) → добивка коробов из пула →
 * normalizeDraft (целые паллеты). Источник у всех строк = `targetWarehouseId`.
 *
 * `wholePallets=true` — строго целые паллеты (хвост < паллеты остаётся на ФФ);
 * `false` — только целые коробы (частичные паллеты допускаются). На мелкой
 * потребности машины целые паллеты часто обнуляют раскладку — режим «коробами»
 * показывает то, что реально набирается коробами (зеркало тумблера «Потребности»).
 */
export function finalizePoolRows(
    effectiveSkus: DraftSkuInput[],
    input: PoolDistInput,
    wholePallets = true,
): AssemblyDraftRow[] {
    const { nmPpb, nmBoxSize, palletOverrides } = input;
    if (effectiveSkus.length === 0) return [];

    let rows = buildDraftRows({ skus: effectiveSkus, palletOverrides });
    if (rows.length === 0) return [];

    // Добить неполные коробы из ОСТАВШЕГОСЯ пула (как в AddFromNeedPanel).
    const used: Record<number, Record<number, number>> = {};
    for (const r of rows) {
        const m = (used[r.nm_id] ??= {});
        for (const [ff, q] of Object.entries(r.src)) m[Number(ff)] = (m[Number(ff)] || 0) + (q || 0);
    }
    const freeAfter: Record<number, Record<number, number>> = {};
    for (const s of effectiveSkus) {
        if (freeAfter[s.nm_id]) continue;
        const pool: Record<number, number> = {};
        for (const [ff, q] of Object.entries(s.ffStock)) {
            const free = (q || 0) - (used[s.nm_id]?.[Number(ff)] || 0);
            if (free > 0) pool[Number(ff)] = free;
        }
        if (Object.keys(pool).length) freeAfter[s.nm_id] = pool;
    }
    rows = roundDraftRowsToWholeBoxes(rows, (nm) => nmPpb.get(nm), freeAfter, () => false).rows;

    if (!wholePallets) return rows; // режим «коробами»: частичные паллеты ок

    // Целые ПАЛЛЕТЫ (per-shipment ФФ→WB) — тот же нормализатор, что у черновика/потребности.
    const inDraft: Record<number, Record<number, number>> = {};
    for (const r of rows) {
        const m = (inDraft[r.nm_id] ??= {});
        for (const [ff, q] of Object.entries(r.src)) m[Number(ff)] = (m[Number(ff)] || 0) + (q || 0);
    }
    const freeByNm: Record<number, Record<number, number>> = {};
    for (const s of effectiveSkus) {
        if (freeByNm[s.nm_id]) continue;
        const pool: Record<number, number> = {};
        for (const [ff, q] of Object.entries(s.ffStock)) {
            const free = (q || 0) - (inDraft[s.nm_id]?.[Number(ff)] || 0);
            if (free > 0) pool[Number(ff)] = free;
        }
        if (Object.keys(pool).length) freeByNm[s.nm_id] = pool;
    }
    const ctx: NormalizeDraftCtx = {
        ppbOf: (nm) => nmPpb.get(nm),
        boxSizeOf: (nm) => nmBoxSize.get(nm) ?? null,
        overrides: palletOverrides,
        isNewcomer: () => false,
        freeByNm,
    };
    return normalizeDraft(rows, ctx).rows;
}

/** Обогащение строки пула данными «Потребности по складам» (для матрицы экрана машины):
 *  есть ли товар в сборке / на WB-остатке / новинка, плюс per-WB-склад срезы. Матч по
 *  nm_id (= `PreDistPoolRow.article_wb`); товар без потребности → нули (фолбэк, не падаем). */
export interface EnrichedSku {
    nm_id: number;
    /** Σ уже в сборке (на все WB-склады). */
    inAssembly: number;
    /** Σ уже едет транзитом (на все WB-склады). */
    inTransit: number;
    /** Σ остаток на WB (Wildberries). */
    stocksWb: number;
    /** Новинка (cold-start) — из ColdStart-таблицы. */
    isNew: boolean;
    /** per WB-склад name → срезы: потребность / остаток WB / в сборке / в пути. */
    byWh: Record<string, { need: number; stock: number; asm: number; transit: number }>;
}

export function enrichPoolRows(
    poolRows: PreDistPoolRow[],
    stockNeed: StockNeedResponse | null,
    coldStartSet: Set<number>,
): Map<number, EnrichedSku> {
    type SNA = NonNullable<StockNeedResponse['articles']>[number];
    const artByNm = new Map<number, SNA>();
    for (const a of stockNeed?.articles ?? []) artByNm.set(a.nm_id, a);

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
    for (const row of poolRows) {
        const nm = row.article_wb ? Number(row.article_wb) : 0;
        if (!nm || out.has(nm)) continue;
        const a = artByNm.get(nm);
        const cells = whCells.get(nm) ?? {};
        const names = new Set<string>([
            ...Object.keys(cells),
            ...Object.keys(a?.asm_by_warehouse ?? {}),
            ...Object.keys(a?.transit_by_warehouse ?? {}),
        ]);
        const byWh: Record<string, { need: number; stock: number; asm: number; transit: number }> = {};
        for (const name of names) {
            byWh[name] = {
                need: cells[name]?.need ?? 0,
                stock: cells[name]?.stock ?? 0,
                asm: Number(a?.asm_by_warehouse?.[name]) || 0,
                transit: Number(a?.transit_by_warehouse?.[name]) || 0,
            };
        }
        out.set(nm, {
            nm_id: nm,
            inAssembly: Number(a?.in_assembly) || 0,
            inTransit: Number(a?.in_transit) || 0,
            stocksWb: Number(a?.stocks_wb) || 0,
            isNew: coldStartSet.has(nm),
            byWh,
        });
    }
    return out;
}

/** Свернуть строки раскладки в позиции запроса предраспределения (per barcode×WB×упаковка). */
export function rowsToPreDistRows(
    rows: AssemblyDraftRow[],
): { barcode: string; wb_warehouse_name: string; qty: number; package_type: PackageType }[] {
    const agg = new Map<string, { barcode: string; wb_warehouse_name: string; qty: number; package_type: PackageType }>();
    for (const r of rows) {
        const pkg = (r.package_type ?? 'BOX') as PackageType;
        for (const [wb, qty] of Object.entries(r.tgt)) {
            if ((qty || 0) <= 0) continue;
            const key = `${r.barcode}::${wb}::${pkg}`;
            const cur = agg.get(key);
            if (cur) cur.qty += qty;
            else agg.set(key, { barcode: r.barcode, wb_warehouse_name: wb, qty, package_type: pkg });
        }
    }
    return [...agg.values()];
}

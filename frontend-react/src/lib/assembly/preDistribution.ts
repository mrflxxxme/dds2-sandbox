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

// Приёмка и сплиты — общий движок (`buildAssemblyDistribution`); реэкспорт для call-site.
export { applyAcceptanceSplits };
export type { AcceptanceSplitMap };

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

/** Геометрия движка из PoolDistInput (кратность/габарит per nm + паллет-override). */
function poolGeom(input: PoolDistInput): DistributionGeom {
    return {
        ppbOf: (nm) => input.nmPpb.get(nm),
        boxSizeOf: (nm) => input.nmBoxSize.get(nm) ?? null,
        palletOverrides: input.palletOverrides,
    };
}

/** Источник доступности МАШИНЫ: весь остаток пула сидит на ФФ-складе разгрузки. */
function poolAvailabilityOf(input: PoolDistInput): AvailabilityOf {
    const availByBarcode = new Map<string, number>();
    for (const row of input.poolRows) {
        availByBarcode.set(row.barcode, Math.max(0, Math.floor(Number(row.available_qty) || 0)));
    }
    return (_nm, barcode) => {
        const avail = availByBarcode.get(barcode) ?? 0;
        return avail > 0 ? { [input.targetWarehouseId]: avail } : {};
    };
}

/** Строки пула машины как кандидаты движка (все — обычные; новинки засеваются отдельно). */
function poolToDistSkus(input: PoolDistInput): { skus: DistSku[]; nmByBarcode: Map<string, number> } {
    const vendorByNm = new Map<number, string>();
    for (const a of input.stockNeed?.articles ?? []) vendorByNm.set(a.nm_id, a.vendor_code);
    const skus: DistSku[] = [];
    const nmByBarcode = new Map<string, number>();
    for (const row of input.poolRows) {
        const nm = row.article_wb ? Number(row.article_wb) : 0;
        nmByBarcode.set(row.barcode, nm);
        skus.push({
            nm_id: nm,
            barcode: row.barcode,
            vendor_code: row.article_seller || vendorByNm.get(nm) || row.barcode,
            is_newcomer: false,
            available: Math.max(0, Math.floor(Number(row.available_qty) || 0)),
        });
    }
    return { skus, nmByBarcode };
}

/** Базовые `DraftSkuInput` ДО приёмки: пул × потребность (источник = пул машины).
 *  Тонкий адаптер над общим движком `buildDistributionSkus` (источник = ФФ разгрузки). */
export function buildPoolSkus(input: PoolDistInput): {
    skus: DraftSkuInput[];
    /** barcode → nm_id (для матрицы/обратного маппинга). */
    nmByBarcode: Map<string, number>;
} {
    const { skus: distSkus, nmByBarcode } = poolToDistSkus(input);
    const skus = buildDistributionSkus(distSkus, input.stockNeed, poolAvailabilityOf(input), poolGeom(input));
    return { skus, nmByBarcode };
}

/**
 * Финальная раскладка машины — тонкий адаптер над общим движком `finalizeDistribution`
 * (celye koroby → добивка из остатка пула → целые паллеты). Источник у всех строк =
 * `targetWarehouseId` (задан в `effectiveSkus[].ffStock` из `buildPoolSkus`).
 *
 * `wholePallets=true` — строго целые паллеты (хвост < паллеты остаётся на ФФ);
 * `false` — только целые коробы (частичные паллеты допускаются). На мелкой потребности
 * машины целые паллеты часто обнуляют раскладку — режим «коробами» показывает то, что
 * реально набирается коробами (зеркало тумблера «Потребности»/«Черновика»).
 */
export function finalizePoolRows(
    effectiveSkus: DraftSkuInput[],
    input: PoolDistInput,
    wholePallets = true,
    extraRows: AssemblyDraftRow[] = [],
): AssemblyDraftRow[] {
    return finalizeDistribution(effectiveSkus, poolGeom(input), wholePallets, extraRows).rows;
}

/** Как `finalizePoolRows`, но ВОЗВРАЩАЕТ И предбронь-остаток (под-паллетные хвосты) —
 *  для машинно-скоупленной вкладки «🅿️ Предбронь» (короб=«Дозабить» / моно=«Предзаявка»).
 *  `extraRows` — пред-построенные целые коробы (засев новинок / дозабор из остатка машины). */
export function finalizePoolDistribution(
    effectiveSkus: DraftSkuInput[],
    input: PoolDistInput,
    wholePallets = true,
    extraRows: AssemblyDraftRow[] = [],
    /** «Не менее 1 короба на нуждающийся склад» — для матрицы pre-dist. */
    minOneBoxPerWh = false,
): { rows: AssemblyDraftRow[]; prebook: AssemblyDraftRow[] } {
    return finalizeDistribution(effectiveSkus, poolGeom(input), wholePallets, extraRows, minOneBoxPerWh);
}

/**
 * Разделить SKU на отгружаемые и удержанные по наличию кратности короба (`ppb`).
 * Правило машины (как в «Черновике»): россыпь запрещена — SKU без кратности НЕ едет
 * (округлять до целого короба нечем), остаётся на машине и показывается баннером.
 * Чистая — юнит-тестируется.
 */
export function splitByKratnost<T extends { nm_id: number }>(
    skus: T[],
    ppbOf: (nm: number) => number | null | undefined,
): { shippable: T[]; heldNoPpb: T[] } {
    const shippable: T[] = [];
    const heldNoPpb: T[] = [];
    for (const s of skus) ((ppbOf(s.nm_id) ?? 0) > 0 ? shippable : heldNoPpb).push(s);
    return { shippable, heldNoPpb };
}

/** Кандидат дозабора направления (из `buildPrebookGroups.topUp.candidates`): nm + коробы. */
export interface BoxTopUpCandidate {
    nmId?: number;
    boxes: number;
}

/**
 * Разложить дозабор BOX-направления (кандидаты nm+коробы) на конкретные баркоды ОСТАТКА
 * машины (onHold = доступно − уже разложено), ЦЕЛЫМИ коробами, крупнейший остаток первым.
 * Возвращает добавки (barcode→wb→штук) для накопления в manualTopUp. Чистая.
 */
export function planBoxTopUp(
    candidates: BoxTopUpCandidate[],
    wb: string,
    poolRows: PreDistPoolRow[],
    allocByBc: Map<string, number>,
    nmPpb: Map<number, number | null>,
): { barcode: string; wb: string; units: number }[] {
    const out: { barcode: string; wb: string; units: number }[] = [];
    for (const c of candidates) {
        if (c.nmId == null) continue;
        const ppb = nmPpb.get(c.nmId) || 0;
        if (ppb <= 0 || c.boxes <= 0) continue;
        let need = c.boxes * ppb;
        const bcs = poolRows
            .filter(pr => (pr.article_wb ? Number(pr.article_wb) : 0) === c.nmId)
            .map(pr => ({ bc: pr.barcode, free: Math.max(0, Math.floor(Number(pr.available_qty) || 0) - (allocByBc.get(pr.barcode) ?? 0)) }))
            .filter(x => x.free >= ppb)
            .sort((a, b) => b.free - a.free);
        for (const x of bcs) {
            if (need <= 0) break;
            const take = Math.min(need, Math.floor(x.free / ppb) * ppb);
            if (take <= 0) continue;
            out.push({ barcode: x.bc, wb, units: take });
            need -= take;
        }
    }
    return out;
}

/**
 * Ручной дозабор (накопленный по баркодам: barcode→wb→штук) → строки ЦЕЛЫХ коробов для
 * нормализации (extraRows). Кап по остатку машины (страховка): целыми коробами и не
 * больше available баркода. Источник = ФФ разгрузки (`targetWh`). Чистая.
 */
export function buildTopUpRows(
    manualTopUp: Map<string, Record<string, number>>,
    poolRows: PreDistPoolRow[],
    targetWh: number,
    nmPpb: Map<number, number | null>,
    /** Уже занятое потребностью/засевом per баркод (Σsrc): топап капится по available −
     *  reserved, чтобы Σsrc баркода не превысил наличие даже при гонке двойного клика. */
    reservedByBc?: Map<string, number>,
): AssemblyDraftRow[] {
    if (manualTopUp.size === 0) return [];
    const metaByBc = new Map<string, { nm: number; vendor: string; avail: number }>();
    for (const pr of poolRows) {
        metaByBc.set(pr.barcode, {
            nm: pr.article_wb ? Number(pr.article_wb) : 0,
            vendor: pr.article_seller || String(pr.article_wb ?? pr.barcode),
            avail: Math.max(0, Math.floor(Number(pr.available_qty) || 0)),
        });
    }
    const rows: AssemblyDraftRow[] = [];
    for (const [bc, wbMap] of manualTopUp) {
        const meta = metaByBc.get(bc);
        if (!meta || !meta.nm) continue;
        const ppb = nmPpb.get(meta.nm) || 0;
        if (ppb <= 0) continue;
        let cap = Math.max(0, meta.avail - (reservedByBc?.get(bc) ?? 0));
        const tgt: Record<string, number> = {};
        let total = 0;
        for (const [wb, q] of Object.entries(wbMap)) {
            const units = Math.min(Math.floor((q || 0) / ppb) * ppb, Math.floor(cap / ppb) * ppb);
            if (units <= 0) continue;
            tgt[wb] = (tgt[wb] ?? 0) + units;
            total += units;
            cap -= units;
        }
        if (total > 0) rows.push({ nm_id: meta.nm, barcode: bc, vendor_code: meta.vendor, src: { [String(targetWh)]: total }, tgt, package_type: 'BOX' });
    }
    return rows;
}

/** Ручное редактирование ячеек матрицы «Раскладка»: barcode → { WB-склад → коробов }.
 *  Абсолютный пин (не дельта): сколько ЦЕЛЫХ коробов юзер хочет отправить в этот склад.
 *  Ноль/отсутствие ключа = ничего в этот склад. Баркод с любым пином полностью
 *  управляется вручную (исключается из авто-раскладки), см. `buildPinnedRows`. */
export type CellEdits = Map<string, Record<string, number>>;

/** Резолвер типа упаковки пин-ячейки по флагам приёмки WB: короб приоритетнее моно/сейфа
 *  (как в авто-сплите). Нет флагов приёмки → короб (фолбэк). */
export type PinnedPkgOf = (nm_id: number, wb: string) => PackageType;

/**
 * Применить ±`delta` коробов к пину ячейки склада (клик −/+ в матрице). Σ коробов по
 * баркоду КЛАМПИТСЯ до `floor(avail/ppb)` — нельзя дорисовать больше, чем реально стоит на
 * машине (соседние склады при этом не трогаются: клампится только кликнутый). Ноль/минус →
 * склад выкидывается. Возвращает НОВУЮ карту {склад→коробов}. Чистая — юнит-тестируется.
 */
export function applyCellBoxDelta(
    rec: Record<string, number>,
    wh: string,
    delta: number,
    ppb: number,
    avail: number,
): Record<string, number> {
    const next = { ...rec };
    if (ppb <= 0) return next;
    const maxBoxes = Math.floor(Math.max(0, avail) / ppb);
    let val = Math.max(0, (next[wh] ?? 0) + delta);
    const others = Object.entries(next).reduce((s, [w, b]) => s + (w === wh ? 0 : b), 0);
    if (others + val > maxBoxes) val = Math.max(0, maxBoxes - others);  // кап остатком машины
    if (val <= 0) delete next[wh]; else next[wh] = val;
    return next;
}

/**
 * Отредактированные вручную ячейки матрицы (пины) → строки ЦЕЛЫХ коробов для общего движка
 * (`finalizeDistribution` как `extraRows`): паллеты/предбронь досчитаются там же, что и авто.
 *
 * Каждый пин (barcode × WB) = `boxes × ppb` штук; тип упаковки — из приёмки (`pinnedPkgOf`);
 * источник = ФФ разгрузки (`targetWh`). Σ по баркоду капится доступным остатком машины
 * (`available_qty`, детерминированный порядок складов по имени) — юзер не может «дорисовать»
 * больше, чем реально стоит на машине. Одна строка на (barcode × package_type), Σsrc==Σtgt.
 * Чистая — юнит-тестируется.
 */
export function buildPinnedRows(
    cellEdits: CellEdits,
    poolRows: PreDistPoolRow[],
    targetWh: number,
    nmPpb: Map<number, number | null>,
    pinnedPkgOf: PinnedPkgOf,
): AssemblyDraftRow[] {
    if (cellEdits.size === 0) return [];
    const metaByBc = new Map<string, { nm: number; vendor: string; avail: number }>();
    for (const pr of poolRows) {
        metaByBc.set(pr.barcode, {
            nm: pr.article_wb ? Number(pr.article_wb) : 0,
            vendor: pr.article_seller || String(pr.article_wb ?? pr.barcode),
            avail: Math.max(0, Math.floor(Number(pr.available_qty) || 0)),
        });
    }
    const rows: AssemblyDraftRow[] = [];
    for (const [bc, whBoxes] of cellEdits) {
        const meta = metaByBc.get(bc);
        if (!meta || !meta.nm) continue;
        const ppb = nmPpb.get(meta.nm) || 0;
        if (ppb <= 0) continue;
        let cap = meta.avail;
        const byPkg = new Map<PackageType, Record<string, number>>();
        for (const wb of Object.keys(whBoxes).sort()) {
            const boxes = Math.max(0, Math.floor(whBoxes[wb] || 0));
            if (boxes <= 0) continue;
            const units = Math.min(boxes * ppb, Math.floor(cap / ppb) * ppb);
            if (units <= 0) continue;
            cap -= units;
            const pkg = pinnedPkgOf(meta.nm, wb);
            const rec = byPkg.get(pkg) ?? {};
            rec[wb] = (rec[wb] ?? 0) + units;
            byPkg.set(pkg, rec);
        }
        for (const [pkg, tgt] of byPkg) {
            const total = Object.values(tgt).reduce((s, v) => s + v, 0);
            if (total <= 0) continue;
            rows.push({ nm_id: meta.nm, barcode: bc, vendor_code: meta.vendor, src: { [String(targetWh)]: total }, tgt, package_type: pkg });
        }
    }
    return rows;
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

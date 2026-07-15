/**
 * ЕДИНЫЙ движок раскладки сборки — общий для «Черновика сборки» (источник = наш ФФ-сток,
 * мульти-склад) и экрана «Распределить машину» (источник = остаток конкретной машины, пул).
 *
 * Развилка черновик↔машина ровно ОДНА — `availabilityOf(nm, barcode)` («сколько можно
 * отправить»). Всё остальное (шейпинг потребности по WB-складам, целые коробы, целые
 * паллеты, засев новинок, предбронь-остаток) — ОБЩЕЕ. Оба экрана зовут этот движок с
 * разным источником доступности → «форма раскладки идентична, отличие только источник и
 * кап» гарантируется конструктивно (см. .claude/…/pre-dist-rework-plan.md, часть A).
 *
 *   • черновик: availabilityOf(nm,bc) = article.rf_stocks[ffId].available  (наш ФФ, мульти-склад)
 *   • машина:   availabilityOf(nm,bc) = { [targetWarehouseId]: pool.available_qty }  (пул машины)
 *
 * Кап = min(availabilityOf, need_shape); серверный ФФ-кап (only_available) НЕ стекается
 * поверх машинного — форму спроса берём БЕЗ него, кап применяем клиентски (buildDraftRows
 * уже делает `min(totalAvail, totalNeed)`).
 *
 * Фазность (IO наружу): `buildDistributionSkus` (pure, даёт цели для приёмки) → компонент
 * зовёт `checkWbAcceptance` (async) → `applyAcceptanceSplits` + `finalizeDistribution`
 * (pure, с уже применёнными сплитами). Приёмку в движок не затаскиваем.
 *
 * Чистые функции (без React/IO) — полностью юнит-тестируются.
 */
import type {
    AssemblyDraftRow,
    PackageType,
    StockNeedResponse,
} from '@/types/api';
import { buildDraftRows, type DraftSkuInput } from '@/lib/assembly/buildDraftRows';
import { seedNewcomerWholeBoxes, type SeedAnchor } from '@/lib/assembly/coldStartSeed';
import { roundDraftRowsToWholeBoxes } from '@/lib/utils/assemblyRoundBoxes';
import { normalizeDraft, type NormalizeDraftCtx } from '@/lib/utils/normalizeDraft';

/** Кандидат к раскладке: одна WB-карточка × баркод (машина: строка пула; черновик: статья). */
export interface DistSku {
    nm_id: number;
    barcode: string;
    vendor_code: string;
    /** Новинка (cold-start) — засевается отдельно (строгие целые коробы), не по потребности. */
    is_newcomer: boolean;
    /** Доступно к отправке всего (для отсева пустых и расчёта остатка на источнике). */
    available: number;
}

/** Источник доступности — единственная развилка черновик↔машина. */
export type AvailabilityOf = (nm_id: number, barcode: string) => Record<number, number>;

/** Геометрия/справочники движка (общие для обоих экранов). */
export interface DistributionGeom {
    /** Кратность короба (шт/короб) per nm_id. null/0 — россыпь. */
    ppbOf: (nm_id: number) => number | null | undefined;
    /** Кратность короба КОНКРЕТНОГО ФФ (может отличаться по складам) — паритет
     *  нормализации со страницей черновика (она re-нормализует с ppbAt). */
    ppbAt?: (nm_id: number, ffId: number) => number | null | undefined;
    /** Габариты короба «ДxШxВ» per nm_id. null — не палетизируется. */
    boxSizeOf: (nm_id: number) => string | null | undefined;
    /** Override «коробов на паллету» по канон-размеру короба. */
    palletOverrides: Record<string, number>;
    /** Вес приоритета WB-склада (серверная «схема воришек») — порядок среза при
     *  дефиците источника (паритет с greedy черновика). */
    priorityOf?: (wh: string) => number;
}

/** Применённый результат приёмки: per (nm_id::barcode) → набор сплитов (тип упаковки × распределение). */
export type AcceptanceSplitMap = Map<
    string,
    { package_type: PackageType; distribution: Record<string, number> }[]
>;

export interface DistributionResult {
    /** Основная раскладка (целые коробы/паллеты, Σsrc==Σtgt). */
    rows: AssemblyDraftRow[];
    /** Предбронь-остаток: под-паллетные хвосты целых коробов (для «Дозабить»/«Предзаявка»). */
    prebook: AssemblyDraftRow[];
    /** barcode → штук, оставшихся на источнике (не разложено). */
    onHoldByBarcode: Map<string, number>;
}

/** Σ значений. */
function sumRec(o: Record<string, number>): number {
    return Object.values(o).reduce((s, v) => s + (v || 0), 0);
}

/**
 * Потребность WB по складам per nm_id из `getStockNeed`: warehouses[].articles[nm].need.
 * Форма спроса БЕЗ ФФ-капа (only_available=false) — кап накладывает `availabilityOf` ниже.
 */
export function needByNmFromStockNeed(stockNeed: StockNeedResponse | null): Map<number, Record<string, number>> {
    const needByNm = new Map<number, Record<string, number>>();
    for (const w of stockNeed?.warehouses ?? []) {
        for (const [nmStr, cell] of Object.entries(w.articles ?? {})) {
            const nm = Number(nmStr);
            const need = Number(cell?.need) || 0; // Decimal сериализуется строкой → Number до арифметики
            if (need <= 0) continue;
            const t = needByNm.get(nm) ?? {};
            t[w.name] = (t[w.name] || 0) + need;
            needByNm.set(nm, t);
        }
    }
    return needByNm;
}

/**
 * Фаза 1 (pure): базовые `DraftSkuInput` ОБЫЧНЫХ SKU ДО приёмки = потребность × источник.
 * Новинки (`is_newcomer`) сюда не попадают — они засеваются отдельно (`seedNewcomerRows`).
 * Источник (`ffStock`) берётся из `availabilityOf` — единственная развилка черновик↔машина.
 */
export function buildDistributionSkus(
    skus: DistSku[],
    stockNeed: StockNeedResponse | null,
    availabilityOf: AvailabilityOf,
    geom: DistributionGeom,
): DraftSkuInput[] {
    const needByNm = needByNmFromStockNeed(stockNeed);
    const out: DraftSkuInput[] = [];
    for (const s of skus) {
        if (s.is_newcomer) continue; // новинки — отдельным засевом
        const avail = availabilityOf(s.nm_id, s.barcode);
        if (sumRec(avail as Record<string, number>) <= 0) continue; // нечем отправить
        const target = s.nm_id ? needByNm.get(s.nm_id) : undefined;
        if (!target || Object.keys(target).length === 0) continue; // нет потребности → на хранение
        out.push({
            nm_id: s.nm_id,
            barcode: s.barcode,
            vendor_code: s.vendor_code,
            target: { ...target },
            ffStock: { ...avail },
            ppb: geom.ppbOf(s.nm_id),
            box_size: geom.boxSizeOf(s.nm_id) ?? null,
            packageType: 'BOX',
        });
    }
    return out;
}

/** Применить сплиты приёмки к базовым скусам (closed→open + тип упаковки per WB-склад).
 *
 *  ВАЖНО: сплиты одного SKU склеиваются в ОДИН вход (`target` = объединение,
 *  упаковка — картой `packageByWh`), а НЕ дублируются отдельными скусами.
 *  Дубли ломали конвейер дважды: `buildDraftRows` кладёт планы в Map по
 *  `${nm_id}::${barcode}` — второй сплит молча перетирал первый (прод-кейс
 *  150х200_серый 2026-07-15: BOX-часть 360 шт исчезала, ехало 13 шт моно), а
 *  выживи оба — каждый сорсил бы ФФ-сток независимо (двойной счёт источника). */
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
        const target: Record<string, number> = {};
        const packageByWh: Record<string, PackageType> = {};
        for (const sp of splits) {
            for (const [wh, q] of Object.entries(sp.distribution || {})) {
                if ((q || 0) <= 0) continue;
                // Один склад в двух сплитах не встречается (сплит — по складам);
                // на всякий случай суммируем qty, упаковка — последнего сплита.
                target[wh] = (target[wh] || 0) + (q || 0);
                packageByWh[wh] = sp.package_type;
            }
        }
        if (Object.keys(target).length === 0) continue;
        out.push({ ...s, target, packageByWh: { ...(s.packageByWh || {}), ...packageByWh } });
    }
    return out;
}

/**
 * Фаза 2 (pure): целые коробы (`buildDraftRows`) → добивка коробов из остатка источника →
 * целые паллеты (`normalizeDraft`, если `wholePalletsOnly`). Возвращает основную раскладку
 * И предбронь-остаток (под-паллетные хвосты `normalizeDraft.dropped`).
 *
 * `wholePalletsOnly=false` — только целые коробы (частичные паллеты ок): на мелкой
 * потребности целые паллеты часто обнуляют раскладку. `true` — строго целые паллеты.
 */
export function finalizeDistribution(
    effectiveSkus: DraftSkuInput[],
    geom: DistributionGeom,
    wholePalletsOnly: boolean,
    /** Пред-построенные ЦЕЛЫЕ коробы (засев новинок / ручной дозабор из остатка машины):
     *  вливаются ДО паллет-нормализации, чтобы паллетизироваться вместе с потребностью, а их
     *  под-паллетный хвост тоже ушёл в предбронь, а НЕ уехал частичной паллетой. */
    extraRows: AssemblyDraftRow[] = [],
    /** «Не менее 1 короба на нуждающийся склад» (pre-dist-матрица) — прокидывается в `buildDraftRows`. */
    minOneBoxPerWh = false,
): { rows: AssemblyDraftRow[]; prebook: AssemblyDraftRow[] } {
    if (effectiveSkus.length === 0 && extraRows.length === 0) return { rows: [], prebook: [] };
    const { ppbOf, boxSizeOf, palletOverrides } = geom;

    let rows = buildDraftRows({ skus: effectiveSkus, palletOverrides, minOneBoxPerWh, priorityOf: geom.priorityOf });

    // Свободный источник per nm = доступно − уже засорсенное (для добивки/паллет).
    const freeAfter = (current: AssemblyDraftRow[]): Record<number, Record<number, number>> => {
        const used: Record<number, Record<number, number>> = {};
        for (const r of current) {
            const m = (used[r.nm_id] ??= {});
            for (const [ff, q] of Object.entries(r.src)) m[Number(ff)] = (m[Number(ff)] || 0) + (q || 0);
        }
        const free: Record<number, Record<number, number>> = {};
        for (const s of effectiveSkus) {
            if (free[s.nm_id]) continue;
            const pool: Record<number, number> = {};
            for (const [ff, q] of Object.entries(s.ffStock)) {
                const left = (q || 0) - (used[s.nm_id]?.[Number(ff)] || 0);
                if (left > 0) pool[Number(ff)] = left;
            }
            if (Object.keys(pool).length) free[s.nm_id] = pool;
        }
        return free;
    };

    // Добить неполные коробы из ОСТАВШЕГОСЯ источника (как в AddFromNeedPanel/finalizePoolRows).
    rows = roundDraftRowsToWholeBoxes(rows, (nm) => ppbOf(nm), freeAfter(rows), () => false, geom.ppbAt).rows;

    // Пред-построенные целые коробы (засев/дозабор) вливаем в набор ДО нормализации.
    const merged = extraRows.length ? [...rows, ...extraRows] : rows;
    if (merged.length === 0) return { rows: [], prebook: [] };

    if (!wholePalletsOnly) return { rows: merged, prebook: [] }; // режим «коробами»: частичные паллеты ок

    // Целые ПАЛЛЕТЫ (per-shipment ФФ→WB) — тот же нормализатор, что у черновика/потребности.
    // Побочный выход `dropped` = под-паллетные хвосты целых коробов → предбронь.
    const ctx: NormalizeDraftCtx = {
        ppbOf: (nm) => ppbOf(nm),
        ppbAt: geom.ppbAt,
        boxSizeOf: (nm) => boxSizeOf(nm) ?? null,
        overrides: palletOverrides,
        freeByNm: freeAfter(merged),
    };
    const norm = normalizeDraft(merged, ctx);
    return { rows: norm.rows, prebook: norm.dropped };
}

/** Coverage-функция для засева новинок: сколько единиц уже «покрыто» на WB-складе
 *  (остаток WB + в сборке + в пути + уже запланированное этим же расчётом). */
export type CoverageOf = (nm_id: number, warehouse: string) => number;

export interface SeedNewcomerInput {
    skus: DistSku[];
    anchors: SeedAnchor[];
    availabilityOf: AvailabilityOf;
    /** Уже отправлено по потребности per barcode (кап остатка источника). */
    shippedByBarcode: Map<string, number>;
    /** Покрытие per nm per WB-склад (остаток WB + сборка + пути + уже запланированное сюда). */
    coverageOf: CoverageOf;
    ppbOf: (nm_id: number) => number | null | undefined;
}

/**
 * Засев НОВИНОК (cold-start) целыми коробами по топ-складам округов (по доле спроса) из
 * ОСТАТКА источника — по требованию пользователя «не держать новинку на источнике, лучше
 * разложить по главным складам». Coverage-aware (не перетаривает склад, где товар уже есть).
 * Хвост < короба остаётся на источнике. Зеркалит AUTO-ветку `newcomerBoxedAlloc` черновика.
 * Только в режиме «коробами» — на мелком засеве целые паллеты обнуляются.
 */
export function seedNewcomerRows(input: SeedNewcomerInput): AssemblyDraftRow[] {
    const { skus, anchors, availabilityOf, shippedByBarcode, coverageOf, ppbOf } = input;
    if (anchors.length === 0) return [];
    const seeded: AssemblyDraftRow[] = [];
    for (const s of skus) {
        if (!s.is_newcomer || !s.nm_id) continue;
        const avail = availabilityOf(s.nm_id, s.barcode);
        const totalAvail = sumRec(avail as Record<string, number>);
        const remaining = totalAvail - (shippedByBarcode.get(s.barcode) ?? 0);
        if (remaining <= 0) continue;
        const covAnchors = anchors.map(a => ({
            warehouse: a.warehouse,
            share_pct: a.share_pct,
            existing: coverageOf(s.nm_id, a.warehouse),
            district: a.district,
        }));
        const alloc = seedNewcomerWholeBoxes(remaining, ppbOf(s.nm_id), covAnchors);
        const tot = sumRec(alloc);
        if (tot <= 0) continue;
        // Источник строки засева = доступность (жадно с крупнейшего ФФ — держим Σsrc==Σtgt).
        const src: Record<string, number> = {};
        let need = tot;
        const ffOrder = Object.keys(avail).map(Number).sort((x, y) => (avail[y] || 0) - (avail[x] || 0));
        for (const ff of ffOrder) {
            if (need <= 0) break;
            const take = Math.min(need, avail[ff] || 0);
            if (take <= 0) continue;
            src[String(ff)] = take;
            need -= take;
        }
        if (need > 0) continue; // источника не хватило (не должно при remaining≤totalAvail)
        seeded.push({
            nm_id: s.nm_id,
            barcode: s.barcode,
            vendor_code: s.vendor_code,
            src,
            tgt: alloc,
            package_type: 'BOX',
        });
    }
    return seeded;
}

/** Остаток на источнике per barcode = доступно − Σsrc по всем строкам этого баркода. */
export function computeOnHold(
    skus: DistSku[],
    availabilityOf: AvailabilityOf,
    rows: AssemblyDraftRow[],
): Map<string, number> {
    const shippedByBc = new Map<string, number>();
    for (const r of rows) {
        const bc = r.barcode;
        shippedByBc.set(bc, (shippedByBc.get(bc) ?? 0) + sumRec(r.src));
    }
    const onHold = new Map<string, number>();
    for (const s of skus) {
        const avail = sumRec(availabilityOf(s.nm_id, s.barcode) as Record<string, number>);
        const left = Math.max(0, avail - (shippedByBc.get(s.barcode) ?? 0));
        if (left > 0) onHold.set(s.barcode, left);
    }
    return onHold;
}

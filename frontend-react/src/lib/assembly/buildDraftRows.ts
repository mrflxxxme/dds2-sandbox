/**
 * Сборка СБАЛАНСИРОВАННЫХ строк черновика сборки (AssemblyDraftRow) из набора
 * выбранных SKU — чистая, переиспользуемая версия inline-пайплайна
 * `WarehouseNeedView` (skuBoxDistribution → skuPlans → okrugBoxConsolidated →
 * regularShipmentAlloc).
 *
 * Используется будущими панелями «добавить позиции в черновик» и «массовый
 * штрихкод → авто-распределение». Зеркалит подход матрицы потребности:
 *   1. per-SKU box-распределение по WB-складам целыми коробами (`ppb`);
 *   2. сплит по типу упаковки (короб / моно / сейф);
 *   3. кросс-SKU консолидация КОРОБОВ округа в целые СМЕШАННЫЕ паллеты
 *      (`consolidateMixedDistrictPallets`) — только для типа «короб»;
 *   4. подбор источника из ФФ — Pass 1 целыми коробами (жадно по убыванию
 *      доступности), Pass 2 россыпью для моно/сейф;
 *   5. при `wholePalletsOnly` — строгий срез каждой ff→wb отгрузки до целых
 *      паллет (`trimToWholePallets`).
 *
 * ЖЁСТКИЙ ИНВАРИАНТ каждой строки: `Σsrc === Σtgt` (требование `_allocate_pairs`
 * на бэке). Количества кратны коробу (`ppb`), капятся доступным ФФ-стоком.
 *
 * Чистая функция (без I/O, без React) — полностью юнит-тестируется.
 */
import type { AssemblyDraftRow, PackageType } from '@/types/api';
import { distributeByBoxMultiple } from '@/lib/utils/boxDistribution';
import {
    parseBoxSize,
    maxPalletHeightCm,
    effectiveBoxesPerPallet,
    consolidateMixedDistrictPallets,
} from '@/lib/utils/boxPallet';

/** Геометрия короба + кратность одного SKU. */
export interface DraftSkuInput {
    nm_id: number;
    barcode: string;
    vendor_code: string;
    /** WB-склад (canonical name) → целевая потребность/qty (штук). */
    target: Record<string, number>;
    /** ФФ-склад (id) → доступный свободный остаток (штук). */
    ffStock: Record<number, number>;
    /** Кратность короба (шт/короб). null/0 — россыпь (не box-режим). */
    ppb: number | null | undefined;
    /** Габариты короба «ДxШxВ» (см). null — нет габаритов (не палетизируется). */
    box_size: string | null | undefined;
    /**
     * Тип упаковки. Либо один на весь SKU (`packageType`), либо per-WB-склад
     * карта приёмки (`packageByWh`). Если задано и то и то — `packageByWh`
     * приоритетнее для складов, где он определён; иначе фолбэк на `packageType`.
     */
    packageType?: PackageType;
    packageByWh?: Record<string, PackageType>;
}

export interface BuildDraftRowsInput {
    skus: DraftSkuInput[];
    /** Только целые паллеты на каждую ff→wb отгрузку (по умолчанию true). */
    wholePalletsOnly?: boolean;
    /** WB-склад (name) → ключ округа/ФО (для кросс-SKU консолидации короба). */
    warehouseToDistrict?: Record<string, string>;
    /** Ручной override «коробок на паллету» по канон-размеру короба. */
    palletOverrides?: Record<string, number>;
}

/** Сумма значений `Record<*, number>`. */
function sumValues(o: Record<string, number>): number {
    return Object.values(o).reduce((s, v) => s + v, 0);
}

/**
 * Превращает выбранные SKU в сбалансированные строки черновика.
 * Каждая строка — `(nm_id × package_type)` с `Σsrc === Σtgt`.
 */
export function buildDraftRows(input: BuildDraftRowsInput): AssemblyDraftRow[] {
    const wholePalletsOnly = input.wholePalletsOnly !== false;
    const districtOf = input.warehouseToDistrict ?? {};
    const overrides = input.palletOverrides;

    // ── Pass A: per-SKU box-распределение + сплит по типу упаковки ──────────────
    interface SkuPlan {
        sku: DraftSkuInput;
        ppb: number;
        boxMode: boolean;
        boxSize: string | null | undefined;
        palletizable: boolean;
        boxTgt: Record<string, number>;
        monoTgt: Record<string, number>;
        superTgt: Record<string, number>;
        ffOrder: number[];
        ffPool: Record<number, number>;
        canBoxAt: (wh: string) => boolean;
    }

    // Ключ плана = `${nm_id}::${barcode}`, НЕ просто nm_id: одна WB-карточка (nm_id)
    // может нести несколько баркодов (размерные варианты), карточка без article_wb —
    // nm_id=0. Ключ только по nm_id схлопнул бы их в один план (last-wins) → молчаливая
    // потеря количества/чужой товар. barcode уникален per-nomenclature.
    const plans = new Map<string, SkuPlan>();
    for (const sku of input.skus) {
        const ppb = sku.ppb && sku.ppb > 0 ? Math.floor(sku.ppb) : 0;
        const boxMode = ppb > 0;
        const dims = sku.box_size ? parseBoxSize(sku.box_size) : null;
        const palletizable = boxMode && !!dims;

        // Доступно на ФФ всего (cap отгрузки) и порядок источников (по убыванию).
        const ffPool: Record<number, number> = {};
        for (const [idStr, av] of Object.entries(sku.ffStock)) {
            const id = Number(idStr);
            ffPool[id] = Math.max(0, Math.floor(av || 0));
        }
        const ffOrder = Object.keys(ffPool)
            .map(Number)
            .sort((x, y) => (ffPool[y] || 0) - (ffPool[x] || 0));
        const totalAvail = ffOrder.reduce((s, id) => s + ffPool[id], 0);

        // Бюджет к отгрузке = min(потребность, доступно на ФФ). В палет-режиме
        // капим до ФФ-сорсимого ЦЕЛЫМИ коробами (как в reference), чтобы
        // консолидация оперировала реально отгружаемым.
        const whNeeds = Object.entries(sku.target)
            .map(([name, need]) => ({ name, need: Math.max(0, Math.floor(need || 0)) }))
            .filter(w => w.need > 0);
        const totalNeed = whNeeds.reduce((s, w) => s + w.need, 0);
        const qty0 = Math.min(totalAvail, totalNeed);

        let tgt: Record<string, number> = {};
        if (qty0 > 0 && whNeeds.length > 0) {
            if (boxMode && qty0 >= ppb) {
                // Палет-режим — СТРОГАЯ кратность (хвост < короба остаётся на ФФ).
                tgt = distributeByBoxMultiple(whNeeds, qty0, ppb, !palletizable);
            } else {
                let rem = qty0;
                for (const w of whNeeds) {
                    if (rem <= 0) break;
                    const give = Math.min(rem, w.need);
                    tgt[w.name] = give;
                    rem -= give;
                }
            }
        }

        const pkgOf = (wh: string): PackageType =>
            sku.packageByWh?.[wh] ?? sku.packageType ?? 'BOX';

        const boxTgt: Record<string, number> = {};
        const monoTgt: Record<string, number> = {};
        const superTgt: Record<string, number> = {};
        for (const [wh, q] of Object.entries(tgt)) {
            if (q <= 0) continue;
            const p = pkgOf(wh);
            if (p === 'MONOPALLET') monoTgt[wh] = q;
            else if (p === 'SUPERSAFE') superTgt[wh] = q;
            else boxTgt[wh] = q;
        }

        // В палет-режиме капим box-портацию до ФФ-сорсимого целыми коробами.
        if (palletizable && Object.keys(boxTgt).length > 0) {
            const sourceable = ffOrder.reduce(
                (s, id) => s + Math.floor((ffPool[id] || 0) / ppb) * ppb, 0,
            );
            let cur = sumValues(boxTgt);
            if (cur > sourceable) {
                for (const wh of Object.keys(boxTgt).sort((a, b) => boxTgt[a] - boxTgt[b])) {
                    while (cur > sourceable && boxTgt[wh] >= ppb) { boxTgt[wh] -= ppb; cur -= ppb; }
                    if (boxTgt[wh] <= 0) delete boxTgt[wh];
                    if (cur <= sourceable) break;
                }
            }
        }

        plans.set(`${sku.nm_id}::${sku.barcode}`, {
            sku, ppb, boxMode, boxSize: sku.box_size, palletizable,
            boxTgt, monoTgt, superTgt, ffOrder, ffPool,
            canBoxAt: (wh: string) => pkgOf(wh) === 'BOX',
        });
    }

    // ── Pass B: кросс-SKU консолидация КОРОБОВ округа в целые смешанные паллеты ──
    // Палетизируемые box-cells группируем по округам, остальные проходят как есть.
    const consolidatedBox = new Map<string, Record<string, number>>();
    const byOkrug = new Map<string, Record<string, Record<string, number>>>();
    for (const [skuKey, plan] of plans) {
        if (!plan.palletizable) {
            if (Object.keys(plan.boxTgt).length) consolidatedBox.set(skuKey, { ...plan.boxTgt });
            continue;
        }
        for (const [wh, units] of Object.entries(plan.boxTgt)) {
            if (units <= 0) continue;
            const ok = districtOf[wh] || `__${wh}`;
            const g = byOkrug.get(ok) ?? {};
            (g[wh] ??= {})[skuKey] = units;
            byOkrug.set(ok, g);
        }
    }
    const ppbOf = (key: string) => plans.get(key)?.ppb || 1;
    const palletUnitsOf = (key: string, wh: string): number | null => {
        const plan = plans.get(key);
        if (!plan) return null;
        const bpp = effectiveBoxesPerPallet(plan.boxSize, maxPalletHeightCm(wh), overrides);
        return bpp && bpp > 0 && plan.ppb > 0 ? bpp * plan.ppb : null;
    };
    for (const [, byWhKey] of byOkrug) {
        // Приоритетный склад округа = крупнейший по суммарным units (тай — по имени).
        let anchor = '';
        let best = -1;
        for (const [wh, km] of Object.entries(byWhKey)) {
            const tot = sumValues(km);
            if (tot > best || (tot === best && (anchor === '' || wh < anchor))) { best = tot; anchor = wh; }
        }
        const canAnchor = (key: string, wh: string) => plans.get(key)?.canBoxAt(wh) ?? true;
        const out = consolidateMixedDistrictPallets({ byWhKey, anchorWh: anchor, ppbOf, palletUnitsOf, canAnchor });
        for (const [wh, km] of Object.entries(out.byWhKey)) {
            for (const [skuKey, units] of Object.entries(km)) {
                if (units <= 0) continue;
                const r = consolidatedBox.get(skuKey) ?? {};
                r[wh] = (r[wh] || 0) + units;
                consolidatedBox.set(skuKey, r);
            }
        }
    }

    // ── Pass C: подбор источника из ФФ + строгий срез до целых паллет ───────────
    const rows: AssemblyDraftRow[] = [];
    for (const [skuKey, plan] of plans) {
        const { sku, ppb, boxMode, palletizable, ffOrder, ffPool } = plan;
        const boxTgt = consolidatedBox.get(skuKey) ?? {};

        const byPkg = new Map<PackageType, Record<string, number>>();
        if (Object.keys(boxTgt).length) byPkg.set('BOX', { ...boxTgt });
        if (Object.keys(plan.monoTgt).length) byPkg.set('MONOPALLET', { ...plan.monoTgt });
        if (Object.keys(plan.superTgt).length) byPkg.set('SUPERSAFE', { ...plan.superTgt });
        if (byPkg.size === 0) continue;

        // Срез tgt поштучно до `total` (наименее объёмные склады первыми).
        const trimTgt = (t: Record<string, number>, total: number): Record<string, number> => {
            const out = { ...t };
            let cur = sumValues(out);
            for (const wh of Object.keys(out).sort((p, q) => out[p] - out[q])) {
                if (cur <= total) break;
                const drop = Math.min(out[wh], cur - total);
                out[wh] -= drop;
                cur -= drop;
                if (out[wh] <= 0) delete out[wh];
            }
            return out;
        };

        const pkgOrder: PackageType[] = ['BOX', 'MONOPALLET', 'SUPERSAFE'];
        const ordered = [
            ...pkgOrder.filter(p => byPkg.has(p)),
            ...[...byPkg.keys()].filter(p => !pkgOrder.includes(p)),
        ];
        // Строгий короб (палет-режим): уже целые паллеты, без россыпь-хвоста.
        const boxStrict = palletizable;

        for (const pkg of ordered) {
            let pkgTgt = { ...byPkg.get(pkg)! };
            let pkgQty = sumValues(pkgTgt);
            if (pkgQty <= 0) continue;
            const pkgSrc: Record<string, number> = {};

            if (boxMode) {
                let need = pkgQty;
                // Pass 1: целые коробки `ppb`, собираемые с одного ФФ (жадно по убыванию).
                for (const id of ffOrder) {
                    if (need < ppb) break;
                    const boxes = Math.floor((ffPool[id] || 0) / ppb);
                    if (boxes <= 0) continue;
                    const take = Math.min(Math.floor(need / ppb), boxes) * ppb;
                    if (take <= 0) continue;
                    pkgSrc[String(id)] = (pkgSrc[String(id)] || 0) + take;
                    ffPool[id] -= take;
                    need -= take;
                }
                // Pass 2: хвост < короба россыпью — моно/сейф и НЕ-строгий короб.
                if (pkg !== 'BOX' || !boxStrict) {
                    for (const id of ffOrder) {
                        if (need <= 0) break;
                        const av = ffPool[id] || 0;
                        if (av <= 0) continue;
                        const take = Math.min(need, av);
                        pkgSrc[String(id)] = (pkgSrc[String(id)] || 0) + take;
                        ffPool[id] -= take;
                        need -= take;
                    }
                }
                const sourced = pkgQty - need;
                if (sourced <= 0) continue;
                if (sourced < pkgQty) {
                    pkgTgt = trimTgt(pkgTgt, sourced);
                    pkgQty = sumValues(pkgTgt);
                }
            } else {
                let need = pkgQty;
                for (const id of ffOrder) {
                    if (need <= 0) break;
                    const av = ffPool[id] || 0;
                    if (av <= 0) continue;
                    const take = Math.min(need, av);
                    pkgSrc[String(id)] = take;
                    ffPool[id] -= take;
                    need -= take;
                }
                const sourced = pkgQty - need;
                if (sourced <= 0) continue;
                if (sourced < pkgQty) {
                    pkgTgt = trimTgt(pkgTgt, sourced);
                    pkgQty = sumValues(pkgTgt);
                }
            }

            // Строгий срез до целых паллет: каждая ff→wb отгрузка либо целыми
            // паллетами, либо ничего (под-паллетный хвост остаётся на ФФ).
            if (wholePalletsOnly && pkg === 'BOX' && palletizable) {
                const trimmed = trimToWholePalletsLocal(pkgTgt, plan, overrides);
                pkgTgt = trimmed;
                pkgQty = sumValues(pkgTgt);
            }

            for (const k of Object.keys(pkgSrc)) if (pkgSrc[k] <= 0) delete pkgSrc[k];
            // Hard-guard баланса Σsrc==Σtgt: подгоняем БОЛЬШУЮ сторону под меньшую.
            let tgtSum = sumValues(pkgTgt);
            let srcSum = sumValues(pkgSrc);
            if (tgtSum > srcSum) { pkgTgt = trimTgt(pkgTgt, srcSum); tgtSum = sumValues(pkgTgt); }
            if (srcSum > tgtSum) { trimSrc(pkgSrc, ffOrder, ffPool, srcSum - tgtSum); srcSum = sumValues(pkgSrc); }
            if (Object.keys(pkgSrc).length === 0 || Object.keys(pkgTgt).length === 0) continue;
            if (tgtSum !== srcSum) continue; // защита: не пускаем несбалансированную строку

            rows.push({
                nm_id: sku.nm_id,
                barcode: sku.barcode,
                vendor_code: sku.vendor_code,
                src: pkgSrc,
                tgt: pkgTgt,
                package_type: pkg,
            });
        }
    }

    return rows;
}

/**
 * Строгий срез box-распределения SKU до ЦЕЛЫХ ПАЛЛЕТ. Когда `tgt` уже консолидирован
 * в целые паллеты (Pass B), но ФФ-сорсинг мог обрезать хвост — снимаем под-паллетные
 * остатки целыми паллетами со складов с наименьшим qty первыми.
 */
function trimToWholePalletsLocal(
    tgt: Record<string, number>,
    plan: { boxSize: string | null | undefined; ppb: number },
    overrides: Record<string, number> | undefined,
): Record<string, number> {
    const palletUnitsForWh = (wh: string): number | null => {
        const bpp = effectiveBoxesPerPallet(plan.boxSize, maxPalletHeightCm(wh), overrides);
        return bpp && bpp > 0 && plan.ppb > 0 ? bpp * plan.ppb : null;
    };
    // Снимаем под-паллетный хвост КАЖДОГО склада (не общий cap, а per-wh целые паллеты).
    const out: Record<string, number> = {};
    for (const [wh, q] of Object.entries(tgt)) {
        if (q <= 0) continue;
        const pu = palletUnitsForWh(wh);
        if (pu === null) { out[wh] = q; continue; } // не палетизируется — не трогаем
        const keep = Math.floor(q / pu) * pu;
        if (keep > 0) out[wh] = keep;
    }
    return out;
}

/** Снять `excess` штук из `src` (наименее объёмные ФФ первыми), вернув их в `ffPool`. */
function trimSrc(
    src: Record<string, number>,
    ffOrder: number[],
    ffPool: Record<number, number>,
    excess: number,
): void {
    let rem = excess;
    for (const wh of Object.keys(src).sort((a, b) => src[a] - src[b])) {
        if (rem <= 0) break;
        const drop = Math.min(src[wh], rem);
        src[wh] -= drop;
        ffPool[Number(wh)] = (ffPool[Number(wh)] || 0) + drop;
        rem -= drop;
        if (src[wh] <= 0) delete src[wh];
    }
}

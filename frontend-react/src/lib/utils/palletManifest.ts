/**
 * Перечисление ФИЗИЧЕСКИХ паллет одной отгрузки с содержимым по SKU — для
 * превью «как формируются паллеты».
 *
 * Агрегатный счётчик (`palletsForLines`) даёт скаляр «сколько паллет»; этот
 * модуль раскрывает ту же величину поштучно: какие SKU и в каком объёме лежат
 * на каждой паллете. Манифест ОБЯЗАН примиряться с агрегатом —
 * `Σ manifest.pallets.length === palletsForLines(...).pallets` для тех же входов
 * (тест это проверяет), иначе per-pallet вид разойдётся с KPI-счётчиком.
 *
 * Режимы:
 *   • `box`  — СМЕШАННЫЕ паллеты: короба ВСЕХ SKU бин-пакуются на паллеты по
 *              суммарной ёмкости (units) до целой паллеты (кросс-SKU, как WB
 *              разрешает микс артикулов в коробе).
 *   • `mono` — паллеты ПО SKU: каждый артикул занимает свои паллеты.
 *
 * SKU без габаритов/кратности (паллету не посчитать) НЕ теряются — уходят в
 * `unpalletized` (бакет «без размеров»). `fillPct` = заполнено units / ёмкость
 * паллеты этого склада-цели.
 *
 * Чистая функция (без I/O, без React) — полностью юнит-тестируется. Зеркалит
 * математику `palletsForLines`: ёмкость паллеты SKU = boxesPerPallet × ppb.
 */
import {
    effectiveBoxesPerPallet,
    DEFAULT_MAX_PALLET_HEIGHT_CM,
    monoPalletBins,
    MONO_MAX_PALLET_ARTICLES,
} from '@/lib/utils/boxPallet';

/** Линия товара отгрузки для манифеста. */
export interface ManifestLine {
    nmId: number;
    vendorCode: string;
    /** Штук в линии (целое ≥ 0). */
    units: number;
    /** Габариты короба «ДxШxВ» (см). null — нет габаритов. */
    boxSize: string | null | undefined;
    /** Кратность короба (шт/короб). null/0 — кратность не задана. */
    ppb: number | null | undefined;
}

export interface ManifestOpts {
    /** 'box' — смешанные паллеты; 'mono' — по SKU. */
    mode: 'box' | 'mono';
    /** Лимит высоты паллеты склада-цели, см (через `maxPalletHeightCm`). */
    maxHeightCm?: number;
    /** Ручной override «коробок на паллету» по канон-размеру короба. */
    overrides?: Record<string, number>;
}

/** Содержимое одного SKU на конкретной паллете. */
export interface PalletItem {
    nmId: number;
    vendorCode: string;
    /** Штук этого SKU на паллете. */
    units: number;
    /** Эквивалент в коробах (units / ppb, округление вверх). */
    boxes: number;
}

/** Одна физическая паллета. */
export interface PalletBin {
    /** Номер паллеты (1-based). */
    palletNo: number;
    items: PalletItem[];
    /** Заполненность 0..1 (units на паллете / ёмкость паллеты). */
    fillPct: number;
}

export interface PalletManifest {
    pallets: PalletBin[];
    /** SKU без габаритов/кратности (паллету не посчитать) — НЕ распалечены. */
    unpalletized: PalletItem[];
}

/** Эквивалент в коробах для дисплея (округление вверх). */
function unitsToBoxes(units: number, ppb: number): number {
    if (ppb <= 0) return 0;
    return Math.ceil(units / ppb);
}

/**
 * Построить манифест паллет для ОДНОЙ отгрузки (на один склад-цель).
 *
 * @param lines линии отгрузки (units + boxSize + ppb).
 * @param opts  режим (box/mono) + высота склада + override.
 */
export function buildPalletManifest(lines: ManifestLine[], opts: ManifestOpts): PalletManifest {
    const maxHeightCm = opts.maxHeightCm ?? DEFAULT_MAX_PALLET_HEIGHT_CM;
    const overrides = opts.overrides;

    // Ёмкость паллеты SKU = коробов/паллету × ppb (units). null — не палетизируется.
    const palletUnitsOf = (line: ManifestLine): number | null => {
        const ppb = line.ppb && line.ppb > 0 ? Math.floor(line.ppb) : 0;
        if (ppb <= 0) return null;
        const bpp = effectiveBoxesPerPallet(line.boxSize, maxHeightCm, overrides);
        return bpp && bpp > 0 ? bpp * ppb : null;
    };

    const unpalletized: PalletItem[] = [];
    // Нормализованные палетизируемые линии (units > 0, есть ёмкость паллеты).
    interface NormLine { line: ManifestLine; units: number; ppb: number; cap: number; }
    const norm: NormLine[] = [];
    for (const line of lines) {
        const units = Math.max(0, Math.floor(line.units || 0));
        if (units <= 0) continue;
        const cap = palletUnitsOf(line);
        const ppb = line.ppb && line.ppb > 0 ? Math.floor(line.ppb) : 0;
        if (cap === null || cap <= 0) {
            // Нет габаритов/кратности — в бакет «без размеров».
            unpalletized.push({
                nmId: line.nmId, vendorCode: line.vendorCode, units,
                boxes: unitsToBoxes(units, ppb),
            });
            continue;
        }
        norm.push({ line, units, ppb, cap });
    }

    const pallets: PalletBin[] =
        opts.mode === 'mono' ? packMono(norm) : packBox(norm);

    return { pallets, unpalletized };
}

interface NormLineLite { line: ManifestLine; units: number; ppb: number; cap: number; }

/**
 * MONO: паллета вмещает ≤3 АРТИКУЛА (правило WB) — мелкие моно-SKU собираются
 * ВМЕСТЕ на одну паллету (`monoPalletBins`), а не каждый на свою. Зеркалит
 * `palletsForLines(..., 'mono')` → `Σ manifest.pallets.length === pallets`.
 */
function packMono(norm: NormLineLite[]): PalletBin[] {
    // Агрегируем по nm (один артикул = один ключ), сохраняем геометрию.
    const byKey = new Map<number, NormLineLite>();
    for (const n of norm) {
        const e = byKey.get(n.line.nmId);
        if (e) e.units += n.units;
        else byKey.set(n.line.nmId, { ...n });
    }
    const items = [...byKey.values()].map((n) => ({ key: String(n.line.nmId), units: n.units, cap: n.cap, box: n.ppb || 0 }));
    const bins = monoPalletBins(items, MONO_MAX_PALLET_ARTICLES);
    const pallets: PalletBin[] = [];
    let no = 1;
    for (const bin of bins) {
        let frac = 0;
        const palletItems: PalletItem[] = [];
        for (const { key, units } of bin) {
            const n = byKey.get(Number(key))!;
            frac += units / n.cap;
            palletItems.push({
                nmId: n.line.nmId, vendorCode: n.line.vendorCode, units,
                boxes: unitsToBoxes(units, n.ppb),
            });
        }
        pallets.push({ palletNo: no++, items: palletItems, fillPct: Math.min(frac, 1) });
    }
    return pallets;
}

/**
 * BOX: смешанные паллеты. Бин-пакуем короба ВСЕХ SKU по суммарной «доле объёма»
 * (units_i / cap_i) — каждый SKU занимает свою долю паллеты по геометрии. Целое
 * число паллет = ⌈Σ units_i / cap_i⌉ (как `palletsForLines(..., 'box')`).
 *
 * РЕКОНСИЛЯЦИЯ С АГРЕГАТОМ: целое число паллет фиксируем ЗАРАНЕЕ как
 * `⌈Σ units_i/cap_i⌉` (ровно `palletsForLines(..., 'box')`), затем раскладываем
 * units в РОВНО столько бинов жадно — так per-pallet вид никогда не разойдётся
 * с KPI-счётчиком (дискретизация коробов не может «добавить» лишнюю паллету:
 * хвост, не влезший по объёму, садится в последний бин, который для аггрегата
 * уже учтён через ceil). Последняя паллета может быть неполной.
 */
function packBox(norm: NormLineLite[]): PalletBin[] {
    if (norm.length === 0) return [];
    const EPS = 1e-9;
    // Целое число паллет = агрегат (тот же `ceil(fill − tol)`, что в palletsForLines 'box':
    // −tol поглощает дискретизационный сливер после snapToWholePallets, иначе 1.05→2 паллеты).
    const totalFill = norm.reduce((s, n) => s + n.units / n.cap, 0);
    const tol = Math.min(0.5, norm.reduce((m, n) => Math.max(m, n.cap > 0 ? 1 / n.cap : 0), 0));
    const palletCount = Math.max(1, Math.ceil(totalFill - Math.max(tol, EPS)));

    const queue = norm.map(n => ({ ...n, rem: n.units }));
    const pallets: PalletBin[] = [];

    for (let p = 0; p < palletCount; p++) {
        const isLast = p === palletCount - 1;
        const itemUnits = new Map<number, number>(); // nmId → units на этой паллете
        let frac = 0; // занятая доля паллеты (Σ units_on / cap)
        for (const q of queue) {
            if (q.rem <= 0) continue;
            // На ПОСЛЕДНЮЮ паллету сваливаем все остатки (хвост < паллеты учтён
            // в ceil); на прочие — только то, что влезает по объёму.
            let take: number;
            if (isLast) {
                take = q.rem;
            } else {
                if (frac >= 1 - EPS) break;
                const fitByVolume = Math.floor((1 - frac) * q.cap + EPS);
                take = Math.min(q.rem, Math.max(fitByVolume, 0));
                if (take <= 0) continue;
            }
            const id = q.line.nmId;
            itemUnits.set(id, (itemUnits.get(id) || 0) + take);
            q.rem -= take;
            frac += take / q.cap;
        }
        // Материализуем items в порядке линий.
        const items: PalletItem[] = [];
        for (const n of norm) {
            const u = itemUnits.get(n.line.nmId);
            if (u && u > 0) {
                items.push({
                    nmId: n.line.nmId, vendorCode: n.line.vendorCode, units: u,
                    boxes: unitsToBoxes(u, n.ppb),
                });
            }
        }
        pallets.push({ palletNo: p + 1, items, fillPct: Math.min(frac, 1) });
    }
    return pallets;
}

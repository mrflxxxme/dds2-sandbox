/**
 * Геометрия паллеты из габаритов коробки (box_size).
 *
 * WB-правила (FBW): поставка укладывается на евро-паллету 120×80 см (без товара
 * высота 14,5 см). Сколько коробок данного размера встаёт на паллету = (коробок
 * в слое по площади основания) × (число слоёв по высоте склада). Вес пока НЕ
 * учитываем (по требованию) — только габариты.
 *
 * box_size — свободная строка «Д×Ш×В» в см (напр. «60x40x40», «40х30х25»,
 * «60*50*40»). Парсим так же, как остальной проект (normalizeBoxSize в
 * factory-orders): сепараторы ×*,/\\ → x, берём 3 числа. Третье — высота.
 */

/** Полезное основание евро-паллеты, см. */
export const EURO_PALLET = { length: 120, width: 80 } as const;
/** Высота самой паллеты без товара, см (евростандарт). */
export const PALLET_BASE_HEIGHT_CM = 14.5;
/** Дефолтный лимит высоты «товар + паллета», см, если склад неизвестен. */
export const DEFAULT_MAX_PALLET_HEIGHT_CM = 180;

export interface BoxDims {
    /** Длина, см. */
    length: number;
    /** Ширина, см. */
    width: number;
    /** Высота, см. */
    height: number;
}

/**
 * Лимит максимальной высоты «товар + паллета» по складам WB (см).
 * Источник: офиц. док «Этап 3 / Ограничения по весу и габаритам» (FBW),
 * сверено 06.2026. min высота — 50 см. Ключи нормализуются (без скобок,
 * lower-case) в `maxPalletHeightCm`.
 */
export const WAREHOUSE_MAX_PALLET_HEIGHT_CM: Record<string, number> = {
    'воронеж': 185,
    'коледино': 180,
    'электросталь': 180,
    'казань': 180,
    'тула': 180,
    'невинномысск': 180,
    'чехов-1': 180,
    'чехов-2': 180,
    'чехов': 180,
    'сарапул': 180,
    'владимир': 180,
    'новосемейкино': 175,
    'самара': 175, // Новосемейкино — канон в WAREHOUSE_COORDS бывает «Самара»
    'краснодар': 170,
    'волгоград': 170,
    'алматы атакент': 170,
    'екатеринбург': 170, // Перспективная
    'астана 2': 170,
    'котовск': 160,
    'рязань': 160, // Тюшевское
};

/** Нормализация имени склада для lookup высоты (без скобок/суффиксов, lower). */
function normWarehouseKey(name: string): string {
    return (name || '')
        .toLowerCase()
        .replace(/\s*\([^)]*\)\s*/g, ' ') // убрать «(...)»
        .replace(/[:,].*$/, '') // отрезать «: Перспективная», «, ...»
        .trim();
}

/**
 * Лимит высоты паллеты для склада (см). Неизвестный склад → дефолт 180.
 * Берём точное совпадение нормализованного ключа, иначе — префиксное (имя
 * склада начинается с известного ключа), иначе дефолт.
 */
export function maxPalletHeightCm(warehouseName: string | null | undefined): number {
    if (!warehouseName) return DEFAULT_MAX_PALLET_HEIGHT_CM;
    const key = normWarehouseKey(warehouseName);
    if (key in WAREHOUSE_MAX_PALLET_HEIGHT_CM) return WAREHOUSE_MAX_PALLET_HEIGHT_CM[key];
    // Однонаправленно: реальное имя начинается с известного ключа (напр.
    // «коледино сгт» → «коледино»). НЕ матчим обратно (ключ начинается с имени) —
    // иначе аббревиатура «во» ложно резолвилась бы в «воронеж».
    for (const [k, v] of Object.entries(WAREHOUSE_MAX_PALLET_HEIGHT_CM)) {
        if (key.startsWith(k)) return v;
    }
    return DEFAULT_MAX_PALLET_HEIGHT_CM;
}

/**
 * Распарсить box_size «Д×Ш×В» (см) → {length,width,height}.
 * Возвращает null, если меньше 3 чисел или какое-то ≤ 0 (нет габаритов —
 * паллету не сформировать, такой SKU подсвечиваем отдельно).
 */
export function parseBoxSize(boxSize: string | null | undefined): BoxDims | null {
    if (!boxSize) return null;
    // Извлекаем ЧИСЛА независимо от разделителей/опечаток между ними:
    // «60x40x40», «60*40*40», «60×40×50», «60x40xc40» (лишняя буква) → [.,.,.].
    // Дробное — только через точку (запятая = разделитель). Берём первые 3.
    const nums = (boxSize.match(/\d+(?:\.\d+)?/g) || []).map(Number);
    if (nums.length < 3) return null;
    const [length, width, height] = nums;
    if (length <= 0 || width <= 0 || height <= 0) return null;
    return { length, width, height };
}

/**
 * Канон ключа размера коробки для ручного override «коробок на паллету».
 * Разделители (×, кир. х, *, запятая, слэши, пробелы) → 'x', lower-case, без
 * крайних 'x'. «60×40×50» / «60x40x50» / «60 Х 40 Х 50» → «60x40x50».
 * Зеркало backend `_canon_box_size` — ключи должны совпадать.
 */
export function canonBoxSize(s: string | null | undefined): string {
    if (!s) return '';
    // Канон по ЧИСЛАМ (первые 3) — устойчив к разделителям/опечаткам:
    // «60x40xc40» и «60*40*40» → один ключ «60x40x40». Дробное «9.0» → «9».
    const nums = (s.match(/\d+(?:\.\d+)?/g) || []).map((n) => String(Number(n)));
    if (nums.length >= 3) return nums.slice(0, 3).join('x');
    // Меньше 3 чисел — не размер; стабильный fallback по компактной строке.
    return s.trim().toLowerCase().replace(/\s+/g, '');
}

/**
 * Эффективное «коробок на паллету»: ручной override по размеру (если задан)
 * ПЕРЕБИВАЕТ геометрию. Иначе — геометрия по габаритам и высоте склада.
 * null — нет габаритов и нет override.
 */
export function effectiveBoxesPerPallet(
    boxSize: string | null | undefined,
    maxHeightCm: number = DEFAULT_MAX_PALLET_HEIGHT_CM,
    overrides?: Record<string, number>,
): number | null {
    const ov = overrides?.[canonBoxSize(boxSize)];
    if (ov && ov > 0) return ov;
    const dims = parseBoxSize(boxSize);
    return dims ? boxesPerPallet(dims, maxHeightCm) : null;
}

/**
 * Сколько коробок данного размера встаёт в ОДИН слой на основание паллеты.
 * Берём лучшую из двух ориентаций коробки (без поворота по высоте).
 * 0 — коробка не помещается на основание (крупногабарит → монопаллета).
 */
export function boxesPerLayer(dims: BoxDims, pallet = EURO_PALLET): number {
    const { length: l, width: w } = dims;
    const orient1 = Math.floor(pallet.length / l) * Math.floor(pallet.width / w);
    const orient2 = Math.floor(pallet.length / w) * Math.floor(pallet.width / l);
    return Math.max(orient1, orient2);
}

/**
 * Сколько коробок данного размера встаёт на ОДНУ паллету (слои × коробок/слой).
 * Возвращает null, если коробка не помещается на основание ИЛИ её высота +
 * паллета превышает лимит высоты склада (хотя бы 1 слой не встаёт) — такую
 * поставку коробами на паллете не сформировать.
 */
export function boxesPerPallet(
    dims: BoxDims,
    maxHeightCm: number = DEFAULT_MAX_PALLET_HEIGHT_CM,
    pallet = EURO_PALLET,
): number | null {
    const perLayer = boxesPerLayer(dims, pallet);
    if (perLayer < 1) return null;
    const heightBudget = maxHeightCm - PALLET_BASE_HEIGHT_CM;
    const layers = Math.floor(heightBudget / dims.height);
    if (layers < 1) return null;
    return perLayer * layers;
}

/**
 * Единиц товара на паллету = коробок/паллету × кратность коробки (шт/короб).
 * null, если нет габаритов (boxesPerPallet=null) или кратность не задана.
 */
export function unitsPerPallet(
    dims: BoxDims,
    boxQty: number | null | undefined,
    maxHeightCm: number = DEFAULT_MAX_PALLET_HEIGHT_CM,
): number | null {
    if (!boxQty || boxQty <= 0) return null;
    const bpp = boxesPerPallet(dims, maxHeightCm);
    return bpp === null ? null : bpp * boxQty;
}

/** Число паллет (с округлением вверх) под заданное кол-во коробок. */
export function palletsForBoxes(boxes: number, boxesPerPalletValue: number): number {
    if (boxes <= 0 || boxesPerPalletValue <= 0) return 0;
    return Math.ceil(boxes / boxesPerPalletValue);
}

/** Линия товара для подсчёта паллет на один склад-цель. */
export interface PalletLine {
    /** Штук в линии. Для короба обычно кратно box_qty (целые короба). */
    units: number;
    /** Кратность короба (шт/короб). null/0 — кратность не задана. */
    boxQty: number | null | undefined;
    /** Габариты коробки «ДxШxВ» (см). null — габаритов нет. */
    boxSize: string | null | undefined;
}

export interface PalletCount {
    /** Целых паллет (округление вверх). 0 — все линии непалетизируемы. */
    pallets: number;
    /** Дробная заполненность в паллетах (Σ долей по геометрии). */
    fill: number;
    /** Линий без габаритов/кратности (в `pallets` НЕ вошли). */
    unknownLines: number;
    /** Штук в непалетизируемых линиях. */
    unknownUnits: number;
}

const PALLET_FILL_EPS = 1e-9;

/**
 * Сколько паллет занимает набор линий на ОДИН склад-цель.
 *
 * `mode='box'` (смешанные паллеты короба): короба РАЗНЫХ SKU суммируются по
 * объёму — `pallets = ⌈Σ units_i / units_на_паллету_i⌉`. WB разрешает микс
 * артикулов в коробе, поэтому считаем по суммарной доле, как
 * [[consolidateMixedDistrictPallets]] (`footprintAt`).
 * `mode='mono'` (моно/сейф): каждый SKU — свои паллеты —
 * `pallets = Σ ⌈units_i / units_на_паллету_i⌉` (микс запрещён).
 *
 * `units_на_паллету_i = effectiveBoxesPerPallet(box_size, высота, override) × box_qty`.
 * Линии без габаритов/кратности не палетизируются — идут в `unknownLines/unknownUnits`
 * (физически их всё равно везут, но число паллет по ним не посчитать).
 *
 * Чистая функция — полностью юнит-тестируется.
 *
 * @param lines      линии (units + box_qty + box_size).
 * @param maxHeightCm лимит высоты паллеты склада-цели (`maxPalletHeightCm`).
 * @param mode       'box' — смешанные паллеты; 'mono' — по SKU.
 * @param overrides  ручной «коробок на паллету» по канон-размеру.
 */
/** Максимум артикулов на одной монопаллете (правило WB). */
export const MONO_MAX_PALLET_ARTICLES = 3;

/**
 * Бин-пакинг МОНО в целые паллеты с лимитом ≤`maxArticles` артикулов на паллету
 * (правило WB). В отличие от «по SKU = свои паллеты», мелкие моно-артикулы
 * собираются ВМЕСТЕ на одну паллету (как смешанный короб, но кап на 3 артикула),
 * чтобы не плодить полупустые паллеты. Жадно: крупнейший footprint (`units/cap`)
 * первым; на паллету добираем артикулы по объёму, пока не заполнится или не
 * наберём `maxArticles`. `cap` = штук в полной паллете SKU (boxesPerPallet×ppb).
 * Возвращает раскладку: паллета → [{key, units}]. Чистая функция.
 */
export function monoPalletBins(
    items: { key: string; units: number; cap: number; box?: number }[],
    maxArticles: number = MONO_MAX_PALLET_ARTICLES,
): { key: string; units: number }[][] {
    const EPS = 1e-9;
    const queue = items
        .filter((i) => i.units > 0 && i.cap > 0)
        .map((i) => ({ ...i, rem: i.units, box: i.box && i.box > 0 ? Math.floor(i.box) : 0 }))
        .sort((a, b) => b.rem / b.cap - a.rem / a.cap || (a.key < b.key ? -1 : 1));
    const pallets: { key: string; units: number }[][] = [];
    while (queue.some((q) => q.rem > 0)) {
        const pallet: { key: string; units: number }[] = [];
        let frac = 0;
        for (const q of queue) {
            if (q.rem <= 0) continue;
            if (pallet.length >= maxArticles) break;
            if (frac >= 1 - EPS) break;
            let fit = Math.floor((1 - frac) * q.cap + EPS);
            if (q.box > 0) fit = Math.floor(fit / q.box) * q.box; // только ЦЕЛЫЕ коробы (не режем короб)
            const take = Math.min(q.rem, Math.max(fit, 0));
            if (take <= 0) continue;
            pallet.push({ key: q.key, units: take });
            q.rem -= take;
            frac += take / q.cap;
        }
        // Предохранитель: по объёму ничего не влезло (паллета полна) — крупнейший остаток
        // на новую паллету, целыми коробами (≤cap; если короб > cap — кладём как есть).
        if (pallet.length === 0) {
            const q = queue.find((x) => x.rem > 0);
            if (!q) break;
            const capBoxes = q.box > 0 ? Math.floor(q.cap / q.box) * q.box : q.cap;
            const take = Math.min(q.rem, capBoxes > 0 ? capBoxes : q.rem);
            pallet.push({ key: q.key, units: take });
            q.rem -= take;
        }
        pallets.push(pallet);
    }
    return pallets;
}

export function palletsForLines(
    lines: PalletLine[],
    maxHeightCm: number,
    mode: 'box' | 'mono',
    overrides?: Record<string, number>,
): PalletCount {
    let fill = 0;
    let tol = 0; // допуск дискретизации = одна штука «крупнейшего» (мин upp) SKU
    let unknownLines = 0;
    let unknownUnits = 0;
    const monoItems: { key: string; units: number; cap: number; box?: number }[] = [];
    let idx = 0;
    for (const line of lines) {
        const units = Math.max(0, Math.floor(line.units || 0));
        if (units <= 0) continue;
        const bpp = effectiveBoxesPerPallet(line.boxSize, maxHeightCm, overrides);
        const boxQty = line.boxQty && line.boxQty > 0 ? line.boxQty : null;
        const upp = bpp !== null && boxQty ? bpp * boxQty : null;
        if (upp === null || upp <= 0) {
            unknownLines += 1;
            unknownUnits += units;
            continue;
        }
        fill += units / upp;
        // Гранула допуска = короб (если кратность задана) — зеркало snapToWholePallets:
        // после короб-гранулярного среза footprint садится в пределах короба от целого.
        tol = Math.max(tol, (boxQty && boxQty > 1 ? boxQty : 1) / upp);
        monoItems.push({ key: String(idx++), units, cap: upp, box: boxQty ?? 0 });
    }
    tol = Math.min(tol, 0.5); // предохранитель (как в snapToWholePallets)
    // mono — ≤3 артикула на паллету (бин-пакинг). box — смешанные паллеты: `ceil(fill − tol)`,
    // а НЕ ceil(fill): после snapToWholePallets footprint садится чуть ВЫШЕ целого (сливер
    // дискретизации ≤ tol, нужен для идемпотентности/не-потери товара). ceil(fill) посчитал
    // бы лишнюю полупустую паллету (1.05 → 2); −tol поглощает сливер (1.05 → 1), а реальный
    // хвост > tol (1.5 → 2) остаётся.
    let palletsRaw: number;
    if (mode === 'mono') {
        // Авторитет = packMonoPallets: считаем только ГОТОВЫЕ (строго полные) паллеты —
        // недобранные не едут (идут в предбронь/«Дозабить»). Показ = авторитет.
        const km: Record<string, number> = {};
        const capBy: Record<string, number> = {};
        const boxBy: Record<string, number> = {};
        for (const it of monoItems) { km[it.key] = (km[it.key] || 0) + it.units; capBy[it.key] = it.cap; boxBy[it.key] = it.box ?? 0; }
        palletsRaw = packMonoPallets(km, (k) => capBy[k] ?? null, MONO_MAX_PALLET_ARTICLES, (k) => boxBy[k] ?? 0).pallets.length;
    } else {
        palletsRaw = Math.ceil(fill - Math.max(tol, PALLET_FILL_EPS));
    }
    const pallets = palletsRaw > 0 ? palletsRaw : 0; // нормализуем -0 → 0
    return { pallets, fill, unknownLines, unknownUnits };
}

/**
 * Можно ли SKU корректно распределять коробами/паллетами:
 * нужны И кратность коробки (box_qty), И габариты (box_size парсится).
 * Используется для подсветки «не распределяется» на UI.
 */
export function canPalletize(
    boxQty: number | null | undefined,
    boxSize: string | null | undefined,
): boolean {
    return Boolean(boxQty && boxQty > 0 && parseBoxSize(boxSize));
}

export interface DistrictPalletResult {
    /** Новое распределение wh → шт (кратно целым паллетам по округам). */
    tgt: Record<string, number>;
    /** Сколько штук реально отгружается (из исходного tgt). */
    shipped: number;
    /** Штук, оставшихся на ФФ (крошки < паллеты, не вошедшие в паллету). */
    droppedToFf: number;
    /** Штук, добранных из ФФ-буфера, чтобы достроить паллету. */
    usedBuffer: number;
}

/**
 * Консолидация распределения SKU в ЦЕЛЫЕ ПАЛЛЕТЫ по направлениям (ФО).
 *
 * Правило: с каждого направления отгрузка кратна целой паллете. Каждый склад
 * держит свои целые паллеты на месте; «крошки» (< паллеты) со всего округа
 * пуллятся на ПРИОРИТЕТНЫЙ склад округа (по умолчанию — с наибольшим
 * распределением, напр. Коледино в ЦФО) и собираются там в целые паллеты.
 * Финальный остаток < паллеты:
 *   • добивается из ФФ-буфера (`ffBufferUnits`), если хватает на полную паллету;
 *   • иначе остаётся на ФФ (не отгружается, `droppedToFf`).
 *
 * Паллета считается геометрически из габаритов коробки (`dims`) и лимита высоты
 * каждого склада (`heightCmForWh`). Если SKU не палетизируется (нет `ppb`, или
 * приоритетный склад округа крупногабаритный → `boxesPerPallet=null`) — округ
 * остаётся как есть (no-op), чтобы не терять товар.
 *
 * Чистая функция (без I/O) — полностью юнит-тестируется.
 *
 * @param tgt           wh → шт (после box-кратного распределения).
 * @param ppb           кратность коробки (шт/короб).
 * @param dims          габариты коробки SKU.
 * @param districtOf    wh → ключ округа.
 * @param heightCmForWh wh → лимит высоты паллеты, см.
 * @param ffBufferUnits штук SKU доступно на ФФ сверх Σtgt (для «добить»).
 * @param opts.topUp    разрешить добивку из буфера (по умолчанию true).
 */
export function consolidateDistrictPallets(
    tgt: Record<string, number>,
    ppb: number | null | undefined,
    dims: BoxDims | null,
    districtOf: (wh: string) => string,
    heightCmForWh: (wh: string) => number,
    ffBufferUnits = 0,
    opts: { topUp?: boolean; boxesOverride?: number } = {},
): DistrictPalletResult {
    // Ручной override «коробок на паллету» по размеру (если задан) перебивает
    // геометрию для ВСЕХ складов (height-independent).
    const ovBoxes = opts.boxesOverride && opts.boxesOverride > 0 ? Math.floor(opts.boxesOverride) : null;
    const sum = (o: Record<string, number>) => Object.values(o).reduce((s, v) => s + v, 0);
    // Без кратности/габаритов — паллету не сформировать, возвращаем как есть.
    if (!ppb || ppb <= 0 || !dims) {
        return { tgt: { ...tgt }, shipped: sum(tgt), droppedToFf: 0, usedBuffer: 0 };
    }

    const topUp = opts.topUp !== false;
    let buffer = Math.max(0, Math.floor(ffBufferUnits));
    const out: Record<string, number> = {};
    let shipped = 0;
    let droppedToFf = 0;
    let usedBuffer = 0;

    // Группируем склады с qty>0 по округам.
    const byDistrict = new Map<string, string[]>();
    for (const [wh, q] of Object.entries(tgt)) {
        if (q <= 0) continue;
        const d = districtOf(wh);
        const arr = byDistrict.get(d);
        if (arr) arr.push(wh);
        else byDistrict.set(d, [wh]);
    }

    for (const whs of byDistrict.values()) {
        // Приоритетный склад округа = с наибольшим qty (тай — по имени), туда пуллим крошки.
        const anchor = whs.reduce((a, b) =>
            tgt[b] > tgt[a] || (tgt[b] === tgt[a] && b < a) ? b : a,
        );
        const anchorBoxes = ovBoxes ?? boxesPerPallet(dims, heightCmForWh(anchor));
        if (anchorBoxes === null) {
            // Приоритетный склад крупногабаритный → округ не консолидируем.
            for (const wh of whs) {
                out[wh] = (out[wh] ?? 0) + tgt[wh];
                shipped += tgt[wh];
            }
            continue;
        }
        const anchorPalletUnits = anchorBoxes * ppb;

        // Каждый склад держит свои ЦЕЛЫЕ паллеты (по своей высоте); остаток — в пул.
        let pool = 0;
        for (const wh of whs) {
            const pb = ovBoxes ?? boxesPerPallet(dims, heightCmForWh(wh));
            const palletUnits = pb !== null ? pb * ppb : 0;
            if (palletUnits > 0) {
                const keep = Math.floor(tgt[wh] / palletUnits) * palletUnits;
                if (keep > 0) {
                    out[wh] = (out[wh] ?? 0) + keep;
                    shipped += keep;
                }
                pool += tgt[wh] - keep;
            } else {
                pool += tgt[wh]; // не палетизируется здесь → весь объём в пул
            }
        }

        // Пул крошек → целые паллеты на приоритетном складе.
        const poolPallets = Math.floor(pool / anchorPalletUnits);
        if (poolPallets > 0) {
            const add = poolPallets * anchorPalletUnits;
            out[anchor] = (out[anchor] ?? 0) + add;
            shipped += add;
            pool -= add;
        }

        // Финальный остаток < паллеты: добить из буфера или оставить на ФФ.
        if (pool > 0) {
            const needed = anchorPalletUnits - pool;
            if (topUp && buffer >= needed) {
                out[anchor] = (out[anchor] ?? 0) + pool + needed;
                shipped += pool;
                usedBuffer += needed;
                buffer -= needed;
            } else {
                droppedToFf += pool;
            }
        }
    }

    for (const k of Object.keys(out)) {
        if (out[k] <= 0) delete out[k];
    }
    return { tgt: out, shipped, droppedToFf, usedBuffer };
}

/**
 * STRICT-обрезка распределения до ЦЕЛЫХ ПАЛЛЕТ так, чтобы Σ ≤ `maxUnits`.
 *
 * Применяется, когда ФФ-сток не покрывает паллет-консолидированный план: вместо
 * поштучной обрезки (которая разрушила бы цело-паллетный инвариант, как `trimTgt`
 * в матрице сборки) снимаем ЦЕЛЫЕ паллеты со складов с наименьшим qty первыми —
 * направление либо отгружает целые паллеты, либо ничего (дробная паллета остаётся
 * на ФФ). Склады, для которых паллета не считается (`palletUnitsForWh` ≤ 0/NaN —
 * напр. крупногабарит → `boxesPerPallet=null`), не трогаем: их объём уже стоит
 * целыми паллетами после консолидации, либо обрезать его паллетами нельзя.
 *
 * Чистая функция — полностью юнит-тестируется.
 *
 * @param tgt               wh → шт (после паллет-консолидации, кратно паллете).
 * @param maxUnits          верхний предел Σ (сколько ФФ реально может отгрузить).
 * @param palletUnitsForWh  wh → штук в одной паллете на этом складе (или null).
 */
export function trimToWholePallets(
    tgt: Record<string, number>,
    maxUnits: number,
    palletUnitsForWh: (wh: string) => number | null,
): Record<string, number> {
    const out: Record<string, number> = { ...tgt };
    let cur = Object.values(out).reduce((s, v) => s + v, 0);
    const cap = Math.max(0, Math.floor(maxUnits));
    while (cur > cap) {
        // Склад с наименьшим qty, у которого есть целая паллета для снятия.
        let pick: string | null = null;
        let pickQty = Infinity;
        let pickPu = 0;
        for (const wh of Object.keys(out)) {
            const pu = palletUnitsForWh(wh);
            if (!pu || pu <= 0 || out[wh] < pu) continue;
            if (out[wh] < pickQty) {
                pick = wh;
                pickQty = out[wh];
                pickPu = pu;
            }
        }
        if (pick === null) break; // нечего снять целыми паллетами
        out[pick] -= pickPu;
        cur -= pickPu;
        if (out[pick] <= 0) delete out[pick];
    }
    for (const k of Object.keys(out)) {
        if (out[k] <= 0) delete out[k];
    }
    return out;
}

export interface SnapWholeResult {
    /** key → оставленные units (целые паллеты по суммарному footprint). */
    kept: Record<string, number>;
    /** key → снятые units (неполный хвост — остаются на ФФ). */
    dropped: Record<string, number>;
}

/**
 * Срез коробов ОДНОГО склада-цели до ЦЕЛЫХ паллет (units-footprint), СТРОГО.
 *
 * Footprint (в паллетах) смешанной поставки = Σ units_i / units_на_паллету_i (короба
 * любых артикулов на одной паллете, как у WB). Оставляем `floor(footprint)` целых
 * паллет; неполный хвост (даже 80%) снимается на ФФ. Снимаем РОВНО хвост: убираем
 * `ceil(excess × upp)` штук с наименее объёмного палетизируемого SKU, не «проскакивая»
 * границу паллеты целыми коробами (в отличие от `shaveToWhole`, который снимает целыми
 * коробами и может уйти ниже целой паллеты, оставив неполную).
 *
 * Для ОДНОГО SKU footprint садится РОВНО на целое (`units mod upp` — целое). Для
 * смешанной поставки с РАЗНЫМ upp точное целое в штучной модели не всегда достижимо —
 * footprint садится в пределах ОДНОЙ штуки от целого (≤ keep). Чтобы это не ломало:
 *   • дисплей (`ceil(fill−eps)`) показывает РОВНО `keep` (хвост-«сливер» < штуки округлится);
 *   • повторный клик ИДЕМПОТЕНТЕН — `keep` считается с допуском дискретизации `tol`
 *     (=одна штука самого «крупного» SKU), поэтому город, уже севший на keep−сливер,
 *     распознаётся целым и не режется снова (иначе floor «съел» бы ещё паллету).
 *
 * keep==0 (поставка < целой паллеты в пределах tol) → все палетизируемые units снимаются
 * (направление исчезает, если непалетизируемых нет). Линии без `upp` (нет габаритов/
 * кратности — `uppOf` вернул null/≤0) НЕ режутся: паллету по ним не посчитать, объём
 * остаётся. Чистая функция — полностью юнит-тестируется.
 *
 * @param km    key → units (целые короба + возможный россыпь-хвост) одного склада-цели.
 * @param uppOf key → штук в полной паллете SKU (или null — не палетизируется).
 * @param boxOf key → кратность короба (шт/короб) или null. Если задана и >1, срез идёт
 *              ТОЛЬКО целыми коробами + исходной россыпью линии целиком: упаковка не
 *              создаёт НОВОЙ некратности (инцидент Екб: хвост 14 при ppb=22 резался
 *              поштучно на 3/11, после чего добивка до короба становилась невозможна).
 *              Допуск tol расширяется до доли короба — иначе footprint, севший
 *              «целое + сливер до короба», на повторном прогоне резался бы снова.
 */
export function snapToWholePallets(
    km: Record<string, number>,
    uppOf: (key: string) => number | null,
    boxOf?: (key: string) => number | null,
): SnapWholeResult {
    const EPS = 1e-9;
    const kept: Record<string, number> = {};
    const dropped: Record<string, number> = {};
    for (const [k, u] of Object.entries(km)) if (u > 0) kept[k] = u;

    // Гранула среза линии: короб (если кратность задана) или штука.
    const chunkOf = (k: string): number => {
        const b = boxOf?.(k);
        return b && b > 1 ? b : 1;
    };

    let fill = 0;
    let tol = 0; // допуск дискретизации = одна ГРАНУЛА «крупнейшего» SKU (короб или штука)
    for (const [k, u] of Object.entries(kept)) {
        const upp = uppOf(k);
        if (upp && upp > 0) { fill += u / upp; tol = Math.max(tol, chunkOf(k) / upp); }
    }
    if (tol <= 0) return { kept, dropped }; // нет палетизируемых линий — не режем
    tol = Math.min(tol, 0.5); // предохранитель от вырожденного upp (≤2 гранулы/паллету)

    // keep = floor(fill + tol) ТОЛЬКО в легаси-ветке (штучный tol ≤ 1 шт — сливер).
    // В короб-гранулярной tol НЕ поднимает keep: гранула-короб «промоутила» бы
    // недобранную до паллеты поставку в «целую» (при bpp=2 — полупустую), а в связке
    // с кросс-ФФ добором фазы-A давала PUT-шторм (адверсарное ревью: 34 PUT подряд,
    // весь свободный ФФ высасывался в предбронь). Допуск здесь работает только на
    // «уже целое» СВЕРХУ: срез-цикл не снимает гранулу, ныряющую ниже keep, поэтому
    // остаточный сливер ≤ гранулы over keep — и повторный прогон его не трогает.
    const keep = boxOf ? Math.floor(fill + EPS) : Math.floor(fill + tol);
    // keep < 1: вся смешанная поставка < целой паллеты → все палетизируемые units на ФФ.
    if (keep < 1) {
        for (const k of Object.keys(kept)) {
            const upp = uppOf(k);
            if (upp != null && upp > 0) { dropped[k] = (dropped[k] || 0) + kept[k]; delete kept[k]; }
        }
        return { kept, dropped };
    }
    const excess = fill - keep;

    if (!boxOf) {
        // ЛЕГАСИ (кратность не передана): поштучный floor-срез, строго не ниже keep.
        // keep ≥ 1 и хвост в пределах допуска → уже целое (сливер) — не трогаем.
        if (excess <= tol) return { kept, dropped };
        let rem = excess;
        const keys = Object.keys(kept)
            .filter((k) => { const u = uppOf(k); return u != null && u > 0; })
            .sort((a, b) => kept[a] - kept[b]);
        for (const k of keys) {
            if (rem <= EPS) break;
            const upp = uppOf(k)!;
            const rm = Math.min(kept[k], Math.floor(rem * upp + EPS));
            if (rm > 0) {
                kept[k] -= rm;
                dropped[k] = (dropped[k] || 0) + rm;
                rem -= rm / upp;
                if (kept[k] <= 0) delete kept[k];
            }
        }
        return { kept, dropped };
    }

    // КОРОБ-ГРАНУЛЯРНЫЙ срез: излишек снимается ГРАНУЛАМИ (сперва суб-коробочная
    // россыпь линии целиком — kept становится кратным, затем целые коробы), СТРОГО
    // НЕ НИЖЕ keep; гранула, ныряющая ниже keep, пропускается (у следующей линии
    // гранула может быть мельче). Инвариант: kept ≡ 0 либо ≡ исходной россыпи
    // (mod ppb) — новой некратности упаковка не создаёт.
    // Фикс-поинт за ОДИН прогон: после цикла остаток излишка МЕНЬШЕ любой доступной
    // гранулы оставшихся линий → повторный прогон не сможет снять ничего (kept не
    // меняется), а keep стабилен (residual < tol, residual + tol < 1). Проверяется
    // fuzz-тестом snapWholeBoxes.
    if (excess <= EPS) return { kept, dropped };
    let fp = fill;
    const keys = Object.keys(kept)
        .filter((k) => { const u = uppOf(k); return u != null && u > 0; })
        .sort((a, b) => kept[a] - kept[b]);
    outer: for (const k of keys) {
        if (fp <= keep + EPS) break;
        const upp = uppOf(k)!;
        const chunk = chunkOf(k);
        while ((kept[k] || 0) > 0) {
            if (fp <= keep + EPS) break outer;
            const loose = chunk > 1 ? kept[k] % chunk : 0;
            const take = loose > 0 ? loose : Math.min(kept[k], chunk);
            if (fp - take / upp < keep - EPS) break; // гранула ныряет ниже keep — линию пропускаем
            kept[k] -= take;
            dropped[k] = (dropped[k] || 0) + take;
            fp -= take / upp;
            if (kept[k] <= 0) { delete kept[k]; break; }
        }
    }
    return { kept, dropped };
}

export interface MixedPalletInput {
    /** wh → (key=nm_id → units). units кратно ppb (целые короба). */
    byWhKey: Record<string, Record<string, number>>;
    /** Приоритетный склад округа (крупнейший по суммарным units) — туда крошки. */
    anchorWh: string;
    /** key → кратность короба (шт/короб). */
    ppbOf: (key: string) => number;
    /** key,wh → шт в ПОЛНОЙ паллете этого SKU на этом складе (boxesPerPallet×ppb)
     *  или null (не палетизируется — крупногабарит). */
    palletUnitsOf: (key: string, wh: string) => number | null;
    /** Можно ли свезти крошку SKU `key` на склад `wh` (приёмка короба). Если SKU не
     *  box-приёмный на anchor — его крошка НЕ едет туда, а роняется на ФФ. По умолчанию
     *  всегда true (приёмка не ограничивает). */
    canAnchor?: (key: string, wh: string) => boolean;
    /** Порог заполненности (0..1) для ФИНАЛЬНОЙ неполной паллеты приоритетного
     *  склада. Если задан — последняя неполная паллета anchor ЕДЕТ, когда заполнена
     *  ≥ `minAnchorFill` (частичная отгрузка), иначе её остаток роняется на ФФ.
     *  Не задан (по умолчанию) — строгое поведение: любой остаток < паллеты на ФФ. */
    minAnchorFill?: number;
}

export interface MixedPalletOutput {
    /** wh → (key → units) после консолидации (целые СМЕШАННЫЕ паллеты). */
    byWhKey: Record<string, Record<string, number>>;
    /** key → units, упавшие на ФФ (финальный объединённый остаток < паллеты). */
    droppedToFf: Record<string, number>;
}

/**
 * Кросс-SKU консолидация КОРОБОВ одного округа в целые СМЕШАННЫЕ паллеты.
 *
 * В отличие от [[consolidateDistrictPallets]] (одна паллета = один артикул), здесь
 * паллета набивается коробами ЛЮБЫХ артикулов — для типа «короб» WB это разрешает.
 * «Заполненность» паллеты = Σ (units / units_на_паллету_этого_SKU): каждый короб
 * занимает свою долю по геометрии. Склады округа держат свои (приблизительно)
 * целые смешанные паллеты; объединённые крошки (< паллеты) свозятся на приоритетный
 * склад округа и там собираются в целые паллеты; финальный объединённый остаток <
 * паллеты роняется на ФФ (строго, без добивки).
 *
 * Дискретность: короба разных размеров не всегда складываются ровно в целую
 * паллету — последняя смешанная паллета может быть чуть неполной (< 1 короб). Это
 * неизбежно для смешанных паллет; держим максимально близко к целому, снимая
 * крошки коробами с наименее объёмного артикула первым.
 *
 * Чистая функция (без I/O) — полностью юнит-тестируется. НЕ учитывает ФФ-сток:
 * caller обязан подать units, уже ограниченные тем, что ФФ реально соберёт.
 */
export function consolidateMixedDistrictPallets(input: MixedPalletInput): MixedPalletOutput {
    const { anchorWh, ppbOf, palletUnitsOf } = input;
    const canAnchor = input.canAnchor ?? (() => true);
    const EPS = 1e-9;
    const byWhKey: Record<string, Record<string, number>> = {};
    for (const [wh, km] of Object.entries(input.byWhKey)) byWhKey[wh] = { ...km };
    const droppedToFf: Record<string, number> = {};

    // Заполненность склада (в паллетах) по палетизируемым артикулам.
    const footprintAt = (wh: string, km: Record<string, number>): number => {
        let f = 0;
        for (const [key, units] of Object.entries(km)) {
            const pu = palletUnitsOf(key, wh);
            if (pu && pu > 0) f += units / pu;
        }
        return f;
    };
    // Снять «крошку» с keymap до целого числа паллет `keep`: убираем целые короба
    // с наименее объёмного артикула первым; `onRemoved(key, units)` — куда деть.
    const shaveToWhole = (
        wh: string, km: Record<string, number>, onRemoved: (key: string, units: number) => void,
    ): void => {
        let fp = footprintAt(wh, km);
        const keep = Math.floor(fp + EPS);
        if (fp <= keep + EPS) return;
        const keys = Object.keys(km)
            .filter(k => { const pu = palletUnitsOf(k, wh); return pu != null && pu > 0; })
            .sort((a, b) => km[a] - km[b]);
        for (const key of keys) {
            const pu = palletUnitsOf(key, wh)!;
            const ppb = ppbOf(key);
            // Снимаем целыми коробами; последний под-коробочный остаток (россыпь <ppb)
            // тоже снимаем целиком — иначе он застрял бы на складе крошечной паллетой.
            while (fp > keep + EPS && km[key] > 0) {
                const take = km[key] >= ppb ? ppb : km[key];
                km[key] -= take;
                fp -= take / pu;
                onRemoved(key, take);
                if (km[key] <= 0) { delete km[key]; break; }
            }
            if (fp <= keep + EPS) break;
        }
    };

    if (!byWhKey[anchorWh]) byWhKey[anchorWh] = {};
    const anchorMap = byWhKey[anchorWh];

    // 1. Сателлиты: держат свои целые (смешанные) паллеты, крошку свозят на anchor —
    //    но только если SKU box-приёмный на anchor; иначе крошка роняется на ФФ
    //    (нельзя слать туда, где склад не принимает короб этого артикула).
    for (const wh of Object.keys(byWhKey)) {
        if (wh === anchorWh) continue;
        shaveToWhole(wh, byWhKey[wh], (key, units) => {
            if (canAnchor(key, anchorWh)) anchorMap[key] = (anchorMap[key] || 0) + units;
            else droppedToFf[key] = (droppedToFf[key] || 0) + units;
        });
    }
    // 2. Anchor: целые смешанные паллеты; финальный остаток < паллеты.
    //    Если задан minAnchorFill и последняя неполная паллета заполнена ≥ порога —
    //    она ЕДЕТ (частичная отгрузка), иначе остаток роняется на ФФ (строго).
    const anchorFp = footprintAt(anchorWh, anchorMap);
    const anchorFrac = anchorFp - Math.floor(anchorFp);
    const keepAnchorPartial =
        input.minAnchorFill != null && anchorFrac > EPS && anchorFrac >= input.minAnchorFill - EPS;
    if (!keepAnchorPartial) {
        shaveToWhole(anchorWh, anchorMap, (key, units) => {
            droppedToFf[key] = (droppedToFf[key] || 0) + units;
        });
    }

    for (const wh of Object.keys(byWhKey)) {
        for (const k of Object.keys(byWhKey[wh])) if (byWhKey[wh][k] <= 0) delete byWhKey[wh][k];
        if (Object.keys(byWhKey[wh]).length === 0) delete byWhKey[wh];
    }
    return { byWhKey, droppedToFf };
}

export interface PackMonoResult {
    /** key → units в ГОТОВЫХ паллетах (100% ИЛИ полный 3-арт слот). */
    kept: Record<string, number>;
    /** key → units недобранных паллет (<3 арт и <100%) — в предбронь/дозабор. */
    dropped: Record<string, number>;
    /** Готовые паллеты (bins) — раскладка строится на них (показ = авторитет). */
    pallets: { key: string; units: number }[][];
}

/**
 * Упаковка МОНО-распределения одного пункта назначения (ФФ→WB или anchor округа) в
 * ЦЕЛЫЕ паллеты с лимитом ≤`maxArticles` артикулов на паллету — правило WB «на
 * монопаллете максимум 3 артикула».
 *
 * Отгружаются ТОЛЬКО ЦЕЛЫЕ паллеты (строго). Две фазы:
 *  1. Каждый артикул — свои ОДНОТОВАРНЫЕ целые паллеты (`floor(units/upp)`).
 *  2. Под-паллетные хвосты собираются в ОБЩИЕ паллеты ≤`maxArticles` артикулов: берём
 *     `maxArticles` крупнейших; если их footprint ≥1 — наполняем целую (до 1.0) и
 *     повторяем. Как только даже они не набирают целой — остаток → `dropped` (предбронь).
 *
 * Возвращает `pallets` — сформированные целые паллеты (bins) — ЕДИНЫЙ АВТОРИТЕТ и для
 * трима (kept/dropped), и для показа (`buildPalletManifest`/`palletsForLines('mono')`),
 * поэтому «раскладка = то, что реально уедет» (нет фантомных неполных паллет).
 *
 * `uppOf(key)` — штук в полной паллете SKU (`boxesPerPallet × ppb`) или null (нет
 * габаритов) → ключ НЕ палетизируем, проходит в `kept` как есть. Добор последнего
 * артикула через `ceil` (footprint ≥1.0) — гарантия ИДЕМПОТЕНТНОСТИ (повторный прогон
 * по `kept` стабилен). `Σ(kept)+Σ(dropped)==Σ(вход)`. Чистая функция.
 */
export function packMonoPallets(
    km: Record<string, number>,
    uppOf: (key: string) => number | null,
    maxArticles = 3,
    boxOf?: (key: string) => number | null,
): PackMonoResult {
    const EPS = 1e-9;
    const kept: Record<string, number> = {};
    const dropped: Record<string, number> = {};
    const pallets: { key: string; units: number }[][] = []; // готовые (целые) паллеты — bins
    const crumbs: Record<string, number> = {};
    const uppCache: Record<string, number | null> = {};
    const upp = (k: string): number | null => (k in uppCache ? uppCache[k] : (uppCache[k] = uppOf(k)));
    // Размер короба ключа (для добора ЦЕЛЫМИ коробами); 0/нет → добор поштучно.
    const box = (k: string): number => { const b = boxOf?.(k); return b && b > 0 ? Math.floor(b) : 0; };

    // Фаза 1: однотоварные ЦЕЛЫЕ паллеты + сбор под-паллетных хвостов.
    for (const [k, raw] of Object.entries(km)) {
        const units = Math.max(0, Math.floor(raw || 0));
        if (units <= 0) continue;
        const u = upp(k);
        if (u == null || u <= 0) { kept[k] = (kept[k] || 0) + units; continue; } // не палетизируем
        const wholePallets = Math.floor(units / u);
        const whole = wholePallets * u;
        if (whole > 0) {
            kept[k] = (kept[k] || 0) + whole;
            for (let p = 0; p < wholePallets; p++) pallets.push([{ key: k, units: u }]);
        }
        const crumb = units - whole;
        if (crumb > 0) crumbs[k] = crumb;
    }

    // Фаза 2: хвосты → ОБЩИЕ целые паллеты ≤maxArticles. Только когда top-maxArticles
    // хвостов набирают ≥1 паллеты — иначе остаток в предбронь («только целые», строго).
    // Добор последнего артикула ВВЕРХ (ceil) — гарантия footprint ≥1.0 → ИДЕМПОТЕНТНОСТЬ
    // (иначе паллета сядет на <1.0, объявится «целой», повторный прогон уронит её хвост).
    const fpOf = (k: string): number => { const u = upp(k); return u && u > 0 ? crumbs[k] / u : 0; };
    for (;;) {
        const keys = Object.keys(crumbs).filter((k) => crumbs[k] > 0);
        if (keys.length === 0) break;
        keys.sort((a, b) => fpOf(b) - fpOf(a) || (a < b ? -1 : a > b ? 1 : 0));
        const top = keys.slice(0, maxArticles);
        const topFp = top.reduce((s, k) => s + fpOf(k), 0);
        if (topFp < 1 - EPS) {
            // Даже maxArticles крупнейших хвостов не набирают целой паллеты → на ФФ (предбронь).
            for (const k of keys) { dropped[k] = (dropped[k] || 0) + crumbs[k]; delete crumbs[k]; }
            break;
        }
        let need = 1;
        const bin: { key: string; units: number }[] = [];
        for (const k of top) {
            if (need <= EPS) break;
            const u = upp(k)!;
            const avFp = crumbs[k] / u;
            let use: number;
            if (avFp <= need + EPS) {
                use = crumbs[k]; // весь хвост влезает
            } else {
                let rawUse = Math.max(1, Math.ceil(need * u - EPS));
                const b = box(k);
                if (b > 0) rawUse = Math.ceil(rawUse / b) * b; // добор ЦЕЛЫМИ коробами (нет россыпи)
                use = Math.min(crumbs[k], rawUse);
            }
            kept[k] = (kept[k] || 0) + use;
            bin.push({ key: k, units: use });
            crumbs[k] -= use;
            need -= use / u;
            if (crumbs[k] <= 0) delete crumbs[k];
        }
        if (bin.length) pallets.push(bin);
    }
    return { kept, dropped, pallets };
}

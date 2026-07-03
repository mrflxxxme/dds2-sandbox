/**
 * Строгая палет-консолидация распределения сборки (поток «Распределение → Предпросмотр»).
 *
 * Берёт строки черновика (nm × src{ФФ} × tgt{город}) и для КАЖДОГО склада-источника,
 * внутри КАЖДОГО федерального округа (ЦФО и т.п.) собирает короба разных артикулов в
 * целые СМЕШАННЫЕ паллеты ([[consolidateMixedDistrictPallets]]):
 *   • каждый город держит свои целые паллеты;
 *   • крошки (< паллеты) городов одного округа свозятся на приоритетный склад округа;
 *   • финальная неполная паллета приоритетного склада ЕДЕТ, если заполнена ≥ `minFill`
 *     (по умолчанию 0.2 = 20%), иначе её остаток остаётся на ФФ (отказ от сборки).
 *
 * БЕЗ перезавоза: товар сверх потребности города не досыпается — только перераспределение
 * уже посчитанных штук между городами одного округа + отказ от хвостов < порога.
 *
 * Моно/сейф-строки (package_type ≠ BOX) НЕ трогаются (по-SKU паллеты). Округ 'unknown'
 * (склад без district_key) — оставляем как есть.
 *
 * НЕсбалансированные строки (Σsrc≠Σtgt — пользователь правил матрицу руками, строка
 * рендерится «!») пропускаются нетронутыми: `allocatePairs` раскладывает ровно Σsrc и
 * пересборка форсит баланс к Σsrc на ОБЕИХ сторонах, что молча переписало бы отгрузку.
 * Пустые строки (Σ=0, «обнулённая позиция») тоже проходят как есть — чтобы не исчезла
 * строка матрицы.
 *
 * Консолидация идёт ПО ОКРУГАМ на стороне tgt (городов), БЕЗ привязки к ФФ-источнику —
 * как в WarehouseNeedView (`okrugBoxConsolidated`): смешанная паллета держит короба
 * любых артикулов независимо от того, с какого ФФ они приедут (ФФ↔город решает commit).
 * НЕ раскладываем строку через `allocatePairs`: он сохраняет лишь ОБЩУЮ сумму, а не
 * по-складовые маргиналы — для строки с ≥2 ФФ-источниками он сдвигает суммы по городам
 * на ±1 и «ломает» уже-целую раскладку. Вместо этого группируем сразу `r.tgt`.
 *
 * Баланс Σsrc==Σtgt держится by construction: tgt пересобирается из финальных аллокаций
 * консолидации, а src КАЖДОЙ строки урезается (`carveSrc`) ровно до новой Σtgt (= старая
 * Σtgt − снятые на ФФ штуки); снятые уходят с обеих сторон. Чистая функция.
 *
 * ПРЕДУСЛОВИЕ: ≤1 короб-строка на (nm_id, barcode) (caller дедупит — distribute
 * через `dedupeRows`). Идентичность строки — (nm_id, barcode), НЕ голый nm_id:
 * одна WB-карточка несёт несколько баркодов (размеры), карточки без article_wb —
 * все nm_id=0; схлоп по nm_id потерял бы/смешал бы разные физические товары.
 * Геометрия (ppb/box_size) — по nm_id (общая у баркодов одной карточки).
 */
import type { AssemblyDraftRow } from '@/types/api';
import { consolidateMixedDistrictPallets, effectiveBoxesPerPallet, maxPalletHeightCm } from './boxPallet';
import { carveSrcToTarget } from './assemblyCarve';

export interface ConsolidatePalletsOpts {
    /** nm → кратность короба (шт/короб). */
    ppbOf: (nm: number) => number | null | undefined;
    /** nm → размер коробки «ДxШxВ». */
    boxSizeOf: (nm: number) => string | null | undefined;
    /** Имя WB-склада → ключ округа ('unknown' / '' → не консолидировать). */
    districtOf: (wb: string) => string;
    /** Ручной «коробов на паллету» по канон-размеру (override геометрии). */
    overrides?: Record<string, number>;
    /** Порог заполненности финальной паллеты приоритетного склада (0..1). По умолчанию
     *  0.2 (частичная паллета ≥20% едет). `null` — СТРОГО: любой остаток < паллеты
     *  остаётся на ФФ (режим инварианта «только целые паллеты»). */
    minFill?: number | null;
    /**
     * Можно ли свезти крошку SKU `nm` на приоритетный склад `wb` (приёмка короба).
     * Если SKU не box-приёмный на anchor — его крошка НЕ едет туда, а роняется на ФФ.
     *
     * НЕ ЗАДАН (default) — крошка всегда едет на anchor. distribute её не передаёт
     * сознательно: подтягивать live acceptance-map — отдельная задача.
     * ВНИМАНИЕ, это НЕ безрисково: anchor выбирается по СУММАРНОМУ (кросс-SKU) объёму
     * округа, а приёмка короба — per-(SKU, склад). Крошка артикула A может уехать на
     * anchor, где у A нулевое присутствие и `can_box` может быть false → WB-отказ
     * приёмки. Приёмочный гейт в WarehouseNeedView (acceptanceMap) — ОПЦИОНАЛЬНЫЙ шаг
     * (после ручного «Проверить приёмку»), так что «ячейки уже прошли приёмку» НЕ
     * гарантировано. Опция оставлена точкой расширения: WNV-подобный caller передаёт
     * `canBoxAt`, чтобы закрыть этот риск.
     */
    canAnchorOf?: (nm: number, wb: string) => boolean;
}

export interface ConsolidatePalletsResult {
    /** Новые строки черновика (моно/сейф без изменений + пересобранные короба). */
    rows: AssemblyDraftRow[];
    /** Штук, снятых с отгрузки (хвосты < порога) — остаются на ФФ. */
    droppedUnits: number;
    /** Направлений (ФФ×город), опустевших после слияния крошек на приоритетный склад. */
    mergedDirections: number;
    /** Округов (ФФ×округ), реально затронутых консолидацией. */
    affectedDistricts: number;
    /** Изменилось ли распределение (для guard'а «нечего консолидировать»). */
    changed: boolean;
}

type WhKeyMap = Record<string, Record<string, number>>; // wb → nm(str) → units

/** Σ units в keymap (города / по складу). */
const sumKm = (km: Record<string, number>): number =>
    Object.values(km).reduce((s, v) => s + (v || 0), 0);

/** Идентичны ли два распределения tgt (по ненулевым складам). Структурное сравнение —
 *  не строковая подпись: имя склада может содержать `:`/`,` и дать коллизию. */
const sameDist = (a: Record<string, number>, b: Record<string, number>): boolean => {
    const ak = Object.keys(a).filter((k) => a[k] > 0);
    const bk = Object.keys(b).filter((k) => b[k] > 0);
    if (ak.length !== bk.length) return false;
    for (const k of ak) if (a[k] !== b[k]) return false;
    return true;
};

// Урезание src до новой Σtgt — общий помощник `carveSrcToTarget` (см. assemblyCarve.ts).

export function consolidatePalletsInDraftRows(
    rows: AssemblyDraftRow[],
    opts: ConsolidatePalletsOpts,
): ConsolidatePalletsResult {
    // null → строгий режим (без частичной якорь-паллеты); undefined → дефолт 0.2.
    const minFill = opts.minFill === null ? null : (opts.minFill ?? 0.2);
    const otherRows = rows.filter((r) => (r.package_type || 'BOX') !== 'BOX');

    // Идентичность строки — (nm_id, barcode). Геометрия — по nm_id (см. docstring).
    const rowKey = (r: AssemblyDraftRow) => `${r.nm_id}::${r.barcode || ''}`;
    const nmOfKey = (k: string) => Number(k.slice(0, k.indexOf('::')));

    // Короб-строки делим на консолидируемые (баланс Σsrc==Σtgt > 0) и passthrough:
    // НЕсбалансированные (ручная правка) и пустые (Σ=0) проходят как есть — их нельзя
    // перебалансировать молча, см. docstring.
    const boxRows: AssemblyDraftRow[] = [];
    const passthroughBoxRows: AssemblyDraftRow[] = [];
    for (const r of rows) {
        if ((r.package_type || 'BOX') !== 'BOX') continue;
        const s = sumKm(r.src);
        const t = sumKm(r.tgt);
        if (s === t && s > 0) boxRows.push(r);
        else passthroughBoxRows.push(r);
    }

    // (nm,barcode) → исходная строка (для баркода/vendor_code/package_type при пересборке).
    const meta = new Map<string, AssemblyDraftRow>();
    for (const r of boxRows) { const k = rowKey(r); if (!meta.has(k)) meta.set(k, r); }

    // Группируем tgt НАПРЯМУ по округу (без ФФ): byWh = wb → (nm::barcode) → units.
    const groups = new Map<string, { district: string; byWh: WhKeyMap }>();
    for (const r of boxRows) {
        const rk = rowKey(r);
        for (const [wb, units] of Object.entries(r.tgt)) {
            if (units <= 0) continue;
            const district = opts.districtOf(wb) || 'unknown';
            let g = groups.get(district);
            if (!g) { g = { district, byWh: {} }; groups.set(district, g); }
            const km = (g.byWh[wb] ||= {});
            km[rk] = (km[rk] || 0) + units;
        }
    }

    // Новый tgt по (nm,barcode) (после консолидации) + снятые на ФФ штуки.
    const nmTgt = new Map<string, Record<string, number>>();
    const addTgt = (key: string, wb: string, units: number) => {
        if (units <= 0) return;
        const t = nmTgt.get(key) || {}; t[wb] = (t[wb] || 0) + units; nmTgt.set(key, t);
    };

    let droppedUnits = 0;
    let mergedDirections = 0;
    let affectedDistricts = 0;

    for (const g of groups.values()) {
        if (g.district === 'unknown') {
            for (const [wb, km] of Object.entries(g.byWh))
                for (const [rk, u] of Object.entries(km)) addTgt(rk, wb, u);
            continue;
        }
        // Приоритетный склад округа — с наибольшими суммарными units (тай — по имени).
        let anchor = ''; let best = -1;
        for (const [wb, km] of Object.entries(g.byWh)) {
            const tot = sumKm(km);
            if (tot > best || (tot === best && (anchor === '' || wb < anchor))) { best = tot; anchor = wb; }
        }
        const directionsBefore = Object.values(g.byWh).filter((km) => sumKm(km) > 0).length;

        const res = consolidateMixedDistrictPallets({
            byWhKey: g.byWh,
            anchorWh: anchor,
            ppbOf: (key) => opts.ppbOf(nmOfKey(key)) || 0,
            palletUnitsOf: (key, wh) => {
                const nm = nmOfKey(key);
                const k = opts.ppbOf(nm);
                const bpp = effectiveBoxesPerPallet(opts.boxSizeOf(nm), maxPalletHeightCm(wh), opts.overrides);
                return bpp != null && k && k > 0 ? bpp * k : null;
            },
            canAnchor: opts.canAnchorOf
                ? (key, wh) => opts.canAnchorOf!(nmOfKey(key), wh)
                : undefined,
            minAnchorFill: minFill ?? undefined, // null → строго (undefined в ядре = без частичной)
        });

        for (const [wb, km] of Object.entries(res.byWhKey))
            for (const [rk, u] of Object.entries(km)) addTgt(rk, wb, u);
        for (const u of Object.values(res.droppedToFf)) droppedUnits += u;

        const directionsAfter = Object.values(res.byWhKey).filter((km) => sumKm(km) > 0).length;
        if (directionsAfter < directionsBefore) mergedDirections += directionsBefore - directionsAfter;
        affectedDistricts += 1;
    }

    // Пересобираем короб-строки: tgt из финальных аллокаций, src урезаем до новой Σtgt
    // (= старая Σtgt − снятые штуки), баланс держится by construction. Полностью
    // отказанный SKU (Σtgt==0, весь хвост <порога) — строку выкидываем.
    const newBoxRows: AssemblyDraftRow[] = [];
    for (const [key, m] of meta) {
        const tgt = nmTgt.get(key) || {};
        const newTotal = sumKm(tgt);
        if (newTotal <= 0) continue;
        // m — исходная строка с верными nm_id и barcode; src урезаем до новой Σtgt.
        newBoxRows.push({ ...m, src: carveSrcToTarget(m.src, newTotal), tgt });
    }

    // `changed` — диффом пересобранного tgt (по nm) против ВХОДНОГО tgt консолидируемых
    // строк, а НЕ из side-effects (dropped/merged/rowcount). Консолидация может ровно
    // ПЕРЕЛОЖИТЬ штуки между городами округа (напр. 290/190 → 320/160), ничего не
    // дропнув и не опустошив направление — это валидное изменение, его нельзя терять.
    // passthrough-строки (моно/несбаланс/пустые) в дифф не входят — они не менялись.
    const origTgtByKey = new Map<string, Record<string, number>>();
    for (const r of boxRows) {
        const rk = rowKey(r);
        for (const [wb, q] of Object.entries(r.tgt)) {
            if (q > 0) {
                const agg = origTgtByKey.get(rk) ?? {};
                agg[wb] = (agg[wb] || 0) + q;
                origTgtByKey.set(rk, agg);
            }
        }
    }
    let changed = origTgtByKey.size !== nmTgt.size;
    if (!changed) {
        for (const [key, t] of nmTgt) {
            const o = origTgtByKey.get(key);
            if (!o || !sameDist(o, t)) { changed = true; break; }
        }
    }

    return {
        rows: [...otherRows, ...passthroughBoxRows, ...newBoxRows],
        droppedUnits,
        mergedDirections,
        affectedDistricts,
        changed,
    };
}

/**
 * Нормализатор черновика сборки к инварианту «целые коробы + целые паллеты» —
 * ЕДИНЫЙ конвейер (rows normalize → добор предброни → релиз некратных), который
 * страница прогоняет после мутаций и на каждом сохранении.
 *
 * Две области применения (параметр `only`):
 *  • `only === undefined` — ПОЛНЫЙ проход по всему черновику (загрузка, saveDraft,
 *    полное «Заполнить из потребности») — сеть безопасности инварианта.
 *  • `only` = набор `${pkg}::${wb}` — СУЖЕННЫЙ проход: нормализуем только тронутые
 *    направления, остальные порции строк/предброни возвращаем БАЙТ-В-БАЙТ. Пустой
 *    набор → no-op (вычитающая операция «На ФФ»/«Удалить» — сдаваемость проверять
 *    нечего). Так действие над одним направлением не переупаковывает соседние.
 *
 * Общий пул свободного ФФ (`ctx.freeByNm`) СТРОИТСЯ ВЫЗЫВАЮЩИМ от ПОЛНОГО черновика
 * (`available − занятое всеми направлениями`) — сужение области нормализации его не
 * искажает: headroom уже учитывает чужие направления, конкуренция за коробку честная.
 *
 * Чистая функция (кроме документированной мутации `ctx.freeByNm` шагами округления,
 * как и у `normalizeDraft`).
 */
import type { AssemblyDraftRow, PackageType } from '@/types/api';
import { normalizeDraft, type NormalizeDraftCtx } from './normalizeDraft';
import { topUpPrebookLooseBoxes, releaseUnfixableLooseBoxes } from './assemblyRoundBoxes';
import { allocatePairs } from './assemblyPreview';

export interface ScopedNormalizeResult {
    rows: AssemblyDraftRow[];
    prebook: AssemblyDraftRow[];
    changed: boolean;
    droppedUnits: number;
    /** Штук, ушедших в предбронь (недобор до целой паллеты). */
    droppedToPrebook: number;
    /** Штук, добитых до целого короба из свободного ФФ. */
    prebookFilledUp: number;
    /** Некратные остатки, возвращённые на ФФ без резерва. */
    releasedTotal: number;
}

/** Схлопнуть строки по (nm_id, barcode, упаковка, as_is), суммируя src/tgt поэлементно.
 *  Зеркало backend merge — предпросмотр = коммит. as_is в ключе: частичная «Оставить
 *  так» не суммируется с обычной строкой того же SKU (иначе флаг расползся бы на целые
 *  паллеты и normalizeDraft перестал бы их чинить). */
export function mergeDraftRows(rs: AssemblyDraftRow[]): AssemblyDraftRow[] {
    const m = new Map<string, AssemblyDraftRow>();
    for (const r of rs) {
        const k = `${r.nm_id}::${r.barcode}::${r.package_type || 'BOX'}::${r.as_is ? 1 : 0}`;
        let e = m.get(k);
        if (!e) {
            e = { nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: {}, tgt: {}, package_type: r.package_type || 'BOX' };
            if (r.as_is) e.as_is = true;
            m.set(k, e);
        }
        for (const [ff, q] of Object.entries(r.src)) e.src[ff] = (e.src[ff] || 0) + (q || 0);
        for (const [w2, q] of Object.entries(r.tgt)) e.tgt[w2] = (e.tgt[w2] || 0) + (q || 0);
    }
    return [...m.values()];
}

/** Ключ направления для набора `only`. */
export const directionKey = (pkg: PackageType | undefined, wb: string): string => `${pkg || 'BOX'}::${wb}`;

/** true, если строка касается хоть одного направления из `only`. */
function rowTouches(r: AssemblyDraftRow, only: Set<string>): boolean {
    const pkg = r.package_type || 'BOX';
    return Object.keys(r.tgt).some((wb) => only.has(directionKey(pkg, wb)));
}

/** Разрезать строку на touched-порцию (направления из `only`) и остаток (rest).
 *  rest = оригинал − touched поэлементно → touched+rest === оригинал (без потерь даже
 *  при дисбалансе). Возвращает null для пустой части. */
function carveRow(
    r: AssemblyDraftRow,
    only: Set<string>,
): { touched: AssemblyDraftRow | null; rest: AssemblyDraftRow | null } {
    const pkg = r.package_type || 'BOX';
    const tSrc: Record<string, number> = {};
    const tTgt: Record<string, number> = {};
    const rSrc: Record<string, number> = { ...r.src };
    const rTgt: Record<string, number> = { ...r.tgt };
    for (const [pairKey, q] of allocatePairs(r.src, r.tgt)) {
        if ((q || 0) <= 0) continue;
        const idx = pairKey.indexOf('::');
        const ff = pairKey.slice(0, idx);
        const wb = pairKey.slice(idx + 2);
        if (!only.has(directionKey(pkg, wb))) continue;
        tSrc[ff] = (tSrc[ff] || 0) + q;
        tTgt[wb] = (tTgt[wb] || 0) + q;
        rSrc[ff] = (rSrc[ff] || 0) - q;
        rTgt[wb] = (rTgt[wb] || 0) - q;
    }
    const clean = (o: Record<string, number>) => {
        for (const k of Object.keys(o)) if (o[k] <= 0) delete o[k];
        return o;
    };
    const mk = (src: Record<string, number>, tgt: Record<string, number>): AssemblyDraftRow | null => {
        clean(src);
        clean(tgt);
        if (Object.keys(tgt).length === 0) return null;
        const out: AssemblyDraftRow = { nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src, tgt, package_type: pkg };
        if (r.as_is) out.as_is = true;
        return out;
    };
    return { touched: mk(tSrc, tTgt), rest: mk(rSrc, rTgt) };
}

/** Полный проход конвейера по переданным rows/prebook (общий пул из ctx). */
function runFull(
    baseRows: AssemblyDraftRow[],
    basePrebook: AssemblyDraftRow[],
    ctx: NormalizeDraftCtx,
): ScopedNormalizeResult {
    const res = normalizeDraft(baseRows, ctx);
    // Срезанные до-паллетные хвосты → в предбронь (не теряем на ФФ и не «съедаем»
    // молча частичную паллету «Оставить так»). Сливаются с лежащей предбронью.
    const merged = res.dropped.length ? mergeDraftRows([...basePrebook, ...res.dropped]) : basePrebook;
    // Хвосты предброни добиваются до ЦЕЛОГО КОРОБА из остатка пула (строго с ФФ порции).
    const topUp = topUpPrebookLooseBoxes(merged, ctx.ppbOf, ctx.freeByNm ?? {}, ctx.ppbAt);
    // «Добить нечем» → не резервируем: россыпь без шанса на короб уходит на ФФ.
    const release = releaseUnfixableLooseBoxes(topUp.changed ? topUp.rows : merged, ctx.ppbOf, ctx.freeByNm ?? {}, ctx.ppbAt);
    const nextPrebook = release.changed ? release.rows : (topUp.changed ? topUp.rows : merged);
    return {
        rows: res.rows,
        prebook: nextPrebook,
        changed: res.changed || topUp.changed || release.changed,
        droppedUnits: res.droppedUnits,
        droppedToPrebook: Math.max(0, res.droppedUnits - res.releasedUnits),
        prebookFilledUp: topUp.filledUp,
        releasedTotal: res.releasedUnits + release.releasedUnits,
    };
}

/**
 * Нормализация черновика с опциональным сужением до тронутых направлений.
 * @param only набор `${pkg}::${wb}`; `undefined` — полный проход по всему черновику.
 */
export function scopedNormalizeDraft(
    baseRows: AssemblyDraftRow[],
    basePrebook: AssemblyDraftRow[],
    ctx: NormalizeDraftCtx,
    only?: Set<string>,
): ScopedNormalizeResult {
    if (only === undefined) return runFull(baseRows, basePrebook, ctx);

    // Разрезаем rows/prebook на touched-порции и passthrough (остаётся байт-в-байт).
    const touchedRows: AssemblyDraftRow[] = [];
    const passRows: AssemblyDraftRow[] = [];
    for (const r of baseRows) {
        if (!rowTouches(r, only)) { passRows.push(r); continue; }
        const { touched, rest } = carveRow(r, only);
        if (touched) touchedRows.push(touched);
        if (rest) passRows.push(rest);
    }
    const touchedPre: AssemblyDraftRow[] = [];
    const passPre: AssemblyDraftRow[] = [];
    for (const r of basePrebook) {
        if (!rowTouches(r, only)) { passPre.push(r); continue; }
        const { touched, rest } = carveRow(r, only);
        if (touched) touchedPre.push(touched);
        if (rest) passPre.push(rest);
    }

    // Ничего не тронуто (пустой `only` ИЛИ направления отсутствуют) → no-op.
    if (touchedRows.length === 0 && touchedPre.length === 0) {
        return { rows: baseRows, prebook: basePrebook, changed: false, droppedUnits: 0, droppedToPrebook: 0, prebookFilledUp: 0, releasedTotal: 0 };
    }

    const sub = runFull(mergeDraftRows(touchedRows), mergeDraftRows(touchedPre), ctx);
    return {
        rows: mergeDraftRows([...passRows, ...sub.rows]),
        prebook: mergeDraftRows([...passPre, ...sub.prebook]),
        changed: sub.changed,
        droppedUnits: sub.droppedUnits,
        droppedToPrebook: sub.droppedToPrebook,
        prebookFilledUp: sub.prebookFilledUp,
        releasedTotal: sub.releasedTotal,
    };
}

/**
 * Нормализатор черновика сборки к ИНВАРИАНТУ «целые коробы + целые паллеты».
 *
 * Единая чистая композиция, которую страница прогоняет после КАЖДОЙ мутации
 * распределения (добавление из потребности/баркода, перепроверка приёмки, удаление).
 * Делает раскладку самодостаточной: то, что лежит в черновике, СОВПАДАЕТ с тем, что
 * уедет в заявки — предпросмотр и коммит больше ничего не «дочищают» вручную.
 *
 *   1. Целые коробы (`roundDraftRowsToWholeBoxes`): неполный короб добивается из
 *      свободного ФФ (`freeByNm`), не хватает — срезается. Россыпи не остаётся.
 *   2. Целые паллеты НА КАЖДУЮ ОТГРУЗКУ ФФ→WB (та же `trimLinesToWholePallets`, что и
 *      коммит): КОРОБ — смешанная паллета (микс артикулов, правило 1); МОНО — паллета
 *      ≤3 артикула (правило 2). Под-паллетный хвост остаётся на ФФ (строго).
 *
 * Проверку лимитов приёмки WB (правило 3) делает CALL-SITE ДО нормализатора (сетевой
 * вызов, инкрементально по изменённым артикулам) — здесь только локальная геометрия.
 * Новинки cold-start без кратности (`isNewcomer`) — россыпь, не палетизируем.
 *
 * Баланс Σsrc==Σtgt держится by construction (строки пересобираются из сбалансированных
 * линий ФФ→WB). Идемпотентна: повторный прогон стабилен (важно — гоняется часто).
 * Чистая функция.
 */
import type { AssemblyDraftRow } from '@/types/api';
import { roundDraftRowsToWholeBoxes, roundMonoToWholeBoxes } from './assemblyRoundBoxes';
import { buildPreviewLines, trimLinesToWholePallets, type PreviewLine } from './assemblyPreview';
import { effectiveBoxesPerPallet, maxPalletHeightCm } from './boxPallet';

export interface NormalizeDraftCtx {
    /** nm → кратность короба (шт/короб). null/0 — россыпь (новинка без габаритов). */
    ppbOf: (nm: number) => number | null | undefined;
    /** nm → размер коробки «ДxШxВ». */
    boxSizeOf: (nm: number) => string | null | undefined;
    /** Ручной «коробов на паллету» по канон-размеру. */
    overrides?: Record<string, number>;
    /** nm новинки cold-start — россыпь, не палетизируем (исключение из инварианта). */
    isNewcomer: (nm: number) => boolean;
    /** Свободный ФФ per nm per ff для добивки неполного короба вверх (опц.). */
    freeByNm?: Record<number, Record<number, number>>;
}

export interface NormalizeDraftResult {
    /** Нормализованные строки черновика (целые коробы + целые паллеты). */
    rows: AssemblyDraftRow[];
    /** Штук, ушедших на ФФ при срезе до целых паллет. */
    droppedUnits: number;
    /** Изменилась ли раскладка относительно входа. */
    changed: boolean;
}

/** Канон-подпись распределения строк (для детекта изменения / идемпотентности). */
function signature(rows: AssemblyDraftRow[]): string {
    const parts: string[] = [];
    for (const r of rows) {
        const tgt = Object.entries(r.tgt)
            .filter(([, q]) => q > 0)
            .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
            .map(([wb, q]) => `${wb}:${q}`)
            .join(',');
        const src = Object.entries(r.src)
            .filter(([, q]) => q > 0)
            .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
            .map(([ff, q]) => `${ff}:${q}`)
            .join(',');
        if (tgt) parts.push(`${r.nm_id}|${r.barcode || ''}|${r.package_type || 'BOX'}|${src}|${tgt}`);
    }
    return parts.sort().join(';');
}

/** Свернуть сбалансированные линии (ФФ→WB→товар) обратно в строки черновика:
 *  src = Σ по ФФ-источникам, tgt = Σ по WB-складам. Σsrc==Σtgt by construction. */
function linesToRows(lines: PreviewLine[]): AssemblyDraftRow[] {
    const map = new Map<string, AssemblyDraftRow>();
    for (const l of lines) {
        if (l.qty <= 0) continue;
        const key = `${l.nmId}::${l.barcode}::${l.pkg}`;
        let r = map.get(key);
        if (!r) {
            r = { nm_id: l.nmId, barcode: l.barcode, vendor_code: l.vendor, src: {}, tgt: {}, package_type: l.pkg };
            map.set(key, r);
        }
        r.src[String(l.ffId)] = (r.src[String(l.ffId)] || 0) + l.qty;
        r.tgt[l.wbName] = (r.tgt[l.wbName] || 0) + l.qty;
    }
    return [...map.values()];
}

export function normalizeDraft(
    rows: AssemblyDraftRow[],
    ctx: NormalizeDraftCtx,
): NormalizeDraftResult {
    // freeByNm мутируется обоими шагами округления (короб расходует пул ФФ первым,
    // моно — что осталось), поэтому ОДИН объект на оба шага.
    const freeByNm = ctx.freeByNm ?? {};
    // 1. Целые коробы: BOX-строки (добор/срез) + МОНО (вариант A: добор короба из ФФ,
    //    иначе срез под-коробочного остатка на ФФ — моно едет только целыми коробами).
    const boxed = roundDraftRowsToWholeBoxes(rows, ctx.ppbOf, freeByNm, ctx.isNewcomer);
    const monoRows = roundMonoToWholeBoxes(boxed.rows, ctx.ppbOf, freeByNm, ctx.isNewcomer);

    // 2. Целые паллеты на каждую отгрузку ФФ→WB (короб смешанные, моно ≤3 АРТИКУЛА,
    //    добор ЦЕЛЫМИ коробами). Разворачиваем в линии, режем, сворачиваем обратно.
    const newcomerSet = new Set(monoRows.map((r) => r.nm_id).filter((nm) => ctx.isNewcomer(nm)));
    const uppOf = (nm: number, wb: string): number | null => {
        if (ctx.isNewcomer(nm)) return null;
        const ppb = ctx.ppbOf(nm);
        const bpp = effectiveBoxesPerPallet(ctx.boxSizeOf(nm), maxPalletHeightCm(wb), ctx.overrides);
        return bpp != null && ppb && ppb > 0 ? bpp * ppb : null;
    };
    const boxOf = (nm: number): number | null => (ctx.isNewcomer(nm) ? null : (ctx.ppbOf(nm) ?? null));
    const lines = buildPreviewLines(monoRows, newcomerSet);
    const trim = trimLinesToWholePallets(lines, uppOf, boxOf);
    const outRows = linesToRows(trim.kept);

    // changed: дешёвые счётчики первыми (короб добит/срезан, паллеты сняты) — если они
    // показали изменение, строковую подпись всего черновика НЕ строим. Подпись считаем
    // только когда счётчики «тихие», но lines↔rows мог пере-собрать раскладку (детект
    // re-pairing без дропа). Так в горячем пути нет двойной пере-сборки подписи.
    const counterChanged = boxed.changed > 0 || trim.droppedUnits > 0 || trim.removedSupplies > 0;
    const changed = counterChanged || signature(rows) !== signature(outRows);

    // droppedUnits = всё, что ушло на ФФ (box-округление моно + срез до целых паллет) =
    // Σвход − Σвыход. Точнее, чем только паллет-срез (моно-россыпь тоже на ФФ).
    const sumTgt = (rs: AssemblyDraftRow[]) => rs.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
    const droppedUnits = Math.max(0, sumTgt(rows) - sumTgt(outRows));

    return { rows: outRows, droppedUnits, changed };
}

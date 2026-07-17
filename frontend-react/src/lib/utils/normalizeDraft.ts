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
 * РОССЫПЬ ЗАПРЕЩЕНА ВСЕМ (канон 2026-07-08), исключения для новинок НЕТ: строка без
 * кратности (ни global, ни per-ФФ) в черновик не едет — уходит в dropped/предбронь.
 *
 * Баланс Σsrc==Σtgt держится by construction (строки пересобираются из сбалансированных
 * линий ФФ→WB). Идемпотентна: повторный прогон стабилен (важно — гоняется часто).
 * Чистая функция.
 */
import type { AssemblyDraftRow, PackageType } from '@/types/api';
import { roundDraftRowsToWholeBoxes, roundMonoToWholeBoxes } from './assemblyRoundBoxes';
import { buildPreviewLines, trimLinesToWholePallets, type PreviewLine } from './assemblyPreview';
import { effectiveBoxesPerPallet, maxPalletHeightCm } from './boxPallet';

export interface NormalizeDraftCtx {
    /** nm → кратность короба (шт/короб). null/0 — россыпь (новинка без габаритов). */
    ppbOf: (nm: number) => number | null | undefined;
    /** nm × ФФ → кратность короба ЭТОГО склада (может отличаться: 80х160_синий —
     *  22 на Хамзе, 30 на Газпроме). Фолбэк — ppbOf. Опциональна (тесты/легаси). */
    ppbAt?: (nm: number, ffId: number) => number | null | undefined;
    /** nm → размер коробки «ДxШxВ». */
    boxSizeOf: (nm: number) => string | null | undefined;
    /** Ручной «коробов на паллету» по канон-размеру. */
    overrides?: Record<string, number>;
    /** Свободный ФФ per nm per ff для добивки неполного короба вверх (опц.). */
    freeByNm?: Record<number, Record<number, number>>;
    /** Класс совместимости категорий (`lib/assembly/categoryCompat`): SKU разных
     *  классов не делят смешанную BOX-паллету. Не задан — прежний микс всех. */
    classOf?: (nm: number) => string;
}

export interface NormalizeDraftResult {
    /** Нормализованные строки черновика (целые коробы + целые паллеты). */
    rows: AssemblyDraftRow[];
    /** Штук, ушедших на ФФ при срезе до целых паллет. */
    droppedUnits: number;
    /** Изменилась ли раскладка относительно входа. */
    changed: boolean;
    /** Срезанные до-паллетные хвосты как строки (целые коробы, не собравшие паллету) —
     *  для предброни. Σtgt(dropped) ⊆ droppedUnits (без сырой россыпи box-округления). */
    dropped: AssemblyDraftRow[];
    /** Некратные остатки, снятые СТРОГИМ box-округлением в свободный ФФ (не едут и
     *  не резервируются — решение юзера). Входят в droppedUnits, но НЕ в dropped. */
    releasedUnits: number;
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
    // ВАЖНО: короб-округление применяется к ВСЕМ строкам, ВКЛЮЧАЯ `as_is` («Оставить
    // так»). `as_is` освобождает лишь от ПАЛЛЕТ-среза (юзер осознанно везёт неполную
    // паллету), но НЕ от короб-инварианта: НЕПОЛНЫЙ КОРОБ РОССЫПЬЮ НЕДОПУСТИМ — добираем
    // до целого короба из свободного ФФ, некратный остаток < короба уходит на ФФ (строго).
    // РОССЫПЬ ЗАПРЕЩЕНА ВСЕМ (канон 2026-07-08) — исключения для новинок больше нет.
    // Новинка С `ppb` и раньше ехала целыми коробами как обычный box-SKU; новинка БЕЗ
    // `ppb` теперь не едет: округлять нечем → паллет-срез снимет её в dropped/предбронь.
    const boxed = roundDraftRowsToWholeBoxes(rows, ctx.ppbOf, freeByNm, undefined, ctx.ppbAt);
    const monoRows = roundMonoToWholeBoxes(boxed.rows, ctx.ppbOf, freeByNm, undefined, ctx.ppbAt);
    // `as_is` (уже приведённые к ЦЕЛЫМ коробам выше) минуют ПАЛЛЕТ-срез ниже — юзер
    // осознанно везёт неполную паллету из ЦЕЛЫХ КОРОБОВ. Строка без известной кратности
    // (ни global, ни per-ФФ её источников) целых коробов не имеет → НЕ освобождается:
    // идёт в общий срез, где upp=null снимет её (россыпь не едет и через «Оставить так»).
    const hasAnyPpb = (r: AssemblyDraftRow): boolean => {
        if ((ctx.ppbOf(r.nm_id) ?? 0) > 0) return true;
        return Object.keys(r.src).some((ff) => (ctx.ppbAt?.(r.nm_id, Number(ff)) ?? 0) > 0);
    };
    const asIsSet = new Set(monoRows.filter((r) => r.as_is === true && hasAnyPpb(r)));
    const asIsRows = [...asIsSet];
    const workRows = asIsSet.size ? monoRows.filter((r) => !asIsSet.has(r)) : monoRows;

    // 2. Целые паллеты на каждую отгрузку ФФ→WB (короб смешанные, моно ≤3 АРТИКУЛА,
    //    добор ЦЕЛЫМИ коробами). Разворачиваем в линии, режем, сворачиваем обратно.
    // upp per-ФФ: вместимость паллеты = bpp × короб ЭТОГО ФФ. Глобальный min резал
    // физически целые паллеты мульти-кратных SKU и давал моно-паллеты, некратные
    // коробу ФФ (PUT-шторм, адверсарное ревью).
    const uppOf = (nm: number, wb: string, ffId?: number): number | null => {
        const pw = ffId != null ? ctx.ppbAt?.(nm, ffId) : null;
        const ppb = pw && pw > 0 ? pw : ctx.ppbOf(nm);
        const bpp = effectiveBoxesPerPallet(ctx.boxSizeOf(nm), maxPalletHeightCm(wb), ctx.overrides);
        return bpp != null && ppb && ppb > 0 ? bpp * ppb : null;
    };
    const boxOf = (nm: number, ffId?: number): number | null => {
        const pw = ffId != null ? ctx.ppbAt?.(nm, ffId) : null;
        return pw && pw > 0 ? pw : (ctx.ppbOf(nm) ?? null);
    };
    const lines = buildPreviewLines(workRows, new Set());
    const trim = trimLinesToWholePallets(lines, uppOf, boxOf, ctx.classOf);
    const outRows = linesToRows(trim.kept);
    const dropped = linesToRows(trim.droppedLines);

    // changed: дешёвые счётчики первыми (короб добит/срезан, паллеты сняты) — если они
    // показали изменение, строковую подпись всего черновика НЕ строим. Подпись считаем
    // только когда счётчики «тихие», но lines↔rows мог пере-собрать раскладку (детект
    // re-pairing без дропа). Так в горячем пути нет двойной пере-сборки подписи.
    const finalRows = [...outRows, ...asIsRows];
    const counterChanged = boxed.changed > 0 || trim.droppedUnits > 0 || trim.removedSupplies > 0;
    // Сравниваем ВЕСЬ вход с ВЕСЬ выходом: as_is теперь тоже короб-округляется (шаг 1).
    const changed = counterChanged || signature(rows) !== signature(finalRows);

    // droppedUnits = всё, что ушло на ФФ (box-округление + срез до целых паллет) =
    // Σвход − Σвыход. as_is-строки теперь тоже участвуют (их под-коробочный остаток на ФФ).
    const sumTgt = (rs: AssemblyDraftRow[]) => rs.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
    const droppedUnits = Math.max(0, sumTgt(rows) - sumTgt(finalRows));

    return { rows: finalRows, droppedUnits, changed, dropped, releasedUnits: boxed.trimmedDown };
}

export interface PrebookConsolidateResult {
    /** Собравшиеся целые паллеты, извлечённые из предброни → в черновик. */
    toDraft: AssemblyDraftRow[];
    /** Остаток предброни: неполные (<1 паллеты) хвосты + непалетизируемая россыпь. */
    prebook: AssemblyDraftRow[];
    /** Извлечено штук в целых паллетах. */
    extractedUnits: number;
    /** Было ли что извлекать. */
    changed: boolean;
}

/**
 * Вынести из предброни СОБРАВШИЕСЯ целые паллеты в черновик, оставив в предброни
 * только неполные (<1 паллеты) хвосты и непалетизируемую россыпь.
 *
 * Предбронь-группа (ФФ→склад) может накопить >1 паллеты (сложение хвостов при
 * дозаборе/добавлении из потребности). Целые паллеты должны ЕХАТЬ (учитываться в
 * черновике), а не «висеть» в предброни (требование юзера). Логика зеркалит
 * `trimLinesToWholePallets`, но `kept` (целые паллеты) уходит в ЧЕРНОВИК, а не
 * остаётся. Пустой `newcomerSet`: непалетизируемая россыпь (upp=null) попадает в
 * `droppedLines` → остаётся в предброни. Чистая функция; идемпотентна (после выноса
 * ни одна группа не набирает целой паллеты → повторный прогон ничего не извлечёт).
 */
export function consolidatePrebookWholePallets(
    prebook: AssemblyDraftRow[],
    ctx: NormalizeDraftCtx,
    /** Линии, которые НЕЛЬЗЯ выносить в черновик — остаются в предброни целиком (напр.
     *  целые ⌛-моно-паллеты → ждут «Создать предзаявку», а не едут обычной сборкой). */
    keepInPrebook?: (nmId: number, wbName: string, pkg: PackageType) => boolean,
): PrebookConsolidateResult {
    if (prebook.length === 0) return { toDraft: [], prebook, extractedUnits: 0, changed: false };
    const uppOf = (nm: number, wb: string, ffId?: number): number | null => {
        const pw = ffId != null ? ctx.ppbAt?.(nm, ffId) : null;
        const ppb = pw && pw > 0 ? pw : ctx.ppbOf(nm);
        const bpp = effectiveBoxesPerPallet(ctx.boxSizeOf(nm), maxPalletHeightCm(wb), ctx.overrides);
        return bpp != null && ppb && ppb > 0 ? bpp * ppb : null;
    };
    const boxOf = (nm: number, ffId?: number): number | null => {
        const pw = ffId != null ? ctx.ppbAt?.(nm, ffId) : null;
        return pw && pw > 0 ? pw : (ctx.ppbOf(nm) ?? null);
    };
    const allLines = buildPreviewLines(prebook, new Set());
    // Отделяем «удерживаемые» линии (предзаявка-моно) от тех, что можно консолидировать.
    const held: PreviewLine[] = [];
    const lines: PreviewLine[] = [];
    for (const l of allLines) {
        if (keepInPrebook?.(l.nmId, l.wbName, l.pkg)) held.push(l);
        else lines.push(l);
    }
    const trim = trimLinesToWholePallets(lines, uppOf, boxOf, ctx.classOf);
    const toDraft = linesToRows(trim.kept);
    const prebookOut = linesToRows([...trim.droppedLines, ...held]);
    const extractedUnits = toDraft.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
    return { toDraft, prebook: prebookOut, extractedUnits, changed: extractedUnits > 0 };
}

/**
 * Согласовать свежий fill из потребности с ЗАРЕЗЕРВИРОВАННОЙ предбронью.
 *
 * Резерв (текущая предбронь) ПИНится к своим направлениям: на каждое
 * (nm, barcode, упаковка, склад) ИТОГ остаётся = свежая потребность (не задваиваем),
 * но зарезервированные коробы входят в итог первыми (со своим ФФ), а свежий fill
 * добирает остаток (уменьшая свою ёмкость на том же ФФ, чтобы не пилить сверху).
 * Излишек резерва сверх потребности ИЛИ резерв на направление, которого в свежей
 * потребности НЕТ, — ОТПУСКАЕТСЯ (не входит → товар свободен на ФФ).
 *
 * Σ combined по каждому (nm,barcode,pkg,склад) == Σ fresh по нему (потребность
 * неизменна) → консервация. Чистая функция.
 */
export function reconcileFillWithReserved(
    fresh: AssemblyDraftRow[],
    reserved: AssemblyDraftRow[],
): AssemblyDraftRow[] {
    if (reserved.length === 0) return fresh;
    const keyOf = (l: PreviewLine) => `${l.nmId}::${l.barcode}::${l.pkg}::${l.wbName}`;
    const group = (lines: PreviewLine[]): Map<string, PreviewLine[]> => {
        const m = new Map<string, PreviewLine[]>();
        for (const l of lines) { if (l.qty <= 0) continue; const a = m.get(keyOf(l)) ?? []; a.push(l); m.set(keyOf(l), a); }
        return m;
    };
    const freshByKey = group(buildPreviewLines(fresh, new Set()));
    const resByKey = group(buildPreviewLines(reserved, new Set()));
    const sumQ = (ls: PreviewLine[]) => ls.reduce((s, l) => s + l.qty, 0);
    const out: PreviewLine[] = [];
    // Идём по свежей потребности: только её направления вообще едут (резерв на
    // отсутствующее направление отпускается — ключа нет во freshByKey).
    for (const [k, fLines] of freshByKey) {
        const rLines = resByKey.get(k);
        const freshQ = sumQ(fLines);
        if (!rLines || rLines.length === 0) { out.push(...fLines); continue; }
        const sample = fLines[0];
        // Пиним резерв, но не больше потребности (излишек отпускаем).
        let keepBudget = freshQ;
        const pinnedByFf = new Map<number, number>();
        for (const rl of rLines) {
            if (keepBudget <= 0) break;
            const take = Math.min(rl.qty, keepBudget);
            if (take > 0) { pinnedByFf.set(rl.ffId, (pinnedByFf.get(rl.ffId) || 0) + take); keepBudget -= take; }
        }
        const pinnedQ = freshQ - keepBudget;
        // Свежий добирает остаток потребности из своей ёмкости за вычетом запиненного
        // на том же ФФ (резерв замещает свежий на своём ФФ).
        const freshByFf = new Map<number, number>();
        for (const fl of fLines) freshByFf.set(fl.ffId, (freshByFf.get(fl.ffId) || 0) + fl.qty);
        let remaining = freshQ - pinnedQ;
        for (const [ff, q] of freshByFf) {
            if (remaining <= 0) break;
            const avail = Math.max(0, q - (pinnedByFf.get(ff) || 0));
            const take = Math.min(avail, remaining);
            if (take > 0) { out.push({ ...sample, ffId: ff, qty: take }); remaining -= take; }
        }
        for (const [ff, q] of pinnedByFf) out.push({ ...sample, ffId: ff, qty: q });
    }
    return linesToRows(out);
}

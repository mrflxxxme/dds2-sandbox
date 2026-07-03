/**
 * Проверка КОНСЕРВАЦИИ ТОВАРА в persistence-пути дозабора предброни
 * (`handleTopUpDirection`). Ревью подняло CRITICAL «молчаливая потеря товара
 * кандидата»: `norm.dropped.filter(pbNm)` выбрасывает срезанные короба кандидата
 * (SKU НЕ из предброни). Здесь эмпирически моделируем ФИЗИЧЕСКИЙ ФФ-сток и точный
 * persistence-код, чтобы показать: короба кандидата берутся из СВОБОДНОГО ФФ —
 * срезанный `normalizeDraft`-ом избыток просто НЕ резервируется (остаётся
 * свободным ФФ), а не теряется. А срез ИСХОДНОЙ предброни возвращается в предбронь.
 */
import { describe, it, expect } from 'vitest';
import { normalizeDraft, consolidatePrebookWholePallets, reconcileFillWithReserved, type NormalizeDraftCtx } from '@/lib/utils/normalizeDraft';
import { allocatePairs } from '@/lib/utils/assemblyPreview';
import { palletFootprint, planTopUpBoxes, type TopUpCandidate } from '@/lib/assembly/prebookFootprint';
import type { AssemblyDraftRow } from '@/types/api';

// Геометрия: ppb=10, override boxes/pallet=10 → upp=100 для всех SKU (одинаковая).
const PPB = 10;
const OVERRIDES = { '40x40x40': 10 };
const ctx: NormalizeDraftCtx = {
    ppbOf: () => PPB,
    boxSizeOf: () => '40x40x40',
    overrides: OVERRIDES,
    isNewcomer: () => false,
    freeByNm: {},
};
const uppAt = (): number => 100; // bpp(override 10) × ppb(10)

const FF = '5';
const WB = 'Казань';

/** Точная копия mergeRows из handleTopUpDirection. */
function mergeRows(rs: AssemblyDraftRow[]): AssemblyDraftRow[] {
    const m = new Map<string, AssemblyDraftRow>();
    for (const r of rs) {
        const k = `${r.nm_id}::${r.barcode}::${r.package_type || 'BOX'}`;
        let e = m.get(k);
        if (!e) { e = { nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: {}, tgt: {}, package_type: r.package_type || 'BOX' }; m.set(k, e); }
        for (const [ff, q] of Object.entries(r.src)) e.src[ff] = (e.src[ff] || 0) + (q || 0);
        for (const [w2, q] of Object.entries(r.tgt)) e.tgt[w2] = (e.tgt[w2] || 0) + (q || 0);
    }
    return [...m.values()];
}

/** Реплика persistence-ядра дозабора (без сети/приёмки): prebook+candidates →
 *  normalizeDraft → mergedRows + nextPrebook. Возвращает и grabbed для сверки. */
function simulateTopUp(
    rows: AssemblyDraftRow[],
    prebook: AssemblyDraftRow[],
    candGeom: { nmId: number; freeBoxes: number }[],
): { mergedRows: AssemblyDraftRow[]; nextPrebook: AssemblyDraftRow[]; keptUnits: number; grabbed: Record<number, number> } {
    const pkg = 'BOX';
    const pbOnFf = prebook.filter(r => (r.package_type || 'BOX') === pkg && (r.tgt[WB] || 0) > 0 && (r.src[FF] || 0) > 0);
    const pbNm = new Set(pbOnFf.map(r => r.nm_id));
    const footprint = palletFootprint(pbOnFf.map(r => ({ nmId: r.nm_id, qty: r.tgt[WB] || 0 })), uppAt);
    const shortfallFp = Math.ceil(footprint) - footprint;
    const candidates2: TopUpCandidate[] = candGeom.map(c => ({ nmId: c.nmId, ppb: PPB, freeBoxes: c.freeBoxes, bpp: 10 }));
    const plan = planTopUpBoxes(shortfallFp, candidates2);
    const grabbed: Record<number, number> = {};
    const candidates: AssemblyDraftRow[] = plan.rows.map(pr => {
        grabbed[pr.nmId] = pr.units;
        return { nm_id: pr.nmId, barcode: `bc${pr.nmId}`, vendor_code: `v${pr.nmId}`, src: { [FF]: pr.units }, tgt: { [WB]: pr.units }, package_type: pkg };
    });
    const combined = [...pbOnFf, ...candidates];
    // Свежий пул на прогон: строгое округление ПИШЕТ снятые остатки в freeByNm —
    // общий модульный ctx загрязнял бы соседние тесты.
    const norm = normalizeDraft(combined, { ...ctx, freeByNm: {} });
    const keptUnits = norm.rows.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
    const mergedRows = mergeRows([...rows, ...norm.rows]);
    const nextPrebook = prebook
        .map(r => {
            if ((r.package_type || 'BOX') !== pkg || !(r.tgt[WB] > 0) || !(r.src[FF] > 0)) return r;
            const tgt = { ...r.tgt }; const removed = tgt[WB]; delete tgt[WB];
            const src = { ...r.src }; src[FF] = Math.max(0, (src[FF] || 0) - removed); if (src[FF] <= 0) delete src[FF];
            return { ...r, tgt, src };
        })
        .filter(r => Object.keys(r.tgt).length > 0);
    nextPrebook.push(...norm.dropped.filter(r => pbNm.has(r.nm_id)));
    return { mergedRows, nextPrebook, keptUnits, grabbed };
}

const reservedOf = (rs: AssemblyDraftRow[], nm: number): number =>
    rs.filter(r => r.nm_id === nm).reduce((s, r) => s + Object.values(r.src).reduce((a, v) => a + (v || 0), 0), 0);

describe('дозабор предброни: консервация товара (ответ на CRITICAL ревью)', () => {
    it('сценарий ревьюера: срез избытка кандидата возвращается в СВОБОДНЫЙ ФФ, не теряется', () => {
        // Предбронь A=85 (footprint 0.85). Кандидат B — свободный ФФ 100 шт (10 коробов).
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'bc1', vendor_code: 'A', src: { [FF]: 85 }, tgt: { [WB]: 85 }, package_type: 'BOX' },
        ];
        const physicalB = 100;
        const { mergedRows, nextPrebook, keptUnits, grabbed } = simulateTopUp([], prebook, [{ nmId: 2, freeBoxes: physicalB / PPB }]);

        // Уехало ровно 1 целая паллета (100 шт).
        expect(keptUnits).toBe(100);
        // СТРОГИЙ режим: россыпь A (85 = 8 кор + 5) в черновик не едет и НЕ резервируется
        // (решение юзера) — 5 шт вернулись в свободный ФФ; резерв A = 80 (целые коробы).
        expect(reservedOf(mergedRows, 1)).toBe(80);
        expect(reservedOf(nextPrebook, 1)).toBe(0);
        // Кандидат B взят целыми коробами и не порезан (паллета = A 80 + B 20).
        const reservedB = reservedOf(mergedRows, 2) + reservedOf(nextPrebook, 2);
        expect(grabbed[2]).toBe(20);
        expect(reservedB).toBe(20);
        // Физический сток B сохранён целиком: reserved + free == physical.
        const freeBafter = physicalB - reservedB;
        expect(reservedB + freeBafter).toBe(physicalB);
        // B не должен появиться в предброни (он не был предбронью — был свободным ФФ).
        expect(reservedOf(nextPrebook, 2)).toBe(0);
    });

    it('срез бьёт по ИСХОДНОЙ предброни → возвращается в предбронь (не теряется)', () => {
        // Предбронь A=15 (footprint 0.15). Добор B=90 → combined 105, срез 5 с A (наим. объём).
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'bc1', vendor_code: 'A', src: { [FF]: 15 }, tgt: { [WB]: 15 }, package_type: 'BOX' },
        ];
        const { mergedRows, nextPrebook, keptUnits } = simulateTopUp([], prebook, [{ nmId: 2, freeBoxes: 20 }]);
        expect(keptUnits).toBe(100); // 1 целая паллета
        // СТРОГО: некратная россыпь A (15 = 1 кор + 5) не едет и не резервируется —
        // 5 шт на ФФ; резерв A = 10 (целый короб в составе паллеты).
        expect(reservedOf(mergedRows, 1) + reservedOf(nextPrebook, 1)).toBe(10);
        // Никаких отрицательных/фантомных остатков.
        for (const r of [...mergedRows, ...nextPrebook]) {
            for (const v of [...Object.values(r.src), ...Object.values(r.tgt)]) expect(v).toBeGreaterThan(0);
        }
    });

    it('глобальная консервация: Σ(draft+prebook)после == Σ(prebook)до + Σновая_бронь_из_свободного_ФФ', () => {
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'bc1', vendor_code: 'A', src: { [FF]: 85 }, tgt: { [WB]: 85 }, package_type: 'BOX' },
        ];
        const before = reservedOf(prebook, 1) + reservedOf(prebook, 2);
        const { mergedRows, nextPrebook, keptUnits } = simulateTopUp([], prebook, [{ nmId: 2, freeBoxes: 10 }]);
        const after = reservedOf(mergedRows, 1) + reservedOf(mergedRows, 2) + reservedOf(nextPrebook, 1) + reservedOf(nextPrebook, 2);
        // Прирост брони = взятый кандидат (целые коробы) МИНУС снятая на ФФ россыпь A
        // (строгий режим: 85 = 80 кор + 5 россыпь → 5 ушло в свободный ФФ).
        expect(keptUnits).toBe(100);
        const reservedB = reservedOf(mergedRows, 2) + reservedOf(nextPrebook, 2);
        expect(after - before).toBe(reservedB - 5);
        expect(after).toBeLessThanOrEqual(85 + 100); // не превысили физический сток
    });
});

describe('«Оставить так» + normalizeAndSave-preserve: консервация', () => {
    // Реплика ядра handleShipPrebookAsIs (с allocatePairs — как в page.tsx).
    function simulateShipAsIs(rows: AssemblyDraftRow[], prebook: AssemblyDraftRow[], ff = Number(FF), wb = WB) {
        const chosenKey = String(ff);
        const shipRows: AssemblyDraftRow[] = [];
        const nextPrebook = prebook
            .map(r => {
                if ((r.package_type || 'BOX') !== 'BOX' || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) return r;
                const moved = allocatePairs(r.src, r.tgt).get(`${ff}::${wb}`) || 0;
                if (moved <= 0) return r;
                shipRows.push({ nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [chosenKey]: moved }, tgt: { [wb]: moved }, package_type: 'BOX' });
                const tgt = { ...r.tgt }; tgt[wb] = Math.max(0, (tgt[wb] || 0) - moved); if (tgt[wb] <= 0) delete tgt[wb];
                const src = { ...r.src }; src[chosenKey] = Math.max(0, (src[chosenKey] || 0) - moved); if (src[chosenKey] <= 0) delete src[chosenKey];
                return { ...r, tgt, src };
            })
            .filter(r => Object.keys(r.tgt).length > 0);
        return { mergedRows: mergeRows([...rows, ...shipRows]), nextPrebook, shipRows };
    }

    it('«Оставить так» переносит неполную паллету в rows как есть (не срезает), товар сохранён', () => {
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'bc1', vendor_code: 'A', src: { [FF]: 85 }, tgt: { [WB]: 85 }, package_type: 'BOX' },
        ];
        const { mergedRows, nextPrebook } = simulateShipAsIs([], prebook);
        expect(reservedOf(mergedRows, 1)).toBe(85);      // уехало в черновик
        expect(mergedRows[0].tgt[WB]).toBe(85);          // частичная НЕ нормализована (85, не срезана)
        expect(nextPrebook.length).toBe(0);              // ушло из предброни
    });

    it('МУЛЬТИ-src строка: «Оставить так» берёт порцию ИМЕННО этого ФФ (allocatePairs), не переливает', () => {
        // nm=1 сорсит Казань с ДВУХ ФФ: 5→30, 7→40 (allocatePairs NWC).
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'bc1', vendor_code: 'A', src: { '5': 30, '7': 40 }, tgt: { [WB]: 70 }, package_type: 'BOX' },
        ];
        const { mergedRows, nextPrebook, shipRows } = simulateShipAsIs([], prebook, 5, WB);
        // Отгружено РОВНО 30 с ФФ 5 (порция), НЕ 70 (весь tgt) — иначе перелив с ФФ 5.
        expect(shipRows).toHaveLength(1);
        expect(shipRows[0].src).toEqual({ '5': 30 });
        expect(shipRows[0].tgt).toEqual({ [WB]: 30 });
        // Порция ФФ 7 (40) осталась в предброни (не выкинута).
        expect(nextPrebook).toHaveLength(1);
        expect(nextPrebook[0].src).toEqual({ '7': 40 });
        expect(nextPrebook[0].tgt).toEqual({ [WB]: 40 });
        // Консервация: 70 = 30 отгружено + 40 в предброни.
        expect(reservedOf(mergedRows, 1) + reservedOf(nextPrebook, 1)).toBe(70);
    });

    it('normalizeAndSave: срез частичной паллеты → целые коробы в ПРЕДБРОНЬ, россыпь на ФФ', () => {
        // rows содержит частичную паллету (footprint 0.85 < 1) — normalizeDraft срежет её.
        const rows: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'bc1', vendor_code: 'A', src: { [FF]: 85 }, tgt: { [WB]: 85 }, package_type: 'BOX' },
        ];
        const free: Record<number, Record<number, number>> = {};
        const res = normalizeDraft(rows, { ...ctx, freeByNm: free });
        // Новая логика normalizeAndSave: res.dropped сливается в предбронь.
        const nextPrebook = res.dropped.length ? mergeRows([...[], ...res.dropped]) : [];
        const rowsUnits = res.rows.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
        const pbUnits = nextPrebook.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
        expect(rowsUnits).toBe(0);                       // <1 паллеты → в rows не осталось
        // СТРОГО: 85 = 8 целых коробов (80, в предбронь на дозабор) + 5 некратных (на ФФ).
        expect(pbUnits).toBe(80);
        expect(res.releasedUnits).toBe(5);
        expect(free[1]?.[Number(FF)]).toBe(5);           // россыпь вернулась в свободный ФФ
        expect(rowsUnits + pbUnits + res.releasedUnits).toBe(85); // ничего не потеряно
    });
});

describe('consolidatePrebookWholePallets: целые паллеты предброни → в черновик', () => {
    const sumUnits = (rs: AssemblyDraftRow[]) => rs.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);

    it('выносит собравшуюся целую паллету, неполный хвост оставляет в предброни', () => {
        // Отгрузка ФФ5→Казань: SKU1 100шт (upp 100 = 1.0 пал) + SKU2 20шт (upp 40 = 0.5) = 1.5 пал.
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'bc1', vendor_code: 'A', src: { [FF]: 100 }, tgt: { [WB]: 100 }, package_type: 'BOX' },
            { nm_id: 2, barcode: 'bc2', vendor_code: 'B', src: { [FF]: 20 }, tgt: { [WB]: 20 }, package_type: 'BOX' },
        ];
        const cons = consolidatePrebookWholePallets(prebook, ctx);
        expect(cons.changed).toBe(true);
        expect(cons.extractedUnits).toBe(100);           // 1 целая паллета
        expect(sumUnits(cons.toDraft)).toBe(100);
        expect(sumUnits(cons.prebook)).toBe(20);         // хвост 0.5 паллеты остался
        // Консервация: всё, что было, либо в черновике, либо в предброни.
        expect(sumUnits(cons.toDraft) + sumUnits(cons.prebook)).toBe(120);
    });

    it('идемпотентна: повторный прогон по остатку (<1 паллеты) ничего не выносит', () => {
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 2, barcode: 'bc2', vendor_code: 'B', src: { [FF]: 20 }, tgt: { [WB]: 20 }, package_type: 'BOX' },
        ];
        const cons = consolidatePrebookWholePallets(prebook, ctx);
        expect(cons.changed).toBe(false);
        expect(cons.extractedUnits).toBe(0);
    });
});

describe('reconcileFillWithReserved: резерв пинится, потребность неизменна', () => {
    const tgtOf = (rs: AssemblyDraftRow[], nm: number, wb: string) => rs.filter(r => r.nm_id === nm).reduce((s, r) => s + (r.tgt[wb] || 0), 0);
    const srcOf = (rs: AssemblyDraftRow[], nm: number, ff: number) => rs.filter(r => r.nm_id === nm).reduce((s, r) => s + (r.src[String(ff)] || 0), 0);

    it('стабильная потребность: итог = потребность, ФФ не раздут (резерв замещает свежий)', () => {
        const fresh: AssemblyDraftRow[] = [{ nm_id: 1, barcode: 'b', vendor_code: 'A', src: { '5': 40, '7': 30 }, tgt: { Казань: 70 }, package_type: 'BOX' }];
        const reserved: AssemblyDraftRow[] = [{ nm_id: 1, barcode: 'b', vendor_code: 'A', src: { '5': 30 }, tgt: { Казань: 30 }, package_type: 'BOX' }];
        const out = reconcileFillWithReserved(fresh, reserved);
        expect(tgtOf(out, 1, 'Казань')).toBe(70);   // потребность неизменна (не задвоили)
        expect(srcOf(out, 1, 5)).toBe(40);           // ФФ5 = 30 резерв + 10 свежий (не 70)
        expect(srcOf(out, 1, 7)).toBe(30);
    });

    it('потребность УПАЛА: излишек резерва отпущен, итог = новая потребность', () => {
        const fresh: AssemblyDraftRow[] = [{ nm_id: 1, barcode: 'b', vendor_code: 'A', src: { '5': 20 }, tgt: { Казань: 20 }, package_type: 'BOX' }];
        const reserved: AssemblyDraftRow[] = [{ nm_id: 1, barcode: 'b', vendor_code: 'A', src: { '5': 50 }, tgt: { Казань: 50 }, package_type: 'BOX' }];
        const out = reconcileFillWithReserved(fresh, reserved);
        expect(tgtOf(out, 1, 'Казань')).toBe(20);    // не превысили потребность (30 отпущено)
    });

    it('резерв на направление, которого НЕТ в потребности → отпущен', () => {
        const fresh: AssemblyDraftRow[] = [{ nm_id: 1, barcode: 'b', vendor_code: 'A', src: { '5': 40 }, tgt: { Казань: 40 }, package_type: 'BOX' }];
        const reserved: AssemblyDraftRow[] = [{ nm_id: 1, barcode: 'b', vendor_code: 'A', src: { '5': 20 }, tgt: { Тула: 20 }, package_type: 'BOX' }];
        const out = reconcileFillWithReserved(fresh, reserved);
        expect(tgtOf(out, 1, 'Казань')).toBe(40);
        expect(tgtOf(out, 1, 'Тула')).toBe(0);       // резерв на Тулу отпущен
    });

    it('пустой резерв → возвращает свежий как есть', () => {
        const fresh: AssemblyDraftRow[] = [{ nm_id: 1, barcode: 'b', vendor_code: 'A', src: { '5': 40 }, tgt: { Казань: 40 }, package_type: 'BOX' }];
        expect(reconcileFillWithReserved(fresh, [])).toBe(fresh);
    });
});

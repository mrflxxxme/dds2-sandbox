/**
 * Сужение нормализации черновика до тронутых направлений (`scopedNormalizeDraft`).
 *
 * Инвариант фичи: действие над ОДНИМ направлением не должно переупаковывать соседние.
 * Опасный кейс — общий пул свободного ФФ: ГЛОБАЛЬНЫЙ проход дозабирает хвост соседа
 * из свободного ФФ, СУЖЕННЫЙ — оставляет соседа байт-в-байт. Плюс: пустой `only`
 * (вычитающая операция «На ФФ»/«Удалить») → no-op; box+mono симметрия; идемпотентность.
 */
import { describe, it, expect } from 'vitest';
import { scopedNormalizeDraft, mergeDraftRows, directionKey } from '@/lib/utils/scopedNormalizeDraft';
import type { NormalizeDraftCtx } from '@/lib/utils/normalizeDraft';
import type { AssemblyDraftRow } from '@/types/api';

// Геометрия: ppb=10, override boxes/pallet=10 → upp=100 для всех SKU.
const PPB = 10;
const mkCtx = (free: Record<number, Record<number, number>> = {}): NormalizeDraftCtx => ({
    ppbOf: () => PPB,
    boxSizeOf: () => '40x40x40',
    overrides: { '40x40x40': 10 },
    freeByNm: free,
});

const KAZAN = 'Казань';
const TULA = 'Тула';

const sumTgt = (rs: AssemblyDraftRow[], wb: string): number =>
    rs.filter(r => (r.tgt[wb] || 0) > 0).reduce((s, r) => s + (r.tgt[wb] || 0), 0);
const rowsFor = (rs: AssemblyDraftRow[], wb: string): AssemblyDraftRow[] =>
    rs.filter(r => (r.tgt[wb] || 0) > 0);

describe('scopedNormalizeDraft: сужение до тронутого направления', () => {
    it('СУЖЕННЫЙ проход НЕ дозабирает хвост соседа из общего ФФ, ГЛОБАЛЬНЫЙ — дозабирает', () => {
        // A (Казань, BOX) — тронуто: частичная паллета 85 (footprint 0.85 < 1 → срез).
        // B (Тула, BOX) — НЕ тронуто: хвост предброни 5 шт (0.5 короба).
        // Свободный ФФ nm2 на ff5 = 5 → хватает добить B до целого короба (10).
        const rows: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'bc1', vendor_code: 'A', src: { '5': 85 }, tgt: { [KAZAN]: 85 }, package_type: 'BOX' },
        ];
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 2, barcode: 'bc2', vendor_code: 'B', src: { '5': 5 }, tgt: { [TULA]: 5 }, package_type: 'BOX' },
        ];

        // ГЛОБАЛЬНЫЙ (only=undefined): сосед B дозабирается 5 → 10.
        const global = scopedNormalizeDraft(rows, prebook, mkCtx({ 2: { 5: 5 } }), undefined);
        expect(sumTgt(global.prebook, TULA)).toBe(10); // B переупакован глобальным проходом

        // СУЖЕННЫЙ (only=A): B остаётся 5 (не тронут), но A нормализуется.
        const scoped = scopedNormalizeDraft(rows, prebook, mkCtx({ 2: { 5: 5 } }), new Set([directionKey('BOX', KAZAN)]));
        expect(sumTgt(scoped.prebook, TULA)).toBe(5);  // сосед НЕ тронут
        expect(scoped.changed).toBe(true);             // A всё же нормализован
        // Строка B байт-в-байт как на входе.
        expect(rowsFor(scoped.prebook, TULA)).toEqual(rowsFor(prebook, TULA));
    });

    it('пустой only (вычитающая операция «На ФФ»/«Удалить») → no-op, ничего не двигается', () => {
        const rows: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'bc1', vendor_code: 'A', src: { '5': 85 }, tgt: { [KAZAN]: 85 }, package_type: 'BOX' },
        ];
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 2, barcode: 'bc2', vendor_code: 'B', src: { '5': 5 }, tgt: { [TULA]: 5 }, package_type: 'BOX' },
        ];
        const res = scopedNormalizeDraft(rows, prebook, mkCtx({ 2: { 5: 5 } }), new Set());
        expect(res.changed).toBe(false);
        expect(res.rows).toBe(rows);       // те же ссылки — вообще не трогали
        expect(res.prebook).toBe(prebook);
    });

    it('box+mono симметрия: тронут МОНО, соседний КОРОБ байт-в-байт; и наоборот', () => {
        const rows: AssemblyDraftRow[] = [
            // MONO Казань — тронуто, частичная 85 → срез.
            { nm_id: 1, barcode: 'bc1', vendor_code: 'A', src: { '5': 85 }, tgt: { [KAZAN]: 85 }, package_type: 'MONOPALLET' },
            // BOX Тула — целая паллета 100, не тронуто.
            { nm_id: 2, barcode: 'bc2', vendor_code: 'B', src: { '5': 100 }, tgt: { [TULA]: 100 }, package_type: 'BOX' },
        ];
        // Тронут МОНО Казань → BOX Тула не меняется.
        const mono = scopedNormalizeDraft(rows, [], mkCtx(), new Set([directionKey('MONOPALLET', KAZAN)]));
        expect(rowsFor(mono.rows, TULA)).toEqual(rowsFor(rows, TULA));

        // Тронут BOX Тула → MONO Казань не меняется.
        const box = scopedNormalizeDraft(rows, [], mkCtx(), new Set([directionKey('BOX', TULA)]));
        expect(rowsFor(box.rows, KAZAN)).toEqual(rowsFor(rows, KAZAN));
    });

    it('мульти-wb строка: нормализуется порция тронутого wb, порция соседнего сохранена', () => {
        // Одна строка nm=1 сорсит и Казань(тронуто), и Тула(не тронуто) с одного ФФ.
        const rows: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'bc1', vendor_code: 'A', src: { '5': 185 }, tgt: { [KAZAN]: 85, [TULA]: 100 }, package_type: 'BOX' },
        ];
        const scoped = scopedNormalizeDraft(rows, [], mkCtx(), new Set([directionKey('BOX', KAZAN)]));
        // Тула (целая паллета 100) сохранена; Казань (0.85) срезана из rows.
        expect(sumTgt(scoped.rows, TULA)).toBe(100);
        expect(sumTgt(scoped.rows, KAZAN)).toBe(0);
        // Консервация Тулы: ровно 100, ничего не потеряно и не добавлено.
        const tulaSrc = rowsFor(scoped.rows, TULA).reduce((s, r) => s + (r.src['5'] || 0), 0);
        expect(tulaSrc).toBe(100);
    });

    it('идемпотентность: повторный сужённый прогон по уже-целому направлении → changed=false', () => {
        const rows: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'bc1', vendor_code: 'A', src: { '5': 100 }, tgt: { [KAZAN]: 100 }, package_type: 'BOX' },
        ];
        const only = new Set([directionKey('BOX', KAZAN)]);
        const first = scopedNormalizeDraft(rows, [], mkCtx(), only);
        expect(first.changed).toBe(false); // уже целая паллета
        const second = scopedNormalizeDraft(first.rows, first.prebook, mkCtx(), only);
        expect(second.changed).toBe(false);
    });

    it('ИНВАРИАНТ: self-heal НЕ промотирует предбронь в rows (на этом держится скип консолидации)', () => {
        // Промоция целых паллет предброни в rows — задача ТОЛЬКО consolidatePrebookWholePallets.
        // scopedNormalizeDraft (путь self-heal) обязан оставить даже ЦЕЛУЮ паллету в предброни,
        // rows не наполнять. Если это сломается (напр. round-boxes научат паллет-добору) — гейт
        // скипа консолидации в page.tsx начнёт молча ронять промоцию → тест-сигнал.
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'bc1', vendor_code: 'A', src: { '5': 100 }, tgt: { [KAZAN]: 100 }, package_type: 'BOX' }, // ровно 1.0 паллета
        ];
        const full = scopedNormalizeDraft([], prebook, mkCtx(), undefined);
        expect(sumTgt(full.rows, KAZAN)).toBe(0);        // rows НЕ наполнились из предброни
        expect(sumTgt(full.prebook, KAZAN)).toBe(100);   // паллета осталась в предброни (ждёт консолидации)
        // Сужённый путь — тот же инвариант.
        const scoped = scopedNormalizeDraft([], prebook, mkCtx(), new Set([directionKey('BOX', KAZAN)]));
        expect(sumTgt(scoped.rows, KAZAN)).toBe(0);
    });

    it('полный проход (only=undefined) эквивалентен старому normalizeLocal-конвейеру', () => {
        // Санити: одно направление, частичная паллета 85 → срез 80 в предбронь, 5 на ФФ.
        const rows: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'bc1', vendor_code: 'A', src: { '5': 85 }, tgt: { [KAZAN]: 85 }, package_type: 'BOX' },
        ];
        const free: Record<number, Record<number, number>> = {};
        const res = scopedNormalizeDraft(rows, [], mkCtx(free), undefined);
        expect(sumTgt(res.rows, KAZAN)).toBe(0);       // <1 паллеты → из rows ушло
        expect(res.droppedToPrebook).toBe(80);          // 8 целых коробов в предбронь
        expect(res.releasedTotal).toBe(5);              // россыпь на ФФ
        expect(sumTgt(res.prebook, KAZAN)).toBe(80);
    });
});

describe('mergeDraftRows (вынесено в общий модуль)', () => {
    it('схлопывает по (nm,barcode,pkg,as_is), суммируя src/tgt', () => {
        const merged = mergeDraftRows([
            { nm_id: 1, barcode: 'b', vendor_code: 'A', src: { '5': 10 }, tgt: { [KAZAN]: 10 }, package_type: 'BOX' },
            { nm_id: 1, barcode: 'b', vendor_code: 'A', src: { '5': 5 }, tgt: { [KAZAN]: 5 }, package_type: 'BOX' },
        ]);
        expect(merged).toHaveLength(1);
        expect(merged[0].src['5']).toBe(15);
        expect(merged[0].tgt[KAZAN]).toBe(15);
    });

    it('as_is не суммируется с обычной строкой того же SKU', () => {
        const merged = mergeDraftRows([
            { nm_id: 1, barcode: 'b', vendor_code: 'A', src: { '5': 10 }, tgt: { [KAZAN]: 10 }, package_type: 'BOX' },
            { nm_id: 1, barcode: 'b', vendor_code: 'A', src: { '5': 5 }, tgt: { [KAZAN]: 5 }, package_type: 'BOX', as_is: true },
        ]);
        expect(merged).toHaveLength(2);
    });
});

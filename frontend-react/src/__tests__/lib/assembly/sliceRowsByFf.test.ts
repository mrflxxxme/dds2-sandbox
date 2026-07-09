/**
 * ФФ-срез строк раскладки (`sliceRowsByFf`) — лупа «Склад забора» в матрице
 * черновика: из каждой строки берётся только доля, физически забираемая с
 * выбранного ФФ. Пары ФФ→WB — тот же `allocatePairs`, что карточки черновика,
 * экспорт и создание заявок → числа между экранами сходятся.
 */
import { describe, it, expect } from 'vitest';
import { sliceRowsByFf } from '@/lib/assembly/draftDistribution';
import { allocatePairs } from '@/lib/utils/assemblyPreview';
import type { AssemblyDraftRow } from '@/types/api';

const row = (
    nm: number, src: Record<string, number>, tgt: Record<string, number>,
    pkg: AssemblyDraftRow['package_type'] = 'BOX',
): AssemblyDraftRow => ({ nm_id: nm, barcode: `bc-${nm}`, vendor_code: `VC-${nm}`, src, tgt, package_type: pkg });

const tTgt = (r: AssemblyDraftRow) => Object.values(r.tgt).reduce((s, v) => s + (v || 0), 0);
const tSrc = (r: AssemblyDraftRow) => Object.values(r.src).reduce((s, v) => s + (v || 0), 0);

describe('sliceRowsByFf', () => {
    it('multi-ФФ строка: срез отдаёт только долю выбранного ФФ, Σsrc==Σtgt', () => {
        const rows = [row(1, { '10': 48, '20': 24 }, { 'Казань': 48, 'Тула': 24 })];
        const s10 = sliceRowsByFf(rows, 10);
        expect(s10).toHaveLength(1);
        expect(tSrc(s10[0])).toBe(48);
        expect(tTgt(s10[0])).toBe(48);
        expect(Object.keys(s10[0].src)).toEqual(['10']);
    });

    it('консервация маргиналов: Σ срезов по всем ФФ == исходной строке per WB-склад', () => {
        const rows = [
            row(1, { '10': 48, '20': 24 }, { 'Казань': 48, 'Тула': 24 }),
            row(2, { '10': 30, '30': 90 }, { 'ЕКБ': 60, 'СПБ': 60 }),
        ];
        const byWb = new Map<string, number>();
        for (const ff of [10, 20, 30]) {
            for (const r of sliceRowsByFf(rows, ff)) {
                for (const [wb, q] of Object.entries(r.tgt)) byWb.set(`${r.nm_id}::${wb}`, (byWb.get(`${r.nm_id}::${wb}`) || 0) + (q || 0));
            }
        }
        expect(byWb.get('1::Казань')).toBe(48);
        expect(byWb.get('1::Тула')).toBe(24);
        expect((byWb.get('2::ЕКБ') || 0) + (byWb.get('2::СПБ') || 0)).toBe(120);
    });

    it('кратность короба сохраняется: кратные маргиналы → кратные доли в ячейках', () => {
        // ppb=24: src {30×24=720? нет — 720 лишнее} — берём src {10: 48, 20: 96}, tgt {A: 72, B: 72}.
        const rows = [row(1, { '10': 48, '20': 96 }, { 'Казань': 72, 'Тула': 72 })];
        for (const ff of [10, 20]) {
            for (const r of sliceRowsByFf(rows, ff)) {
                for (const q of Object.values(r.tgt)) expect((q || 0) % 24).toBe(0);
            }
        }
    });

    it('ФФ без доли в строке → строка не попадает; неизвестный ФФ → пусто', () => {
        const rows = [row(1, { '10': 48 }, { 'Казань': 48 })];
        expect(sliceRowsByFf(rows, 20)).toHaveLength(0);
        expect(sliceRowsByFf([], 10)).toHaveLength(0);
    });

    it('согласован с allocatePairs (карточки черновика): доля == Σ пар этого ФФ', () => {
        const r = row(7, { '10': 130, '20': 44 }, { 'Казань': 96, 'Тула': 78 });
        const expected = new Map<string, number>();
        for (const [pair, q] of allocatePairs(r.src, r.tgt)) {
            const [ff, wb] = pair.split('::');
            if (ff === '10') expected.set(wb, (expected.get(wb) || 0) + q);
        }
        const sliced = sliceRowsByFf([r], 10);
        expect(sliced).toHaveLength(1);
        expect(tTgt(sliced[0])).toBe([...expected.values()].reduce((s, v) => s + v, 0));
        for (const [wb, q] of expected) expect(sliced[0].tgt[wb] || 0).toBe(q);
    });

    it('метаданные строки (pkg, barcode, as_is) сохраняются в срезе', () => {
        const r = { ...row(9, { '10': 22, '20': 22 }, { 'Казань': 44 }, 'MONOPALLET'), as_is: true };
        const sliced = sliceRowsByFf([r], 20);
        expect(sliced).toHaveLength(1);
        expect(sliced[0].package_type).toBe('MONOPALLET');
        expect(sliced[0].barcode).toBe('bc-9');
        expect(sliced[0].as_is).toBe(true);
    });
});

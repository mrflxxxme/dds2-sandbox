import { describe, it, expect } from 'vitest';
import { roundDraftRowsToWholeBoxes } from '@/lib/utils/assemblyRoundBoxes';
import type { AssemblyDraftRow, PackageType } from '@/types/api';

const row = (nm: number, src: Record<string, number>, tgt: Record<string, number>, pkg: PackageType = 'BOX'): AssemblyDraftRow =>
    ({ nm_id: nm, barcode: `b${nm}`, vendor_code: `v${nm}`, src, tgt, package_type: pkg });
const sum = (o: Record<string, number>) => Object.values(o).reduce((s, v) => s + v, 0);

describe('roundDraftRowsToWholeBoxes', () => {
    it('добивает неполный короб ВВЕРХ из свободного ФФ', () => {
        const free = { 1: { 10: 100 } };
        const res = roundDraftRowsToWholeBoxes([row(1, { '10': 95 }, { 'Коледино': 95 })], () => 10, free);
        expect(res.rows[0].tgt['Коледино']).toBe(100);
        expect(sum(res.rows[0].src)).toBe(100);
        expect(res.filledUp).toBe(5);
        expect(free[1][10]).toBe(95); // 5 израсходовано из пула
    });

    it('один склад без ФФ — россыпь сохраняется (штуки не теряем, не режем вниз)', () => {
        const res = roundDraftRowsToWholeBoxes([row(1, { '10': 95 }, { 'Коледино': 95 })], () => 10, { 1: {} });
        expect(res.rows[0].tgt['Коледино']).toBe(95);   // некуда консолидировать → остаётся россыпью
        expect(sum(res.rows[0].src)).toBe(95);
        expect(res.trimmedDown).toBe(0);
        expect(res.looseLeft).toBe(5);
        expect(res.changed).toBe(0);                     // строка не изменилась
    });

    it('КОНСОЛИДАЦИЯ хвостов: огрызки по 2 складам → 1 целый короб + россыпь на одном складе', () => {
        // 36 + 36 = 72 = 1 короб (60) + 12 россыпь; ФФ нет → 1 короб на склад с бОльшим хвостом, 12 россыпью
        const res = roundDraftRowsToWholeBoxes(
            [row(1, { '10': 72 }, { 'Коледино': 36, 'Казань': 36 })], () => 60, { 1: {} },
        );
        const t = res.rows[0].tgt;
        expect(sum(t)).toBe(72);                          // штуки целы
        expect(sum(res.rows[0].src)).toBe(72);            // баланс
        // Хвосты схлопнулись на ОДИН склад (1 короб + 12 россыпь = 72), вместо двух россыпь-строк.
        expect(Object.keys(t).length).toBe(1);
        const looseCells = Object.values(t).filter((q) => q % 60 !== 0);
        expect(looseCells.length).toBe(1);
        expect(looseCells.reduce((s, q) => s + (q % 60), 0)).toBe(12);
        expect(res.consolidated).toBe(60);
        expect(res.looseLeft).toBe(12);
    });

    it('КОНСОЛИДАЦИЯ + добор последнего короба из ФФ → 0 россыпи (кейс chashka)', () => {
        // tgt-хвосты 8 + 24 = 32, ppb 36, свободный ФФ 4 → 32+4 = 36 = ровно 1 короб, россыпи нет
        const res = roundDraftRowsToWholeBoxes(
            [row(1, { '10': 32 }, { 'Самара': 8, 'Екатеринбург': 24 })], () => 36, { 1: { 10: 4 } },
        );
        const t = res.rows[0].tgt;
        expect(sum(t)).toBe(36);
        expect(sum(res.rows[0].src)).toBe(36);
        for (const q of Object.values(t)) expect(q % 36).toBe(0);
        expect(res.looseLeft).toBe(0);
        expect(res.filledUp).toBe(4);
    });

    it('новинки / без кратности (ppb null) НЕ трогает — россыпь сохраняется', () => {
        const res = roundDraftRowsToWholeBoxes([row(1, { '10': 95 }, { 'Коледино': 95 })], () => null, {});
        expect(res.rows[0].tgt['Коледино']).toBe(95);
        expect(res.changed).toBe(0);
    });

    it('моно/сейф (не BOX) не трогает', () => {
        const res = roundDraftRowsToWholeBoxes([row(1, { '10': 95 }, { 'Коледино': 95 }, 'MONOPALLET')], () => 10, { 1: { 10: 100 } });
        expect(res.rows[0].tgt['Коледино']).toBe(95);
        expect(res.changed).toBe(0);
    });

    it('баланс держится и все cells кратны коробу (мульти-склад/мульти-ФФ)', () => {
        const free = { 1: { 10: 200 } };
        const res = roundDraftRowsToWholeBoxes([row(1, { '10': 50, '20': 45 }, { 'Коледино': 60, 'Казань': 35 })], () => 10, free);
        for (const r of res.rows) {
            expect(sum(r.src)).toBe(sum(r.tgt));
            for (const q of Object.values(r.tgt)) expect(q % 10).toBe(0);
        }
    });

    it('новинка со свободным ФФ добивается до короба (60)', () => {
        const res = roundDraftRowsToWholeBoxes(
            [row(1, { '10': 36 }, { 'Коледино': 36 })], () => 60, { 1: { 10: 500 } }, () => true,
        );
        expect(res.rows[0].tgt['Коледино']).toBe(60);
        expect(res.filledUp).toBe(24);
    });

    it('новинка БЕЗ свободного ФФ — россыпь сохраняется, не дропается в ноль', () => {
        const res = roundDraftRowsToWholeBoxes(
            [row(1, { '10': 36 }, { 'Коледино': 36 })], () => 60, { 1: {} }, () => true,
        );
        expect(res.rows[0].tgt['Коледино']).toBe(36);  // осталась россыпь
        expect(res.changed).toBe(0);
        expect(res.trimmedDown).toBe(0);
    });
});

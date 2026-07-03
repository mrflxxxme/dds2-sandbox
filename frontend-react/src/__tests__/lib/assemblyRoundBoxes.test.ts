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

    it('СТРОГО: без ФФ некратный остаток снимается на ФФ (не резервируем, решение юзера)', () => {
        const free: Record<number, Record<number, number>> = { 1: {} };
        const res = roundDraftRowsToWholeBoxes([row(1, { '10': 95 }, { 'Коледино': 95 })], () => 10, free);
        expect(res.rows[0].tgt['Коледино']).toBe(90);    // 9 целых коробов; 5 некратных → на ФФ
        expect(sum(res.rows[0].src)).toBe(90);
        expect(res.trimmedDown).toBe(5);
        expect(res.looseLeft).toBe(0);
        expect(free[1][10]).toBe(5);                     // снятое вернулось в свободный ФФ
    });

    it('КОНСОЛИДАЦИЯ хвостов: огрызки по 2 складам → 1 целый короб, остаток на ФФ', () => {
        // 36 + 36 = 72 = 1 короб (60) + 12; строгий режим: короб на склад с бОльшим
        // хвостом, 12 некратных → в свободный ФФ (не едут, не резервируются).
        const free: Record<number, Record<number, number>> = { 1: {} };
        const res = roundDraftRowsToWholeBoxes(
            [row(1, { '10': 72 }, { 'Коледино': 36, 'Казань': 36 })], () => 60, free,
        );
        const t = res.rows[0].tgt;
        expect(sum(t)).toBe(60);                          // едет ровно целый короб
        expect(sum(res.rows[0].src)).toBe(60);            // баланс
        expect(Object.keys(t).length).toBe(1);
        for (const q of Object.values(t)) expect(q % 60).toBe(0);
        expect(res.consolidated).toBe(60);
        expect(res.trimmedDown).toBe(12);
        expect(free[1][10]).toBe(12);                     // остаток вернулся в свободный ФФ
    });

    it('МУЛЬТИ-КРАТНОСТЬ: порция меряется коробом СВОЕГО ФФ (30 на Газпроме не режется по min=22)', () => {
        // Реальный кейс 80х160_синий: Хамза(3) ppb 22, Газпром(5) ppb 30; глобальный min 22.
        // Порция 30@Газпром — физически ЦЕЛЫЙ короб, по min-22 выглядела бы как 22+8.
        const ppbAt = (_nm: number, ff: number) => (ff === 3 ? 22 : ff === 5 ? 30 : null);
        const free: Record<number, Record<number, number>> = { 1: { 3: 8 } };
        const res = roundDraftRowsToWholeBoxes(
            [row(1, { '3': 14, '5': 30 }, { 'Екб': 44 })], () => 22, free, undefined, ppbAt,
        );
        const r = res.rows[0];
        expect(sum(r.tgt)).toBe(52);                      // хамза-порция добита 14→22, Газпром цел
        expect(r.src['3']).toBe(22);
        expect(r.src['5']).toBe(30);
        expect(res.filledUp).toBe(8);
        expect(res.trimmedDown).toBe(0);
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

import { describe, expect, it } from 'vitest';
import { allocateWholeBoxes, buildLeftoverWeights } from '@/lib/assembly/leftoverAlloc';

const sig = (need = 0, stock = 0, asm = 0, transit = 0) => ({ need, stock, asm, transit });

describe('buildLeftoverWeights', () => {
    it('приоритет — склады с need>0', () => {
        const w = buildLeftoverWeights(
            { A: sig(10, 100), B: sig(0, 500), C: sig(5, 0) },
            () => true,
        );
        expect([...w.entries()]).toEqual([['A', 10], ['C', 5]]);
    });

    it('фолбэк — присутствие (stock+asm+transit), когда need нулевой', () => {
        const w = buildLeftoverWeights(
            { A: sig(0, 30, 5), B: sig(0, 0, 0, 15), C: sig(0) },
            () => true,
        );
        expect([...w.entries()]).toEqual([['A', 35], ['B', 15]]);
    });

    it('закрытые склады выфильтровываются на любом шаге', () => {
        const w = buildLeftoverWeights(
            { A: sig(10), B: sig(20) },
            (wh) => wh !== 'B',
        );
        expect([...w.entries()]).toEqual([['A', 10]]);
        expect(buildLeftoverWeights({ A: sig(10) }, () => false).size).toBe(0);
    });

    it('пустой вход → пусто', () => {
        expect(buildLeftoverWeights(undefined, () => true).size).toBe(0);
        expect(buildLeftoverWeights({ A: sig(0) }, () => true).size).toBe(0);
    });
});

describe('allocateWholeBoxes', () => {
    it('Σ раздачи === boxes; пропорция по весам', () => {
        const out = allocateWholeBoxes(10, new Map([['A', 60], ['B', 30], ['C', 10]]));
        expect([...out.values()].reduce((s, v) => s + v, 0)).toBe(10);
        expect(out.get('A')).toBe(6);
        expect(out.get('B')).toBe(3);
        expect(out.get('C')).toBe(1);
    });

    it('largest remainder: остаток уходит наибольшей дробной части', () => {
        // 5 коробов на веса 1:1:1 → 2+2+1 (детерминизм по имени).
        const out = allocateWholeBoxes(5, new Map([['B', 1], ['A', 1], ['C', 1]]));
        expect([...out.values()].reduce((s, v) => s + v, 0)).toBe(5);
        expect(out.get('A')).toBe(2);
        expect(out.get('B')).toBe(2);
        expect(out.get('C')).toBe(1);
    });

    it('boxes меньше числа складов — только топовые по весу', () => {
        const out = allocateWholeBoxes(1, new Map([['A', 5], ['B', 10]]));
        expect([...out.values()].reduce((s, v) => s + v, 0)).toBe(1);
        expect(out.get('B')).toBe(1);
    });

    it('края: 0 коробов / пустые веса / нулевые веса', () => {
        expect(allocateWholeBoxes(0, new Map([['A', 1]])).size).toBe(0);
        expect(allocateWholeBoxes(5, new Map()).size).toBe(0);
        expect(allocateWholeBoxes(5, new Map([['A', 0]])).size).toBe(0);
    });

    it('раздача больше, чем складов с весом — по кругу без потерь', () => {
        const out = allocateWholeBoxes(7, new Map([['A', 1], ['B', 1]]));
        expect([...out.values()].reduce((s, v) => s + v, 0)).toBe(7);
    });
});

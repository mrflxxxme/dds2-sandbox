import { describe, it, expect } from 'vitest';
import { distributeByBoxMultiple, distributeByBoxMultipleWeighted } from '@/lib/utils/boxDistribution';

describe('distributeByBoxMultiple', () => {
    it('returns empty object when ppb<=0', () => {
        expect(distributeByBoxMultiple([{ name: 'A', need: 100 }], 100, 0)).toEqual({});
        expect(distributeByBoxMultiple([{ name: 'A', need: 100 }], 100, -5)).toEqual({});
    });

    it('returns empty object when totalCanSend<=0', () => {
        expect(distributeByBoxMultiple([{ name: 'A', need: 100 }], 0, 10)).toEqual({});
        expect(distributeByBoxMultiple([{ name: 'A', need: 100 }], -3, 10)).toEqual({});
    });

    it('returns empty object when no warehouse has need', () => {
        expect(distributeByBoxMultiple([{ name: 'A', need: 0 }], 100, 10)).toEqual({});
    });

    it('gives one box to each needy warehouse first', () => {
        // 30 шт, ppb=10, 3 склада с need >= 10 — каждому по коробке
        const result = distributeByBoxMultiple(
            [
                { name: 'A', need: 50 },
                { name: 'B', need: 30 },
                { name: 'C', need: 20 },
            ],
            30, 10,
        );
        expect(result).toEqual({ A: 10, B: 10, C: 10 });
    });

    it('greedy fills remaining boxes by largest unmet need', () => {
        // 50 шт, ppb=10, складов 3. Pass1: A=10 B=10 C=10 (30 шт). Остаток 20 шт = 2 box.
        // Pass2: A нужно 40 ещё (50-10), B нужно 20 (30-10), C нужно 10 (20-10).
        //   1-я коробка → A (unmet=40, max)
        //   2-я коробка → A (unmet=30, всё ещё max)
        const result = distributeByBoxMultiple(
            [
                { name: 'A', need: 50 },
                { name: 'B', need: 30 },
                { name: 'C', need: 20 },
            ],
            50, 10,
        );
        expect(result).toEqual({ A: 30, B: 10, C: 10 });
    });

    it('skips warehouse for further boxes when remaining unmet < ppb', () => {
        // need=15, ppb=10 → unmet после первой коробки = 5 < ppb → больше коробок не получит
        const result = distributeByBoxMultiple(
            [
                { name: 'A', need: 15 },
                { name: 'B', need: 100 },
            ],
            100, 10,
        );
        // Pass1: A=10, B=10 (20). Pass2: остаток 80 = 8 box. A unmet=5 (skip коробки),
        // B unmet=90 → всё B (B=90). A коробок больше не получает (unmet 5 < ppb).
        expect(result.A).toBe(10);
        expect(result.B).toBe(90);
    });

    it('drops the sub-box tail — only whole boxes ship', () => {
        // 47 шт, ppb=10. 4 коробки = 40 шт; хвост 7 шт остаётся на ФФ, в WB не уходит.
        const result = distributeByBoxMultiple(
            [{ name: 'A', need: 200 }],
            47, 10,
        );
        expect(result).toEqual({ A: 40 });
    });

    it('skips warehouses when not enough boxes for everyone in pass1', () => {
        // 20 шт, ppb=10 → 2 box. Складов 3 (все нуждаются). Получат A и B (топ-2 по need).
        const result = distributeByBoxMultiple(
            [
                { name: 'A', need: 100 },
                { name: 'B', need: 50 },
                { name: 'C', need: 30 },
            ],
            20, 10,
        );
        expect(result).toEqual({ A: 10, B: 10 });
        expect(result.C).toBeUndefined();
    });

    it('does not allocate to warehouse with need=0', () => {
        const result = distributeByBoxMultiple(
            [
                { name: 'A', need: 50 },
                { name: 'B', need: 0 },
            ],
            100, 10,
        );
        expect(result.B).toBeUndefined();
        expect(result.A).toBe(50);
    });

    it('caps total at whole boxes within Σneed (no overshoot, no tail)', () => {
        // need=15, total=100, ppb=10. budget=⌊min(100,15)/10⌋×10=10.
        // 1 коробка; хвост 5 шт остаётся на ФФ (а не уходит россыпью до need).
        const result = distributeByBoxMultiple(
            [{ name: 'A', need: 15 }],
            100, 10,
        );
        expect(result.A).toBe(10);
    });

    it('ships only whole boxes, остаток на ФФ (23 шт, ppb=7)', () => {
        // need=23, total=23, ppb=7. 3 коробки = 21; хвост 2 шт остаётся на ФФ.
        const result = distributeByBoxMultiple(
            [{ name: 'A', need: 23 }],
            23, 7,
        );
        expect(result.A).toBe(21);
    });

    it('returns empty when available is less than one box', () => {
        // 5 шт, ppb=10 — целой коробки не набрать → ничего не уходит, всё остаётся на ФФ.
        const result = distributeByBoxMultiple(
            [{ name: 'A', need: 100 }],
            5, 10,
        );
        expect(result).toEqual({});
    });

    it('ships only whole boxes, leftover остаётся на ФФ', () => {
        // total=27, ppb=10, два склада. budget=⌊min(27,80)/10⌋×10=20.
        // Pass1: A=10, B=10. Хвост 7 шт НЕ уходит россыпью — остаётся на ФФ.
        const result = distributeByBoxMultiple(
            [
                { name: 'A', need: 50 },
                { name: 'B', need: 30 },
            ],
            27, 10,
        );
        expect(result).toEqual({ A: 10, B: 10 });
        expect(result.A + result.B).toBe(20);
    });
});

describe('distributeByBoxMultipleWeighted', () => {
    it('returns empty object when ppb<=0 or qty<=0', () => {
        expect(distributeByBoxMultipleWeighted([{ name: 'A', weight: 60 }], 100, 0)).toEqual({});
        expect(distributeByBoxMultipleWeighted([{ name: 'A', weight: 60 }], 0, 10)).toEqual({});
    });

    it('returns empty object when no positive weight', () => {
        expect(distributeByBoxMultipleWeighted([{ name: 'A', weight: 0 }], 100, 10)).toEqual({});
        expect(distributeByBoxMultipleWeighted([], 100, 10)).toEqual({});
    });

    it('returns empty when qty < one box (строгая кратность — хвост на ФФ)', () => {
        // 8 шт, ppb=10 — целой коробки не набрать → ничего не распределяем.
        expect(distributeByBoxMultipleWeighted(
            [{ name: 'A', weight: 60 }, { name: 'B', weight: 40 }],
            8, 10,
        )).toEqual({});
    });

    it('splits whole boxes proportionally to weight', () => {
        // qty=20, ppb=10. needs A=12, B=8. Pass1: каждому по коробке.
        const result = distributeByBoxMultipleWeighted(
            [{ name: 'A', weight: 60 }, { name: 'B', weight: 40 }],
            20, 10,
        );
        expect(result).toEqual({ A: 10, B: 10 });
    });

    it('drops the sub-box tail (qty=25, ppb=10)', () => {
        // budget=⌊25/10⌋×10=20; хвост 5 шт остаётся на ФФ.
        const result = distributeByBoxMultipleWeighted(
            [{ name: 'A', weight: 60 }, { name: 'B', weight: 40 }],
            25, 10,
        );
        expect(result.A + result.B).toBe(20);
    });

    it('gives extra boxes to the heaviest weight (skewed shares)', () => {
        // qty=30, ppb=10, веса 90/10. needs A=27, B=3. budget=30=3 box.
        // Pass1: A=10, B=10. Pass2: остаток 10, unmet A=17≥10 → A+=10.
        const result = distributeByBoxMultipleWeighted(
            [{ name: 'A', weight: 90 }, { name: 'B', weight: 10 }],
            30, 10,
        );
        expect(result).toEqual({ A: 20, B: 10 });
    });

    it('single warehouse takes all whole boxes', () => {
        const result = distributeByBoxMultipleWeighted([{ name: 'A', weight: 100 }], 30, 10);
        expect(result).toEqual({ A: 30 });
    });

    it('normalizes arbitrary weight scale (percent vs fraction equivalent)', () => {
        const asPct = distributeByBoxMultipleWeighted(
            [{ name: 'A', weight: 75 }, { name: 'B', weight: 25 }], 40, 10,
        );
        const asFraction = distributeByBoxMultipleWeighted(
            [{ name: 'A', weight: 0.75 }, { name: 'B', weight: 0.25 }], 40, 10,
        );
        expect(asPct).toEqual(asFraction);
    });
});

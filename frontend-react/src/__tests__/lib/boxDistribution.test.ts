import { describe, it, expect } from 'vitest';
import { distributeByBoxMultiple } from '@/lib/utils/boxDistribution';

describe('distributeByBoxMultiple', () => {
    it('returns empty object when ppb<=0', () => {
        expect(distributeByBoxMultiple([{ name: 'A', need: 100 }], 100, 0)).toEqual({});
        expect(distributeByBoxMultiple([{ name: 'A', need: 100 }], 100, -5)).toEqual({});
    });

    it('returns empty object when totalCanSend<ppb', () => {
        expect(distributeByBoxMultiple([{ name: 'A', need: 100 }], 5, 10)).toEqual({});
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

    it('skips warehouse when remaining unmet < ppb', () => {
        // need=15, ppb=10 → unmet после первой коробки = 5 < ppb → больше не получит
        const result = distributeByBoxMultiple(
            [
                { name: 'A', need: 15 },
                { name: 'B', need: 100 },
            ],
            100, 10,
        );
        // Pass1: A=10, B=10 (20). Остаток 80 = 8 box. A unmet=5 (skip), B unmet=90 → всё B.
        expect(result.A).toBe(10);
        expect(result.B).toBe(90);
    });

    it('limits total to floor(totalCanSend / ppb) * ppb', () => {
        // 47 шт, ppb=10 → max 4 коробки = 40 шт
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

    it('allocates only multiples of ppb', () => {
        const result = distributeByBoxMultiple(
            [{ name: 'A', need: 23 }],
            23, 7,
        );
        // 23/7 = 3 box = 21 шт. need=23 → одна коробка не нужна (21 ≤ 23 ok)
        expect(result.A).toBe(21);
        expect(result.A % 7).toBe(0);
    });

    it('caps allocation at need (no overshoot)', () => {
        // need=15, ppb=10. Pass1: 10. Pass2: unmet=5 < ppb → стоп. Итого 10, не 20.
        const result = distributeByBoxMultiple(
            [{ name: 'A', need: 15 }],
            100, 10,
        );
        expect(result.A).toBe(10);
    });
});

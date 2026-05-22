import { describe, it, expect } from 'vitest';
import { distributeByBoxMultiple } from '@/lib/utils/boxDistribution';

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

    it('ships the sub-box tail россыпью (полные короба + остаток)', () => {
        // 47 шт, ppb=10. 4 коробки = 40 шт + хвост 7 шт россыпью → всё уходит.
        const result = distributeByBoxMultiple(
            [{ name: 'A', need: 200 }],
            47, 10,
        );
        expect(result).toEqual({ A: 47 });
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

    it('caps total at Σneed, хвост уходит россыпью (need=15, ppb=10)', () => {
        // need=15, total=100, ppb=10. budget=min(100,15)=15.
        // 1 коробка (10) + хвост 5 россыпью = 15 (не больше потребности).
        const result = distributeByBoxMultiple(
            [{ name: 'A', need: 15 }],
            100, 10,
        );
        expect(result.A).toBe(15);
    });

    it('ships полные короба + хвост россыпью (23 шт, ppb=7)', () => {
        // need=23, total=23, ppb=7. 3 коробки = 21 + хвост 2 россыпью = 23.
        const result = distributeByBoxMultiple(
            [{ name: 'A', need: 23 }],
            23, 7,
        );
        expect(result.A).toBe(23);
    });

    it('ships россыпью даже когда короба не набрать (3 шт, ppb=12 — кейс ромбсерый)', () => {
        // 3 шт, ppb=12 — целой коробки нет, но хвост всё равно уезжает россыпью.
        const result = distributeByBoxMultiple(
            [{ name: 'СПБ Шушары', need: 100 }],
            3, 12,
        );
        expect(result).toEqual({ 'СПБ Шушары': 3 });
    });

    it('ships полные короба + хвост россыпью в склад с макс. потребностью', () => {
        // total=27, ppb=10, два склада (need 50/30). budget=min(27,80)=27.
        // Pass1: A=10, B=10 (2 короба). Хвост 7 → A (unmet 40 > B unmet 20).
        const result = distributeByBoxMultiple(
            [
                { name: 'A', need: 50 },
                { name: 'B', need: 30 },
            ],
            27, 10,
        );
        expect(result).toEqual({ A: 17, B: 10 });
        expect(result.A + result.B).toBe(27);
    });

    it('хвост россыпью не превышает потребность склада (без overshoot)', () => {
        // total=8, ppb=10, два склада need 3/3. Коробов нет. budget=min(8,6)=6.
        // Россыпь по убыванию unmet, cap на need: A=3, B=3 (Σ=6, не 8).
        const result = distributeByBoxMultiple(
            [
                { name: 'A', need: 3 },
                { name: 'B', need: 3 },
            ],
            8, 10,
        );
        expect(result).toEqual({ A: 3, B: 3 });
    });

    // ─── Строгий режим (looseTail=false) — новинки cold-start ──────────────────
    // Хвост < короба НЕ уходит, остаётся на ФФ. Поведение «как раньше».
    describe('строгий режим (looseTail=false) — новинки', () => {
        it('хвост < короба остаётся на ФФ (47 шт, ppb=10 → 40)', () => {
            const result = distributeByBoxMultiple([{ name: 'A', need: 200 }], 47, 10, false);
            expect(result).toEqual({ A: 40 });
        });

        it('целой коробки не набрать → пусто (3 шт, ppb=12)', () => {
            const result = distributeByBoxMultiple([{ name: 'СПБ Шушары', need: 100 }], 3, 12, false);
            expect(result).toEqual({});
        });

        it('два склада: только целые коробки, хвост на ФФ (27 шт, ppb=10)', () => {
            const result = distributeByBoxMultiple(
                [{ name: 'A', need: 50 }, { name: 'B', need: 30 }],
                27, 10, false,
            );
            expect(result).toEqual({ A: 10, B: 10 });
            expect(result.A + result.B).toBe(20);
        });

        it('cap до короба внутри Σneed, хвост на ФФ (need=15, ppb=10 → 10)', () => {
            const result = distributeByBoxMultiple([{ name: 'A', need: 15 }], 100, 10, false);
            expect(result.A).toBe(10);
        });

        it('целые коробки распределяются по потребности как обычно (50 шт, ppb=10)', () => {
            // looseTail не влияет на саму раздачу коробов — только убирает хвост.
            const result = distributeByBoxMultiple(
                [{ name: 'A', need: 50 }, { name: 'B', need: 30 }, { name: 'C', need: 20 }],
                50, 10, false,
            );
            expect(result).toEqual({ A: 30, B: 10, C: 10 });
        });
    });
});

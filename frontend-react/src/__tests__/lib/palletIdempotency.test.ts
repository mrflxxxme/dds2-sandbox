import { describe, it, expect } from 'vitest';
import { packMonoPallets, snapToWholePallets } from '@/lib/utils/boxPallet';

// Идемпотентность + сохранение товара пакеров целых паллет. Регрессия на находки
// code-review: повторный прогон (normalizeDraft гоняется после каждой мутации) не
// должен ронять товар, который первый прогон оставил. Корень бага — паллета
// объявлялась «целой» при footprint < целого.

const sum = (o: Record<string, number>) => Object.values(o).reduce((s, v) => s + v, 0);

describe('packMonoPallets — идемпотентность и сохранение товара', () => {
    it('репро code-review: 3 моно-SKU не теряют товар на повторном прогоне', () => {
        const upp: Record<string, number> = { a: 112, b: 112, c: 64 };
        const uppOf = (k: string) => upp[k] ?? null;
        const r1 = packMonoPallets({ a: 180, b: 266, c: 196 }, uppOf);
        const r2 = packMonoPallets(r1.kept, uppOf);
        expect(sum(r2.dropped)).toBe(0);           // второй прогон ничего не роняет
        expect(r2.kept).toEqual(r1.kept);          // раскладка стабильна
    });

    it('фаззинг: 5000 случайных входов — идемпотентность + Σ сохраняется', () => {
        // Детерминированный PRNG (без Math.random — он запрещён в воркфлоу, а тут просто
        // стабильность сидов между прогонами CI).
        let seed = 123456789;
        const rnd = (n: number) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };
        for (let t = 0; t < 5000; t++) {
            const nKeys = 1 + rnd(4);
            const km: Record<string, number> = {};
            const upp: Record<string, number> = {};
            for (let i = 0; i < nKeys; i++) {
                upp[`k${i}`] = 8 + rnd(200);
                km[`k${i}`] = 1 + rnd(600);
            }
            const uppOf = (k: string) => upp[k] ?? null;
            const total = sum(km);
            const r1 = packMonoPallets(km, uppOf);
            expect(sum(r1.kept) + sum(r1.dropped)).toBe(total);   // conservation прогон 1
            const r2 = packMonoPallets(r1.kept, uppOf);
            expect(sum(r2.dropped)).toBe(0);                       // идемпотентность: 0 потерь
            expect(sum(r2.kept)).toBe(sum(r1.kept));               // ничего не утекло
        }
    });
});

describe('snapToWholePallets — идемпотентность и сохранение товара', () => {
    it('репро code-review: смешанная паллета multi-upp не теряет товар на повторе', () => {
        const upp: Record<string, number> = { k0: 48, k1: 160, k2: 160 };
        const uppOf = (k: string) => upp[k] ?? null;
        const r1 = snapToWholePallets({ k0: 35, k1: 216, k2: 261 }, uppOf);
        const r2 = snapToWholePallets(r1.kept, uppOf);
        expect(sum(r2.dropped)).toBe(0);
        expect(r2.kept).toEqual(r1.kept);
    });

    it('фаззинг: 5000 случайных смешанных паллет — идемпотентность + Σ', () => {
        let seed = 987654321;
        const rnd = (n: number) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };
        for (let t = 0; t < 5000; t++) {
            const nKeys = 1 + rnd(4);
            const km: Record<string, number> = {};
            const upp: Record<string, number> = {};
            for (let i = 0; i < nKeys; i++) {
                upp[`k${i}`] = 8 + rnd(200);
                km[`k${i}`] = 1 + rnd(800);
            }
            const uppOf = (k: string) => upp[k] ?? null;
            const total = sum(km);
            const r1 = snapToWholePallets(km, uppOf);
            expect(sum(r1.kept) + sum(r1.dropped)).toBe(total);
            const r2 = snapToWholePallets(r1.kept, uppOf);
            expect(sum(r2.dropped)).toBe(0);
            expect(sum(r2.kept)).toBe(sum(r1.kept));
        }
    });
});

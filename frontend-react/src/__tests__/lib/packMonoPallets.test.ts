import { describe, it, expect } from 'vitest';
import { packMonoPallets } from '@/lib/utils/boxPallet';

// Хелперы суммирования.
const sum = (o: Record<string, number>) => Object.values(o).reduce((s, v) => s + v, 0);

describe('packMonoPallets — целые моно-паллеты, ≤3 артикула на паллете', () => {
    it('один SKU, ровно целые паллеты → всё оставляем, ничего не дропаем', () => {
        const upp: Record<string, number> = { a: 100 };
        const res = packMonoPallets({ a: 200 }, (k) => upp[k] ?? null);
        expect(res.kept).toEqual({ a: 200 });
        expect(res.dropped).toEqual({});
    });

    it('один SKU 1.4 паллеты → 1 целая едет, хвост 0.4 на ФФ', () => {
        const upp: Record<string, number> = { a: 100 };
        const res = packMonoPallets({ a: 140 }, (k) => upp[k] ?? null);
        expect(res.kept).toEqual({ a: 100 });
        expect(res.dropped).toEqual({ a: 40 });
    });

    it('3 мелких SKU по 0.4 паллеты → собираются в 1 целую (≤3), остаток на ФФ', () => {
        const upp: Record<string, number> = { a: 100, b: 100, c: 100 };
        const res = packMonoPallets({ a: 40, b: 40, c: 40 }, (k) => upp[k] ?? null);
        // Σ crumbs = 120 → 1 целая паллета (100), 20 остаётся на ФФ.
        expect(sum(res.kept)).toBe(100);
        expect(sum(res.dropped)).toBe(20);
    });

    it('кап 3 артикула: 4 SKU по 0.3 — троих не хватает на паллету → всё на ФФ', () => {
        const upp: Record<string, number> = { a: 100, b: 100, c: 100, d: 100 };
        const res = packMonoPallets({ a: 30, b: 30, c: 30, d: 30 }, (k) => upp[k] ?? null);
        // 3 крупнейших = 0.9 < 1.0 паллеты → собрать целую нельзя → ничего не едет.
        expect(res.kept).toEqual({});
        expect(sum(res.dropped)).toBe(120);
    });

    it('целые паллеты SKU остаются однотоварными, миксуются только хвосты', () => {
        const upp: Record<string, number> = { a: 100, b: 100, c: 100 };
        // a = 2 целых + 0.4; b,c по 0.4. Хвосты 0.4+0.4+0.4=1.2 → 1 общая паллета.
        const res = packMonoPallets({ a: 240, b: 40, c: 40 }, (k) => upp[k] ?? null);
        expect(sum(res.kept)).toBe(300); // 200 (целые a) + 100 (общая паллета)
        expect(sum(res.dropped)).toBe(20);
        expect(res.kept.a).toBeGreaterThanOrEqual(200); // минимум свои 2 целые
    });

    it('разные upp: 0.6 + 0.4 = ровно 1 паллета двумя артикулами', () => {
        const upp: Record<string, number> = { a: 100, b: 50 };
        // a: 60/100 = 0.6 паллеты; b: 20/50 = 0.4 паллеты → Σ = 1.0.
        const res = packMonoPallets({ a: 60, b: 20 }, (k) => upp[k] ?? null);
        expect(res.kept).toEqual({ a: 60, b: 20 });
        expect(res.dropped).toEqual({});
    });

    it('SKU без геометрии (upp=null) — не палетизируем, проходит как есть', () => {
        const res = packMonoPallets({ a: 55 }, () => null);
        expect(res.kept).toEqual({ a: 55 });
        expect(res.dropped).toEqual({});
    });

    it('идемпотентность: повторный прогон по kept не меняет раскладку', () => {
        const upp: Record<string, number> = { a: 100, b: 100, c: 100 };
        const uppOf = (k: string) => upp[k] ?? null;
        const r1 = packMonoPallets({ a: 240, b: 40, c: 40 }, uppOf);
        const r2 = packMonoPallets(r1.kept, uppOf);
        expect(r2.kept).toEqual(r1.kept);
        expect(sum(r2.dropped)).toBe(0);
    });

    it('баланс: Σ(kept)+Σ(dropped) == Σ(вход) всегда', () => {
        const upp: Record<string, number> = { a: 100, b: 70, c: 40, d: 100 };
        const input = { a: 137, b: 55, c: 33, d: 240 };
        const res = packMonoPallets(input, (k) => upp[k] ?? null);
        expect(sum(res.kept) + sum(res.dropped)).toBe(sum(input));
    });
});

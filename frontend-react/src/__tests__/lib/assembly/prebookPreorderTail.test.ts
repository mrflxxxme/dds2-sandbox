/**
 * Инвариант под-вкладки «Предзаявка» (МОНО): «Создать предзаявку»/«Убрать неполную»
 * бронируют/оставляют ТОЛЬКО ЦЕЛЫЕ паллеты, а неполный хвост отделяют — тем же
 * авторитетным сплитом `consolidatePrebookWholePallets` (моно ≤3 арт), что и коммит.
 *
 * Ключевое: показ группы (`whole` = packMonoPallets.pallets.length) и бронирование
 * (trimLinesToWholePallets → packMonoPallets) идут через ОДИН упаковщик с ОДНОЙ
 * геометрией → «N целых» на UI == забронированному. Здесь проверяем сам сплит:
 * консервация Σ, целая часть кратна паллете, хвост < паллеты.
 */
import { describe, it, expect } from 'vitest';
import { consolidatePrebookWholePallets, type NormalizeDraftCtx } from '@/lib/utils/normalizeDraft';
import type { AssemblyDraftRow } from '@/types/api';

// Геометрия: ppb=10, override boxes/pallet=10 → upp=100 (целая моно-паллета = 100 шт).
const PPB = 10;
const UPP = 100;
const ctx: NormalizeDraftCtx = {
    ppbOf: () => PPB,
    boxSizeOf: () => '40x40x40',
    overrides: { '40x40x40': 10 },
    freeByNm: {},
};
const FF = '5';
const WB = 'Казань';

const mono = (nm: number, qty: number): AssemblyDraftRow => ({
    nm_id: nm, barcode: `bc${nm}`, vendor_code: `v${nm}`,
    src: { [FF]: qty }, tgt: { [WB]: qty }, package_type: 'MONOPALLET',
});
const sumTgt = (rs: AssemblyDraftRow[]): number =>
    rs.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);

describe('предзаявка: сплит целые/хвост (моно)', () => {
    it('1.5 паллеты (3 арт×50) → 1 целая (100 шт) + хвост (50 шт), Σ сохранена', () => {
        const dir = [mono(1, 50), mono(2, 50), mono(3, 50)]; // 150 шт = 1.5 паллеты
        const split = consolidatePrebookWholePallets(dir, ctx);
        const whole = sumTgt(split.toDraft);
        const tail = sumTgt(split.prebook);
        expect(whole).toBe(UPP);          // ровно 1 целая паллета в бронь
        expect(tail).toBe(50);            // неполный хвост остаётся
        expect(whole + tail).toBe(150);   // консервация товара
        expect(whole % UPP).toBe(0);      // целая часть кратна паллете
        expect(tail).toBeLessThan(UPP);   // хвост < паллеты
    });

    it('ровно 1.0 паллета (2 арт×50) → вся в бронь, хвоста нет', () => {
        const dir = [mono(1, 50), mono(2, 50)]; // 100 шт = 1.0
        const split = consolidatePrebookWholePallets(dir, ctx);
        expect(sumTgt(split.toDraft)).toBe(UPP);
        expect(sumTgt(split.prebook)).toBe(0);
    });

    it('2.3 паллеты → 2 целых (200 шт) + хвост (30 шт)', () => {
        const dir = [mono(1, 100), mono(2, 100), mono(3, 30)]; // 230 = 2.3
        const split = consolidatePrebookWholePallets(dir, ctx);
        const whole = sumTgt(split.toDraft);
        const tail = sumTgt(split.prebook);
        expect(whole).toBe(200);
        expect(tail).toBe(30);
        expect(whole + tail).toBe(230);
        expect(whole % UPP).toBe(0);
    });

    it('меньше паллеты (2 арт×20) → целых нет, всё в хвост', () => {
        const dir = [mono(1, 20), mono(2, 20)]; // 40 = 0.4
        const split = consolidatePrebookWholePallets(dir, ctx);
        expect(sumTgt(split.toDraft)).toBe(0);
        expect(sumTgt(split.prebook)).toBe(40);
    });
});

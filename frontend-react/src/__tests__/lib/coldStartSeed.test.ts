/**
 * seedNewcomerWholeBoxes — засев новинок целыми коробами по якорям округов
 * + гарантия СЗФО (аудит 2026-07-09): при партии ≥ NW_GUARANTEE_MIN_BOXES
 * коробов открытый непокрытый якорь СЗФО получает минимум 1 короб (доля ~9%
 * проигрывала largest-remainder крупным ФО до ~6-8 коробов).
 */
import { describe, expect, it } from 'vitest';
import { NW_GUARANTEE_MIN_BOXES, seedNewcomerWholeBoxes, type SeedAnchor } from '@/lib/assembly/coldStartSeed';

// Реалистичные якоря (share_pct ≈ доли ФО из cold_start_table.main_warehouses).
const ANCHORS: SeedAnchor[] = [
    { warehouse: 'Электросталь', share_pct: 26, district: 'central' },
    { warehouse: 'Екатеринбург - Перспективная 14', share_pct: 22, district: 'ural' },
    { warehouse: 'Краснодар', share_pct: 18, district: 'south_caucasus' },
    { warehouse: 'Казань', share_pct: 18, district: 'volga' },
    { warehouse: 'СПБ Шушары', share_pct: 9, district: 'northwest' },
];

const sum = (r: Record<string, number>) => Object.values(r).reduce((s, v) => s + v, 0);

describe('seedNewcomerWholeBoxes — база', () => {
    it('раскладывает целыми коробами, хвост остаётся на ФФ', () => {
        const out = seedNewcomerWholeBoxes(47, 10, ANCHORS);
        expect(sum(out) % 10).toBe(0);
        expect(sum(out)).toBeLessThanOrEqual(40);
    });

    it('без кратности или при партии < короба — пусто', () => {
        expect(seedNewcomerWholeBoxes(50, 0, ANCHORS)).toEqual({});
        expect(seedNewcomerWholeBoxes(7, 10, ANCHORS)).toEqual({});
    });

    it('перетаренный якорь (existing ≥ доли) не получает коробов', () => {
        const anchors = ANCHORS.map(a =>
            a.district === 'central' ? { ...a, existing: 1000 } : a,
        );
        const out = seedNewcomerWholeBoxes(40, 10, anchors);
        expect(out['Электросталь']).toBeUndefined();
    });
});

describe('seedNewcomerWholeBoxes — гарантия СЗФО', () => {
    it(`партия ${NW_GUARANTEE_MIN_BOXES} короба → СЗФО получает ровно 1 короб`, () => {
        // Без гарантии largest-remainder отдал бы все 4 короба крупным ФО
        // (want СЗФО = 4×0.097 ≈ 0.39 — проигрывает остаткам .04/.88/.72/.72).
        const out = seedNewcomerWholeBoxes(NW_GUARANTEE_MIN_BOXES * 10, 10, ANCHORS);
        expect(out['СПБ Шушары']).toBe(10);
        expect(sum(out)).toBe(NW_GUARANTEE_MIN_BOXES * 10);
    });

    it('партия 3 короба (ниже порога) → СЗФО ноль, форма не искажается', () => {
        const out = seedNewcomerWholeBoxes(30, 10, ANCHORS);
        expect(out['СПБ Шушары']).toBeUndefined();
        expect(sum(out)).toBe(30);
    });

    it('СЗФО уже покрыт стоком/транзитом (sf=0) → гарантия не вмешивается', () => {
        const anchors = ANCHORS.map(a =>
            a.district === 'northwest' ? { ...a, existing: 500 } : a,
        );
        const out = seedNewcomerWholeBoxes(40, 10, anchors);
        expect(out['СПБ Шушары']).toBeUndefined();
        // shipTotal сжался до Σshortfall остальных (36.1) → 3 короба.
        expect(sum(out)).toBe(30);
    });

    it('без district-поля (старые callers) — прежнее поведение, гарантии нет', () => {
        const legacy = ANCHORS.map(({ warehouse, share_pct }) => ({ warehouse, share_pct }));
        const out = seedNewcomerWholeBoxes(40, 10, legacy);
        expect(out['СПБ Шушары']).toBeUndefined();
        expect(sum(out)).toBe(40);
    });

    it('на большой партии СЗФО и так получает короб — гарантия не дублирует', () => {
        const out = seedNewcomerWholeBoxes(200, 10, ANCHORS);
        expect(out['СПБ Шушары']).toBeGreaterThanOrEqual(10);
        expect(sum(out)).toBe(200);
    });

    it('Σ коробов сохраняется: донор отдаёт ровно 1 короб', () => {
        const withGuarantee = seedNewcomerWholeBoxes(40, 10, ANCHORS);
        const legacy = seedNewcomerWholeBoxes(
            40, 10, ANCHORS.map(({ warehouse, share_pct }) => ({ warehouse, share_pct })),
        );
        expect(sum(withGuarantee)).toBe(sum(legacy));
    });
});

import { describe, it, expect } from 'vitest';
import { seedNewcomerWholeBoxes, type SeedAnchor } from '@/lib/assembly/coldStartSeed';

const sum = (o: Record<string, number>) => Object.values(o).reduce((s, v) => s + v, 0);

// Топ-анкеры округов (как в проде project 4), по доле.
const ANCHORS: SeedAnchor[] = [
    { warehouse: 'Электросталь', share_pct: 16.27 },
    { warehouse: 'Алексин (Тула)', share_pct: 11.69 },
    { warehouse: 'Невинномысск', share_pct: 10.84 },
    { warehouse: 'Казань', share_pct: 10.81 },
    { warehouse: 'Краснодар', share_pct: 9.03 },
    { warehouse: 'Подольск', share_pct: 8.5 },
    { warehouse: 'Екатеринбург', share_pct: 7.66 },
    { warehouse: 'Самара', share_pct: 6.48 },
    { warehouse: 'Сарапул', share_pct: 4.79 },
    { warehouse: 'Пенза', share_pct: 4.32 },
    { warehouse: 'Волгоград', share_pct: 3.61 },
    { warehouse: 'СПБ Шушары', share_pct: 0 },
];

describe('seedNewcomerWholeBoxes · засев новинки целыми коробами по топ-анкерам', () => {
    it('STYL_lipa: 160 шт, кратно 40 → 4 короба на топ-4 склада по доле', () => {
        const seed = seedNewcomerWholeBoxes(160, 40, ANCHORS);
        expect(sum(seed)).toBe(160);                 // все 4 короба уехали
        expect(Object.keys(seed).sort()).toEqual(['Алексин (Тула)', 'Казань', 'Невинномысск', 'Электросталь'].sort());
        for (const v of Object.values(seed)) expect(v).toBe(40); // по 1 коробу
    });

    it('каждый склад получает количество, кратное коробу', () => {
        const seed = seedNewcomerWholeBoxes(400, 40, ANCHORS); // 10 коробов
        expect(sum(seed)).toBe(400);
        for (const v of Object.values(seed)) expect(v % 40).toBe(0);
        // больше коробов у складов с большей долей
        expect(seed['Электросталь']).toBeGreaterThanOrEqual(seed['Сарапул'] || 0);
    });

    it('меньше короба → пусто (хвост остаётся на ФФ)', () => {
        expect(seedNewcomerWholeBoxes(39, 40, ANCHORS)).toEqual({});
        expect(seedNewcomerWholeBoxes(160, 0, ANCHORS)).toEqual({});  // нет кратности
        expect(seedNewcomerWholeBoxes(160, null, ANCHORS)).toEqual({});
        expect(seedNewcomerWholeBoxes(160, 40, [])).toEqual({});       // нет анкеров
    });

    it('хвост коробов сверх целого числа долей не теряется (Σ = floor(qty/ppb)*ppb)', () => {
        const seed = seedNewcomerWholeBoxes(175, 40, ANCHORS); // floor(175/40)=4 короба=160
        expect(sum(seed)).toBe(160);
    });

    it('нулевые доли → равномерно по топ-складам по кругу', () => {
        const flat: SeedAnchor[] = [
            { warehouse: 'A', share_pct: 0 }, { warehouse: 'B', share_pct: 0 }, { warehouse: 'C', share_pct: 0 },
        ];
        const seed = seedNewcomerWholeBoxes(100, 40, flat); // 2 короба
        expect(sum(seed)).toBe(80);
        expect(Object.keys(seed).length).toBe(2);
    });

    it('coverage: топ-склад уже затарен/едет → туда НЕ слать (не перетарить)', () => {
        // Электросталь (топ по доле) уже имеет 500 → её target ниже → shortfall=0.
        const withCov = ANCHORS.map(a => a.warehouse === 'Электросталь' ? { ...a, existing: 500 } : a);
        const seed = seedNewcomerWholeBoxes(160, 40, withCov);
        expect(seed['Электросталь'] ?? 0).toBe(0);   // перетаренный склад пропущен
        expect(sum(seed)).toBeGreaterThan(0);          // остальное уехало на другие анкеры
    });

    it('coverage: всё покрыто → пусто (остаётся на ФФ)', () => {
        const allCovered = ANCHORS.map(a => ({ ...a, existing: 100000 }));
        expect(seedNewcomerWholeBoxes(160, 40, allCovered)).toEqual({});
    });

    it('coverage=0 эквивалентно раскладке по доле (обратная совместимость)', () => {
        const a = seedNewcomerWholeBoxes(160, 40, ANCHORS);
        const b = seedNewcomerWholeBoxes(160, 40, ANCHORS.map(x => ({ ...x, existing: 0 })));
        expect(a).toEqual(b);
    });
});

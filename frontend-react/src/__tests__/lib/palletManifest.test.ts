import { describe, it, expect } from 'vitest';
import { buildPalletManifest, type ManifestLine } from '@/lib/utils/palletManifest';
import { palletsForLines, type PalletLine } from '@/lib/utils/boxPallet';

// 60×40×40 @180 → 16 кор/паллету; ppb=10 → 160 шт/паллету.
const SIZE = '60x40x40';
const PPB = 10;
const H = 180;

/** ManifestLine-builder. */
function line(nmId: number, units: number, over: Partial<ManifestLine> = {}): ManifestLine {
    return { nmId, vendorCode: `V${nmId}`, units, boxSize: SIZE, ppb: PPB, ...over };
}

/** Те же линии для аггрегата palletsForLines. */
function toPalletLines(lines: ManifestLine[]): PalletLine[] {
    return lines.map(l => ({ units: l.units, boxQty: l.ppb, boxSize: l.boxSize }));
}

const totalUnits = (m: { pallets: { items: { units: number }[] }[]; unpalletized: { units: number }[] }) =>
    m.pallets.reduce((s, p) => s + p.items.reduce((a, i) => a + i.units, 0), 0)
    + m.unpalletized.reduce((s, i) => s + i.units, 0);

describe('buildPalletManifest · mode box (смешанные паллеты)', () => {
    it('один SKU на 2 паллеты + хвост', () => {
        // 350 units = 2.1875 паллеты → ⌈⌉ = 3 паллеты.
        const m = buildPalletManifest([line(1, 350)], { mode: 'box', maxHeightCm: H });
        expect(m.pallets.length).toBe(3);
        expect(m.unpalletized).toEqual([]);
        expect(totalUnits(m)).toBe(350);
    });

    it('МИКС двух SKU на одной паллете', () => {
        // A=100 (0.625) + B=80 (0.5) = 1.125 → 2 паллеты. Микс на первой.
        const m = buildPalletManifest([line(1, 100), line(2, 80)], { mode: 'box', maxHeightCm: H });
        expect(m.pallets.length).toBe(2);
        // Первая паллета содержит оба SKU (смешанная).
        expect(m.pallets[0].items.length).toBe(2);
        expect(totalUnits(m)).toBe(180);
    });

    it('fillPct первой полной паллеты ≈ 1, последней — дробная', () => {
        const m = buildPalletManifest([line(1, 200)], { mode: 'box', maxHeightCm: H });
        // 200 = 1.25 → 2 паллеты: первая полная (160 → 1.0), вторая 40 (0.25).
        expect(m.pallets.length).toBe(2);
        expect(m.pallets[0].fillPct).toBeCloseTo(1, 6);
        expect(m.pallets[1].fillPct).toBeCloseTo(0.25, 6);
    });

    it('boxes считаются (units / ppb, округление вверх)', () => {
        const m = buildPalletManifest([line(1, 160)], { mode: 'box', maxHeightCm: H });
        expect(m.pallets[0].items[0].boxes).toBe(16); // 160/10
    });
});

describe('buildPalletManifest · mode mono (≤3 артикула на паллету)', () => {
    it('мелкие моно-SKU собираются на одну паллету (≤3 артикула)', () => {
        // 3 SKU по 40 шт (0.25 паллеты) → 1 общая паллета на 3 артикула.
        const m = buildPalletManifest([line(1, 40), line(2, 40), line(3, 40)], { mode: 'mono', maxHeightCm: H });
        expect(m.pallets.length).toBe(1);
        expect(m.pallets[0].items.length).toBe(3);
        expect(totalUnits(m)).toBe(120);
    });

    it('реконсиляция: Σ паллет манифеста == palletsForLines(mono)', () => {
        const lines = [line(1, 200), line(2, 160)];
        const m = buildPalletManifest(lines, { mode: 'mono', maxHeightCm: H });
        expect(m.pallets.length).toBe(palletsForLines(toPalletLines(lines), H, 'mono').pallets);
        expect(totalUnits(m)).toBe(360);
    });

    it('последняя паллета SKU неполная — fillPct дробный', () => {
        const m = buildPalletManifest([line(1, 200)], { mode: 'mono', maxHeightCm: H });
        expect(m.pallets.length).toBe(2);
        expect(m.pallets[0].fillPct).toBeCloseTo(1, 6);
        expect(m.pallets[1].fillPct).toBeCloseTo(40 / 160, 6);
    });
});

describe('buildPalletManifest · без габаритов → unpalletized', () => {
    it('SKU без box_size попадает в unpalletized, не теряется', () => {
        const m = buildPalletManifest(
            [line(1, 160), line(2, 99, { boxSize: null })],
            { mode: 'box', maxHeightCm: H },
        );
        expect(m.pallets.length).toBe(1); // только палетизируемый SKU 1
        expect(m.unpalletized.length).toBe(1);
        expect(m.unpalletized[0].nmId).toBe(2);
        expect(m.unpalletized[0].units).toBe(99);
        expect(totalUnits(m)).toBe(259); // ничего не потеряно
    });

    it('SKU без ppb → unpalletized', () => {
        const m = buildPalletManifest([line(1, 50, { ppb: null })], { mode: 'mono', maxHeightCm: H });
        expect(m.pallets).toEqual([]);
        expect(m.unpalletized.length).toBe(1);
        expect(m.unpalletized[0].units).toBe(50);
    });
});

describe('buildPalletManifest · реконсиляция с palletsForLines', () => {
    const cases: { name: string; lines: ManifestLine[] }[] = [
        { name: 'один SKU 160', lines: [line(1, 160)] },
        { name: 'один SKU 350', lines: [line(1, 350)] },
        { name: 'два SKU микс', lines: [line(1, 100), line(2, 80)] },
        { name: 'три SKU', lines: [line(1, 230), line(2, 90), line(3, 170)] },
        { name: 'малая загрузка', lines: [line(1, 20)] },
        { name: 'много SKU дробные', lines: [line(1, 47), line(2, 153), line(3, 211), line(4, 99)] },
        {
            name: 'с непалетизируемым (тот в unknown по обе стороны)',
            lines: [line(1, 160), line(2, 80), line(3, 50, { boxSize: null })],
        },
    ];

    it.each(cases)('box: Σ pallets.length === palletsForLines.pallets — $name', ({ lines }) => {
        const m = buildPalletManifest(lines, { mode: 'box', maxHeightCm: H });
        const agg = palletsForLines(toPalletLines(lines), H, 'box');
        expect(m.pallets.length).toBe(agg.pallets);
    });

    it.each(cases)('mono: Σ pallets.length === palletsForLines.pallets — $name', ({ lines }) => {
        const m = buildPalletManifest(lines, { mode: 'mono', maxHeightCm: H });
        const agg = palletsForLines(toPalletLines(lines), H, 'mono');
        expect(m.pallets.length).toBe(agg.pallets);
    });

    it('непалетизируемые units идут в unpalletized, как unknownUnits у аггрегата', () => {
        const lines = [line(1, 160), line(2, 50, { boxSize: null }), line(3, 30, { ppb: null })];
        const m = buildPalletManifest(lines, { mode: 'box', maxHeightCm: H });
        const agg = palletsForLines(toPalletLines(lines), H, 'box');
        const unp = m.unpalletized.reduce((s, i) => s + i.units, 0);
        expect(unp).toBe(agg.unknownUnits);
        expect(m.unpalletized.length).toBe(agg.unknownLines);
    });
});

describe('buildPalletManifest · fillPct корректность', () => {
    it('ровно целая паллета → fillPct 1.0', () => {
        const m = buildPalletManifest([line(1, 160)], { mode: 'box', maxHeightCm: H });
        expect(m.pallets.length).toBe(1);
        expect(m.pallets[0].fillPct).toBeCloseTo(1, 6);
    });

    it('четверть паллеты → 0.25', () => {
        const m = buildPalletManifest([line(1, 40)], { mode: 'box', maxHeightCm: H });
        expect(m.pallets[0].fillPct).toBeCloseTo(0.25, 6);
    });

    it('fillPct ∈ (0, 1] для всех паллет', () => {
        const m = buildPalletManifest(
            [line(1, 230), line(2, 90), line(3, 170)],
            { mode: 'box', maxHeightCm: H },
        );
        for (const p of m.pallets) {
            expect(p.fillPct).toBeGreaterThan(0);
            expect(p.fillPct).toBeLessThanOrEqual(1 + 1e-9);
        }
    });
});

describe('buildPalletManifest · пустой / нулевой ввод', () => {
    it('пустой список → пусто', () => {
        const m = buildPalletManifest([], { mode: 'box', maxHeightCm: H });
        expect(m.pallets).toEqual([]);
        expect(m.unpalletized).toEqual([]);
    });

    it('нулевые units игнорируются', () => {
        const m = buildPalletManifest([line(1, 0), line(2, 160)], { mode: 'box', maxHeightCm: H });
        expect(m.pallets.length).toBe(1);
        expect(totalUnits(m)).toBe(160);
    });

    it('реконсиляция держится и на нулях/пустых', () => {
        const lines = [line(1, 0)];
        const m = buildPalletManifest(lines, { mode: 'box', maxHeightCm: H });
        const agg = palletsForLines(toPalletLines(lines), H, 'box');
        expect(m.pallets.length).toBe(agg.pallets); // обе 0
    });
});

import { describe, it, expect } from 'vitest';
import { buildPalletManifest, type ManifestLine } from '@/lib/utils/palletManifest';
import { palletsForLines, snapToWholePallets, type PalletLine } from '@/lib/utils/boxPallet';

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
    it('моно-SKU собираются в ОДНУ целую паллету (≤3 артикула)', () => {
        // 3 SKU вместе на ЦЕЛУЮ паллету (60+60+40=160, cap 160) → 1 паллета, 3 артикула.
        const m = buildPalletManifest([line(1, 60), line(2, 60), line(3, 40)], { mode: 'mono', maxHeightCm: H });
        expect(m.pallets.length).toBe(1);
        expect(m.pallets[0].items.length).toBe(3);
        expect(totalUnits(m)).toBe(160);
    });

    it('мелкие моно-SKU НЕ на целую (Σ0.75) → 0 паллет, всё в «без целой» (предбронь)', () => {
        // 3 SKU по 40 (0.25 каждая, Σ0.75 < 1) → целой нет → 0 паллет, штуки не потеряны.
        const m = buildPalletManifest([line(1, 40), line(2, 40), line(3, 40)], { mode: 'mono', maxHeightCm: H });
        expect(m.pallets.length).toBe(0);
        expect(totalUnits(m)).toBe(120); // 120 шт → unpalletized (уедут в предбронь «Дозабить»)
    });

    it('реконсиляция: Σ паллет манифеста == palletsForLines(mono)', () => {
        const lines = [line(1, 200), line(2, 160)];
        const m = buildPalletManifest(lines, { mode: 'mono', maxHeightCm: H });
        expect(m.pallets.length).toBe(palletsForLines(toPalletLines(lines), H, 'mono').pallets);
        expect(totalUnits(m)).toBe(360);
    });

    it('под-паллетный хвост моно — не отдельная неполная паллета, а «без целой» (предбронь)', () => {
        // 200 (cap 160) = 1 ЦЕЛАЯ (160) + хвост 40. Строго целые: хвост НЕ рисуется
        // отдельной неполной паллетой, а уходит в «без целой» (→ предбронь). Штуки целы.
        const m = buildPalletManifest([line(1, 200)], { mode: 'mono', maxHeightCm: H });
        expect(m.pallets.length).toBe(1);
        expect(m.pallets[0].fillPct).toBeCloseTo(1, 6);
        expect(m.unpalletized.length).toBe(1);
        expect(m.unpalletized[0].units).toBe(40);
        expect(totalUnits(m)).toBe(200);
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
        // 500 = 3.125 пал: излишек больше короба (10/160) — счётчик и манифест сходятся.
        // Суб-коробочный сливер (490 = 3.0625) разнёс бы их: счётчик округляет вниз
        // (tol = короб, зеркало короб-гранулярного snapToWholePallets), манифест честно
        // строит +1 паллету — но в черновике такого состояния не бывает (self-heal
        // срезает сливер в предбронь); см. отдельный пост-snap тест ниже.
        { name: 'три SKU', lines: [line(1, 230), line(2, 90), line(3, 180)] },
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

    it('суб-коробочный сливер (490 = 3.0625 пал): после snap показы сходятся на 3', () => {
        // Пайплайн-инвариант: черновик всегда пост-snap; сливер-короб уходит в предбронь,
        // а не в 4-ю паллету — манифест и счётчик согласованы на реальном состоянии.
        const snap = snapToWholePallets({ 1: 230, 2: 90, 3: 170 }, () => 160, () => PPB);
        const lines = Object.entries(snap.kept).map(([k, u]) => line(Number(k), u));
        const m = buildPalletManifest(lines, { mode: 'box', maxHeightCm: H });
        const agg = palletsForLines(toPalletLines(lines), H, 'box');
        expect(m.pallets.length).toBe(agg.pallets);
        expect(agg.pallets).toBe(3);
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

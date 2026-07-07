/**
 * КРОСС-НАПРАВЛЕНЧЕСКОЕ РЕЗЕРВИРОВАНИЕ дозабора предброни (`buildPrebookGroups`).
 *
 * Баг: свободный пул ФФ (`freeBoxesByFf`) считался один раз и предлагался КАЖДОМУ
 * направлению на этом ФФ целиком — один и тот же артикул «дозаберётся ×N» показывался
 * во всех отгрузках сразу. Физически коробов столько нет → пересорт (двойное обещание).
 *
 * Фикс: пул резервируется между направлениями — ближайшее к целой паллете (наим.
 * shortfall) получает дефицитный сток первым; следующему тот же короб уже не предлагается.
 */
import { describe, it, expect } from 'vitest';
import { buildPrebookGroups, type PrebookGroupsSources } from '@/lib/assembly/buildPrebookGroups';
import type { AssemblyDraftRow } from '@/types/api';

// Геометрия: ppb=10, override boxes/pallet=10 → upp=100 для всех SKU.
const PPB = 10;
const BOX = '40x40x40';
const OVERRIDES = { [BOX]: 10 };
const FF = 5;

const baseSources = (prebook: AssemblyDraftRow[], articles: PrebookGroupsSources['articles']): PrebookGroupsSources => ({
    prebook,
    usedRows: [],
    articles,
    ffName: (id) => (id === FF ? 'Газпром' : `ФФ${id}`),
    ppbOf: () => PPB,
    ppbAt: () => PPB,
    boxSizeOf: () => BOX,
    palletOverrides: OVERRIDES,
});

const row = (nm: number, wb: string, qty: number): AssemblyDraftRow => ({
    nm_id: nm, barcode: `bc${nm}`, vendor_code: `V${nm}`, src: { [FF]: qty }, tgt: { [wb]: qty }, package_type: 'BOX',
});

describe('buildPrebookGroups: дозабор не двоит один короб между направлениями', () => {
    it('дефицитный свободный SKU предлагается ТОЛЬКО одному направлению (ближайшему к целой)', () => {
        // Две отгрузки на ФФ «Газпром»: Казань недобор 0.15 паллеты (85шт), Москва 0.20 (80шт).
        // Свободный кандидат nm=3 — всего 2 короба (20шт). Обоим нужно ~2 короба, но их 2 всего.
        const prebook = [row(1, 'Казань', 85), row(2, 'Москва', 80)];
        const articles: PrebookGroupsSources['articles'] = [
            { nm_id: 1, vendor_code: 'V1', rfStocks: { [FF]: 85 } }, // весь в предброни → 0 свободно
            { nm_id: 2, vendor_code: 'V2', rfStocks: { [FF]: 80 } }, // весь в предброни → 0 свободно
            { nm_id: 3, vendor_code: 'V3', rfStocks: { [FF]: 20 } }, // 2 короба свободно
        ];
        const groups = buildPrebookGroups(baseSources(prebook, articles));
        const kazan = groups.find(g => g.wb === 'Казань')!;
        const moscow = groups.find(g => g.wb === 'Москва')!;

        // Казань ближе к целой (shortfall 0.15 < 0.20) → берёт дефицитный nm=3 первой.
        expect(kazan.topUp).not.toBeNull();
        expect(kazan.topUp!.candidates.map(c => c.nmId)).toContain(3);
        const kazanBoxes3 = kazan.topUp!.candidates.filter(c => c.nmId === 3).reduce((s, c) => s + c.boxes, 0);
        expect(kazanBoxes3).toBe(2);

        // Москве nm=3 уже НЕ предлагается (зарезервирован Казанью) — нечем → topUp null.
        const moscowBoxes3 = moscow.topUp?.candidates.filter(c => c.nmId === 3).reduce((s, c) => s + c.boxes, 0) ?? 0;
        expect(moscowBoxes3).toBe(0);

        // Консервация: суммарно обещано по nm=3 не больше физически свободного (2 короба).
        const promised3 = groups.reduce((s, g) => s + (g.topUp?.candidates.filter(c => c.nmId === 3).reduce((a, c) => a + c.boxes, 0) ?? 0), 0);
        expect(promised3).toBeLessThanOrEqual(2);
    });

    it('хватает на обоих: пул делится, суммарно не превышает свободный сток', () => {
        // nm=3 свободно 4 короба (40шт) — хватает обоим (по 2).
        const prebook = [row(1, 'Казань', 85), row(2, 'Москва', 80)];
        const articles: PrebookGroupsSources['articles'] = [
            { nm_id: 1, vendor_code: 'V1', rfStocks: { [FF]: 85 } },
            { nm_id: 2, vendor_code: 'V2', rfStocks: { [FF]: 80 } },
            { nm_id: 3, vendor_code: 'V3', rfStocks: { [FF]: 40 } },
        ];
        const groups = buildPrebookGroups(baseSources(prebook, articles));
        const promised3 = groups.reduce((s, g) => s + (g.topUp?.candidates.filter(c => c.nmId === 3).reduce((a, c) => a + c.boxes, 0) ?? 0), 0);
        expect(promised3).toBeLessThanOrEqual(4);
        // Оба направления дозаберутся.
        expect(groups.find(g => g.wb === 'Казань')!.topUp).not.toBeNull();
        expect(groups.find(g => g.wb === 'Москва')!.topUp).not.toBeNull();
    });
});

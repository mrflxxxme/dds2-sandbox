/**
 * Классы совместимости в предброни: BOX-карточка направления режется на срезы
 * per-класс, дозабор предлагает ТОЛЬКО кандидатов своего класса, общий пул ФФ
 * резервируется между всеми срезами/направлениями (короб не обещается дважды).
 */
import { describe, it, expect } from 'vitest';
import { buildPrebookGroups, type PrebookGroupsSources } from '@/lib/assembly/buildPrebookGroups';
import { buildCategoryOf, buildCompatClassOf, classLabelOf } from '@/lib/assembly/categoryCompat';
import type { AssemblyDraftRow, PalletCategoryCompat } from '@/types/api';

const RULES: PalletCategoryCompat = {
    enabled: true,
    groups: [{ name: 'текстиль', categories: ['Чехлы', 'Пледы'] }],
};
const SUBJECTS: Record<number, string> = { 1: 'Чехлы', 2: 'Пледы', 3: 'Ковры' };
const categoryOf = buildCategoryOf({}, (nm) => SUBJECTS[nm]);
const classOf = buildCompatClassOf(RULES, categoryOf);

const SIZE = '50x50x50';
const PPB = 10;
// override 10 коробов/паллету × ppb 10 = 100 шт/паллета
const OVERRIDES = { [SIZE]: 10 };

const row = (nmId: number, qty: number): AssemblyDraftRow => ({
    nm_id: nmId, barcode: `bc${nmId}`, vendor_code: `v${nmId}`,
    src: { '1': qty }, tgt: { 'Казань': qty }, package_type: 'BOX',
});

const sources = (withClasses: boolean): PrebookGroupsSources => ({
    prebook: [row(1, 50), row(3, 50)],
    usedRows: [],
    articles: [
        { nm_id: 1, vendor_code: 'v1', rfStocks: { 1: 1000 } },
        { nm_id: 2, vendor_code: 'v2', rfStocks: { 1: 1000 } },
        { nm_id: 3, vendor_code: 'v3', rfStocks: { 1: 1000 } },
    ],
    ffName: () => 'Газпром',
    ppbOf: () => PPB,
    ppbAt: () => PPB,
    boxSizeOf: () => SIZE,
    palletOverrides: OVERRIDES,
    ...(withClasses ? { classOf, classLabelOf: (cls: string) => classLabelOf(cls, RULES) } : {}),
});

describe('buildPrebookGroups + классы совместимости', () => {
    it('без classOf — легаси: одна карточка, групповой topUp, classes нет', () => {
        // 30+30 = 0.6 паллеты → дозабор нужен (при 50+50 footprint ровно 1.0 —
        // дозабирать нечего, topUp легитимно null).
        const groups = buildPrebookGroups({ ...sources(false), prebook: [row(1, 30), row(3, 30)] });
        expect(groups.length).toBe(1);
        expect(groups[0].classes).toBeUndefined();
        expect(groups[0].topUp).not.toBeNull();
    });

    it('с classOf — карточка та же, но 2 среза per-класс, групповой topUp снят', () => {
        const groups = buildPrebookGroups(sources(true));
        expect(groups.length).toBe(1);
        const g = groups[0];
        expect(g.topUp).toBeNull();
        expect(g.classes?.length).toBe(2);
        const byCls = new Map(g.classes!.map((c) => [c.cls, c]));
        expect(byCls.has('g:0')).toBe(true);
        expect(byCls.has('c:Ковры')).toBe(true);
        expect(byCls.get('g:0')!.label).toBe('текстиль');
        // Footprint аддитивен: сумма срезов == групповому.
        const sumFp = g.classes!.reduce((s, c) => s + c.footprint, 0);
        expect(sumFp).toBeCloseTo(g.footprint, 9);
    });

    it('дозабор среза предлагает только SKU своего класса', () => {
        const groups = buildPrebookGroups(sources(true));
        const byCls = new Map(groups[0].classes!.map((c) => [c.cls, c]));
        const textile = byCls.get('g:0')!;
        const kovry = byCls.get('c:Ковры')!;
        expect(textile.topUp).not.toBeNull();
        expect(kovry.topUp).not.toBeNull();
        for (const c of textile.topUp!.candidates) {
            expect(classOf(c.nmId!)).toBe('g:0');
        }
        for (const c of kovry.topUp!.candidates) {
            expect(classOf(c.nmId!)).toBe('c:Ковры');
        }
    });

    it('общий пул ФФ резервируется: два направления не делят один дефицитный короб', () => {
        // Дефицит: свободно ровно 1 короб пледов (nm 2) сверх занятого, оба
        // направления-текстиль хотят добор — короб должен достаться одному.
        const src: PrebookGroupsSources = {
            ...sources(true),
            prebook: [row(1, 90), { ...row(1, 90), tgt: { 'Тула': 90 } }],
            articles: [
                { nm_id: 1, vendor_code: 'v1', rfStocks: { 1: 180 } }, // всё занято предбронью
                { nm_id: 2, vendor_code: 'v2', rfStocks: { 1: 10 } },  // 1 свободный короб
            ],
        };
        const groups = buildPrebookGroups(src);
        const withTopUp = groups
            .flatMap((g) => g.classes ?? [])
            .filter((c) => c.topUp && c.topUp.candidates.length > 0);
        const promisedBoxes = withTopUp
            .flatMap((c) => c.topUp!.candidates)
            .filter((c) => c.nmId === 2)
            .reduce((s, c) => s + c.boxes, 0);
        expect(promisedBoxes).toBeLessThanOrEqual(1);
    });

    it('моно классами не режется: classes у MONOPALLET-карточки нет', () => {
        const src: PrebookGroupsSources = {
            ...sources(true),
            prebook: [{ ...row(1, 50), package_type: 'MONOPALLET' }, { ...row(3, 50), package_type: 'MONOPALLET' }],
        };
        const groups = buildPrebookGroups(src);
        expect(groups.length).toBe(1);
        expect(groups[0].pkg).toBe('MONOPALLET');
        expect(groups[0].classes).toBeUndefined();
    });
});

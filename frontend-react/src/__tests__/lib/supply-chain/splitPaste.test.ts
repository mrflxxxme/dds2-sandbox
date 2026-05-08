import { describe, expect, it } from 'vitest';
import type { AvailableItem } from '@/types/api';
import { matchesPasteParams, normalizeBox, splitRowAcrossFois } from '@/lib/supply-chain/splitPaste';

const foi = (over: Partial<AvailableItem>): AvailableItem => ({
    id: 1,
    barcode: '2044388467336',
    qty: 0,
    assigned_qty: 0,
    remaining_qty: 0,
    price_cny: '58.88',
    box_size: '60×40×50',
    pcs_per_box: 12,
    ...over,
});

describe('normalizeBox', () => {
    it('normalizes separators across cyrillic/latin/star/cross variants', () => {
        expect(normalizeBox('60x40x50')).toBe('60×40×50');
        expect(normalizeBox('60*40*50')).toBe('60×40×50');
        expect(normalizeBox('60Х40Х50')).toBe('60×40×50');
        expect(normalizeBox('  60×40×50  ')).toBe('60×40×50');
    });
});

describe('matchesPasteParams', () => {
    const f = foi({ price_cny: '58.88', box_size: '60×40×50', pcs_per_box: 12 });

    it('matches when price/box/ppb all coincide', () => {
        expect(matchesPasteParams(f, { qty: 24, price: 58.88, boxRaw: '60x40x50', pcsPerBox: 12 })).toBe(true);
    });

    it('matches when paste leaves box/ppb empty', () => {
        expect(matchesPasteParams(f, { qty: 24, price: 58.88, boxRaw: '', pcsPerBox: 0 })).toBe(true);
    });

    it('rejects on price diff > 0.0001', () => {
        expect(matchesPasteParams(f, { qty: 24, price: 58.5, boxRaw: '60x40x50', pcsPerBox: 12 })).toBe(false);
    });

    it('rejects on box diff', () => {
        expect(matchesPasteParams(f, { qty: 24, price: 58.88, boxRaw: '70×40×50', pcsPerBox: 12 })).toBe(false);
    });

    it('rejects on ppb diff', () => {
        expect(matchesPasteParams(f, { qty: 24, price: 58.88, boxRaw: '60x40x50', pcsPerBox: 15 })).toBe(false);
    });
});

describe('splitRowAcrossFois — the core paste-mode bug fix', () => {
    it('reproduces the prod bug: 2044388467336 across two FOIs (20+28=48), user enters 24', () => {
        const fois: AvailableItem[] = [
            foi({ id: 775, remaining_qty: 20 }),
            foi({ id: 1182, remaining_qty: 28 }),
        ];
        const out = splitRowAcrossFois(
            fois,
            { qty: 24, price: 58.88, boxRaw: '60x40x50', pcsPerBox: 12 },
            false,
        );
        // Should consume FOI #775 fully (20), then 4 from #1182 — total 24, not "exceeded".
        expect(out).toEqual([
            { factory_order_item_id: 775, qty: 20 },
            { factory_order_item_id: 1182, qty: 4 },
        ]);
    });

    it('keeps qty in a single FOI when one is enough', () => {
        const fois: AvailableItem[] = [foi({ id: 1, remaining_qty: 100 })];
        const out = splitRowAcrossFois(fois, { qty: 24, price: 58.88, boxRaw: '60x40x50', pcsPerBox: 12 }, false);
        expect(out).toEqual([{ factory_order_item_id: 1, qty: 24 }]);
    });

    it('prefers FOIs whose params match the paste row over plain FIFO', () => {
        const fois: AvailableItem[] = [
            foi({ id: 1, remaining_qty: 50, price_cny: '40.00', box_size: '70×40×50', pcs_per_box: 8 }),
            foi({ id: 2, remaining_qty: 50, price_cny: '58.88', box_size: '60×40×50', pcs_per_box: 12 }),
        ];
        const out = splitRowAcrossFois(fois, { qty: 24, price: 58.88, boxRaw: '60x40x50', pcsPerBox: 12 }, false);
        expect(out).toEqual([{ factory_order_item_id: 2, qty: 24 }]);
    });

    it('falls back to FIFO when no FOI matches paste params (consumes all needed FOIs)', () => {
        const fois: AvailableItem[] = [
            foi({ id: 1, remaining_qty: 5, price_cny: '50.00' }),
            foi({ id: 2, remaining_qty: 30, price_cny: '40.00' }),
        ];
        const out = splitRowAcrossFois(fois, { qty: 24, price: 58.88, boxRaw: '60x40x50', pcsPerBox: 12 }, false);
        expect(out).toEqual([
            { factory_order_item_id: 1, qty: 5 },
            { factory_order_item_id: 2, qty: 19 },
        ]);
    });

    it('skips FOIs with zero remaining', () => {
        const fois: AvailableItem[] = [
            foi({ id: 1, remaining_qty: 0 }),
            foi({ id: 2, remaining_qty: 24 }),
        ];
        const out = splitRowAcrossFois(fois, { qty: 24, price: 58.88, boxRaw: '60x40x50', pcsPerBox: 12 }, false);
        expect(out).toEqual([{ factory_order_item_id: 2, qty: 24 }]);
    });

    it('returns no items when fois empty or qty<=0', () => {
        expect(splitRowAcrossFois([], { qty: 24, price: 58.88, boxRaw: '', pcsPerBox: 0 }, false)).toEqual([]);
        expect(
            splitRowAcrossFois([foi({ id: 1, remaining_qty: 100 })], { qty: 0, price: 58.88, boxRaw: '', pcsPerBox: 0 }, false),
        ).toEqual([]);
    });

    it('emits per-vehicle overrides only when paste params differ AND overrides flag set', () => {
        const fois: AvailableItem[] = [
            foi({ id: 1, remaining_qty: 100, box_size: '60×40×50', pcs_per_box: 12 }),
        ];
        const params = { qty: 10, price: 58.88, boxRaw: '70*40*50', pcsPerBox: 8 };

        const noOverrides = splitRowAcrossFois(fois, params, false);
        expect(noOverrides[0].box_size_override).toBeUndefined();
        expect(noOverrides[0].pcs_per_box_override).toBeUndefined();

        const withOverrides = splitRowAcrossFois(fois, params, true);
        expect(withOverrides[0].box_size_override).toBe('70*40*50');
        expect(withOverrides[0].pcs_per_box_override).toBe(8);
    });
});

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

    it('without overrides: returns empty when no FOI matches paste params (no silent fallback to FIFO)', () => {
        // sc20.2: split must NOT leak qty onto non-matching FOIs in default mode.
        // The mismatch modal surfaces this, then user opts into withOverrides=true.
        const fois: AvailableItem[] = [
            foi({ id: 1, remaining_qty: 5, price_cny: '50.00' }),
            foi({ id: 2, remaining_qty: 30, price_cny: '40.00' }),
        ];
        const out = splitRowAcrossFois(fois, { qty: 24, price: 58.88, boxRaw: '60x40x50', pcsPerBox: 12 }, false);
        expect(out).toEqual([]);
    });

    it('with overrides: falls back to FIFO when no FOI matches paste params (consumes all needed FOIs)', () => {
        const fois: AvailableItem[] = [
            foi({ id: 1, remaining_qty: 5, price_cny: '50.00' }),
            foi({ id: 2, remaining_qty: 30, price_cny: '40.00' }),
        ];
        const out = splitRowAcrossFois(fois, { qty: 24, price: 58.88, boxRaw: '60x40x50', pcsPerBox: 12 }, true);
        expect(out).toHaveLength(2);
        expect(out[0].factory_order_item_id).toBe(1);
        expect(out[0].qty).toBe(5);
        expect(out[1].factory_order_item_id).toBe(2);
        expect(out[1].qty).toBe(19);
    });

    it('without overrides: prod V-0008 bug — matching FOI exhausted, leftover does NOT spill onto zero-price FOI', () => {
        // Real prod scenario (project 4, V-0008, barcode 2044388594698):
        // FO 35 has matching FOI (price 58.88, box 60x40x50, ppb 12) with remaining=174.
        // FO 76 has placeholder FOI (price 0, no box, no ppb) with remaining=54.
        // User pastes qty=228 at price=58.88. Old behavior: split 174 + 54 (silently
        // booking 54 against price=0 FOI). New behavior: take only matching 174,
        // surface mismatch in UI so user explicitly chooses overrides.
        const fois: AvailableItem[] = [
            foi({ id: 1500, remaining_qty: 54, price_cny: '0.00', box_size: '', pcs_per_box: 0 }),
            foi({ id: 750, remaining_qty: 174, price_cny: '58.88', box_size: '60×40×50', pcs_per_box: 12 }),
        ];
        const out = splitRowAcrossFois(fois, { qty: 228, price: 58.88, boxRaw: '60x40x50', pcsPerBox: 12 }, false);
        expect(out).toEqual([{ factory_order_item_id: 750, qty: 174 }]);
    });

    it('without overrides: prod V-0008 bug — matching FOI exhausted, leftover does NOT spill onto different-price FOI', () => {
        // Real prod scenario (project 4, V-0008, barcode 2049989649853):
        // FO 76 matching FOI (price 77) remaining=5, FO 20 non-matching (price 99.36) remaining=100.
        // User pastes qty=105 at price=77. Old behavior silently took 100 from price=99.36 FOI.
        const fois: AvailableItem[] = [
            foi({ id: 1513, remaining_qty: 5, price_cny: '77.00', box_size: '', pcs_per_box: 0 }),
            foi({ id: 489, remaining_qty: 100, price_cny: '99.36', box_size: '', pcs_per_box: 0 }),
        ];
        const out = splitRowAcrossFois(fois, { qty: 105, price: 77, boxRaw: '', pcsPerBox: 0 }, false);
        expect(out).toEqual([{ factory_order_item_id: 1513, qty: 5 }]);
    });

    it('without overrides: splits across multiple matching FOIs (cross-factory same price)', () => {
        // V-0008 group A — same barcode in 2 factory orders, identical params.
        // Should still split (this is the legitimate by-design case).
        const fois: AvailableItem[] = [
            foi({ id: 1508, remaining_qty: 4 }),
            foi({ id: 784, remaining_qty: 40 }),
        ];
        const out = splitRowAcrossFois(fois, { qty: 44, price: 58.88, boxRaw: '60x40x50', pcsPerBox: 12 }, false);
        expect(out).toHaveLength(2);
        const total = out.reduce((s, x) => s + x.qty, 0);
        expect(total).toBe(44);
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

    it('shared consumed map prevents double-booking same FOI across multiple paste rows of one barcode', () => {
        // Real prod scenario: paste contains several rows for the same barcode.
        // Without `consumed`, each call sees full remaining and bug repeats —
        // backend then rejects later items as exceeded.
        const fois: AvailableItem[] = [
            foi({ id: 429, remaining_qty: 286, price_cny: '48.00', box_size: '60×40×50', pcs_per_box: 15 }),
        ];
        const consumed: Record<number, number> = {};
        const r1 = splitRowAcrossFois(fois, { qty: 30, price: 48, boxRaw: '60x40x50', pcsPerBox: 15 }, false, consumed);
        const r2 = splitRowAcrossFois(fois, { qty: 285, price: 48, boxRaw: '60x40x50', pcsPerBox: 15 }, false, consumed);
        expect(r1).toEqual([{ factory_order_item_id: 429, qty: 30 }]);
        // Only 256 remaining for the second row — and it gets exactly that, not 285.
        expect(r2).toEqual([{ factory_order_item_id: 429, qty: 256 }]);
        expect(consumed[429]).toBe(286);
    });

    it('shared consumed spills over to second FOI when first is exhausted by previous rows', () => {
        const fois: AvailableItem[] = [
            foi({ id: 1, remaining_qty: 100 }),
            foi({ id: 2, remaining_qty: 100 }),
        ];
        const consumed: Record<number, number> = {};
        const r1 = splitRowAcrossFois(fois, { qty: 90, price: 58.88, boxRaw: '60x40x50', pcsPerBox: 12 }, false, consumed);
        const r2 = splitRowAcrossFois(fois, { qty: 50, price: 58.88, boxRaw: '60x40x50', pcsPerBox: 12 }, false, consumed);
        expect(r1).toEqual([{ factory_order_item_id: 1, qty: 90 }]);
        // Row 2 takes remaining 10 from FOI #1, then 40 from FOI #2.
        expect(r2).toEqual([
            { factory_order_item_id: 1, qty: 10 },
            { factory_order_item_id: 2, qty: 40 },
        ]);
    });

    it('emits per-vehicle overrides only when paste params differ AND overrides flag set', () => {
        // Box/ppb mismatch: withOverrides=false skips this FOI entirely
        // (mismatch surfaces in UI), withOverrides=true takes it with overrides.
        const fois: AvailableItem[] = [
            foi({ id: 1, remaining_qty: 100, box_size: '60×40×50', pcs_per_box: 12 }),
        ];
        const params = { qty: 10, price: 58.88, boxRaw: '70*40*50', pcsPerBox: 8 };

        const noOverrides = splitRowAcrossFois(fois, params, false);
        expect(noOverrides).toEqual([]);

        const withOverrides = splitRowAcrossFois(fois, params, true);
        expect(withOverrides[0].box_size_override).toBe('70*40*50');
        expect(withOverrides[0].pcs_per_box_override).toBe(8);
    });

    it('does NOT emit overrides when paste matches FOI even with withOverrides=true', () => {
        const fois: AvailableItem[] = [
            foi({ id: 1, remaining_qty: 100, box_size: '60×40×50', pcs_per_box: 12, price_cny: '58.88' }),
        ];
        const params = { qty: 10, price: 58.88, boxRaw: '60x40x50', pcsPerBox: 12 };
        const withOverrides = splitRowAcrossFois(fois, params, true);
        expect(withOverrides[0].box_size_override).toBeUndefined();
        expect(withOverrides[0].pcs_per_box_override).toBeUndefined();
    });
});

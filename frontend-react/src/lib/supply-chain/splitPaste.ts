import type { AvailableItem } from '@/types/api';

export const normalizeBox = (s: string): string =>
    s.trim().toLowerCase().replace(/[*xхх×]/g, '×');

export interface PasteParams {
    qty: number;
    price: number;
    boxRaw: string;
    pcsPerBox: number;
}

export interface SplitItem {
    factory_order_item_id: number;
    qty: number;
    box_size_override?: string;
    pcs_per_box_override?: number;
}

export const matchesPasteParams = (foi: AvailableItem, p: PasteParams): boolean => {
    const fp = parseFloat(foi.price_cny) || 0;
    if (Math.abs(p.price - fp) > 0.0001) return false;
    const pasteBoxNorm = normalizeBox(p.boxRaw);
    const foiBoxNorm = normalizeBox(foi.box_size || '');
    if (pasteBoxNorm && pasteBoxNorm !== foiBoxNorm) return false;
    const fppb = foi.pcs_per_box || 0;
    if (p.pcsPerBox && p.pcsPerBox !== fppb) return false;
    return true;
};

/**
 * Split user-entered qty across multiple FactoryOrderItems of the same barcode.
 *
 * `withOverrides=false` (default paste): consume ONLY FOIs whose params
 * (price/box/ppb) match the paste row. If matching FOIs don't cover qty,
 * the remainder is left unbooked and the UI surfaces a mismatch modal so
 * the user explicitly opts into overrides. This prevents silent split onto
 * placeholder FOIs (price=0) or FOIs from older orders with different prices.
 *
 * `withOverrides=true` (post-modal "overwrite"): matching first, then FIFO
 * across non-matching FOIs, attaching per-vehicle overrides for box/ppb
 * differences. Used when the user explicitly accepts override semantics.
 *
 * `consumed` (optional, mutable) tracks how much was already booked from
 * each FOI by previous calls in the same submit batch — pass the same map
 * across all paste rows so multiple rows of the same barcode don't
 * double-book one FOI.
 */
export const splitRowAcrossFois = (
    fois: AvailableItem[],
    p: PasteParams,
    withOverrides: boolean,
    consumed: Record<number, number> = {},
): SplitItem[] => {
    const eligible = withOverrides
        ? [...fois].sort(
              (a, b) => (matchesPasteParams(b, p) ? 1 : 0) - (matchesPasteParams(a, p) ? 1 : 0),
          )
        : fois.filter(f => matchesPasteParams(f, p));
    const out: SplitItem[] = [];
    let left = p.qty;
    const pasteBoxNorm = normalizeBox(p.boxRaw);
    for (const foi of eligible) {
        if (left <= 0) break;
        const already = consumed[foi.id] || 0;
        const availableNow = foi.remaining_qty - already;
        if (availableNow <= 0) continue;
        const take = Math.min(left, availableNow);
        if (take <= 0) continue;
        const it: SplitItem = { factory_order_item_id: foi.id, qty: take };
        if (withOverrides) {
            const foiBoxNorm = normalizeBox(foi.box_size || '');
            if (p.boxRaw.trim() && pasteBoxNorm !== foiBoxNorm) it.box_size_override = p.boxRaw.trim();
            const fppb = foi.pcs_per_box || 0;
            if (p.pcsPerBox && p.pcsPerBox !== fppb) it.pcs_per_box_override = p.pcsPerBox;
        }
        out.push(it);
        consumed[foi.id] = already + take;
        left -= take;
    }
    return out;
};

export interface PriceOverwriteRow {
    barcode: string;
    qty: number;
    priceRaw: string;
    boxRaw: string;
    pcsPerBox: number;
}

export interface PriceOverwrite {
    factory_order_item_id: number;
    new_price_cny: string;
}

/**
 * Compute factory-order price overwrites for a paste batch ("overwrite order
 * prices" path).
 *
 * A barcode can map to multiple FOIs across factory orders, and the paste qty
 * is split (matching-first, then FIFO) onto several of them. The price must be
 * overwritten on EVERY FOI that receives qty whose current price differs — not
 * just the representative FOI. Mirrors performAdd's split (same withOverrides
 * mode + shared `consumed`) so the booked FOIs and the repriced FOIs are
 * exactly the same set. (sc20.3: single-FOI map missed siblings — V-0010.)
 */
export const buildPriceOverwrites = (
    rows: PriceOverwriteRow[],
    foisByBarcode: Record<string, AvailableItem[]>,
): PriceOverwrite[] => {
    const consumed: Record<number, number> = {};
    const updates: PriceOverwrite[] = [];
    for (const r of rows) {
        const price = parseFloat(r.priceRaw) || 0;
        if (price <= 0 || r.qty <= 0) continue;
        const fois = foisByBarcode[r.barcode] || [];
        const foiById = new Map(fois.map(f => [f.id, f]));
        const split = splitRowAcrossFois(
            fois,
            { qty: r.qty, price, boxRaw: r.boxRaw, pcsPerBox: r.pcsPerBox },
            true,
            consumed,
        );
        for (const s of split) {
            const foi = foiById.get(s.factory_order_item_id);
            const oldPrice = parseFloat(foi?.price_cny || '0') || 0;
            if (Math.abs(price - oldPrice) > 0.0001) {
                updates.push({ factory_order_item_id: s.factory_order_item_id, new_price_cny: r.priceRaw });
            }
        }
    }
    return updates;
};

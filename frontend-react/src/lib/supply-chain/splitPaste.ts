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
 * Preference order: FOIs whose params (price/box/ppb) match the paste row, then FIFO.
 * Consumes each FOI's `remaining_qty` greedily.
 *
 * Returns API items ready for `addItemsToVehicle` / `addPostShipmentItems`.
 * `withOverrides` controls whether mismatched box/ppb leak as per-vehicle overrides.
 */
export const splitRowAcrossFois = (
    fois: AvailableItem[],
    p: PasteParams,
    withOverrides: boolean,
): SplitItem[] => {
    const sorted = [...fois].sort(
        (a, b) => (matchesPasteParams(b, p) ? 1 : 0) - (matchesPasteParams(a, p) ? 1 : 0),
    );
    const out: SplitItem[] = [];
    let left = p.qty;
    const pasteBoxNorm = normalizeBox(p.boxRaw);
    for (const foi of sorted) {
        if (left <= 0) break;
        const take = Math.min(left, foi.remaining_qty);
        if (take <= 0) continue;
        const it: SplitItem = { factory_order_item_id: foi.id, qty: take };
        if (withOverrides) {
            const foiBoxNorm = normalizeBox(foi.box_size || '');
            if (p.boxRaw.trim() && pasteBoxNorm !== foiBoxNorm) it.box_size_override = p.boxRaw.trim();
            const fppb = foi.pcs_per_box || 0;
            if (p.pcsPerBox && p.pcsPerBox !== fppb) it.pcs_per_box_override = p.pcsPerBox;
        }
        out.push(it);
        left -= take;
    }
    return out;
};

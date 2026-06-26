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

/**
 * Per-vehicle box/ppb override for a single FOI given the paste row's packing.
 *
 * Box/ppb из вставки — это ФАКТИЧЕСКАЯ упаковка машины. «Вставил список → как
 * написал, так и записано». Пишется как override ВСЕГДА, когда отличается от
 * FOI (в т.ч. когда у FOI-заглушки нет своих габаритов — тогда '' ≠ '60×40×50'
 * → override пишется). Цена при этом не трогается.
 *
 * SINGLE SOURCE OF TRUTH — used by splitRowAcrossFois AND the «exceeded»/extend
 * branch of the vehicle paste-add. The exceeded branch собирал items вручную и
 * РОНЯЛ эти override'ы → строки на FOI-заглушках (дозаказ/новые, price 0, без
 * box/ppb) сохранялись без габаритов: пустой «Размер короба» и 0 в «Мест»
 * (прод-инцидент V-0027).
 */
export const buildBoxOverrides = (
    foi: AvailableItem,
    boxRaw: string,
    pcsPerBox: number,
): { box_size_override?: string; pcs_per_box_override?: number } => {
    const out: { box_size_override?: string; pcs_per_box_override?: number } = {};
    const box = (boxRaw || '').trim();
    if (box && normalizeBox(box) !== normalizeBox(foi.box_size || '')) out.box_size_override = box;
    if (pcsPerBox && pcsPerBox !== (foi.pcs_per_box || 0)) out.pcs_per_box_override = pcsPerBox;
    return out;
};

export const matchesPasteParams = (foi: AvailableItem, p: PasteParams): boolean => {
    // Match on PRICE only. price влияет на заказ фабрики (деньги) — поэтому
    // расхождение цены остаётся осознанным выбором keep/overwrite (инцидент V-0008:
    // не сплитить молча на FOI с другой ценой). А box/pcs — это ФАКТИЧЕСКАЯ упаковка
    // именно этой машины: вставленные пользователем значения пишутся как per-vehicle
    // override ВСЕГДА (см. splitRowAcrossFois) и НЕ должны блокировать бронь/матчинг.
    const fp = parseFloat(foi.price_cny) || 0;
    return Math.abs(p.price - fp) <= 0.0001;
};

/**
 * Split user-entered qty across multiple FactoryOrderItems of the same barcode.
 *
 * Box/ppb из вставки — это ФАКТИЧЕСКАЯ упаковка машины: пишется как per-vehicle
 * override ВСЕГДА (в обоих режимах), когда отличается от FOI. `withOverrides`
 * управляет ТОЛЬКО ценой/матчингом:
 *
 * `withOverrides=false` (default paste): consume ONLY FOIs whose PRICE matches
 * the paste row. If price-matching FOIs don't cover qty, the remainder is left
 * unbooked and the UI surfaces a mismatch modal so the user explicitly opts into
 * price overwrite. Prevents silent split onto placeholder/different-price FOIs (V-0008).
 *
 * `withOverrides=true` (post-modal "overwrite"): price-matching first, then FIFO
 * across other FOIs. Used when the user accepts rewriting the factory-order price.
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
    for (const foi of eligible) {
        if (left <= 0) break;
        const already = consumed[foi.id] || 0;
        const availableNow = foi.remaining_qty - already;
        if (availableNow <= 0) continue;
        const take = Math.min(left, availableNow);
        if (take <= 0) continue;
        // Упаковка из вставки пишется ВСЕГДА (независимо от keep/overwrite): это
        // фактические габариты/штук-в-коробке этой машины. Цена не трогается.
        const it: SplitItem = {
            factory_order_item_id: foi.id,
            qty: take,
            ...buildBoxOverrides(foi, p.boxRaw, p.pcsPerBox),
        };
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
    consumed: Record<number, number> = {},
): PriceOverwrite[] => {
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

/**
 * Price overwrites for the «!» (exceeded → extend_plan) basket.
 *
 * Mirrors the exceeded branch of the vehicle paste-add EXACTLY — plain FIFO consume
 * across the barcode's FOIs (NOT matching-first; the overflow basket grows whatever
 * line exists), then the leftover extends `fois[0]`. Every FOI that receives qty
 * (including the extend target) whose current price differs from the paste row is
 * repriced, so the FOIs the add books onto and the FOIs we reprice are the same set.
 *
 * Why this exists (prod incident V-0031): when a paste row's qty EXCEEDS the order
 * line's remaining, the row lands in the «!» basket, which `buildPriceOverwrites`
 * (fed only ✓ valid rows) never saw. The qty got booked via extend_plan but the
 * pasted price was never written to the FactoryOrderItem → the new vehicle line
 * snapshotted `price_cny = 0`. 16 carpet lines (858 pcs) landed at 0 ¥ because the
 * pasted qty 108/102 exceeded the order line's planned 100.
 *
 * `consumed` MUST be the SAME map already populated by `buildPriceOverwrites` for
 * the ✓ valid rows of this batch — the add books valid first, then exceeded, so
 * cumulative consumption across both baskets has to line up (a FOI partly eaten by
 * a valid row has less room for the exceeded overflow).
 *
 * Only writes when overwrite was chosen AND the price actually differs: a paste row
 * whose price already equals the FOI price produces no update (no silent re-write).
 */
export const buildExceededPriceOverwrites = (
    rows: PriceOverwriteRow[],
    foisByBarcode: Record<string, AvailableItem[]>,
    consumed: Record<number, number> = {},
): PriceOverwrite[] => {
    const updates: PriceOverwrite[] = [];
    const repriced = new Set<number>();
    const reprice = (foi: AvailableItem | undefined, priceRaw: string, price: number): void => {
        if (!foi || repriced.has(foi.id)) return;
        const oldPrice = parseFloat(foi.price_cny || '0') || 0;
        if (Math.abs(price - oldPrice) > 0.0001) {
            updates.push({ factory_order_item_id: foi.id, new_price_cny: priceRaw });
            repriced.add(foi.id);
        }
    };
    for (const r of rows) {
        const price = parseFloat(r.priceRaw) || 0;
        if (price <= 0 || r.qty <= 0) continue;
        const fois = foisByBarcode[r.barcode] || [];
        if (fois.length === 0) continue;
        let left = r.qty;
        for (const foi of fois) {
            if (left <= 0) break;
            const already = consumed[foi.id] || 0;
            const avail = Math.max(0, foi.remaining_qty - already);
            const take = Math.min(left, avail);
            if (take > 0) {
                reprice(foi, r.priceRaw, price);
                consumed[foi.id] = already + take;
                left -= take;
            }
        }
        // Overflow → extend_plan on fois[0]: that line also receives qty, so reprice it
        // too (covers the case where every FOI was already exhausted and the whole row
        // lands on the extend target).
        if (left > 0) {
            reprice(fois[0], r.priceRaw, price);
            consumed[fois[0].id] = (consumed[fois[0].id] || 0) + left;
        }
    }
    return updates;
};

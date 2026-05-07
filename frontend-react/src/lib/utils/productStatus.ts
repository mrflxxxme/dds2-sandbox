/**
 * Product status helper for nomenclature table.
 *
 * Classifies a SKU based on its first sale date:
 * - no first sale → "Без продаж" (info)
 * - first sale within NEW_PRODUCT_THRESHOLD_DAYS (≤40 days ago) → "Новинка" (success)
 * - older → "Активный" (secondary)
 *
 * Pure function: depends only on the input string and `Date.now()`. Tests use
 * `vi.useFakeTimers()` to make boundaries deterministic.
 */

export const NEW_PRODUCT_THRESHOLD_DAYS = 40;

const MS_PER_DAY = 86_400_000;

export interface ProductStatus {
    label: string;
    className: string;
}

export function computeProductStatus(firstSaleDate: string | null | undefined): ProductStatus {
    if (!firstSaleDate) {
        return { label: 'Без продаж', className: 'badge badge-info' };
    }
    const first = new Date(firstSaleDate);
    if (isNaN(first.getTime())) {
        return { label: 'Без продаж', className: 'badge badge-info' };
    }
    const days = Math.floor((Date.now() - first.getTime()) / MS_PER_DAY);
    if (days <= NEW_PRODUCT_THRESHOLD_DAYS) {
        return { label: 'Новинка', className: 'badge badge-success' };
    }
    return { label: 'Активный', className: 'badge badge-secondary' };
}

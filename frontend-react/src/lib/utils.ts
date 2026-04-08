/**
 * Excel export utility — converts any data array to .xlsx download
 */
export function exportToExcel(data: Record<string, any>[], filename: string) {
    import('xlsx').then(XLSX => {
        const ws = XLSX.utils.json_to_sheet(data);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, 'Data');
        XLSX.writeFile(wb, `${filename}.xlsx`);
    });
}

/**
 * Format number with locale
 */
export function formatNumber(n: number | null | undefined, decimals = 2): string {
    if (n == null) return '—';
    return n.toLocaleString('ru-RU', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

/**
 * Format date string
 */
export function formatDate(d: string | null | undefined): string {
    if (!d) return '—';
    return new Date(d).toLocaleDateString('ru-RU');
}

export function formatDateTime(d: string | null | undefined): string {
    if (!d) return '—';
    return new Date(d).toLocaleString('ru-RU');
}

/** Calculate total boxes with mix group deduplication */
export function calcTotalBoxesWithMix(items: { qty: number; pcs_per_box?: number | null | undefined; mix_group_id?: string | null | undefined; mix_pcs_per_box?: number | null | undefined }[]): number {
    let total = 0;
    const mixBoxes = new Map<string, number>(); // mix_group_id → max boxes in group
    for (const item of items) {
        if (item.mix_group_id) {
            const ppb = item.mix_pcs_per_box || item.pcs_per_box || 0;
            const boxes = ppb > 0 ? Math.ceil(item.qty / ppb) : 0;
            const prev = mixBoxes.get(item.mix_group_id) || 0;
            mixBoxes.set(item.mix_group_id, Math.max(prev, boxes));
        } else {
            const ppb = item.pcs_per_box || 0;
            if (ppb > 0) total += Math.ceil(item.qty / ppb);
        }
    }
    for (const boxes of mixBoxes.values()) total += boxes;
    return total;
}

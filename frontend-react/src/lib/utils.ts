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

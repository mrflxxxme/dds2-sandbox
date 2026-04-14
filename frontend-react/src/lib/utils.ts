export interface ExcelExportColumn {
    key: string;
    label: string;
    getValue?: (row: any) => any;
    exportValue?: (row: any) => any;
}

function isPrimitive(v: unknown): boolean {
    if (v == null) return true;
    const t = typeof v;
    return t === 'string' || t === 'number' || t === 'boolean' || v instanceof Date;
}

/**
 * Excel export utility — converts any data array to .xlsx download.
 * If `columns` is provided, uses column labels as headers and respects
 * `exportValue`/`getValue` accessors so nested/computed cells export correctly.
 */
export function exportToExcel(
    data: Record<string, any>[],
    filename: string,
    columns?: ExcelExportColumn[],
) {
    import('exceljs').then(ExcelJS => {
        const workbook = new ExcelJS.Workbook();
        const worksheet = workbook.addWorksheet('Data');

        if (data.length === 0) return;

        if (columns && columns.length > 0) {
            worksheet.columns = columns.map(c => ({ header: c.label, key: c.key }));
            worksheet.addRows(
                data.map(row => {
                    const out: Record<string, any> = {};
                    for (const c of columns) {
                        let v: any;
                        if (c.exportValue) v = c.exportValue(row);
                        else if (c.getValue) v = c.getValue(row);
                        else v = row[c.key];
                        out[c.key] = isPrimitive(v) ? v : '';
                    }
                    return out;
                }),
            );
        } else {
            const headers = Object.keys(data[0]);
            worksheet.columns = headers.map(key => ({ header: key, key }));
            worksheet.addRows(data);
        }

        workbook.xlsx.writeBuffer().then(buffer => {
            const blob = new Blob([buffer], {
                type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            });
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `${filename}.xlsx`;
            link.click();
            URL.revokeObjectURL(url);
        });
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

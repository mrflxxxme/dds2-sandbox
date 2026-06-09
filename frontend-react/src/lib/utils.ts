export interface ExcelExportColumn {
    key: string;
    label: string;
    getValue?: (row: any) => any;
    exportValue?: (row: any) => any;
}

export interface ExcelExtraSheet {
    sheetName: string;
    data: Record<string, any>[];
    columns?: ExcelExportColumn[];
}

function isPrimitive(v: unknown): boolean {
    if (v == null) return true;
    const t = typeof v;
    return t === 'string' || t === 'number' || t === 'boolean' || v instanceof Date;
}

// Заполняем worksheet по data + columns с учётом exportValue/getValue.
function _fillSheet(worksheet: any, data: Record<string, any>[], columns?: ExcelExportColumn[]) {
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
}

/**
 * Excel export utility — converts any data array to .xlsx download.
 * If `columns` is provided, uses column labels as headers and respects
 * `exportValue`/`getValue` accessors so nested/computed cells export correctly.
 * `additionalSheets` — необязательные доп. листы (например, шаблон для вставки).
 */
export function exportToExcel(
    data: Record<string, any>[],
    filename: string,
    columns?: ExcelExportColumn[],
    additionalSheets?: ExcelExtraSheet[],
) {
    import('exceljs').then(ExcelJS => {
        const workbook = new ExcelJS.Workbook();
        const worksheet = workbook.addWorksheet('Data');

        if (data.length === 0 && (!additionalSheets || additionalSheets.length === 0)) return;

        _fillSheet(worksheet, data, columns);

        for (const extra of additionalSheets ?? []) {
            const ws = workbook.addWorksheet(extra.sheetName);
            _fillSheet(ws, extra.data, extra.columns);
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

/**
 * Calculate total boxes with mix group deduplication.
 *
 * Same barcode split across multiple source rows (one per factory order) must
 * be combined BEFORE rounding up to whole boxes — otherwise each row's partial
 * box is counted separately and inflates the total (e.g. 14+30 @22/box = 2
 * boxes combined, not ceil(14/22)+ceil(30/22)=3). Only combines rows with the
 * same barcode AND the same pcs_per_box.
 */
export function calcTotalBoxesWithMix(items: { barcode?: string | null | undefined; qty: number; pcs_per_box?: number | null | undefined; mix_group_id?: string | null | undefined; mix_pcs_per_box?: number | null | undefined; box_detail?: number[] | null | undefined }[]): number {
    let total = 0;
    const mixBoxes = new Map<string, number>(); // mix_group_id → max boxes in group
    const qtyByBarcode = new Map<string, { qty: number; ppb: number }>(); // barcode|ppb → combined qty
    for (const item of items) {
        if (item.mix_group_id) {
            const ppb = item.mix_pcs_per_box || item.pcs_per_box || 0;
            const boxes = item.box_detail && item.box_detail.length > 0
                ? item.box_detail.length
                : (ppb > 0 ? Math.ceil(item.qty / ppb) : 0);
            const prev = mixBoxes.get(item.mix_group_id) || 0;
            mixBoxes.set(item.mix_group_id, Math.max(prev, boxes));
        } else if (item.box_detail && item.box_detail.length > 0) {
            total += item.box_detail.length;
        } else {
            const ppb = item.pcs_per_box || 0;
            if (ppb <= 0) continue;
            if (item.barcode) {
                const key = `${item.barcode}|${ppb}`;
                const cur = qtyByBarcode.get(key) || { qty: 0, ppb };
                cur.qty += item.qty;
                qtyByBarcode.set(key, cur);
            } else {
                total += Math.ceil(item.qty / ppb);
            }
        }
    }
    for (const { qty, ppb } of qtyByBarcode.values()) total += Math.ceil(qty / ppb);
    for (const boxes of mixBoxes.values()) total += boxes;
    return total;
}

/**
 * Combined box breakdown for ONE barcode split across several source rows (ФЗ).
 *
 * A barcode coming from N factory orders is a SINGLE position for the vehicle —
 * boxes are packed by the COMBINED qty, not per source. Per-source rounding shows
 * each source's tail as its own partial box (208@14 → [14×14, 12] и 2@14 → [2]),
 * while the true packing is 210@14 → [14×15] (хвосты 12+2=14 сливаются). Mirrors
 * calcTotalBoxesWithMix (combine by barcode|ppb before rounding) but returns the
 * box ARRAY (parallel to autoBoxDetail in BoxDetailCell), so the breakdown matches
 * the «Мест» number.
 *
 * Returns null when combining is unsafe — caller falls back to per-source display:
 *   • mixed pcs_per_box across sources — different box sizes can't merge;
 *   • any source carries an explicit box_detail (manual packing override) — that
 *     source's layout is fixed and must not be silently absorbed;
 *   • mix-group items — handled separately (max boxes per group).
 */
export function calcCombinedBoxDetail(
    items: { qty: number; pcs_per_box?: number | null; box_detail?: number[] | null; mix_group_id?: string | null }[],
): number[] | null {
    if (items.length === 0) return null;
    const ppb = items[0].pcs_per_box || 0;
    if (ppb <= 0) return null;
    for (const it of items) {
        if (it.mix_group_id) return null;
        if (it.box_detail && it.box_detail.length > 0) return null;
        if ((it.pcs_per_box || 0) !== ppb) return null;
    }
    const totalQty = items.reduce((sum, it) => sum + it.qty, 0);
    if (totalQty <= 0) return null;
    const full = Math.floor(totalQty / ppb);
    const remainder = totalQty % ppb;
    const detail: number[] = Array(full).fill(ppb);
    if (remainder > 0) detail.push(remainder);
    return detail;
}

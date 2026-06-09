import { describe, it, expect, vi, beforeEach } from 'vitest';
import { formatNumber, formatDate, formatDateTime, exportToExcel, calcTotalBoxesWithMix, calcCombinedBoxDetail } from '@/lib/utils';

let captured: { columns: any[]; rows: any[] } | null = null;

vi.mock('exceljs', () => {
    class MockWorksheet {
        columns: any[] = [];
        _rows: any[] = [];
        addRows(rows: any[]) { this._rows = rows; }
    }
    class MockWorkbook {
        worksheet = new MockWorksheet();
        addWorksheet() { return this.worksheet; }
        xlsx = {
            writeBuffer: async () => {
                captured = { columns: this.worksheet.columns, rows: this.worksheet._rows };
                return new ArrayBuffer(8);
            },
        };
    }
    return { Workbook: MockWorkbook, default: { Workbook: MockWorkbook } };
});

describe('formatNumber', () => {
  it('formats positive numbers with 2 decimals by default', () => {
    const result = formatNumber(1234.5);
    // ru-RU uses non-breaking space as thousands separator and comma for decimals
    expect(result).toContain('1');
    expect(result).toContain('234');
    expect(result).toContain('50');
  });

  it('formats with custom decimal places', () => {
    const result = formatNumber(1234.5678, 0);
    expect(result).toContain('1');
    expect(result).toContain('235'); // rounded
  });

  it('returns em-dash for null', () => {
    expect(formatNumber(null)).toBe('\u2014');
  });

  it('returns em-dash for undefined', () => {
    expect(formatNumber(undefined)).toBe('\u2014');
  });

  it('formats zero correctly', () => {
    const result = formatNumber(0);
    expect(result).toContain('0');
    expect(result).toContain('00');
  });

  it('formats negative numbers', () => {
    const result = formatNumber(-500.99);
    expect(result).toContain('500');
    expect(result).toContain('99');
  });
});

describe('formatDate', () => {
  it('formats ISO date string to ru-RU locale', () => {
    const result = formatDate('2025-03-15');
    // ru-RU date format: DD.MM.YYYY
    expect(result).toContain('15');
    expect(result).toContain('03');
    expect(result).toContain('2025');
  });

  it('returns em-dash for null', () => {
    expect(formatDate(null)).toBe('\u2014');
  });

  it('returns em-dash for undefined', () => {
    expect(formatDate(undefined)).toBe('\u2014');
  });

  it('returns em-dash for empty string', () => {
    expect(formatDate('')).toBe('\u2014');
  });
});

describe('formatDateTime', () => {
  it('formats ISO datetime string', () => {
    const result = formatDateTime('2025-03-15T14:30:00');
    expect(result).toContain('15');
    expect(result).toContain('2025');
  });

  it('returns em-dash for null', () => {
    expect(formatDateTime(null)).toBe('\u2014');
  });

  it('returns em-dash for undefined', () => {
    expect(formatDateTime(undefined)).toBe('\u2014');
  });

  it('returns em-dash for empty string', () => {
    expect(formatDateTime('')).toBe('\u2014');
  });
});

describe('exportToExcel', () => {
    beforeEach(() => {
        captured = null;
        (URL as any).createObjectURL = vi.fn(() => 'blob:mock');
        (URL as any).revokeObjectURL = vi.fn();
    });

    it('uses column.label as header and resolves nested values via exportValue', async () => {
        type Row = { barcode: string; warehouses: Record<number, number>; total: number };
        const data: Row[] = [
            { barcode: '2042072507733', warehouses: { 1: 192, 2: 72 }, total: 264 },
            { barcode: '2042072507740', warehouses: { 1: 164, 2: 5 }, total: 169 },
        ];
        const cols = [
            { key: 'barcode', label: 'ШК' },
            { key: 'wh_1', label: 'ХАНДИ', exportValue: (r: Row) => r.warehouses[1] || 0 },
            { key: 'wh_2', label: 'НАТАЛИ', exportValue: (r: Row) => r.warehouses[2] || 0 },
            { key: 'total', label: 'Итого' },
        ];

        exportToExcel(data as any, 'stock_summary', cols as any);
        // Wait for the dynamic import + writeBuffer chain to settle
        await new Promise(r => setTimeout(r, 50));

        expect(captured).not.toBeNull();
        expect(captured!.columns.map((c: any) => c.header)).toEqual(['ШК', 'ХАНДИ', 'НАТАЛИ', 'Итого']);
        expect(captured!.rows[0]).toEqual({ barcode: '2042072507733', wh_1: 192, wh_2: 72, total: 264 });
        expect(captured!.rows[1]).toEqual({ barcode: '2042072507740', wh_1: 164, wh_2: 5, total: 169 });
        // Crucially: no JSON-stringified objects should leak into the export
        for (const row of captured!.rows) {
            for (const v of Object.values(row)) {
                expect(typeof v === 'object' && v !== null).toBe(false);
            }
        }
    });

    it('falls back to row[key] when no columns are passed (legacy behavior)', async () => {
        const data = [{ a: 1, b: 'x' }, { a: 2, b: 'y' }];
        exportToExcel(data, 'legacy');
        await new Promise(r => setTimeout(r, 50));

        expect(captured).not.toBeNull();
        expect(captured!.columns.map((c: any) => c.header)).toEqual(['a', 'b']);
        expect(captured!.rows).toEqual(data);
    });

    it('replaces nested objects with empty string when no exportValue is defined', async () => {
        const data = [{ id: 1, nested: { foo: 'bar' } }];
        const cols = [
            { key: 'id', label: 'ID' },
            { key: 'nested', label: 'Nested' },
        ];
        exportToExcel(data as any, 'nested', cols as any);
        await new Promise(r => setTimeout(r, 50));

        expect(captured).not.toBeNull();
        expect(captured!.rows[0]).toEqual({ id: 1, nested: '' });
    });
});

describe('calcTotalBoxesWithMix', () => {
    it('combines same-barcode source rows before rounding up (V-0012 bug)', () => {
        // 14 + 30 @ 22/box = 44 -> 2 boxes combined, not ceil(14/22)+ceil(30/22)=3.
        const boxes = calcTotalBoxesWithMix([
            { barcode: 'BC1', qty: 14, pcs_per_box: 22 },
            { barcode: 'BC1', qty: 30, pcs_per_box: 22 },
        ]);
        expect(boxes).toBe(2);
    });

    it('combines two sources of equal pcs_per_box into whole boxes (V-0022 bug)', () => {
        // 30 + 26 @ 4/box = 56 -> 14 whole boxes combined, NOT ceil(30/4)+ceil(26/4)=8+7=15.
        // Для машины баркод из 2 ФЗ — единая позиция, делится ровно по коробке.
        const boxes = calcTotalBoxesWithMix([
            { barcode: '20494046010912', qty: 30, pcs_per_box: 4 },
            { barcode: '20494046010912', qty: 26, pcs_per_box: 4 },
        ]);
        expect(boxes).toBe(14);
    });

    it('rounds up per barcode, not per row, across many barcodes', () => {
        const boxes = calcTotalBoxesWithMix([
            { barcode: 'A', qty: 14, pcs_per_box: 22 }, // +30 -> 44 -> 2
            { barcode: 'A', qty: 30, pcs_per_box: 22 },
            { barcode: 'B', qty: 24, pcs_per_box: 20 }, // +16 -> 40 -> 2
            { barcode: 'B', qty: 16, pcs_per_box: 20 },
        ]);
        expect(boxes).toBe(4); // not 3+... per-row would be ceil(14/22)+ceil(30/22)+ceil(24/20)+ceil(16/20)=1+2+2+1=6
    });

    it('does NOT combine when pcs_per_box differs for same barcode', () => {
        const boxes = calcTotalBoxesWithMix([
            { barcode: 'A', qty: 10, pcs_per_box: 22 }, // -> 1
            { barcode: 'A', qty: 10, pcs_per_box: 10 }, // -> 1
        ]);
        expect(boxes).toBe(2);
    });

    it('single source: plain ceil', () => {
        expect(calcTotalBoxesWithMix([{ barcode: 'A', qty: 45, pcs_per_box: 22 }])).toBe(3);
    });

    it('mix groups deduped by max boxes, independent of barcode combine', () => {
        const boxes = calcTotalBoxesWithMix([
            { barcode: 'M1', qty: 22, pcs_per_box: 22, mix_group_id: 'g1', mix_pcs_per_box: 22 },
            { barcode: 'M2', qty: 11, pcs_per_box: 22, mix_group_id: 'g1', mix_pcs_per_box: 22 },
            { barcode: 'C', qty: 14, pcs_per_box: 22 },
            { barcode: 'C', qty: 30, pcs_per_box: 22 },
        ]);
        // mix g1 -> max(ceil(22/22), ceil(11/22)) = 1; barcode C -> 2; total 3
        expect(boxes).toBe(3);
    });

    it('falls back to per-row when barcode is missing', () => {
        const boxes = calcTotalBoxesWithMix([
            { qty: 14, pcs_per_box: 22 },
            { qty: 30, pcs_per_box: 22 },
        ]);
        expect(boxes).toBe(3); // no barcode -> cannot combine -> 1 + 2
    });
});

describe('calcCombinedBoxDetail', () => {
    it('combines two sources of one barcode into whole boxes (V-0015 bug)', () => {
        // 208 + 2 @ 14/box = 210 -> [14×15] (15 полных, 0 неполных),
        // НЕ [14×14, 12] (ВИНТАЖ) + [2] (тиамо). Хвосты 12+2=14 сливаются.
        expect(calcCombinedBoxDetail([
            { qty: 208, pcs_per_box: 14 },
            { qty: 2, pcs_per_box: 14 },
        ])).toEqual(Array(15).fill(14));
    });

    it('leaves exactly one partial box when combined qty is not divisible', () => {
        // 209 + 2 @ 14 = 211 -> 15×14 + 1 (ровно одна неполная)
        expect(calcCombinedBoxDetail([
            { qty: 209, pcs_per_box: 14 },
            { qty: 2, pcs_per_box: 14 },
        ])).toEqual([...Array(15).fill(14), 1]);
    });

    it('single source equals plain autoBoxDetail', () => {
        expect(calcCombinedBoxDetail([{ qty: 45, pcs_per_box: 22 }])).toEqual([22, 22, 1]);
    });

    it('returns null when pcs_per_box differs across sources (cannot merge box sizes)', () => {
        expect(calcCombinedBoxDetail([
            { qty: 10, pcs_per_box: 22 },
            { qty: 10, pcs_per_box: 10 },
        ])).toBeNull();
    });

    it('returns null when any source carries a manual box_detail override', () => {
        // override фиксирует фактическую упаковку источника — не поглощаем в сводную.
        expect(calcCombinedBoxDetail([
            { qty: 208, pcs_per_box: 14, box_detail: [14, 14, 180] },
            { qty: 2, pcs_per_box: 14 },
        ])).toBeNull();
    });

    it('returns null for mix-group items', () => {
        expect(calcCombinedBoxDetail([
            { qty: 22, pcs_per_box: 22, mix_group_id: 'g1' },
        ])).toBeNull();
    });

    it('returns null when pcs_per_box is missing or zero', () => {
        expect(calcCombinedBoxDetail([{ qty: 10 }])).toBeNull();
    });
});

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { formatNumber, formatDate, formatDateTime, exportToExcel } from '@/lib/utils';

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

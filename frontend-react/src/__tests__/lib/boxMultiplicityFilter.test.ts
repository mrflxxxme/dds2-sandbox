import { describe, it, expect } from 'vitest';
import { matchesStockFilter, passesRowFilter } from '@/lib/utils/boxMultiplicityFilter';
import type { BoxMultiplicityRow, BoxMultiplicityPerWarehouseRow } from '@/types/api';

function wh(over: Partial<BoxMultiplicityPerWarehouseRow> = {}): BoxMultiplicityPerWarehouseRow {
    return {
        warehouse_id: 1,
        warehouse_name: 'ФФ',
        box_qty: null,
        box_size: null,
        source: 'manual',
        editable: true,
        use_box_multiplicity: true,
        rf_stock: 0,
        machine_order_no: null,
        machine_received_at: null,
        machine_variants: 0,
        ...over,
    };
}

function row(over: Partial<BoxMultiplicityRow> = {}): BoxMultiplicityRow {
    return {
        nm_id: 100,
        vendor_code: 'ART-1',
        barcode: '111',
        brand: null,
        subject: null,
        box_qty_override: null,
        use_box_multiplicity: true,
        has_machine_data: false,
        has_mixed_ppb: false,
        rf_stock: 0,
        in_assembly: 0,
        in_transit: 0,
        wb_stock: 0,
        per_warehouse: [],
        ...over,
    };
}

describe('matchesStockFilter — no_box_qty', () => {
    it('строка со стоком без кратности проходит фильтр «Нет кратности»', () => {
        const r = row({ rf_stock: 10, per_warehouse: [wh({ rf_stock: 10, box_qty: null })] });
        expect(matchesStockFilter(r, 'no_box_qty')).toBe(true);
    });

    it('после задания кратности строка выпадает из «Нет кратности» (корень бага B)', () => {
        const r = row({ rf_stock: 10, per_warehouse: [wh({ rf_stock: 10, box_qty: 6 })] });
        expect(matchesStockFilter(r, 'no_box_qty')).toBe(false);
    });
});

describe('passesRowFilter — липкость отредактированной строки', () => {
    it('липкая строка остаётся видимой даже после того, как перестала матчить фильтр', () => {
        const edited = row({ nm_id: 100, rf_stock: 10, per_warehouse: [wh({ rf_stock: 10, box_qty: 6 })] });
        const sticky = new Set<number>([100]);
        // без липкости выпала бы (см. тест выше), с липкостью — остаётся
        expect(passesRowFilter(edited, 'no_box_qty', new Set())).toBe(false);
        expect(passesRowFilter(edited, 'no_box_qty', sticky)).toBe(true);
    });

    it('не-липкая строка, не матчащая фильтр, скрыта', () => {
        const r = row({ nm_id: 200, rf_stock: 10, per_warehouse: [wh({ rf_stock: 10, box_qty: 6 })] });
        expect(passesRowFilter(r, 'no_box_qty', new Set([100]))).toBe(false);
    });
});

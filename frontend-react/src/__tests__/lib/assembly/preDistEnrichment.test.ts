import { describe, it, expect } from 'vitest';
import { enrichPoolRows } from '@/lib/assembly/preDistribution';
import type { PreDistPoolRow, StockNeedResponse, StockNeedArticle } from '@/types/api';

function poolRow(over: Partial<PreDistPoolRow>): PreDistPoolRow {
    return {
        barcode: 'B1', article_seller: 'ART1', article_wb: '1', name: 'Ковёр', brand: 'НУ',
        gross_qty: 100, distributed_qty: 0, available_qty: 100, ...over,
    };
}

function article(over: Partial<StockNeedArticle>): StockNeedArticle {
    return {
        nm_id: 1, vendor_code: 'ART1', barcode: 'B1', brand: '', subject: '',
        total_need: 0, revenue_30d: 0, rf_stocks: {}, in_assembly: 0, in_transit: 0,
        in_transit_date: null, can_send: 0, deficit: 0, stocks_wb: 0, ...over,
    };
}

/** StockNeed: per-WB-склад {need, stock} по nm + статьи с агрегатами/asm-by-wh. */
function mkNeed(
    warehouses: { name: string; district_key?: string; cells: Record<number, { need: number; stock: number }> }[],
    articles: StockNeedArticle[],
): StockNeedResponse {
    return {
        warehouses: warehouses.map(w => ({
            name: w.name,
            district_key: w.district_key,
            total_need: Object.values(w.cells).reduce((s, c) => s + c.need, 0),
            articles: Object.fromEntries(
                Object.entries(w.cells).map(([nm, c]) => [nm, { need: c.need, stock: c.stock, avg_daily: 0 }]),
            ),
        })),
        articles,
        rf_warehouses: [], brands: [], subjects: [], supply_days: 14, analysis_days: 14,
        mode: 'actual', total_warehouses: 0, total_articles: 0,
        summary: { total_need: 0, total_can_send: 0, total_deficit: 0, avg_delivery_days: 0, deficit_count: 0, can_send_count: 0, no_wb_count: 0 },
    } as StockNeedResponse;
}

describe('enrichPoolRows · обогащение строк машины данными «Потребности»', () => {
    it('матч по nm_id (article_wb): in_assembly / stocks_wb / per-WB need+stock+asm+transit', () => {
        const need = mkNeed(
            [
                { name: 'Тула', district_key: 'central', cells: { 1: { need: 50, stock: 12 } } },
                { name: 'Казань', district_key: 'volga', cells: { 1: { need: 0, stock: 5 } } },
            ],
            [article({
                nm_id: 1, in_assembly: 30, in_transit: 10, stocks_wb: 17,
                asm_by_warehouse: { Тула: 30 }, transit_by_warehouse: { Казань: 10 },
            })],
        );
        const map = enrichPoolRows([poolRow({ article_wb: '1' })], need, new Set());
        const e = map.get(1);
        expect(e).toBeDefined();
        expect(e!.inAssembly).toBe(30);
        expect(e!.inTransit).toBe(10);
        expect(e!.stocksWb).toBe(17);
        expect(e!.isNew).toBe(false);
        expect(e!.byWh['Тула']).toEqual({ need: 50, stock: 12, asm: 30, transit: 0 });
        expect(e!.byWh['Казань']).toEqual({ need: 0, stock: 5, asm: 0, transit: 10 });
    });

    it('фолбэк: товар не в потребности → нули, без падения', () => {
        const need = mkNeed([{ name: 'Тула', cells: { 1: { need: 50, stock: 0 } } }], [article({ nm_id: 1 })]);
        const map = enrichPoolRows([poolRow({ barcode: 'BX', article_wb: '999' })], need, new Set());
        const e = map.get(999);
        expect(e).toBeDefined();
        expect(e!.inAssembly).toBe(0);
        expect(e!.stocksWb).toBe(0);
        expect(Object.keys(e!.byWh)).toHaveLength(0);
    });

    it('новинка из cold-start set', () => {
        const need = mkNeed([], [article({ nm_id: 7, stocks_wb: 0 })]);
        const map = enrichPoolRows([poolRow({ barcode: 'B7', article_wb: '7' })], need, new Set([7]));
        expect(map.get(7)!.isNew).toBe(true);
    });

    it('null stockNeed → запись с нулями для строк с nm', () => {
        const map = enrichPoolRows([poolRow({ article_wb: '1' })], null, new Set());
        const e = map.get(1);
        expect(e).toBeDefined();
        expect(e!.inAssembly).toBe(0);
        expect(e!.stocksWb).toBe(0);
        expect(Object.keys(e!.byWh)).toHaveLength(0);
    });

    it('строка без article_wb (nm=0) пропускается', () => {
        const map = enrichPoolRows([poolRow({ barcode: 'B0', article_wb: null })], null, new Set());
        expect(map.size).toBe(0);
    });

    it('Decimal-строки коэрсятся в number (need/stock/in_assembly)', () => {
        const need = mkNeed(
            [{ name: 'Тула', cells: {} }],
            [article({ nm_id: 1, in_assembly: '30' as unknown as number, stocks_wb: '17' as unknown as number })],
        );
        // per-WB cell с Decimal-строками
        (need.warehouses[0].articles as Record<number, { need: unknown; stock: unknown; avg_daily: number }>)[1] =
            { need: '50' as unknown, stock: '12' as unknown, avg_daily: 0 };
        const map = enrichPoolRows([poolRow({ article_wb: '1' })], need, new Set());
        const e = map.get(1)!;
        expect(e.inAssembly).toBe(30);
        expect(e.stocksWb).toBe(17);
        expect(e.byWh['Тула']).toEqual({ need: 50, stock: 12, asm: 0, transit: 0 });
    });
});

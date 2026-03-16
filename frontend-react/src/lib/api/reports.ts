/** Reports API methods */
import { ApiClient } from './client';
import type { DdsMonthRow, Transaction } from '@/types/api';

export function addReportMethods(api: ApiClient) {
    return {
        getBalance() {
            return api.request<Array<{
                account: string; account_name: string | null;
                currency: string; balance: number;
            }>>('GET', '/api/v1/reports/balance');
        },
        getDashboardSummary(dateFrom?: string, dateTo?: string) {
            const q = new URLSearchParams();
            if (dateFrom) q.set('date_from', dateFrom);
            if (dateTo) q.set('date_to', dateTo);
            return api.request<{
                balance_rub: number; balance_cny: number;
                month_income: number; month_expense: number;
                orders_count: number; orders_total_cny: number;
                debt_rub: number; debt_cny: number;
                inbox_count: number; accounts_count: number;
                daily_cashflow: Array<{ date: string; income: number; expense: number }>;
                expense_by_category: Array<{ name: string; value: number }>;
                income_counterparties: Array<{ name: string; total: number; count: number }>;
                date_from: string; date_to: string;
            }>('GET', `/api/v1/reports/dashboard_summary?${q.toString()}`);
        },
        getDailyFiltered(dateFrom: string, dateTo: string, cpKey?: string, category?: string) {
            const q = new URLSearchParams();
            q.set('date_from', dateFrom);
            q.set('date_to', dateTo);
            if (cpKey) q.set('cp_key', cpKey);
            if (category) q.set('category', category);
            return api.request<Array<{ date: string; income: number; expense: number }>>(
                'GET', `/api/v1/reports/dashboard_daily_filtered?${q.toString()}`
            );
        },
        getFilteredTransactions(dateFrom: string, dateTo: string, opts: {
            cpKey?: string; category?: string; flow?: string; limit?: number; offset?: number;
        } = {}) {
            const q = new URLSearchParams();
            q.set('date_from', dateFrom);
            q.set('date_to', dateTo);
            if (opts.cpKey) q.set('cp_key', opts.cpKey);
            if (opts.category) q.set('category', opts.category);
            if (opts.flow) q.set('flow', opts.flow);
            if (opts.limit) q.set('limit', String(opts.limit));
            if (opts.offset) q.set('offset', String(opts.offset));
            return api.request<{
                total: number;
                items: Array<{
                    date: string; counterparty: string; income: number; expense: number;
                    purpose: string; category: string; account: string;
                }>;
            }>('GET', `/api/v1/reports/dashboard_transactions?${q.toString()}`);
        },
        getCategoryCounterparties(dateFrom: string, dateTo: string, category: string) {
            const q = new URLSearchParams({ date_from: dateFrom, date_to: dateTo, category });
            return api.request<Array<{ key: string; name: string; total: number; count: number }>>(
                'GET', `/api/v1/reports/category_counterparties?${q.toString()}`
            );
        },
        getDDS(params: { start?: string; end?: string } = {}) {
            const q = new URLSearchParams();
            if (params.start) q.set('date_from', params.start);
            if (params.end) q.set('date_to', params.end);
            return api.request<{ rows: DdsMonthRow[]; totals: Record<string, number> }>('GET', `/api/v1/reports/dds_month?${q}`);
        },
        getTransactions(params: { start?: string; end?: string; account?: string } = {}) {
            const q = new URLSearchParams();
            if (params.start) q.set('date_from', params.start);
            if (params.end) q.set('date_to', params.end);
            if (params.account) q.set('account', params.account);
            return api.request<Transaction[]>('GET', `/api/v1/reports/transactions?${q}`);
        },
        getDDSMonth(year: number, month: number, currency = 'RUB') {
            return api.request<{ rows: Array<{ cat_lvl1: string; cat_lvl2: string; income: number; expense: number }>; totals: Record<string, number> }>('GET', `/api/v1/reports/dds_month?year=${year}&month=${month}&currency=${currency}`);
        },
        getDDSPnL(year: number) {
            return api.request<any>('GET', `/api/v1/reports/dds_pnl?year=${year}`);
        },
        getOpiu(dateFrom: string, dateTo: string, brand?: string, article?: string) {
            let url = `/api/v1/reports/opiu?date_from=${dateFrom}&date_to=${dateTo}`;
            if (brand) url += `&brand=${encodeURIComponent(brand)}`;
            if (article) url += `&article=${encodeURIComponent(article)}`;
            return api.request<any>('GET', url);
        },
        getWbBdr(dateFrom: string, dateTo: string, brand?: string, article?: string) {
            let url = `/api/v1/reports/wb_bdr?date_from=${dateFrom}&date_to=${dateTo}`;
            if (brand) url += `&brand=${encodeURIComponent(brand)}`;
            if (article) url += `&article=${encodeURIComponent(article)}`;
            return api.request<any>('GET', url);
        },
        getWbBdrSyncStatus() {
            return api.request<any>('GET', `/api/v1/reports/wb_bdr/sync_status`);
        },
        getWbBdrAvailableWeeks() {
            return api.request<{ available_dates: string[] }>('GET', `/api/v1/reports/wb_bdr/available_weeks`);
        },
        triggerWbBdrSync() {
            return api.request<any>('POST', `/api/v1/reports/wb_bdr/sync`);
        },
        getCostHistory(article?: string, brand?: string) {
            const params = new URLSearchParams();
            if (article) params.set('article', article);
            if (brand) params.set('brand', brand);
            const qs = params.toString();
            return api.request<any>('GET', `/api/v1/reports/cost_history${qs ? '?' + qs : ''}`);
        },
        getBalanceDaily(account: string, currency: string, start: string, end: string) {
            return api.request<Array<{ date: string; balance: number }>>('GET', `/api/v1/reports/balance_daily?account=${encodeURIComponent(account)}&currency=${currency}&date_from=${start}&date_to=${end}`);
        },
        getFxControl(start: string, end: string) {
            return api.request<Array<{ date: string; rate: number; income: number; expense: number }>>('GET', `/api/v1/reports/fx_control?date_from=${start}&date_to=${end}`);
        },
        getCustomsControl(start: string, end: string) {
            return api.request<Array<Record<string, unknown>>>('GET', `/api/v1/reports/customs_control?date_from=${start}&date_to=${end}`);
        },
        getIncomeDailyReport(start: string, end: string) {
            return api.request<Array<{ date: string; income: number; expense: number; net: number }>>('GET', `/api/v1/reports/income_daily?date_from=${start}&date_to=${end}`);
        },
        getTaxRates(year: number) {
            return api.request<any>('GET', `/api/v1/reports/tax_rates?year=${year}`);
        },
        saveTaxRates(payload: { year: number; tax_regime: string; months: any[] }) {
            return api.request<any>('POST', '/api/v1/reports/tax_rates', payload);
        },
        getStockAnalytics(trendDays: number = 7, subject?: string, brand?: string, article?: string) {
            const q = new URLSearchParams();
            q.set('trend_days', String(trendDays));
            if (subject) q.set('subject', subject);
            if (brand) q.set('brand', brand);
            if (article) q.set('article', article);
            return api.request<any>('GET', `/api/v1/reports/stock_analytics?${q.toString()}`);
        },
        syncWarehouseStocks() {
            return api.request<{ synced: number }>('POST', '/api/v1/reports/stock_warehouses/sync');
        },
        getWarehouseStocks() {
            return api.request<any>('GET', '/api/v1/reports/stock_warehouses');
        },
        getStockNeed(supplyDays: number = 14, analysisDays: number = 14, mode: string = 'actual') {
            return api.request<any>('GET', `/api/v1/reports/stock_need?supply_days=${supplyDays}&analysis_days=${analysisDays}&mode=${mode}`);
        },
        async uploadOrderCities(file: File) {
            const formData = new FormData();
            formData.append('file', file);
            return api.uploadFormData<{ ok: boolean; total_mappings: number; affected_rows: number }>(
                '/api/v1/reports/stock_analytics/upload_order_cities', formData
            );
        },
        getOrderGeography(dateFrom: string, dateTo: string, brand?: string, category?: string, article?: string) {
            let url = `/api/v1/reports/order_geography?date_from=${dateFrom}&date_to=${dateTo}`;
            if (brand) url += `&brand=${encodeURIComponent(brand)}`;
            if (category) url += `&category=${encodeURIComponent(category)}`;
            if (article) url += `&article=${encodeURIComponent(article)}`;
            return api.request<any>('GET', url);
        },
    };
}

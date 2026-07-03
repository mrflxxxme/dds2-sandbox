/** Reports API methods */
import { ApiClient } from './client';
import type {
    DdsMonthRow, Transaction, DDSPnLResponse, CostHistoryResponse,
    WbStocksResponse, WbStocksArticlesResponse, WbStockHistoryResponse,
    CostDnaResponse,
    CounterpartyTurnoversResponse, CounterpartyType,
    ColdStartTableResponse,
} from '@/types/api';

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
                daily_income_by_type: Array<{ date: string; marketplace: number; financing: number; other: number }>;
                income_by_type: Array<{ key: string; name: string; value: number; count: number }>;
                expense_by_category: Array<{ name: string; value: number }>;
                expense_by_type: Array<{ type: string | null; type_label: string; value: number; count: number; categories: Array<{ name: string; value: number; count?: number }> }>;
                income_counterparties: Array<{ name: string; key: string; total: number; count: number }>;
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
            return api.request<DDSPnLResponse>('GET', `/api/v1/reports/dds_pnl?year=${year}`);
        },
        getOpiu(dateFrom: string, dateTo: string, brand?: string, article?: string) {
            const q = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
            if (brand) q.set('brand', brand);
            if (article) q.set('article', article);
            return api.request<any>('GET', `/api/v1/reports/opiu?${q.toString()}`);
        },
        getWbBdr(dateFrom: string, dateTo: string, brand?: string, article?: string, groupBy?: string) {
            const q = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
            if (brand) q.set('brand', brand);
            if (article) q.set('article', article);
            if (groupBy) q.set('group_by', groupBy);
            return api.request<any>('GET', `/api/v1/reports/wb_bdr?${q.toString()}`);
        },
        getWbBdrSyncStatus(dateFrom?: string, dateTo?: string) {
            let url = `/api/v1/reports/wb_bdr/sync_status`;
            if (dateFrom && dateTo) {
                url += `?date_from=${dateFrom}&date_to=${dateTo}`;
            }
            return api.request<any>('GET', url);
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
            return api.request<CostHistoryResponse>('GET', `/api/v1/reports/cost_history${qs ? '?' + qs : ''}`);
        },
        getCostDna(params: { periodDays?: number; dateFrom?: string; dateTo?: string } = {}) {
            const q = new URLSearchParams();
            if (params.dateFrom && params.dateTo) {
                q.set('date_from', params.dateFrom);
                q.set('date_to', params.dateTo);
            } else {
                q.set('period_days', String(params.periodDays ?? 30));
            }
            return api.request<CostDnaResponse>('GET', `/api/v1/reports/cost_dna?${q.toString()}`);
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
        getStockAnalytics(trendDays: number = 7, subject?: string, brand?: string, article?: string, mode?: string) {
            const q = new URLSearchParams();
            q.set('trend_days', String(trendDays));
            if (subject) q.set('subject', subject);
            if (brand) q.set('brand', brand);
            if (article) q.set('article', article);
            if (mode) q.set('mode', mode);
            return api.request<any>('GET', `/api/v1/reports/stock_analytics?${q.toString()}`);
        },
        syncWarehouseStocks() {
            return api.request<{ synced: number }>('POST', '/api/v1/reports/stock_warehouses/sync');
        },
        getWarehouseStocks() {
            return api.request<WbStocksResponse>('GET', '/api/v1/reports/stock_warehouses');
        },
        getWarehouseStocksByArticle(search?: string) {
            const q = new URLSearchParams();
            if (search) q.set('search', search);
            const qs = q.toString();
            return api.request<WbStocksArticlesResponse>('GET', `/api/v1/reports/stock_warehouses/articles${qs ? '?' + qs : ''}`);
        },
        getStockHistory(dateFrom: string, dateTo: string, warehouse?: string) {
            const q = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
            if (warehouse) q.set('warehouse', warehouse);
            return api.request<WbStockHistoryResponse>('GET', `/api/v1/reports/stock_warehouses/history?${q.toString()}`);
        },
        getStockNeed(
            supplyDays: number = 14,
            analysisDays: number = 14,
            mode: string = 'actual',
            localizationOptimized: boolean = false,
            onlyAvailable: boolean = false,
            minStockPerMainWarehouse: number = 0,
            localizationTarget: number = 75,
        ) {
            const q = new URLSearchParams();
            q.set('supply_days', String(supplyDays));
            q.set('analysis_days', String(analysisDays));
            q.set('mode', mode);
            if (localizationOptimized) q.set('localization_optimized', 'true');
            if (onlyAvailable) q.set('only_available', 'true');
            if (minStockPerMainWarehouse > 0) q.set('min_stock_per_main_warehouse', String(minStockPerMainWarehouse));
            if (localizationTarget !== 75) q.set('localization_target', String(localizationTarget));
            return api.request<any>('GET', `/api/v1/reports/stock_need?${q.toString()}`);
        },
        getOrderCitiesStatus() {
            return api.request<{ has_data: boolean; total_mappings: number; date_from: string | null; date_to: string | null; last_updated: string | null }>('GET', '/api/v1/reports/stock_analytics/order_cities_status');
        },
        async uploadOrderCities(file: File) {
            const formData = new FormData();
            formData.append('file', file);
            // Use dedicated Next.js API route to avoid rewrite proxy timeout on large files
            return api.uploadFormData<{ ok: boolean; total_mappings: number; affected_rows: number }>(
                '/api/upload-order-cities', formData
            );
        },
        getOrderGeography(dateFrom: string, dateTo: string, brand?: string, category?: string, article?: string) {
            const q = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
            if (brand) q.set('brand', brand);
            if (category) q.set('category', category);
            if (article) q.set('article', article);
            return api.request<any>('GET', `/api/v1/reports/order_geography?${q.toString()}`);
        },
        getCounterpartyTurnovers(params: {
            date_from: string;
            date_to: string;
            type?: CounterpartyType;
            currency?: 'RUB' | 'CNY';
        }) {
            const q = new URLSearchParams();
            q.set('date_from', params.date_from);
            q.set('date_to', params.date_to);
            if (params.type) q.set('type', params.type);
            if (params.currency) q.set('currency', params.currency);
            return api.request<CounterpartyTurnoversResponse>(
                'GET', `/api/v1/reports/counterparty-turnovers?${q.toString()}`
            );
        },
        // localizationTarget=90 (не бэкенд-дефолт 75): концентрация до 75% отбрасывала
        // округа с реальным спросом и ОТКРЫТЫМ складом (напр. СЗФО/СПБ Шушары — 8.5%
        // спроса, но раздутый far-east→Урал вытеснял его за порог). 90% включает такие
        // округа, оставляя на ФФ только самый дальний хвост (ДВ и т.п. — их склады закрыты).
        getColdStartTable(windowDays: number = 30, minPack: number = 5, shipPct: number = 55, shipFloor: number = 50, benchFromProjectId?: number, localizationTarget: number = 90) {
            const q = new URLSearchParams();
            q.set('window_days', String(windowDays));
            q.set('min_pack', String(minPack));
            q.set('ship_pct', String(shipPct));
            q.set('ship_floor', String(shipFloor));
            q.set('localization_target', String(localizationTarget));
            if (benchFromProjectId) q.set('bench_from_project_id', String(benchFromProjectId));
            return api.request<ColdStartTableResponse>(
                'GET', `/api/v1/reports/cold_start_table?${q.toString()}`
            );
        },
    };
}

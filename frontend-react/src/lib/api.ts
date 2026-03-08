/**
 * DDS API Client — typed HTTP client with JWT auth.
 */

import type {
    TokenResponse, UserProfile, Project, ProjectMember, ProjectInvite,
    Transaction, UnassignedGroupRow, Account, CounterpartyCategory,
    CategoryRef, BalanceRow, DdsMonthRow, CashflowDailyRow, PlannedPayment,
    PlannedIncome, WbPayout, CostOrder, CostOrderItem, Nomenclature,
    DutyRule, IntegrationKey, SyncLog, FunnelDayRow, FunnelSummary,
    MessageResponse, ImportResult, Order, LeadTime,
} from '@/types/api';

const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

class ApiClient {
    private _isRefreshing = false;

    private getToken(): string | null {
        if (typeof window === 'undefined') return null;
        return localStorage.getItem('dds_token');
    }

    setToken(token: string) {
        localStorage.setItem('dds_token', token);
    }

    getRefreshToken(): string | null {
        if (typeof window === 'undefined') return null;
        return localStorage.getItem('dds_refresh_token');
    }

    setRefreshToken(token: string) {
        localStorage.setItem('dds_refresh_token', token);
    }

    clearToken() {
        localStorage.removeItem('dds_token');
        localStorage.removeItem('dds_refresh_token');
        localStorage.removeItem('dds_project_id');
    }

    isAuthenticated(): boolean {
        return !!this.getToken();
    }

    getProjectId(): number | null {
        if (typeof window === 'undefined') return null;
        const pid = localStorage.getItem('dds_project_id');
        return pid ? parseInt(pid) : null;
    }

    setProjectId(id: number) {
        localStorage.setItem('dds_project_id', String(id));
    }

    private async tryRefresh(): Promise<boolean> {
        const refreshToken = this.getRefreshToken();
        if (!refreshToken || this._isRefreshing) return false;

        this._isRefreshing = true;
        try {
            const res = await fetch(`${API_URL}/api/v1/auth/refresh`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_token: refreshToken }),
            });
            if (res.ok) {
                const data = await res.json();
                this.setToken(data.access_token);
                if (data.refresh_token) this.setRefreshToken(data.refresh_token);
                return true;
            }
        } catch { /* refresh failed */ }
        finally { this._isRefreshing = false; }
        return false;
    }

    private async request<T>(
        method: string,
        path: string,
        body?: unknown,
    ): Promise<T> {
        const headers: Record<string, string> = {
            'Content-Type': 'application/json',
        };

        const token = this.getToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;

        const projectId = this.getProjectId();
        if (projectId) headers['X-Project-Id'] = String(projectId);

        let res = await fetch(`${API_URL}${path}`, {
            method,
            headers,
            body: body ? JSON.stringify(body) : undefined,
        });

        // On 401 — try refresh before giving up
        if (res.status === 401) {
            const refreshed = await this.tryRefresh();
            if (refreshed) {
                // Retry the original request with new token
                headers['Authorization'] = `Bearer ${this.getToken()}`;
                res = await fetch(`${API_URL}${path}`, {
                    method,
                    headers,
                    body: body ? JSON.stringify(body) : undefined,
                });
            }
            if (res.status === 401) {
                this.clearToken();
                if (typeof window !== 'undefined') {
                    window.location.href = '/login';
                }
                throw new Error('Unauthorized');
            }
        }

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            const detail = err.detail ?? err.error?.message;
            if (typeof detail === 'string') throw new Error(detail);
            if (Array.isArray(detail)) throw new Error(detail.map((d: any) => d.msg || JSON.stringify(d)).join('; '));
            throw new Error(typeof detail === 'object' ? JSON.stringify(detail) : `Error ${res.status}`);
        }

        return res.json();
    }

    // Auth
    login(username: string, password: string) {
        return this.request<{ access_token: string; token_type: string }>(
            'POST', '/api/v1/auth/login', { username, password }
        );
    }

    register(data: { username: string; password: string; email?: string; first_name?: string; last_name?: string }) {
        return this.request<{ access_token: string; token_type: string }>(
            'POST', '/api/v1/auth/register', data
        );
    }

    getProfile() {
        return this.request<{
            id: number; username: string; email: string | null;
            first_name: string | null; last_name: string | null;
            is_active: boolean; created_at: string;
        }>('GET', '/api/v1/auth/me');
    }

    updateProfile(data: { email?: string; first_name?: string; last_name?: string }) {
        return this.request<UserProfile>('PUT', '/api/v1/auth/me', data);
    }

    changePassword(old_password: string, new_password: string) {
        return this.request<MessageResponse>('POST', '/api/v1/auth/change_password', { old_password, new_password });
    }

    // Projects
    getProjects() {
        return this.request<Array<{
            id: number; name: string; slug: string; owner_id: number;
            created_at: string; members_count: number;
        }>>('GET', '/api/v1/projects');
    }

    getProject(slug: string) {
        return this.request<{
            id: number; name: string; slug: string; owner_id: number;
            created_at: string;
        }>('GET', `/api/v1/projects/${slug}`);
    }

    createProject(name: string) {
        return this.request<Project>('POST', '/api/v1/projects', { name });
    }

    deleteProject(slug: string) {
        return this.request<MessageResponse>('DELETE', `/api/v1/projects/${slug}`);
    }

    // Team
    getMembers(slug: string) {
        return this.request<Array<{
            id: number; user_id: number; username: string;
            email: string | null; first_name: string | null;
            last_name: string | null; joined_at: string;
        }>>('GET', `/api/v1/projects/${slug}/members`);
    }

    inviteByEmail(slug: string, email: string) {
        return this.request<ProjectInvite>('POST', `/api/v1/projects/${slug}/invite?email=${encodeURIComponent(email)}`);
    }

    getInviteLink(slug: string) {
        return this.request<{ invite_token: string; link: string }>('GET', `/api/v1/projects/${slug}/invite-link`);
    }

    getInvites(slug: string) {
        return this.request<Array<{
            id: number; email: string | null; invite_token: string;
            status: string; created_at: string; accepted_at: string | null;
        }>>('GET', `/api/v1/projects/${slug}/invites`);
    }

    removeMember(slug: string, userId: number) {
        return this.request<MessageResponse>('DELETE', `/api/v1/projects/${slug}/members/${userId}`);
    }

    acceptInvite(token: string) {
        return this.request<MessageResponse>('POST', `/api/v1/projects/invite/accept/${token}`);
    }

    // Integrations
    getIntegrationKeys() {
        return this.request<Array<{
            id: number; service: string; label: string | null;
            encrypted_key: string; is_active: boolean;
            created_at: string; last_sync_at: string | null;
        }>>('GET', '/api/v1/integrations/keys');
    }

    addIntegrationKey(service: string, api_key: string, label?: string) {
        return this.request<IntegrationKey>('POST', '/api/v1/integrations/keys', { service, api_key, label });
    }

    deleteIntegrationKey(id: number) {
        return this.request<MessageResponse>('DELETE', `/api/v1/integrations/keys/${id}`);
    }

    syncWb(keyId: number, dateFrom: string) {
        return this.request<{ ok: boolean; rows: number }>('POST', `/api/v1/integrations/sync/wb/${keyId}?date_from=${dateFrom}`);
    }

    getSyncLog() {
        return this.request<Array<{
            id: number; service: string; sync_type: string; status: string;
            started_at: string; finished_at: string | null;
            rows_fetched: number; rows_inserted: number; error_msg: string | null;
        }>>('GET', '/api/v1/integrations/sync_log');
    }

    // Reports
    getBalance() {
        return this.request<Array<{
            account: string; account_name: string | null;
            currency: string; balance: number;
        }>>('GET', '/api/v1/reports/balance');
    }

    getDashboardSummary(dateFrom?: string, dateTo?: string) {
        const q = new URLSearchParams();
        if (dateFrom) q.set('date_from', dateFrom);
        if (dateTo) q.set('date_to', dateTo);
        return this.request<{
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
    }

    getDailyFiltered(dateFrom: string, dateTo: string, cpKey?: string, category?: string) {
        const q = new URLSearchParams();
        q.set('date_from', dateFrom);
        q.set('date_to', dateTo);
        if (cpKey) q.set('cp_key', cpKey);
        if (category) q.set('category', category);
        return this.request<Array<{ date: string; income: number; expense: number }>>(
            'GET', `/api/v1/reports/dashboard_daily_filtered?${q.toString()}`
        );
    }

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
        return this.request<{
            total: number;
            items: Array<{
                date: string; counterparty: string; income: number; expense: number;
                purpose: string; category: string; account: string;
            }>;
        }>('GET', `/api/v1/reports/dashboard_transactions?${q.toString()}`);
    }

    getCategoryCounterparties(dateFrom: string, dateTo: string, category: string) {
        const q = new URLSearchParams({ date_from: dateFrom, date_to: dateTo, category });
        return this.request<Array<{ key: string; name: string; total: number; count: number }>>(
            'GET', `/api/v1/reports/category_counterparties?${q.toString()}`
        );
    }

    getDDS(params: { start?: string; end?: string } = {}) {
        const q = new URLSearchParams();
        if (params.start) q.set('date_from', params.start);
        if (params.end) q.set('date_to', params.end);
        return this.request<{ rows: DdsMonthRow[]; totals: Record<string, number> }>('GET', `/api/v1/reports/dds_month?${q}`);
    }

    getTransactions(params: { start?: string; end?: string; account?: string } = {}) {
        const q = new URLSearchParams();
        if (params.start) q.set('date_from', params.start);
        if (params.end) q.set('date_to', params.end);
        if (params.account) q.set('account', params.account);
        return this.request<Transaction[]>('GET', `/api/v1/reports/transactions?${q}`);
    }

    // Transactions
    searchTransactions(params: Record<string, unknown> = {}) {
        return this.request<Transaction[]>('POST', '/api/v1/transactions/search', params);
    }
    getUnassigned(limit = 500) {
        return this.request<Transaction[]>('GET', `/api/v1/transactions/unassigned?limit=${limit}`);
    }
    getUnassignedGrouped() {
        return this.request<UnassignedGroupRow[]>('GET', '/api/v1/transactions/unassigned_grouped');
    }
    assignCategory(data: { txn_id: string; cat_lvl1: string; cat_lvl2?: string }) {
        return this.request<MessageResponse>('POST', '/api/v1/transactions/assign_category', data);
    }
    assignCategoryBulk(data: { cp_key: string; cat_lvl1: string; cat_lvl2?: string }) {
        return this.request<{ updated: number }>('POST', '/api/v1/transactions/assign_category_bulk', data);
    }
    assignCategoryByIds(data: { txn_ids: string[]; cat_lvl1: string; cat_lvl2?: string }) {
        return this.request<{ updated: number }>('POST', '/api/v1/transactions/assign_category_by_ids', data);
    }

    // Reports
    getDDSMonth(year: number, month: number, currency = 'RUB') {
        return this.request<{ rows: Array<{ cat_lvl1: string; cat_lvl2: string; income: number; expense: number }>; totals: Record<string, number> }>('GET', `/api/v1/reports/dds_month?year=${year}&month=${month}&currency=${currency}`);
    }
    getDDSPnL(year: number) {
        return this.request<any>('GET', `/api/v1/reports/dds_pnl?year=${year}`);
    }
    getWbBdr(dateFrom: string, dateTo: string, brand?: string, article?: string, mode?: string) {
        let url = `/api/v1/reports/wb_bdr?date_from=${dateFrom}&date_to=${dateTo}`;
        if (brand) url += `&brand=${encodeURIComponent(brand)}`;
        if (article) url += `&article=${encodeURIComponent(article)}`;
        if (mode) url += `&mode=${mode}`;
        return this.request<any>('GET', url);
    }
    getWbBdrSyncStatus() {
        return this.request<any>('GET', `/api/v1/reports/wb_bdr/sync_status`);
    }
    triggerWbBdrSync() {
        return this.request<any>('POST', `/api/v1/reports/wb_bdr/sync`);
    }
    getCostHistory(article?: string, brand?: string) {
        const params = new URLSearchParams();
        if (article) params.set('article', article);
        if (brand) params.set('brand', brand);
        const qs = params.toString();
        return this.request<any>('GET', `/api/v1/reports/cost_history${qs ? '?' + qs : ''}`);
    }
    getBalanceDaily(account: string, currency: string, start: string, end: string) {
        return this.request<Array<{ date: string; balance: number }>>('GET', `/api/v1/reports/balance_daily?account=${encodeURIComponent(account)}&currency=${currency}&date_from=${start}&date_to=${end}`);
    }
    getFxControl(start: string, end: string) {
        return this.request<Array<{ date: string; rate: number; income: number; expense: number }>>('GET', `/api/v1/reports/fx_control?date_from=${start}&date_to=${end}`);
    }
    getCustomsControl(start: string, end: string) {
        return this.request<Array<Record<string, unknown>>>('GET', `/api/v1/reports/customs_control?date_from=${start}&date_to=${end}`);
    }
    getIncomeDailyReport(start: string, end: string) {
        return this.request<Array<{ date: string; income: number; expense: number; net: number }>>('GET', `/api/v1/reports/income_daily?date_from=${start}&date_to=${end}`);
    }

    // Refs — Accounts
    getAccounts() {
        return this.request<Account[]>('GET', '/api/v1/refs/accounts');
    }
    upsertAccount(data: Partial<Account>) {
        return this.request<Account>('POST', '/api/v1/refs/accounts', data);
    }
    deleteAccount(id: number) {
        return this.request<MessageResponse>('DELETE', `/api/v1/refs/accounts/${id}`);
    }

    // Refs — CP Categories
    getCpCategories() {
        return this.request<CounterpartyCategory[]>('GET', '/api/v1/refs/cp_categories');
    }
    upsertCpCategory(data: Partial<CounterpartyCategory>) {
        return this.request<CounterpartyCategory>('POST', '/api/v1/refs/cp_categories', data);
    }
    deleteCpCategory(id: number) {
        return this.request<MessageResponse>('DELETE', `/api/v1/refs/cp_categories/${id}`);
    }

    // Refs — Overrides
    getOverrides() {
        return this.request<Array<{ id: number; field: string; pattern: string; value: string }>>('GET', '/api/v1/refs/overrides');
    }
    deleteOverride(id: number) {
        return this.request<MessageResponse>('DELETE', `/api/v1/refs/overrides/${id}`);
    }

    // Refs — Opening balances
    getOpeningBalances() {
        return this.request<Array<{ id: number; account: string; currency: string; amount: number; date: string }>>('GET', '/api/v1/refs/opening_balances');
    }
    upsertOpeningBalance(data: { account: string; currency: string; amount: number; date: string }) {
        return this.request<MessageResponse>('POST', '/api/v1/refs/opening_balances', data);
    }

    // Refs — Category reference
    getCategoryRef() {
        return this.request<CategoryRef[]>('GET', '/api/v1/refs/categories');
    }
    addCategoryRef(data: Partial<CategoryRef>) {
        return this.request<CategoryRef>('POST', '/api/v1/refs/categories', data);
    }
    deleteCategoryRef(id: number) {
        return this.request<MessageResponse>('DELETE', `/api/v1/refs/categories/${id}`);
    }

    // Cost — Orders
    getCostOrders() {
        return this.request<CostOrder[]>('GET', '/api/v1/cost/orders');
    }
    createCostOrder(data: Partial<CostOrder>) {
        return this.request<CostOrder>('POST', '/api/v1/cost/orders', data);
    }
    updateCostOrder(orderNo: string, data: Partial<CostOrder>) {
        return this.request<CostOrder>('PUT', `/api/v1/cost/orders/${encodeURIComponent(orderNo)}`, data);
    }
    deleteCostOrder(orderNo: string) {
        return this.request<MessageResponse>('DELETE', `/api/v1/cost/orders/${encodeURIComponent(orderNo)}`);
    }
    getCostOrderItems(orderNo: string) {
        return this.request<CostOrderItem[]>('GET', `/api/v1/cost/orders/${encodeURIComponent(orderNo)}/items`);
    }
    generatePlan(orderNo: string) {
        return this.request<{ ok: boolean; order_no: string; payments_created: number; plan: Record<string, unknown> }>('POST', `/api/v1/cost/orders/${encodeURIComponent(orderNo)}/generate_plan`);
    }

    // Cost — Nomenclature
    getNomenclature() {
        return this.request<Nomenclature[]>('GET', '/api/v1/cost/nomenclature');
    }
    syncNomenclature() {
        return this.request<{ ok: boolean; synced: number }>('POST', '/api/v1/integrations/wb/sync_nomenclature');
    }

    // Cost — Duty rules
    getDutyRules() {
        return this.request<DutyRule[]>('GET', '/api/v1/cost/duty_rules');
    }
    addDutyRule(data: Partial<DutyRule>) {
        return this.request<DutyRule>('POST', '/api/v1/cost/duty_rules', data);
    }
    deleteDutyRule(id: number) {
        return this.request<MessageResponse>('DELETE', `/api/v1/cost/duty_rules/${id}`);
    }

    // Cost — VAT Rate
    getVatRate() {
        return this.request<{ vat_rate: number }>('GET', '/api/v1/cost/vat_rate');
    }
    setVatRate(vatRate: number) {
        return this.request<{ status: string; vat_rate: number }>('PUT', '/api/v1/cost/vat_rate', { vat_rate: vatRate });
    }

    // Tax Rates
    getTaxRates(year: number) {
        return this.request<any>('GET', `/api/v1/reports/tax_rates?year=${year}`);
    }
    saveTaxRates(payload: { year: number; tax_regime: string; months: any[] }) {
        return this.request<any>('POST', '/api/v1/reports/tax_rates', payload);
    }

    // Planning — Orders
    getPlanningOrders() {
        return this.request<Order[]>('GET', '/api/v1/planning/orders');
    }
    createPlanningOrder(data: Partial<Order>) {
        return this.request<Order>('POST', '/api/v1/planning/orders', data);
    }
    upsertPlanningOrder(data: Partial<Order>) {
        return this.request<Order>('POST', '/api/v1/planning/orders', data);
    }
    deletePlanningOrder(id: number) {
        return this.request<MessageResponse>('DELETE', `/api/v1/planning/orders/${id}`);
    }
    getPlanningOrderSummary(orderNo: string) {
        return this.request<{ order: Order; planned_payments: PlannedPayment[]; totals: Record<string, number> }>('GET', `/api/v1/planning/orders/${encodeURIComponent(orderNo)}/summary`);
    }

    // Planning — Payments
    getPlanningPayments() {
        return this.request<PlannedPayment[]>('GET', '/api/v1/planning/payments');
    }
    createPlanningPayment(data: Partial<PlannedPayment>) {
        return this.request<PlannedPayment>('POST', '/api/v1/planning/payments', data);
    }
    deletePlanningPayment(id: number) {
        return this.request<MessageResponse>('DELETE', `/api/v1/planning/payments/${id}`);
    }
    markPaymentPaid(id: number) {
        return this.request<MessageResponse>('POST', `/api/v1/planning/payments/${id}/mark_paid`);
    }
    syncPlanPayments() {
        return this.request<{ ok: boolean; synced: number }>('POST', '/api/v1/planning/sync_plan_payments');
    }

    // Planning — Incomes
    getPlanningIncomes() {
        return this.request<PlannedIncome[]>('GET', '/api/v1/planning/incomes');
    }
    createPlanningIncome(data: Partial<PlannedIncome>) {
        return this.request<PlannedIncome>('POST', '/api/v1/planning/incomes', data);
    }
    deletePlanningIncome(id: number) {
        return this.request<MessageResponse>('DELETE', `/api/v1/planning/incomes/${id}`);
    }
    refreshWbForecast(trendDays: number = 7) {
        return this.request<{ ok: boolean; daily_avg: number; weekly_total: number; weekday_pattern: Record<string, number>; created: number; forecast_days: number }>('POST', `/api/v1/planning/wb_forecast/refresh?trend_days=${trendDays}`);
    }

    // Planning — WB Payouts
    getWbPayouts() {
        return this.request<WbPayout[]>('GET', '/api/v1/planning/wb_payouts');
    }
    deleteWbPayout(id: number) {
        return this.request<MessageResponse>('DELETE', `/api/v1/planning/wb_payouts/${id}`);
    }

    // Planning — Cashflow
    getCashflowDaily() {
        return this.request<CashflowDailyRow[]>('GET', '/api/v1/planning/cashflow_daily');
    }

    // Planning — Lead Times
    getLeadTimes() {
        return this.request<LeadTime[]>('GET', '/api/v1/planning/lead_times');
    }
    upsertLeadTime(data: Partial<LeadTime>) {
        return this.request<LeadTime>('POST', '/api/v1/planning/lead_times', data);
    }

    // Planning — Customs
    getCustomsDt() {
        return this.request<Array<{ id: number; dt_number: string; dt_date: string; amount_rub: number; order_no?: string; note?: string }>>('GET', '/api/v1/planning/customs_dt');
    }
    getCustomsAlloc() {
        return this.request<Array<{ id: number; order_no: number; dt_number: string; alloc_amount: number }>>('GET', '/api/v1/planning/customs/alloc');
    }
    getCustomsTopup() {
        return this.request<Array<{ id: number; date: string; amount_rub: number; purpose: string; allocated: number; remaining: number }>>('GET', '/api/v1/planning/customs/topup');
    }
    updateCustomsDt(dtId: number, payload: { order_no?: number | null; note?: string }) {
        return this.request<{ ok: boolean }>('PUT', `/api/v1/planning/customs_dt/${dtId}`, payload);
    }

    async uploadFtsPdf(file: File): Promise<{ ok: boolean; created: number; skipped: number; parsed: Array<Record<string, unknown>> }> {
        const formData = new FormData();
        formData.append('file', file);
        const headers: Record<string, string> = {};
        const token = this.getToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;
        const projectId = this.getProjectId();
        if (projectId) headers['X-Project-Id'] = String(projectId);
        const res = await fetch(`${API_URL}/api/v1/planning/customs_dt/upload_fts`, {
            method: 'POST', headers, body: formData,
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `Error ${res.status}`);
        }
        return res.json();
    }

    getAccountsList() {
        return this.request<Account[]>('GET', '/api/v1/planning/accounts_list');
    }
    getCandidateTransactions(account?: string) {
        const params = account ? `?account=${encodeURIComponent(account)}` : '';
        return this.request<Transaction[]>('GET', `/api/v1/planning/candidate_transactions${params}`);
    }
    getFactLinks(paymentId: number) {
        return this.request<Array<{ id: number; payment_id: number; txn_id: string; amount_rub: number; note?: string }>>('GET', `/api/v1/planning/fact_links/${paymentId}`);
    }
    createFactLink(data: { payment_id: number; txn_id: string; amount_rub: number; note?: string }) {
        return this.request<{ ok: boolean; id: number }>('POST', '/api/v1/planning/fact_links', data);
    }
    deleteFactLink(linkId: number) {
        return this.request<MessageResponse>('DELETE', `/api/v1/planning/fact_links/${linkId}`);
    }
    upsertPlanningPayment(data: Partial<PlannedPayment>) {
        return this.request<PlannedPayment>('POST', '/api/v1/planning/payments', data);
    }

    // Import
    getImportLogs() {
        return this.request<Array<{ id: number; filename: string; source_type: string; imported_at: string; rows_raw: number; rows_inserted: number; rows_skipped: number; status: string }>>('GET', '/api/v1/import/logs');
    }

    // Upload
    async uploadFile(file: File, sourceType: string, accountNo: string): Promise<ImportResult> {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('source_type', sourceType);
        if (accountNo) formData.append('account_no', accountNo);

        const headers: Record<string, string> = {};
        const token = this.getToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;
        const projectId = this.getProjectId();
        if (projectId) headers['X-Project-Id'] = String(projectId);

        const res = await fetch(`${API_URL}/api/v1/import/upload`, {
            method: 'POST',
            headers,
            body: formData,
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            const detail = err.detail;
            if (typeof detail === 'string') throw new Error(detail);
            if (Array.isArray(detail)) throw new Error(detail.map((d: any) => `${d.loc ? d.loc.join('.') + ': ' : ''}${d.msg || JSON.stringify(d)}`).join('; '));
            throw new Error(JSON.stringify(detail) || `Error ${res.status}`);
        }
        return res.json();
    }

    async uploadCostFile(orderNo: string, file: File): Promise<{ inserted: number; unrecognized: number }> {
        const formData = new FormData();
        formData.append('file', file);
        const headers: Record<string, string> = {};
        const token = this.getToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;
        const projectId = this.getProjectId();
        if (projectId) headers['X-Project-Id'] = String(projectId);
        const res = await fetch(`${API_URL}/api/v1/cost/orders/${encodeURIComponent(orderNo)}/upload`, {
            method: 'POST', headers, body: formData,
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `Error ${res.status}`);
        }
        return res.json();
    }

    // Wb Payouts import
    async uploadWbPayouts(file: File): Promise<{ ok: boolean; created: number; updated: number; skipped: number }> {
        const formData = new FormData();
        formData.append('file', file);
        const headers: Record<string, string> = {};
        const token = this.getToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;
        const projectId = this.getProjectId();
        if (projectId) headers['X-Project-Id'] = String(projectId);
        const res = await fetch(`${API_URL}/api/v1/planning/wb_payouts/upload`, {
            method: 'POST', headers, body: formData,
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `Error ${res.status}`);
        }
        return res.json();
    }
    // ─── Funnel (Воронка продаж) ─────────────────────────────────
    async syncFunnel(dateFrom: string, dateTo: string): Promise<{ ok: boolean; days_synced: number }> {
        return this.request('POST', '/api/v1/funnel/sync', { date_from: dateFrom, date_to: dateTo });
    }
    async getFunnelData(params?: { date_from?: string; date_to?: string; brand?: string; vendor_code?: string; subject?: string }): Promise<{ data: FunnelDayRow[]; detailed: boolean; tax_rate: number }> {
        const q = new URLSearchParams();
        if (params?.date_from) q.set('date_from', params.date_from);
        if (params?.date_to) q.set('date_to', params.date_to);
        if (params?.brand) q.set('brand', params.brand);
        if (params?.vendor_code) q.set('vendor_code', params.vendor_code);
        if (params?.subject) q.set('subject', params.subject);
        return this.request('GET', `/api/v1/funnel/data?${q.toString()}`);
    }
    async getFunnelSummary(dateFrom?: string, dateTo?: string, brand?: string, subject?: string): Promise<FunnelSummary> {
        const q = new URLSearchParams();
        if (dateFrom) q.set('date_from', dateFrom);
        if (dateTo) q.set('date_to', dateTo);
        if (brand) q.set('brand', brand);
        if (subject) q.set('subject', subject);
        return this.request('GET', `/api/v1/funnel/summary?${q.toString()}`);
    }
    async getFunnelFilters(): Promise<{ brands: string[]; subjects: string[]; vendor_codes: string[]; min_date: string | null; max_date: string | null }> {
        return this.request('GET', '/api/v1/funnel/filters');
    }
    async getFunnelCosts(): Promise<Array<{ nm_id: number; cost_price: number; source: string }>> {
        return this.request('GET', '/api/v1/funnel/costs');
    }
    async setFunnelCost(nmId: number, costPrice: number): Promise<MessageResponse> {
        return this.request('POST', '/api/v1/funnel/cost', { nm_id: nmId, cost_price: costPrice });
    }
    async getFunnelTax(): Promise<{ tax_rate: number }> {
        return this.request('GET', '/api/v1/funnel/tax');
    }
    async setFunnelTax(taxRate: number): Promise<MessageResponse> {
        return this.request('POST', '/api/v1/funnel/tax', { tax_rate: taxRate });
    }
    async getSyncStatus(): Promise<{ scheduler: Record<string, unknown>; last_syncs: Array<{ id: number; sync_type: string; status: string; rows_inserted: number; started_at: string | null; finished_at: string | null; error_msg: string | null }> }> {
        return this.request('GET', '/api/v1/funnel/sync_status');
    }
    // ─── Day Analysis ─────────────────────────────────────────────
    async getDayAnalysis(params: { target_date: string; brand?: string; subject?: string; trend_days?: number }): Promise<Record<string, unknown>> {
        const q = new URLSearchParams();
        q.set('target_date', params.target_date);
        if (params.brand) q.set('brand', params.brand);
        if (params.subject) q.set('subject', params.subject);
        if (params.trend_days) q.set('trend_days', String(params.trend_days));
        return this.request('GET', `/api/v1/funnel/day-analysis?${q.toString()}`);
    }
    // ─── Product Trends ───────────────────────────────────────────
    async getProductTrends(params: { trend_days?: number; brand?: string; search?: string } = {}): Promise<{ products: any[]; trend_days: number; total_products: number }> {
        const q = new URLSearchParams();
        if (params.trend_days) q.set('trend_days', String(params.trend_days));
        if (params.brand) q.set('brand', params.brand);
        if (params.search) q.set('search', params.search);
        return this.request('GET', `/api/v1/funnel/trends?${q.toString()}`);
    }
}

export const api = new ApiClient();

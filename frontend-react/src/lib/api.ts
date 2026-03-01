/**
 * DDS API Client — typed HTTP client with JWT auth.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

class ApiClient {
    private getToken(): string | null {
        if (typeof window === 'undefined') return null;
        return localStorage.getItem('dds_token');
    }

    setToken(token: string) {
        localStorage.setItem('dds_token', token);
    }

    clearToken() {
        localStorage.removeItem('dds_token');
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

        const res = await fetch(`${API_URL}${path}`, {
            method,
            headers,
            body: body ? JSON.stringify(body) : undefined,
        });

        if (res.status === 401) {
            this.clearToken();
            if (typeof window !== 'undefined') {
                window.location.href = '/login';
            }
            throw new Error('Unauthorized');
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
        return this.request<any>('PUT', '/api/v1/auth/me', data);
    }

    changePassword(old_password: string, new_password: string) {
        return this.request<any>('POST', '/api/v1/auth/change_password', { old_password, new_password });
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
        return this.request<any>('POST', '/api/v1/projects', { name });
    }

    deleteProject(slug: string) {
        return this.request<any>('DELETE', `/api/v1/projects/${slug}`);
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
        return this.request<any>('POST', `/api/v1/projects/${slug}/invite?email=${encodeURIComponent(email)}`);
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
        return this.request<any>('DELETE', `/api/v1/projects/${slug}/members/${userId}`);
    }

    acceptInvite(token: string) {
        return this.request<any>('POST', `/api/v1/projects/invite/accept/${token}`);
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
        return this.request<any>('POST', '/api/v1/integrations/keys', { service, api_key, label });
    }

    deleteIntegrationKey(id: number) {
        return this.request<any>('DELETE', `/api/v1/integrations/keys/${id}`);
    }

    syncWb(keyId: number, dateFrom: string) {
        return this.request<any>('POST', `/api/v1/integrations/sync/wb/${keyId}?date_from=${dateFrom}`);
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

    getDDS(params: { start?: string; end?: string } = {}) {
        const q = new URLSearchParams();
        if (params.start) q.set('start', params.start);
        if (params.end) q.set('end', params.end);
        return this.request<any>('GET', `/api/v1/reports/dds?${q}`);
    }

    getTransactions(params: { start?: string; end?: string; account?: string } = {}) {
        const q = new URLSearchParams();
        if (params.start) q.set('start', params.start);
        if (params.end) q.set('end', params.end);
        if (params.account) q.set('account', params.account);
        return this.request<any>('GET', `/api/v1/reports/transactions?${q}`);
    }

    // Transactions
    searchTransactions(params: any = {}) {
        return this.request<any>('POST', '/api/v1/transactions/search', params);
    }
    getUnassigned(limit = 500) {
        return this.request<any[]>('GET', `/api/v1/transactions/unassigned?limit=${limit}`);
    }
    getUnassignedGrouped() {
        return this.request<any[]>('GET', '/api/v1/transactions/unassigned_grouped');
    }
    assignCategory(data: any) {
        return this.request<any>('POST', '/api/v1/transactions/assign_category', data);
    }
    assignCategoryBulk(data: any) {
        return this.request<any>('POST', '/api/v1/transactions/assign_category_bulk', data);
    }
    assignCategoryByIds(data: any) {
        return this.request<any>('POST', '/api/v1/transactions/assign_category_by_ids', data);
    }

    // Reports
    getDDSMonth(year: number, month: number, currency = 'RUB') {
        return this.request<any>('GET', `/api/v1/reports/dds_month?year=${year}&month=${month}&currency=${currency}`);
    }
    getBalanceDaily(account: string, currency: string, start: string, end: string) {
        return this.request<any[]>('GET', `/api/v1/reports/balance_daily?account=${encodeURIComponent(account)}&currency=${currency}&start=${start}&end=${end}`);
    }
    getFxControl(start: string, end: string) {
        return this.request<any[]>('GET', `/api/v1/reports/fx_control?start=${start}&end=${end}`);
    }
    getCustomsControl(start: string, end: string) {
        return this.request<any[]>('GET', `/api/v1/reports/customs_control?start=${start}&end=${end}`);
    }
    getIncomeDailyReport(start: string, end: string) {
        return this.request<any[]>('GET', `/api/v1/reports/income_daily?start=${start}&end=${end}`);
    }

    // Refs — Accounts
    getAccounts() {
        return this.request<any[]>('GET', '/api/v1/refs/accounts');
    }
    upsertAccount(data: any) {
        return this.request<any>('POST', '/api/v1/refs/accounts', data);
    }
    deleteAccount(id: number) {
        return this.request<any>('DELETE', `/api/v1/refs/accounts/${id}`);
    }

    // Refs — CP Categories
    getCpCategories() {
        return this.request<any[]>('GET', '/api/v1/refs/cp_categories');
    }
    upsertCpCategory(data: any) {
        return this.request<any>('POST', '/api/v1/refs/cp_categories', data);
    }
    deleteCpCategory(id: number) {
        return this.request<any>('DELETE', `/api/v1/refs/cp_categories/${id}`);
    }

    // Refs — Overrides
    getOverrides() {
        return this.request<any[]>('GET', '/api/v1/refs/overrides');
    }
    deleteOverride(id: number) {
        return this.request<any>('DELETE', `/api/v1/refs/overrides/${id}`);
    }

    // Refs — Opening balances
    getOpeningBalances() {
        return this.request<any[]>('GET', '/api/v1/refs/opening_balances');
    }
    upsertOpeningBalance(data: any) {
        return this.request<any>('POST', '/api/v1/refs/opening_balances', data);
    }

    // Refs — Category reference
    getCategoryRef() {
        return this.request<any[]>('GET', '/api/v1/refs/categories');
    }
    addCategoryRef(data: any) {
        return this.request<any>('POST', '/api/v1/refs/categories', data);
    }
    deleteCategoryRef(id: number) {
        return this.request<any>('DELETE', `/api/v1/refs/categories/${id}`);
    }

    // Cost — Orders
    getCostOrders() {
        return this.request<any[]>('GET', '/api/v1/cost/orders');
    }
    createCostOrder(data: any) {
        return this.request<any>('POST', '/api/v1/cost/orders', data);
    }
    updateCostOrder(orderNo: string, data: any) {
        return this.request<any>('PUT', `/api/v1/cost/orders/${orderNo}`, data);
    }
    deleteCostOrder(orderNo: string) {
        return this.request<any>('DELETE', `/api/v1/cost/orders/${orderNo}`);
    }
    getCostOrderItems(orderNo: string) {
        return this.request<any[]>('GET', `/api/v1/cost/orders/${orderNo}/items`);
    }
    generatePlan(orderNo: string) {
        return this.request<any>('POST', `/api/v1/cost/orders/${orderNo}/generate_plan`);
    }

    // Cost — Nomenclature
    getNomenclature() {
        return this.request<any[]>('GET', '/api/v1/cost/nomenclature');
    }
    syncNomenclature() {
        return this.request<any>('POST', '/api/v1/integrations/wb/sync_nomenclature');
    }

    // Cost — Duty rules
    getDutyRules() {
        return this.request<any[]>('GET', '/api/v1/cost/duty_rules');
    }
    addDutyRule(data: any) {
        return this.request<any>('POST', '/api/v1/cost/duty_rules', data);
    }
    deleteDutyRule(id: number) {
        return this.request<any>('DELETE', `/api/v1/cost/duty_rules/${id}`);
    }

    // Planning — Orders
    getPlanningOrders() {
        return this.request<any[]>('GET', '/api/v1/planning/orders');
    }
    createPlanningOrder(data: any) {
        return this.request<any>('POST', '/api/v1/planning/orders', data);
    }
    upsertPlanningOrder(data: any) {
        return this.request<any>('POST', '/api/v1/planning/orders', data);
    }
    deletePlanningOrder(id: number) {
        return this.request<any>('DELETE', `/api/v1/planning/orders/${id}`);
    }
    getPlanningOrderSummary(orderNo: string) {
        return this.request<any>('GET', `/api/v1/planning/orders/${orderNo}/summary`);
    }

    // Planning — Payments
    getPlanningPayments() {
        return this.request<any[]>('GET', '/api/v1/planning/payments');
    }
    createPlanningPayment(data: any) {
        return this.request<any>('POST', '/api/v1/planning/payments', data);
    }
    deletePlanningPayment(id: number) {
        return this.request<any>('DELETE', `/api/v1/planning/payments/${id}`);
    }
    markPaymentPaid(id: number) {
        return this.request<any>('POST', `/api/v1/planning/payments/${id}/mark_paid`);
    }
    syncPlanPayments() {
        return this.request<any>('POST', '/api/v1/planning/sync_plan_payments');
    }

    // Planning — Incomes
    getPlanningIncomes() {
        return this.request<any[]>('GET', '/api/v1/planning/incomes');
    }
    createPlanningIncome(data: any) {
        return this.request<any>('POST', '/api/v1/planning/incomes', data);
    }
    deletePlanningIncome(id: number) {
        return this.request<any>('DELETE', `/api/v1/planning/incomes/${id}`);
    }

    // Planning — WB Payouts
    getWbPayouts() {
        return this.request<any[]>('GET', '/api/v1/planning/wb_payouts');
    }
    deleteWbPayout(id: number) {
        return this.request<any>('DELETE', `/api/v1/planning/wb_payouts/${id}`);
    }

    // Planning — Cashflow
    getCashflowDaily() {
        return this.request<any[]>('GET', '/api/v1/planning/cashflow_daily');
    }

    // Planning — Lead Times
    getLeadTimes() {
        return this.request<any[]>('GET', '/api/v1/planning/lead_times');
    }
    upsertLeadTime(data: any) {
        return this.request<any>('POST', '/api/v1/planning/lead_times', data);
    }

    // Planning — Customs
    getCustomsDt() {
        return this.request<any[]>('GET', '/api/v1/planning/customs_dt');
    }
    getCustomsAlloc() {
        return this.request<any[]>('GET', '/api/v1/planning/customs/alloc');
    }
    getCustomsTopup() {
        return this.request<any[]>('GET', '/api/v1/planning/customs/topup');
    }

    getAccountsList() {
        return this.request<any[]>('GET', '/api/v1/planning/accounts_list');
    }
    getCandidateTransactions(account?: string) {
        const params = account ? `?account=${encodeURIComponent(account)}` : '';
        return this.request<any[]>('GET', `/api/v1/planning/candidate_transactions${params}`);
    }
    getFactLinks(paymentId: number) {
        return this.request<any[]>('GET', `/api/v1/planning/fact_links/${paymentId}`);
    }
    createFactLink(data: any) {
        return this.request<any>('POST', '/api/v1/planning/fact_links', data);
    }
    deleteFactLink(linkId: number) {
        return this.request<any>('DELETE', `/api/v1/planning/fact_links/${linkId}`);
    }
    upsertPlanningPayment(data: any) {
        return this.request<any>('POST', '/api/v1/planning/payments', data);
    }

    // Import
    getImportLogs() {
        return this.request<any[]>('GET', '/api/v1/import/logs');
    }

    // Upload
    async uploadFile(file: File, sourceType: string, accountNo: string): Promise<any> {
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

    async uploadCostFile(orderNo: string, file: File): Promise<any> {
        const formData = new FormData();
        formData.append('file', file);
        const headers: Record<string, string> = {};
        const token = this.getToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;
        const projectId = this.getProjectId();
        if (projectId) headers['X-Project-Id'] = String(projectId);
        const res = await fetch(`${API_URL}/api/v1/cost/orders/${orderNo}/upload`, {
            method: 'POST', headers, body: formData,
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `Error ${res.status}`);
        }
        return res.json();
    }

    // Wb Payouts import
    async uploadWbPayouts(file: File): Promise<any> {
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
    async syncFunnel(dateFrom: string, dateTo: string): Promise<any> {
        return this.request('POST', '/api/v1/funnel/sync', { date_from: dateFrom, date_to: dateTo });
    }
    async getFunnelData(params?: { date_from?: string; date_to?: string; brand?: string; vendor_code?: string; subject?: string }): Promise<any> {
        const q = new URLSearchParams();
        if (params?.date_from) q.set('date_from', params.date_from);
        if (params?.date_to) q.set('date_to', params.date_to);
        if (params?.brand) q.set('brand', params.brand);
        if (params?.vendor_code) q.set('vendor_code', params.vendor_code);
        if (params?.subject) q.set('subject', params.subject);
        return this.request('GET', `/api/v1/funnel/data?${q.toString()}`);
    }
    async getFunnelSummary(dateFrom?: string, dateTo?: string, brand?: string, subject?: string): Promise<any> {
        const q = new URLSearchParams();
        if (dateFrom) q.set('date_from', dateFrom);
        if (dateTo) q.set('date_to', dateTo);
        if (brand) q.set('brand', brand);
        if (subject) q.set('subject', subject);
        return this.request('GET', `/api/v1/funnel/summary?${q.toString()}`);
    }
    async getFunnelFilters(): Promise<any> {
        return this.request('GET', '/api/v1/funnel/filters');
    }
    async getFunnelCosts(): Promise<any> {
        return this.request('GET', '/api/v1/funnel/costs');
    }
    async setFunnelCost(nmId: number, costPrice: number): Promise<any> {
        return this.request('POST', '/api/v1/funnel/cost', { nm_id: nmId, cost_price: costPrice });
    }
    async getFunnelTax(): Promise<any> {
        return this.request('GET', '/api/v1/funnel/tax');
    }
    async setFunnelTax(taxRate: number): Promise<any> {
        return this.request('POST', '/api/v1/funnel/tax', { tax_rate: taxRate });
    }
    async getSyncStatus(): Promise<any> {
        return this.request('GET', '/api/v1/funnel/sync_status');
    }
}

export const api = new ApiClient();

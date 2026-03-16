/** Planning API methods */
import { ApiClient } from './client';
import type {
    PlannedPayment, PlannedIncome, WbPayout, CashflowDailyRow,
    Order, LeadTime, Account, Transaction, MessageResponse,
} from '@/types/api';

export function addPlanningMethods(api: ApiClient) {
    return {
        // Orders
        getPlanningOrders() { return api.request<Order[]>('GET', '/api/v1/planning/orders'); },
        createPlanningOrder(data: Partial<Order>) { return api.request<Order>('POST', '/api/v1/planning/orders', data); },
        upsertPlanningOrder(data: Partial<Order>) { return api.request<Order>('POST', '/api/v1/planning/orders', data); },
        deletePlanningOrder(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/planning/orders/${id}`); },
        getPlanningOrderSummary(orderNo: string) {
            return api.request<{ order: Order; planned_payments: PlannedPayment[]; totals: Record<string, number> }>('GET', `/api/v1/planning/orders/${encodeURIComponent(orderNo)}/summary`);
        },

        // Payments
        getPlanningPayments() { return api.request<PlannedPayment[]>('GET', '/api/v1/planning/payments'); },
        createPlanningPayment(data: Partial<PlannedPayment>) { return api.request<PlannedPayment>('POST', '/api/v1/planning/payments', data); },
        upsertPlanningPayment(data: Partial<PlannedPayment>) { return api.request<PlannedPayment>('POST', '/api/v1/planning/payments', data); },
        deletePlanningPayment(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/planning/payments/${id}`); },
        markPaymentPaid(id: number) { return api.request<MessageResponse>('POST', `/api/v1/planning/payments/${id}/mark_paid`); },
        syncPlanPayments() { return api.request<{ ok: boolean; synced: number }>('POST', '/api/v1/planning/sync_plan_payments'); },

        // Incomes
        getPlanningIncomes() { return api.request<PlannedIncome[]>('GET', '/api/v1/planning/incomes'); },
        createPlanningIncome(data: Partial<PlannedIncome>) { return api.request<PlannedIncome>('POST', '/api/v1/planning/incomes', data); },
        deletePlanningIncome(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/planning/incomes/${id}`); },
        refreshWbForecast(trendDays: number = 7) {
            return api.request<{ ok: boolean; daily_avg: number; weekly_total: number; weekday_pattern: Record<string, number>; created: number; forecast_days: number }>('POST', `/api/v1/planning/wb_forecast/refresh?trend_days=${trendDays}`);
        },

        // WB Payouts
        getWbPayouts() { return api.request<WbPayout[]>('GET', '/api/v1/planning/wb_payouts'); },
        deleteWbPayout(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/planning/wb_payouts/${id}`); },
        async uploadWbPayouts(file: File) {
            const formData = new FormData();
            formData.append('file', file);
            return api.uploadFormData<{ ok: boolean; created: number; updated: number; skipped: number }>(
                '/api/v1/planning/wb_payouts/upload', formData
            );
        },

        // Cashflow
        getCashflowDaily() { return api.request<CashflowDailyRow[]>('GET', '/api/v1/planning/cashflow_daily'); },

        // Lead Times
        getLeadTimes() { return api.request<LeadTime[]>('GET', '/api/v1/planning/lead_times'); },
        upsertLeadTime(data: Partial<LeadTime>) { return api.request<LeadTime>('POST', '/api/v1/planning/lead_times', data); },

        // Customs
        getCustomsDt() {
            return api.request<Array<{ id: number; dt_number: string; dt_date: string; amount_rub: number; order_no?: string; note?: string }>>('GET', '/api/v1/planning/customs_dt');
        },
        getCustomsAlloc() {
            return api.request<Array<{ id: number; order_no: number; dt_number: string; alloc_amount: number }>>('GET', '/api/v1/planning/customs/alloc');
        },
        getCustomsTopup() {
            return api.request<Array<{ id: number; date: string; amount_rub: number; purpose: string; allocated: number; remaining: number }>>('GET', '/api/v1/planning/customs/topup');
        },
        updateCustomsDt(dtId: number, payload: { order_no?: number | null; note?: string }) {
            return api.request<{ ok: boolean }>('PUT', `/api/v1/planning/customs_dt/${dtId}`, payload);
        },
        async uploadFtsPdf(file: File) {
            const formData = new FormData();
            formData.append('file', file);
            return api.uploadFormData<{ ok: boolean; created: number; skipped: number; parsed: Array<Record<string, unknown>> }>(
                '/api/v1/planning/customs_dt/upload_fts', formData
            );
        },

        // Accounts & Fact Links
        getAccountsList() { return api.request<Account[]>('GET', '/api/v1/planning/accounts_list'); },
        getCandidateTransactions(account?: string) {
            const params = account ? `?account=${encodeURIComponent(account)}` : '';
            return api.request<Transaction[]>('GET', `/api/v1/planning/candidate_transactions${params}`);
        },
        getFactLinks(paymentId: number) {
            return api.request<Array<{ id: number; payment_id: number; txn_id: string; amount_rub: number; note?: string }>>('GET', `/api/v1/planning/fact_links/${paymentId}`);
        },
        createFactLink(data: { payment_id: number; txn_id: string; amount_rub: number; note?: string }) {
            return api.request<{ ok: boolean; id: number }>('POST', '/api/v1/planning/fact_links', data);
        },
        deleteFactLink(linkId: number) {
            return api.request<MessageResponse>('DELETE', `/api/v1/planning/fact_links/${linkId}`);
        },
    };
}

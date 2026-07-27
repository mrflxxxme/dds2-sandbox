/** Loans API methods */
import { ApiClient } from './client';
import type {
    LenderDetail,
    LoanStuckResponse,
    LoanDetail,
    LoanListResponse,
    LoanCreate,
    LoanUpdate,
    LoanPayment,
    LoanDirection,
    LoanStatus,
    LoanEntityType,
    LoanPaymentMatch,
    LoanExtend,
    LoanRepay,
    Loan,
    LoanDashboard,
    LoanByLenderResponse,
    LoanForecastResponse,
    LenderAccessCreate,
    LenderAccessCreated,
    LenderAccessInfo,
    LenderAccessListResponse,
    LoanImportResult,
} from '@/types/api';

export function addLoanMethods(api: ApiClient) {
    return {
        // ─── Loans CRUD ───────────────────────────────────────────────────────
        listLoans(params?: {
            direction?: LoanDirection;
            status?: LoanStatus;
            counterparty_id?: number;
            entity_type?: LoanEntityType;
            limit?: number;
            offset?: number;
        }) {
            const q = new URLSearchParams();
            if (params?.direction) q.set('direction', params.direction);
            if (params?.status) q.set('status', params.status);
            if (params?.counterparty_id != null) q.set('counterparty_id', String(params.counterparty_id));
            if (params?.entity_type) q.set('entity_type', params.entity_type);
            if (params?.limit != null) q.set('limit', String(params.limit));
            if (params?.offset != null) q.set('offset', String(params.offset));
            const qs = q.toString();
            return api.request<LoanListResponse>(
                'GET', `/api/v1/loans${qs ? `?${qs}` : ''}`
            );
        },

        getLoan(id: number) {
            return api.request<LoanDetail>('GET', `/api/v1/loans/${id}`);
        },

        createLoan(data: LoanCreate) {
            return api.request<LoanDetail>('POST', '/api/v1/loans', data);
        },

        updateLoan(id: number, data: LoanUpdate) {
            return api.request<LoanDetail>('PATCH', `/api/v1/loans/${id}`, data);
        },

        extendLoan(id: number, data: LoanExtend) {
            return api.request<Loan>('POST', `/api/v1/loans/${id}/extend`, data);
        },

        // ─── Loan Payments ────────────────────────────────────────────────────
        matchLoanTransaction(loanId: number, data: LoanPaymentMatch) {
            return api.request<LoanPayment>(
                'POST', `/api/v1/loans/${loanId}/payments/match`, data
            );
        },

        // ─── Analytics ────────────────────────────────────────────────────────
        loanDashboard() {
            return api.request<LoanDashboard>('GET', '/api/v1/loans/dashboard');
        },

        repayLoan(id: number, data: LoanRepay) {
            return api.request<Loan>('POST', `/api/v1/loans/${id}/repay`, data);
        },
        stuckLoans() {
            return api.request<LoanStuckResponse>('GET', '/api/v1/loans/stuck');
        },
        lenderDetail(counterpartyId: number, monthsBack = 12) {
            return api.request<LenderDetail>(
                'GET', `/api/v1/loans/lenders/${counterpartyId}?months_back=${monthsBack}`
            );
        },
        loansByLender(periodEnd?: string) {
            const qs = periodEnd ? `?${new URLSearchParams({ period_end: periodEnd })}` : '';
            return api.request<LoanByLenderResponse>('GET', `/api/v1/loans/by-lender${qs}`);
        },

        loanForecast(horizonMonths = 12) {
            return api.request<LoanForecastResponse>(
                'GET', `/api/v1/loans/forecast?horizon_months=${horizonMonths}`
            );
        },

        // ─── Lender portal access (admin) ─────────────────────────────────────
        listLenderAccess() {
            return api.request<LenderAccessListResponse>('GET', '/api/v1/loans/lenders/access');
        },

        createLenderAccess(data: LenderAccessCreate) {
            return api.request<LenderAccessCreated>('POST', '/api/v1/loans/lenders/access', data);
        },

        resetLenderPassword(counterpartyId: number) {
            return api.request<LenderAccessCreated>(
                'POST', `/api/v1/loans/lenders/access/${counterpartyId}/reset`
            );
        },

        revokeLenderAccess(counterpartyId: number) {
            return api.request<LenderAccessInfo>(
                'POST', `/api/v1/loans/lenders/access/${counterpartyId}/revoke`
            );
        },

        // ─── Excel import ─────────────────────────────────────────────────────
        importLoans(file: File) {
            const form = new FormData();
            form.append('file', file);
            return api.uploadFormData<LoanImportResult>('/api/v1/loans/import', form);
        },
    };
}

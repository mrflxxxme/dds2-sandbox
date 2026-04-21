/** Loans API methods */
import { ApiClient } from './client';
import type {
    LoanDetail,
    LoanListResponse,
    LoanCreate,
    LoanUpdate,
    LoanPayment,
    LoanDirection,
    LoanStatus,
    LoanPaymentMatch,
} from '@/types/api';

export function addLoanMethods(api: ApiClient) {
    return {
        // ─── Loans CRUD ───────────────────────────────────────────────────────
        listLoans(params?: {
            direction?: LoanDirection;
            status?: LoanStatus;
            counterparty_id?: number;
            limit?: number;
            offset?: number;
        }) {
            const q = new URLSearchParams();
            if (params?.direction) q.set('direction', params.direction);
            if (params?.status) q.set('status', params.status);
            if (params?.counterparty_id != null) q.set('counterparty_id', String(params.counterparty_id));
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

        // ─── Loan Payments ────────────────────────────────────────────────────
        matchLoanTransaction(loanId: number, data: LoanPaymentMatch) {
            return api.request<LoanPayment>(
                'POST', `/api/v1/loans/${loanId}/payments/match`, data
            );
        },
    };
}

/** Counterparty API methods */
import { ApiClient } from './client';
import type {
    CounterpartyListItem,
    CounterpartyListResponse,
    CounterpartyDetail,
    CounterpartyCreate,
    CounterpartyUpdate,
    CounterpartyDocument,
    CounterpartyType,
    CounterpartyTransactionsResponse,
    CounterpartySummaryResponse,
    SetExpenseCategoryResponse,
    DocType,
} from '@/types/api';

export function addCounterpartyMethods(api: ApiClient) {
    return {
        // ─── Counterparties CRUD ──────────────────────────────────────────────
        listCounterparties(params?: {
            type?: CounterpartyType;
            q?: string;
            active_only?: boolean;
            limit?: number;
            offset?: number;
            date_from?: string;
            date_to?: string;
        }) {
            const q = new URLSearchParams();
            if (params?.type) q.set('type', params.type);
            if (params?.q) q.set('q', params.q);
            if (params?.active_only) q.set('active_only', 'true');
            if (params?.limit != null) q.set('limit', String(params.limit));
            if (params?.offset != null) q.set('offset', String(params.offset));
            if (params?.date_from) q.set('date_from', params.date_from);
            if (params?.date_to) q.set('date_to', params.date_to);
            const qs = q.toString();
            return api.request<CounterpartyListResponse>(
                'GET', `/api/v1/counterparties${qs ? `?${qs}` : ''}`
            );
        },

        getCounterpartiesSummary(params?: { date_from?: string; date_to?: string }) {
            const q = new URLSearchParams();
            if (params?.date_from) q.set('date_from', params.date_from);
            if (params?.date_to) q.set('date_to', params.date_to);
            const qs = q.toString();
            return api.request<CounterpartySummaryResponse>(
                'GET', `/api/v1/counterparties/summary${qs ? `?${qs}` : ''}`
            );
        },

        getCounterparty(id: number, params?: { date_from?: string; date_to?: string }) {
            const q = new URLSearchParams();
            if (params?.date_from) q.set('date_from', params.date_from);
            if (params?.date_to) q.set('date_to', params.date_to);
            const qs = q.toString();
            return api.request<CounterpartyDetail>(
                'GET', `/api/v1/counterparties/${id}${qs ? `?${qs}` : ''}`
            );
        },

        createCounterparty(data: CounterpartyCreate) {
            return api.request<CounterpartyDetail>('POST', '/api/v1/counterparties', data);
        },

        updateCounterparty(id: number, data: CounterpartyUpdate) {
            return api.request<CounterpartyDetail>('PATCH', `/api/v1/counterparties/${id}`, data);
        },

        /** Set/clear the counterparty's expense category (level-2); propagates to its transactions. */
        setCounterpartyCategory(id: number, cat_lvl1: string | null, cat_lvl2: string | null) {
            return api.request<SetExpenseCategoryResponse>('PUT', `/api/v1/counterparties/${id}/category`, { cat_lvl1, cat_lvl2 });
        },

        deleteCounterparty(id: number) {
            return api.request<void>('DELETE', `/api/v1/counterparties/${id}`);
        },

        getCounterpartyTransactions(id: number, params?: {
            date_from?: string;
            date_to?: string;
            currency?: string;
            limit?: number;
            offset?: number;
        }) {
            const q = new URLSearchParams();
            if (params?.date_from) q.set('date_from', params.date_from);
            if (params?.date_to) q.set('date_to', params.date_to);
            if (params?.currency) q.set('currency', params.currency);
            if (params?.limit != null) q.set('limit', String(params.limit));
            if (params?.offset != null) q.set('offset', String(params.offset));
            const qs = q.toString();
            return api.request<CounterpartyTransactionsResponse>(
                'GET', `/api/v1/counterparties/${id}/transactions${qs ? `?${qs}` : ''}`
            );
        },

        // ─── Documents ───────────────────────────────────────────────────────
        listCounterpartyDocuments(id: number) {
            return api.request<CounterpartyDocument[]>(
                'GET', `/api/v1/counterparties/${id}/documents`
            );
        },

        uploadCounterpartyDocument(id: number, file: File, docType: DocType) {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('doc_type', docType);
            return api.uploadFormData<CounterpartyDocument>(
                `/api/v1/counterparties/${id}/documents`, formData
            );
        },

        deleteCounterpartyDocument(counterpartyId: number, docId: number) {
            return api.request<void>(
                'DELETE', `/api/v1/counterparties/${counterpartyId}/documents/${docId}`
            );
        },
    };
}

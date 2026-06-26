/** Payment Requests API methods */
import { ApiClient } from './client';
import type {
    PaymentRequestListResponse,
    PaymentRequestDetail,
    PaymentRequestDocument,
    PaymentRequestCreate,
    PaymentRequestStatusPoll,
    ShippableShipmentRow,
    CounterpartyReconciliation,
    CreateDraftsResponse,
    PaymentRequestDocType,
    PaymentRequestStatus,
} from '@/types/api';

export function addPaymentRequestMethods(api: ApiClient) {
    return {
        /** GET /api/v1/payment-requests — list with optional filters */
        listPaymentRequests(filters?: {
            status?: PaymentRequestStatus;
            date_from?: string;
            date_to?: string;
            counterparty_id?: number;
            search?: string;
            limit?: number;
        }): Promise<PaymentRequestListResponse> {
            const qs = new URLSearchParams();
            if (filters?.status) qs.set('status', filters.status);
            if (filters?.date_from) qs.set('date_from', filters.date_from);
            if (filters?.date_to) qs.set('date_to', filters.date_to);
            if (filters?.counterparty_id != null) qs.set('counterparty_id', String(filters.counterparty_id));
            if (filters?.search) qs.set('search', filters.search);
            if (filters?.limit != null) qs.set('limit', String(filters.limit));
            const q = qs.toString();
            return api.request<PaymentRequestListResponse>('GET', `/api/v1/payment-requests${q ? '?' + q : ''}`);
        },

        /** GET /api/v1/payment-requests/{id} */
        getPaymentRequest(id: number): Promise<PaymentRequestDetail> {
            return api.request<PaymentRequestDetail>('GET', `/api/v1/payment-requests/${id}`);
        },

        /** GET /api/v1/payment-requests/shippable — отгруженные/сданные заборы */
        listShippable(search?: string, hasRequest?: boolean, archived?: boolean): Promise<ShippableShipmentRow[]> {
            const qs = new URLSearchParams();
            if (search) qs.set('search', search);
            if (hasRequest != null) qs.set('has_request', String(hasRequest));
            if (archived) qs.set('archived', 'true');
            const q = qs.toString();
            return api.request<ShippableShipmentRow[]>('GET', `/api/v1/payment-requests/shippable${q ? '?' + q : ''}`);
        },

        /** POST /api/v1/payment-requests/shippable/archive — массовый перенос заборов в архив */
        archiveShipments(shipmentIds: number[]): Promise<{ archived: number }> {
            return api.request('POST', '/api/v1/payment-requests/shippable/archive', { shipment_ids: shipmentIds });
        },

        /** POST /api/v1/payment-requests/shippable/unarchive — вернуть заборы из архива */
        unarchiveShipments(shipmentIds: number[]): Promise<{ unarchived: number }> {
            return api.request('POST', '/api/v1/payment-requests/shippable/unarchive', { shipment_ids: shipmentIds });
        },

        /** GET /api/v1/payment-requests/reconciliation — сверка заборов и платежей перевозчика */
        getCounterpartyReconciliation(counterpartyId: number, archived?: boolean): Promise<CounterpartyReconciliation> {
            const qs = new URLSearchParams({ counterparty_id: String(counterpartyId) });
            if (archived) qs.set('archived', 'true');
            return api.request<CounterpartyReconciliation>('GET', `/api/v1/payment-requests/reconciliation?${qs.toString()}`);
        },

        /** POST /api/v1/payment-requests/orphan-payments/archive — пометить платежи разобранными */
        archiveOrphanPayments(transactionIds: number[]): Promise<{ archived: number }> {
            return api.request('POST', '/api/v1/payment-requests/orphan-payments/archive', { transaction_ids: transactionIds });
        },

        /** POST /api/v1/payment-requests/orphan-payments/unarchive — вернуть платежи из архива */
        unarchiveOrphanPayments(transactionIds: number[]): Promise<{ unarchived: number }> {
            return api.request('POST', '/api/v1/payment-requests/orphan-payments/unarchive', { transaction_ids: transactionIds });
        },

        /** POST /api/v1/payment-requests/payments/link — привязать N заборов к одной оплате */
        linkShipmentsToPayment(transactionId: number, shipmentIds: number[]): Promise<{
            transaction_id: number; linked_count: number; amount: string; linked_sum: string; diff: string;
        }> {
            return api.request('POST', '/api/v1/payment-requests/payments/link', {
                transaction_id: transactionId, shipment_ids: shipmentIds,
            });
        },

        /** POST /api/v1/payment-requests/payments/unlink — отвязать заборы от оплаты */
        unlinkShipments(shipmentIds: number[]): Promise<{ unlinked: number }> {
            return api.request('POST', '/api/v1/payment-requests/payments/unlink', { shipment_ids: shipmentIds });
        },

        /** POST /api/v1/payment-requests */
        createPaymentRequest(body: PaymentRequestCreate): Promise<PaymentRequestDetail> {
            return api.request<PaymentRequestDetail>('POST', '/api/v1/payment-requests', body);
        },

        /** PATCH /api/v1/payment-requests/{id} — only allowed in DRAFT status */
        updatePaymentRequest(id: number, body: Partial<PaymentRequestCreate>): Promise<PaymentRequestDetail> {
            return api.request<PaymentRequestDetail>('PATCH', `/api/v1/payment-requests/${id}`, body);
        },

        /** POST /api/v1/payment-requests/{id}/documents — multipart upload */
        uploadPaymentRequestDocument(id: number, file: File, docType: PaymentRequestDocType): Promise<PaymentRequestDocument> {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('doc_type', docType);
            return api.uploadFormData<PaymentRequestDocument>(`/api/v1/payment-requests/${id}/documents`, formData);
        },

        /** Returns the URL to open/download a document PDF */
        paymentRequestDocumentDownloadUrl(id: number, docId: number): string {
            return `/api/v1/payment-requests/${id}/documents/${docId}/download`;
        },

        /** DELETE /api/v1/payment-requests/{id}/documents/{docId} */
        deletePaymentRequestDocument(id: number, docId: number): Promise<void> {
            return api.request<void>('DELETE', `/api/v1/payment-requests/${id}/documents/${docId}`);
        },

        /** POST /api/v1/payment-requests/{id}/submit */
        submitPaymentRequest(id: number, comment?: string): Promise<PaymentRequestDetail> {
            return api.request<PaymentRequestDetail>('POST', `/api/v1/payment-requests/${id}/submit`, { comment: comment ?? null });
        },

        /** POST /api/v1/payment-requests/{id}/cancel — отменить заявку (PENDING_REVIEW/DRAFT → CANCELLED) */
        cancelPaymentRequest(id: number, comment?: string): Promise<PaymentRequestDetail> {
            return api.request<PaymentRequestDetail>('POST', `/api/v1/payment-requests/${id}/cancel`, { comment: comment ?? null });
        },

        /** GET /api/v1/payment-requests/{id}/status — lightweight poll */
        getPaymentRequestStatus(id: number): Promise<PaymentRequestStatusPoll> {
            return api.request<PaymentRequestStatusPoll>('GET', `/api/v1/payment-requests/${id}/status`);
        },

        /** POST /api/v1/payment-requests/{id}/create-draft — IRREVERSIBLE bank write */
        createPaymentDraft(id: number, confirm: true, payerAccountId?: string): Promise<PaymentRequestDetail> {
            return api.request<PaymentRequestDetail>('POST', `/api/v1/payment-requests/${id}/create-draft`, {
                confirm,
                payer_account_id: payerAccountId ?? null,
            });
        },

        /** POST /api/v1/payment-requests/create-drafts — массовое создание черновиков (IRREVERSIBLE) */
        createPaymentDrafts(ids: number[], confirm: true, payerAccountId?: string): Promise<CreateDraftsResponse> {
            return api.request<CreateDraftsResponse>('POST', '/api/v1/payment-requests/create-drafts', {
                ids,
                confirm,
                payer_account_id: payerAccountId ?? null,
            });
        },
    };
}

/**
 * FF billing API — тарифы услуг ФФ, хранение по дням, счета ФФ.
 * Контракт: backend/schemas/ff_billing.py (роутер /api/v1).
 *
 * Numeric/Decimal-поля бэка сериализуются в JSON СТРОКОЙ — здесь же коэрсим
 * их в number, чтобы страницы могли доверять типам из types/api.ts.
 */
import { ApiClient } from './client';
import type {
    FfTariffRow,
    FfTariffPayload,
    FfTariffUpdatePayload,
    FfStorageDailyRow,
    FfAssemblyExpectedCost,
    FfTransferExpectedCost,
    FfExpectedComponent,
    FfCustomCostPayload,
    FfInvoiceRow,
    FfInvoiceLineRow,
    FfInvoiceDetail,
    FfInvoiceListResponse,
    FfInvoiceCreatePayload,
    FfInvoiceUpdatePayload,
    FfInvoiceUploadResponse,
    FfInvoiceStatus,
    FfInvoiceKind,
    FfCandidatePayment,
    FfInvoicePaymentLinkPayload,
    FfInvoicePaymentUnlinkPayload,
    FfInvoiceCreatePaymentRequestResponse,
} from '@/types/api';

// ─── Decimal → number коэрсия ───────────────────────────────────────────────

function num(v: unknown): number {
    return Number(v);
}

function numOrNull(v: unknown): number | null {
    return v == null ? null : Number(v);
}

function coerceTariff(t: FfTariffRow): FfTariffRow {
    return { ...t, rate: num(t.rate) };
}

function coerceStorageRow(r: FfStorageDailyRow): FfStorageDailyRow {
    return { ...r, storage_rate: numOrNull(r.storage_rate), storage_cost: numOrNull(r.storage_cost) };
}

function coerceComponent(comp: FfExpectedComponent): FfExpectedComponent {
    return { ...comp, rate: numOrNull(comp.rate), cost: numOrNull(comp.cost) };
}

function coerceExpectedCost(c: FfAssemblyExpectedCost): FfAssemblyExpectedCost {
    return {
        ...c,
        components: (c.components ?? []).map(coerceComponent),
        custom_cost: numOrNull(c.custom_cost),
        total: numOrNull(c.total),
    };
}

function coerceTransferExpectedCost(c: FfTransferExpectedCost): FfTransferExpectedCost {
    return {
        ...c,
        components: (c.components ?? []).map(coerceComponent),
        custom_cost: numOrNull(c.custom_cost),
        total: numOrNull(c.total),
    };
}

function coerceInvoiceRow<T extends FfInvoiceRow>(inv: T): T {
    return {
        ...inv,
        amount: num(inv.amount),
        our_amount: numOrNull(inv.our_amount),
        diff: numOrNull(inv.diff),
    };
}

function coerceInvoiceLine(l: FfInvoiceLineRow): FfInvoiceLineRow {
    return { ...l, our_cost: numOrNull(l.our_cost), allocated_amount: numOrNull(l.allocated_amount) };
}

function coerceInvoiceDetail(inv: FfInvoiceDetail): FfInvoiceDetail {
    return { ...coerceInvoiceRow(inv), lines: (inv.lines ?? []).map(coerceInvoiceLine) };
}

function coerceCandidate(c: FfCandidatePayment): FfCandidatePayment {
    return { ...c, amount: num(c.amount) };
}

// ─── Методы ─────────────────────────────────────────────────────────────────

export function addFfBillingMethods(api: ApiClient) {
    return {
        // ── Тарифы ──────────────────────────────────────────────────────────
        /** GET /warehouse/{id}/ff-tariffs — все ставки склада (с историей) */
        async listFfTariffs(warehouseId: number): Promise<FfTariffRow[]> {
            const rows = await api.request<FfTariffRow[]>('GET', `/api/v1/warehouse/${warehouseId}/ff-tariffs`);
            return (rows ?? []).map(coerceTariff);
        },

        /** POST /warehouse/{id}/ff-tariffs — новая ставка (бэк закрывает предыдущую) */
        async createFfTariff(warehouseId: number, payload: FfTariffPayload): Promise<FfTariffRow> {
            const row = await api.request<FfTariffRow>('POST', `/api/v1/warehouse/${warehouseId}/ff-tariffs`, payload);
            return coerceTariff(row);
        },

        /** PATCH /ff-tariffs/{id} */
        async updateFfTariff(tariffId: number, payload: FfTariffUpdatePayload): Promise<FfTariffRow> {
            const row = await api.request<FfTariffRow>('PATCH', `/api/v1/ff-tariffs/${tariffId}`, payload);
            return coerceTariff(row);
        },

        /** DELETE /ff-tariffs/{id} (soft) */
        deleteFfTariff(tariffId: number): Promise<void> {
            return api.request<void>('DELETE', `/api/v1/ff-tariffs/${tariffId}`);
        },

        // ── Хранение по дням ───────────────────────────────────────────────
        /** GET /warehouse/{id}/ff-storage-daily?date_from&date_to */
        async listFfStorageDaily(warehouseId: number, dateFrom?: string, dateTo?: string): Promise<FfStorageDailyRow[]> {
            const qs = new URLSearchParams();
            if (dateFrom) qs.set('date_from', dateFrom);
            if (dateTo) qs.set('date_to', dateTo);
            const q = qs.toString();
            const rows = await api.request<FfStorageDailyRow[]>(
                'GET', `/api/v1/warehouse/${warehouseId}/ff-storage-daily${q ? '?' + q : ''}`,
            );
            return (rows ?? []).map(coerceStorageRow);
        },

        /** POST /warehouse/{id}/ff-storage-daily/recompute — пересчёт ₽ за диапазон (после правки тарифа задним числом).
         *  Бэк возвращает счётчик обновлённых строк; свежие данные перечитываются отдельным GET. */
        recomputeFfStorage(warehouseId: number, dateFrom: string, dateTo: string): Promise<{ updated: number }> {
            return api.request<{ updated: number }>(
                'POST', `/api/v1/warehouse/${warehouseId}/ff-storage-daily/recompute`,
                { date_from: dateFrom, date_to: dateTo },
            );
        },

        // ── Ожидаемая стоимость услуг ФФ по заявке ─────────────────────────
        /** GET /warehouse/assembly/{id}/ff-expected-cost */
        async getFfExpectedCost(requestId: number): Promise<FfAssemblyExpectedCost> {
            const res = await api.request<FfAssemblyExpectedCost>('GET', `/api/v1/warehouse/assembly/${requestId}/ff-expected-cost`);
            return coerceExpectedCost(res);
        },

        /** PATCH /warehouse/assembly/{id}/ff-custom-cost — доп-услуги ФФ (null = очистить) */
        async setFfCustomCost(requestId: number, payload: FfCustomCostPayload): Promise<FfAssemblyExpectedCost> {
            const res = await api.request<FfAssemblyExpectedCost>('PATCH', `/api/v1/warehouse/assembly/${requestId}/ff-custom-cost`, payload);
            return coerceExpectedCost(res);
        },

        // ── Ожидаемая стоимость услуг ФФ по переезду ───────────────────────
        /** GET /warehouse/transfers/{id}/ff-expected-cost — тариф склада-ИСТОЧНИКА */
        async getFfTransferExpectedCost(transferId: number): Promise<FfTransferExpectedCost> {
            const res = await api.request<FfTransferExpectedCost>('GET', `/api/v1/warehouse/transfers/${transferId}/ff-expected-cost`);
            return coerceTransferExpectedCost(res);
        },

        /** PATCH /warehouse/transfers/{id}/ff-custom-cost — доп-услуги ФФ (null = очистить) */
        async setFfTransferCustomCost(transferId: number, payload: FfCustomCostPayload): Promise<FfTransferExpectedCost> {
            const res = await api.request<FfTransferExpectedCost>('PATCH', `/api/v1/warehouse/transfers/${transferId}/ff-custom-cost`, payload);
            return coerceTransferExpectedCost(res);
        },

        // ── Счета ──────────────────────────────────────────────────────────
        /** GET /ff-invoices?warehouse_id&status&kind */
        async listFfInvoices(filters?: {
            warehouse_id?: number;
            status?: FfInvoiceStatus;
            kind?: FfInvoiceKind;
        }): Promise<FfInvoiceListResponse> {
            const qs = new URLSearchParams();
            if (filters?.warehouse_id != null) qs.set('warehouse_id', String(filters.warehouse_id));
            if (filters?.status) qs.set('status', filters.status);
            if (filters?.kind) qs.set('kind', filters.kind);
            const q = qs.toString();
            const res = await api.request<FfInvoiceListResponse>('GET', `/api/v1/ff-invoices${q ? '?' + q : ''}`);
            return { total: res.total, items: (res.items ?? []).map(coerceInvoiceRow) };
        },

        /** POST /ff-invoices — создать счёт вручную */
        async createFfInvoice(payload: FfInvoiceCreatePayload): Promise<FfInvoiceDetail> {
            const res = await api.request<FfInvoiceDetail>('POST', '/api/v1/ff-invoices', payload);
            return coerceInvoiceDetail(res);
        },

        /** POST /ff-invoices/upload — файл → распознанный черновик счёта.
         *  Ретраи при 5xx/сети: разовый рестарт backend в окно деплоя (как parse-invoice). */
        async uploadFfInvoice(file: File): Promise<FfInvoiceUploadResponse> {
            const formData = new FormData();
            formData.append('file', file);
            const res = await api.uploadFormData<FfInvoiceUploadResponse>(
                '/api/v1/ff-invoices/upload', formData, { retries: 2, retryDelayMs: 3000 },
            );
            return { ...res, invoice: coerceInvoiceDetail(res.invoice) };
        },

        /** GET /ff-invoices/{id} */
        async getFfInvoice(invoiceId: number): Promise<FfInvoiceDetail> {
            const res = await api.request<FfInvoiceDetail>('GET', `/api/v1/ff-invoices/${invoiceId}`);
            return coerceInvoiceDetail(res);
        },

        /** PATCH /ff-invoices/{id} */
        async updateFfInvoice(invoiceId: number, payload: FfInvoiceUpdatePayload): Promise<FfInvoiceDetail> {
            const res = await api.request<FfInvoiceDetail>('PATCH', `/api/v1/ff-invoices/${invoiceId}`, payload);
            return coerceInvoiceDetail(res);
        },

        /** DELETE /ff-invoices/{id} (soft) */
        deleteFfInvoice(invoiceId: number): Promise<void> {
            return api.request<void>('DELETE', `/api/v1/ff-invoices/${invoiceId}`);
        },

        /** GET /ff-invoices/{id}/document/download */
        downloadFfInvoiceDocument(invoiceId: number): Promise<Blob> {
            return api.requestBlob(`/api/v1/ff-invoices/${invoiceId}/document/download`);
        },

        /** POST /ff-invoices/{id}/reconcile — пересборка строк + diff */
        async reconcileFfInvoice(invoiceId: number): Promise<FfInvoiceDetail> {
            const res = await api.request<FfInvoiceDetail>('POST', `/api/v1/ff-invoices/${invoiceId}/reconcile`);
            return coerceInvoiceDetail(res);
        },

        /** POST /ff-invoices/{id}/create-payment-request */
        createFfInvoicePaymentRequest(invoiceId: number): Promise<FfInvoiceCreatePaymentRequestResponse> {
            return api.request<FfInvoiceCreatePaymentRequestResponse>(
                'POST', `/api/v1/ff-invoices/${invoiceId}/create-payment-request`,
            );
        },

        /** GET /ff-invoices/candidate-payments?invoice_id — дебеты по ИНН юрлиц склада */
        async listFfCandidatePayments(invoiceId: number): Promise<FfCandidatePayment[]> {
            const qs = new URLSearchParams({ invoice_id: String(invoiceId) });
            const rows = await api.request<FfCandidatePayment[]>(
                'GET', `/api/v1/ff-invoices/candidate-payments?${qs.toString()}`,
            );
            return (rows ?? []).map(coerceCandidate);
        },

        /** POST /ff-invoices/payments/link — N счетов → 1 платёж */
        linkFfInvoicePayments(payload: FfInvoicePaymentLinkPayload): Promise<void> {
            return api.request<void>('POST', '/api/v1/ff-invoices/payments/link', payload);
        },

        /** POST /ff-invoices/payments/unlink */
        unlinkFfInvoicePayments(payload: FfInvoicePaymentUnlinkPayload): Promise<void> {
            return api.request<void>('POST', '/api/v1/ff-invoices/payments/unlink', payload);
        },
    };
}

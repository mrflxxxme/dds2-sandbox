/** Cost API methods */
import { ApiClient } from './client';
import type {
    CostOrder, CostOrderItem, Nomenclature, DutyRule, DutyException,
    MissingAreaBarcode, MissingWeightBarcode, MessageResponse,
    SkuValuation, ValuationSummaryRow, OpeningBalanceItem, ValuationMethod,
} from '@/types/api';

export function addCostMethods(api: ApiClient) {
    return {
        getCostOrders() { return api.request<CostOrder[]>('GET', '/api/v1/cost/orders'); },
        createCostOrder(data: Partial<CostOrder>) { return api.request<CostOrder>('POST', '/api/v1/cost/orders', data); },
        updateCostOrder(orderNo: string, data: Partial<CostOrder>) { return api.request<CostOrder>('PUT', `/api/v1/cost/orders/${encodeURIComponent(orderNo)}`, data); },
        deleteCostOrder(orderNo: string) { return api.request<MessageResponse>('DELETE', `/api/v1/cost/orders/${encodeURIComponent(orderNo)}`); },
        getCostOrderItems(orderNo: string) { return api.request<CostOrderItem[]>('GET', `/api/v1/cost/orders/${encodeURIComponent(orderNo)}/items`); },
        generatePlan(orderNo: string) {
            return api.request<{ ok: boolean; order_no: string; payments_created: number; plan: Record<string, unknown> }>('POST', `/api/v1/cost/orders/${encodeURIComponent(orderNo)}/generate_plan`);
        },
        getNomenclature() { return api.request<Nomenclature[]>('GET', '/api/v1/cost/nomenclature'); },
        getNomenclatureSubjects() { return api.request<string[]>('GET', '/api/v1/cost/nomenclature/subjects'); },
        getMissingAreaBarcodes() { return api.request<MissingAreaBarcode[]>('GET', '/api/v1/cost/nomenclature/missing_area'); },
        getMissingWeightBarcodes() { return api.request<MissingWeightBarcode[]>('GET', '/api/v1/cost/nomenclature/missing_weight'); },
        syncNomenclature() { return api.request<{ ok: boolean; synced: number; status?: string; error_msg?: string; finished_at?: string }>('POST', '/api/v1/integrations/wb/sync_nomenclature'); },
        getDutyRules() { return api.request<DutyRule[]>('GET', '/api/v1/cost/duty_rules'); },
        addDutyRule(data: Partial<DutyRule>) { return api.request<DutyRule>('POST', '/api/v1/cost/duty_rules', data); },
        deleteDutyRule(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/cost/duty_rules/${id}`); },
        getDutyExceptions() { return api.request<DutyException[]>('GET', '/api/v1/cost/duty_exceptions'); },
        addDutyException(data: Partial<DutyException>) { return api.request<DutyException>('POST', '/api/v1/cost/duty_exceptions', data); },
        deleteDutyException(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/cost/duty_exceptions/${id}`); },
        getVatRate() { return api.request<{ vat_rate: number }>('GET', '/api/v1/cost/vat_rate'); },
        setVatRate(vatRate: number) { return api.request<{ status: string; vat_rate: number }>('PUT', '/api/v1/cost/vat_rate', { vat_rate: vatRate }); },
        bulkUpdateNomenclatureArea(items: { barcode: string; area_m2: number }[]) {
            return api.request<{ ok: boolean; updated: number; not_found: string[] }>('PUT', '/api/v1/cost/nomenclature/bulk_area', { items });
        },
        bulkUpdateNomenclatureWeight(items: { barcode: string; weight_kg: number }[]) {
            return api.request<{ ok: boolean; updated: number; not_found: string[] }>('PUT', '/api/v1/cost/nomenclature/bulk_weight', { items });
        },
        async uploadCostFile(orderNo: string, file: File) {
            const formData = new FormData();
            formData.append('file', file);
            return api.uploadFormData<{ inserted: number; unrecognized: number }>(
                `/api/v1/cost/orders/${encodeURIComponent(orderNo)}/upload`, formData
            );
        },

        // ─── SKU Valuation analytics (FIFO / moving / lifetime) ─────────────
        getSkuValuation(barcode: string, dateFrom?: string, dateTo?: string) {
            const q = new URLSearchParams();
            q.set('barcode', barcode);
            if (dateFrom) q.set('date_from', dateFrom);
            if (dateTo) q.set('date_to', dateTo);
            return api.request<SkuValuation>('GET', `/api/v1/cost/valuation/sku?${q.toString()}`);
        },
        getValuationSummary(dateFrom?: string, dateTo?: string) {
            const q = new URLSearchParams();
            if (dateFrom) q.set('date_from', dateFrom);
            if (dateTo) q.set('date_to', dateTo);
            const qs = q.toString();
            return api.request<ValuationSummaryRow[]>('GET', `/api/v1/cost/valuation/summary${qs ? '?' + qs : ''}`);
        },
        getValuationMethod() {
            return api.request<{ method: ValuationMethod }>('GET', '/api/v1/cost/valuation_method');
        },
        setValuationMethod(method: ValuationMethod) {
            return api.request<{ method: ValuationMethod }>('PUT', '/api/v1/cost/valuation_method', { method });
        },
        getValuationStart() {
            return api.request<{ start_date: string | null }>('GET', '/api/v1/cost/valuation_start');
        },
        setValuationStart(startDate: string | null) {
            return api.request<{ start_date: string | null }>('PUT', '/api/v1/cost/valuation_start', { start_date: startDate });
        },
        setOrderArrivalDate(orderNo: string, actualArrivalDate: string | null) {
            return api.request<MessageResponse>(
                'PUT',
                `/api/v1/cost/orders/${encodeURIComponent(orderNo)}/arrival_date`,
                { actual_arrival_date: actualArrivalDate },
            );
        },
        getOpeningBalances() {
            return api.request<OpeningBalanceItem[]>('GET', '/api/v1/cost/opening_balance');
        },
        setOpeningBalances(items: OpeningBalanceItem[]) {
            return api.request<{ saved: number; not_found: string[] }>(
                'PUT', '/api/v1/cost/opening_balance', { items },
            );
        },
    };
}

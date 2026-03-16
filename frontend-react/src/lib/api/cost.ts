/** Cost API methods */
import { ApiClient } from './client';
import type { CostOrder, CostOrderItem, Nomenclature, DutyRule, MessageResponse } from '@/types/api';

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
        syncNomenclature() { return api.request<{ ok: boolean; synced: number }>('POST', '/api/v1/integrations/wb/sync_nomenclature'); },
        getDutyRules() { return api.request<DutyRule[]>('GET', '/api/v1/cost/duty_rules'); },
        addDutyRule(data: Partial<DutyRule>) { return api.request<DutyRule>('POST', '/api/v1/cost/duty_rules', data); },
        deleteDutyRule(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/cost/duty_rules/${id}`); },
        getVatRate() { return api.request<{ vat_rate: number }>('GET', '/api/v1/cost/vat_rate'); },
        setVatRate(vatRate: number) { return api.request<{ status: string; vat_rate: number }>('PUT', '/api/v1/cost/vat_rate', { vat_rate: vatRate }); },
        async uploadCostFile(orderNo: string, file: File) {
            const formData = new FormData();
            formData.append('file', file);
            return api.uploadFormData<{ inserted: number; unrecognized: number }>(
                `/api/v1/cost/orders/${encodeURIComponent(orderNo)}/upload`, formData
            );
        },
    };
}

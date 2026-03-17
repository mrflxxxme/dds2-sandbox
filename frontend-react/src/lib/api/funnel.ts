/** Funnel (Воронка продаж) API methods */
import { ApiClient } from './client';
import type { FunnelDayRow, FunnelSummary, MessageResponse, WbTariff, WbTariffUploadResult } from '@/types/api';

export function addFunnelMethods(api: ApiClient) {
    return {
        syncFunnel(dateFrom: string, dateTo: string) {
            return api.request<{ ok: boolean; days_synced: number }>('POST', '/api/v1/funnel/sync', { date_from: dateFrom, date_to: dateTo });
        },
        getFunnelData(params?: { date_from?: string; date_to?: string; brand?: string; vendor_code?: string; subject?: string }) {
            const q = new URLSearchParams();
            if (params?.date_from) q.set('date_from', params.date_from);
            if (params?.date_to) q.set('date_to', params.date_to);
            if (params?.brand) q.set('brand', params.brand);
            if (params?.vendor_code) q.set('vendor_code', params.vendor_code);
            if (params?.subject) q.set('subject', params.subject);
            return api.request<{ data: FunnelDayRow[]; detailed: boolean; tax_rate: number }>('GET', `/api/v1/funnel/data?${q.toString()}`);
        },
        getFunnelSummary(dateFrom?: string, dateTo?: string, brand?: string, subject?: string) {
            const q = new URLSearchParams();
            if (dateFrom) q.set('date_from', dateFrom);
            if (dateTo) q.set('date_to', dateTo);
            if (brand) q.set('brand', brand);
            if (subject) q.set('subject', subject);
            return api.request<FunnelSummary>('GET', `/api/v1/funnel/summary?${q.toString()}`);
        },
        getFunnelFilters() {
            return api.request<{ brands: string[]; subjects: string[]; vendor_codes: string[]; min_date: string | null; max_date: string | null }>('GET', '/api/v1/funnel/filters');
        },
        getFunnelCosts() {
            return api.request<Array<{ nm_id: number; cost_price: number; source: string }>>('GET', '/api/v1/funnel/costs');
        },
        setFunnelCost(nmId: number, costPrice: number) {
            return api.request<MessageResponse>('POST', '/api/v1/funnel/cost', { nm_id: nmId, cost_price: costPrice });
        },
        bulkSetFunnelCosts(items: { barcode: string; cost_price: number; currency?: string }[]) {
            return api.request<{ saved: number; not_found: string[]; errors: string[] }>('POST', '/api/v1/funnel/costs/bulk', { items });
        },
        getFunnelTax() {
            return api.request<{ tax_rate: number }>('GET', '/api/v1/funnel/tax');
        },
        setFunnelTax(taxRate: number) {
            return api.request<MessageResponse>('POST', '/api/v1/funnel/tax', { tax_rate: taxRate });
        },
        getSyncStatus() {
            return api.request<{ scheduler: Record<string, unknown>; last_syncs: Array<{ id: number; sync_type: string; status: string; rows_inserted: number; started_at: string | null; finished_at: string | null; error_msg: string | null }> }>('GET', '/api/v1/funnel/sync_status');
        },
        getDayAnalysis(params: { target_date: string; brand?: string; subject?: string; trend_days?: number }) {
            const q = new URLSearchParams();
            q.set('target_date', params.target_date);
            if (params.brand) q.set('brand', params.brand);
            if (params.subject) q.set('subject', params.subject);
            if (params.trend_days) q.set('trend_days', String(params.trend_days));
            return api.request<Record<string, unknown>>('GET', `/api/v1/funnel/day-analysis?${q.toString()}`);
        },
        getProductTrends(params: { trend_days?: number; brand?: string; search?: string } = {}) {
            const q = new URLSearchParams();
            if (params.trend_days) q.set('trend_days', String(params.trend_days));
            if (params.brand) q.set('brand', params.brand);
            if (params.search) q.set('search', params.search);
            return api.request<{ products: any[]; trend_days: number; total_products: number }>('GET', `/api/v1/funnel/trends?${q.toString()}`);
        },
        getTariffs() {
            return api.request<WbTariff[]>('GET', '/api/v1/funnel/tariffs');
        },
        uploadTariffs(file: File) {
            const formData = new FormData();
            formData.append('file', file);
            return api.uploadFormData<WbTariffUploadResult>('/api/v1/funnel/tariffs/upload', formData);
        },
        deleteTariff(id: number) {
            return api.request<{ status: string }>('DELETE', `/api/v1/funnel/tariffs/${id}`);
        },
    };
}

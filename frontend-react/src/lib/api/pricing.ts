/** Ценообразование (наценка по артикулам) API methods */
import { ApiClient } from './client';
import type { PricingResponse, PricingAiResponse, SppMapResponse } from '@/types/api';

export interface PricingMarkupParams {
    date_from?: string;
    date_to?: string;
    brand?: string;
    category?: string;
    search?: string;
    min_orders?: number;
    only_in_stock?: boolean;
    anomaly_only?: boolean;
    group_by?: 'category' | 'sku' | 'anomaly' | 'size' | 'imt';
}

export function addPricingMethods(api: ApiClient) {
    return {
        getPricingMarkup(params?: PricingMarkupParams) {
            const q = new URLSearchParams();
            if (params?.date_from) q.set('date_from', params.date_from);
            if (params?.date_to) q.set('date_to', params.date_to);
            if (params?.brand) q.set('brand', params.brand);
            if (params?.category) q.set('category', params.category);
            if (params?.search) q.set('search', params.search);
            if (params?.min_orders) q.set('min_orders', String(params.min_orders));
            if (params?.only_in_stock) q.set('only_in_stock', 'true');
            if (params?.anomaly_only) q.set('anomaly_only', 'true');
            if (params?.group_by) q.set('group_by', params.group_by);
            return api.request<PricingResponse>('GET', `/api/v1/pricing/markup?${q.toString()}`);
        },
        syncPricing() {
            return api.request<{ status: string; rows: number; synced_at: string | null; message: string | null }>(
                'POST',
                '/api/v1/pricing/sync',
            );
        },
        syncPricingSpp() {
            return api.request<{ status: string; requested: number; fetched: number; synced_at: string | null }>(
                'POST',
                '/api/v1/pricing/sync-spp',
            );
        },
        getSppMap(params?: { days?: number; step?: number; source?: string; category?: string }) {
            const q = new URLSearchParams();
            if (params?.days) q.set('days', String(params.days));
            if (params?.step) q.set('step', String(params.step));
            if (params?.source) q.set('source', params.source);
            if (params?.category) q.set('category', params.category);
            return api.request<SppMapResponse>('GET', `/api/v1/pricing/spp-map?${q.toString()}`);
        },
        observeSpp(backfillDays = 0) {
            const q = backfillDays ? `?backfill_days=${backfillDays}` : '';
            return api.request<{
                snapshot: { requested: number; written: number; stale: number };
                backfill: { written: number; days: number };
            }>('POST', `/api/v1/pricing/spp-observe${q}`);
        },
        getPricingAiRecommendations(params?: { date_from?: string; date_to?: string; only_in_stock?: boolean }) {
            const q = new URLSearchParams();
            if (params?.date_from) q.set('date_from', params.date_from);
            if (params?.date_to) q.set('date_to', params.date_to);
            if (params?.only_in_stock) q.set('only_in_stock', 'true');
            return api.request<PricingAiResponse>('POST', `/api/v1/pricing/ai-recommendations?${q.toString()}`);
        },
    };
}

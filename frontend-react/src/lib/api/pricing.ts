/** Ценообразование (наценка по артикулам) API methods */
import { ApiClient } from './client';
import type { PricingResponse, PricingAiResponse } from '@/types/api';

export interface PricingMarkupParams {
    date_from?: string;
    date_to?: string;
    brand?: string;
    category?: string;
    search?: string;
    min_orders?: number;
    only_in_stock?: boolean;
    anomaly_only?: boolean;
    group_by?: 'category' | 'sku' | 'anomaly' | 'size';
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
        getPricingAiRecommendations(params?: { date_from?: string; date_to?: string; only_in_stock?: boolean }) {
            const q = new URLSearchParams();
            if (params?.date_from) q.set('date_from', params.date_from);
            if (params?.date_to) q.set('date_to', params.date_to);
            if (params?.only_in_stock) q.set('only_in_stock', 'true');
            return api.request<PricingAiResponse>('POST', `/api/v1/pricing/ai-recommendations?${q.toString()}`);
        },
    };
}

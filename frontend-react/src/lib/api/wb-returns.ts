/** WB Goods Returns (ПВЗ) API methods */
import { ApiClient } from './client';
import type {
    CreateReceiptFromReturnsInput,
    CreateReceiptFromReturnsResult,
    WbGoodsReturn,
    WbGoodsReturnPvzGroup,
    WbGoodsReturnSummary,
    WbGoodsReturnsListResponse,
    WbGoodsReturnsSyncResult,
} from '@/types/api';

export interface GetWbReturnsParams {
    tab?: 'pvz' | 'in_transit' | 'history' | 'all';
    search?: string;
    pvz_id?: number;
    date_from?: string;
    date_to?: string;
    limit?: number;
    offset?: number;
}

export function addWbReturnsMethods(api: ApiClient) {
    return {
        getWbReturns(params: GetWbReturnsParams = {}) {
            const q = new URLSearchParams();
            if (params.tab) q.set('tab', params.tab);
            if (params.search) q.set('search', params.search);
            if (params.pvz_id != null) q.set('pvz_id', String(params.pvz_id));
            if (params.date_from) q.set('date_from', params.date_from);
            if (params.date_to) q.set('date_to', params.date_to);
            if (params.limit != null) q.set('limit', String(params.limit));
            if (params.offset != null) q.set('offset', String(params.offset));
            const qs = q.toString();
            return api.request<WbGoodsReturnsListResponse>('GET', `/api/v1/wb-returns${qs ? `?${qs}` : ''}`);
        },
        getWbReturnsSummary() {
            return api.request<WbGoodsReturnSummary>('GET', '/api/v1/wb-returns/summary');
        },
        getWbReturnsPvzGroups() {
            return api.request<WbGoodsReturnPvzGroup[]>('GET', '/api/v1/wb-returns/pvz-groups');
        },
        createReceiptFromReturns(data: CreateReceiptFromReturnsInput) {
            return api.request<CreateReceiptFromReturnsResult>('POST', '/api/v1/wb-returns/create-receipt', data);
        },
        syncWbReturns(data: { date_from?: string; date_to?: string } = {}) {
            return api.request<WbGoodsReturnsSyncResult>('POST', '/api/v1/wb-returns/sync', data);
        },
    };
}

// Re-export type helpers for external consumers (tests, etc.)
export type { WbGoodsReturn, WbGoodsReturnPvzGroup, WbGoodsReturnSummary };

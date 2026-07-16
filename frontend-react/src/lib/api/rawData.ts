/** Raw data API methods — обзор сырых источников и принудительная дозагрузка */
import { ApiClient } from './client';
import type { RawSourcesResponse, RawRefreshProgress, RawRefreshStart, RawSourceRows } from '@/types/api';

export function addRawDataMethods(api: ApiClient) {
    return {
        getRawSources() {
            return api.request<RawSourcesResponse>('GET', '/api/v1/raw-data/sources');
        },
        /** Содержимое таблицы источника: страница строк за период. */
        getRawSourceRows(key: string, params?: { limit?: number; offset?: number; date_from?: string; date_to?: string; sort_by?: string; sort_dir?: string }) {
            const q = new URLSearchParams();
            if (params?.limit != null) q.set('limit', String(params.limit));
            if (params?.offset != null) q.set('offset', String(params.offset));
            if (params?.date_from) q.set('date_from', params.date_from);
            if (params?.date_to) q.set('date_to', params.date_to);
            if (params?.sort_by) q.set('sort_by', params.sort_by);
            if (params?.sort_dir) q.set('sort_dir', params.sort_dir);
            return api.request<RawSourceRows>('GET', `/api/v1/raw-data/sources/${key}/rows?${q.toString()}`);
        },
        getRawRefreshProgress() {
            return api.request<{ progress: Record<string, RawRefreshProgress> }>('GET', '/api/v1/raw-data/refresh-progress');
        },
        /** Принудительно дозагрузить источник. Период учитывается только у ranged-источников. */
        refreshRawSource(key: string, body?: { date_from?: string; date_to?: string }) {
            return api.request<RawRefreshStart>('POST', `/api/v1/raw-data/sources/${key}/refresh`, body ?? {});
        },
    };
}

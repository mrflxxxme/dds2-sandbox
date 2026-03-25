/** Monitoring API methods */
import { ApiClient } from './client';
import type { MonitoringOverview, MonitoringSyncLogEntry } from '@/types/api';

export function addMonitoringMethods(api: ApiClient) {
    return {
        getMonitoringOverview() {
            return api.request<MonitoringOverview>('GET', '/api/v1/monitoring/overview');
        },
        getMonitoringSyncLog(params?: {
            service?: string;
            sync_type?: string;
            status?: string;
            limit?: number;
            offset?: number;
        }) {
            const searchParams = new URLSearchParams();
            if (params?.service) searchParams.set('service', params.service);
            if (params?.sync_type) searchParams.set('sync_type', params.sync_type);
            if (params?.status) searchParams.set('status', params.status);
            if (params?.limit) searchParams.set('limit', String(params.limit));
            if (params?.offset) searchParams.set('offset', String(params.offset));
            const qs = searchParams.toString();
            return api.request<MonitoringSyncLogEntry[]>(
                'GET',
                `/api/v1/monitoring/sync_log${qs ? '?' + qs : ''}`,
            );
        },
    };
}

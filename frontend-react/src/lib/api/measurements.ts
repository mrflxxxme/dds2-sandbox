/** Measurements API methods — замеры складов WB + удержания за габариты */
import { ApiClient } from './client';
import type {
    WarehouseMeasurementListResponse,
    MeasurementPenaltyListResponse,
    MeasurementFiltersResponse,
    MeasurementSyncResult,
    PenaltyArticleSummaryResponse,
} from '@/types/api';

interface MeasurementFilters {
    dateFrom?: string;
    dateTo?: string;
    nmId?: number;
    subject?: string;
    brand?: string;
    search?: string;
    limit?: number;
    offset?: number;
}

function buildQuery(f: MeasurementFilters): string {
    const q = new URLSearchParams();
    if (f.dateFrom) q.set('date_from', f.dateFrom);
    if (f.dateTo) q.set('date_to', f.dateTo);
    if (f.nmId != null) q.set('nm_id', String(f.nmId));
    if (f.subject) q.set('subject', f.subject);
    if (f.brand) q.set('brand', f.brand);
    if (f.search) q.set('search', f.search);
    if (f.limit != null) q.set('limit', String(f.limit));
    if (f.offset != null) q.set('offset', String(f.offset));
    return q.toString();
}

export function addMeasurementMethods(api: ApiClient) {
    return {
        getWarehouseMeasurements(f: MeasurementFilters = {}) {
            return api.request<WarehouseMeasurementListResponse>(
                'GET', `/api/v1/measurements/warehouse?${buildQuery(f)}`,
            );
        },
        getMeasurementPenalties(f: MeasurementFilters = {}) {
            return api.request<MeasurementPenaltyListResponse>(
                'GET', `/api/v1/measurements/penalties?${buildQuery(f)}`,
            );
        },
        getMeasurementFilters() {
            return api.request<MeasurementFiltersResponse>(
                'GET', '/api/v1/measurements/filters',
            );
        },
        getPenaltySummary(f: MeasurementFilters = {}) {
            return api.request<PenaltyArticleSummaryResponse>(
                'GET', `/api/v1/measurements/penalties/summary?${buildQuery(f)}`,
            );
        },
        syncMeasurements(days = 90) {
            return api.request<MeasurementSyncResult>(
                'POST', `/api/v1/measurements/sync?days=${days}`,
            );
        },
    };
}

/** Localization Index API methods (КТР / ИРП — индекс локализации). */
import { ApiClient } from './client';
import type { LocalizationSummary, LocalizationSkuRow } from '@/types/api';

export function addLocalizationMethods(api: ApiClient) {
    return {
        getLocalizationSummary(dateFrom: string, dateTo: string) {
            const q = new URLSearchParams();
            q.set('date_from', dateFrom);
            q.set('date_to', dateTo);
            return api.request<LocalizationSummary>(
                'GET',
                `/api/v1/localization/summary?${q.toString()}`,
            );
        },
        getLocalizationSkus(dateFrom: string, dateTo: string) {
            const q = new URLSearchParams();
            q.set('date_from', dateFrom);
            q.set('date_to', dateTo);
            return api.request<LocalizationSkuRow[]>(
                'GET',
                `/api/v1/localization/skus?${q.toString()}`,
            );
        },
    };
}

/** Localization Index API methods (КТР / ИРП — индекс локализации). */
import { ApiClient } from './client';
import type {
    LocalizationSummary,
    LocalizationSkuRow,
    LocalizationSyncResult,
    LocalizationDailyPoint,
} from '@/types/api';

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
        /** Динамика ИЛ/ИРП по дням с фильтрами по subject/brand. */
        getLocalizationDaily(
            dateFrom: string,
            dateTo: string,
            subject?: string,
            brand?: string,
        ) {
            const q = new URLSearchParams();
            q.set('date_from', dateFrom);
            q.set('date_to', dateTo);
            if (subject) q.set('subject', subject);
            if (brand) q.set('brand', brand);
            return api.request<LocalizationDailyPoint[]>(
                'GET',
                `/api/v1/localization/daily?${q.toString()}`,
            );
        },
        /** ISO-даты, за которые есть `localization_percent` в funnel.
         * Используется UI для блокировки недоступных дней в date-picker'е. */
        getLocalizationAvailableDates() {
            return api.request<string[]>('GET', '/api/v1/localization/available-dates');
        },
        /**
         * Manual refresh — триггерит синк wb_orders (последние 30 дней) +
         * funnel за выбранный период (для localization_percent).
         * Если даты не передать — синкнутся только wb_orders.
         */
        syncLocalizationNow(dateFrom?: string, dateTo?: string) {
            const q = new URLSearchParams();
            if (dateFrom) q.set('date_from', dateFrom);
            if (dateTo) q.set('date_to', dateTo);
            const qs = q.toString();
            return api.request<LocalizationSyncResult>(
                'POST',
                `/api/v1/localization/sync${qs ? `?${qs}` : ''}`,
                {},
            );
        },
    };
}

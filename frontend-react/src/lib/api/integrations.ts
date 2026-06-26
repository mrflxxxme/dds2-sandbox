/** Integrations API methods */
import { ApiClient } from './client';
import type { IntegrationKey, MessageResponse, FakturaStatus, SyncLog } from '@/types/api';

export function addIntegrationMethods(api: ApiClient) {
    return {
        getIntegrationKeys() {
            return api.request<Array<{
                id: number; service: string; label: string | null;
                key_preview: string | null; is_active: boolean;
                created_at: string; last_sync_at: string | null;
            }>>('GET', '/api/v1/integrations/keys');
        },
        addIntegrationKey(service: string, api_key: string, label?: string) {
            return api.request<IntegrationKey>('POST', '/api/v1/integrations/keys', { service, api_key, label });
        },
        deleteIntegrationKey(id: number) {
            return api.request<MessageResponse>('DELETE', `/api/v1/integrations/keys/${id}`);
        },
        syncWb(keyId: number, dateFrom: string) {
            return api.request<{ ok: boolean; rows: number }>('POST', `/api/v1/integrations/sync/wb/${keyId}?date_from=${dateFrom}`);
        },
        getSyncLog() {
            return api.request<Array<{
                id: number; service: string; sync_type: string; status: string;
                started_at: string; finished_at: string | null;
                rows_fetched: number; rows_inserted: number; error_msg: string | null;
            }>>('GET', '/api/v1/integrations/sync_log');
        },
        /** GET /api/v1/integrations/faktura/status — настроена ли Faktura + последняя выписка */
        getFakturaStatus() {
            return api.request<FakturaStatus>('GET', '/api/v1/integrations/faktura/status');
        },
        /** POST /api/v1/integrations/faktura/sync — ручная выгрузка выписки ВБ Банка по API */
        syncFaktura(lookbackDays?: number) {
            return api.request<SyncLog>(
                'POST',
                '/api/v1/integrations/faktura/sync',
                lookbackDays != null ? { lookback_days: lookbackDays } : undefined,
            );
        },
    };
}

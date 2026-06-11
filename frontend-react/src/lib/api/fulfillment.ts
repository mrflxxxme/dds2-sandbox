/** Fulfillment integration API methods (skladbot.ru) */
import { ApiClient } from './client';
import type {
    FfLinkPayload,
    FfRequestDetail,
    FfRequestKind,
    FfRequestRow,
    FfStocksResponse,
    FfSyncResult,
    FulfillmentConnectPayload,
    FulfillmentStatus,
} from '@/types/api';

export function addFulfillmentMethods(api: ApiClient) {
    return {
        // ─── Connection ──────────────────────────────────────────────
        getFulfillmentStatus(warehouseId: number) {
            return api.request<FulfillmentStatus>('GET', `/api/v1/warehouse/${warehouseId}/fulfillment/status`);
        },
        connectFulfillment(warehouseId: number, token: string) {
            const body: FulfillmentConnectPayload = { provider: 'skladbot', token };
            return api.request<FulfillmentStatus>('POST', `/api/v1/warehouse/${warehouseId}/fulfillment/connect`, body);
        },
        disconnectFulfillment(warehouseId: number) {
            return api.request<{ ok: boolean }>('DELETE', `/api/v1/warehouse/${warehouseId}/fulfillment/connect`);
        },

        // ─── Sync ────────────────────────────────────────────────────
        syncFulfillment(warehouseId: number) {
            return api.request<FfSyncResult>('POST', `/api/v1/warehouse/${warehouseId}/fulfillment/sync`);
        },

        // ─── Stocks ──────────────────────────────────────────────────
        getFulfillmentStocks(warehouseId: number) {
            return api.request<FfStocksResponse>('GET', `/api/v1/warehouse/${warehouseId}/fulfillment/stocks`);
        },

        // ─── Requests (заявки ФФ: сборка / приёмки) ─────────────────
        getFulfillmentRequests(warehouseId: number, kind?: FfRequestKind) {
            const params = new URLSearchParams();
            if (kind) params.set('kind', kind);
            const qs = params.toString();
            return api.request<FfRequestRow[]>('GET', `/api/v1/warehouse/${warehouseId}/fulfillment/requests${qs ? `?${qs}` : ''}`);
        },
        getFfRequestDetail(warehouseId: number, ffRequestId: number) {
            return api.request<FfRequestDetail>('GET', `/api/v1/warehouse/${warehouseId}/fulfillment/requests/${ffRequestId}/detail`);
        },
        linkFulfillmentRequest(warehouseId: number, ffRequestId: number, payload: FfLinkPayload) {
            return api.request<FfRequestRow>('POST', `/api/v1/warehouse/${warehouseId}/fulfillment/requests/${ffRequestId}/link`, payload);
        },
        unlinkFulfillmentRequest(warehouseId: number, ffRequestId: number) {
            return api.request<FfRequestRow>('DELETE', `/api/v1/warehouse/${warehouseId}/fulfillment/requests/${ffRequestId}/link`);
        },
    };
}

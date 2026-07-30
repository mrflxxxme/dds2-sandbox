/** Fulfillment integration API methods (skladbot.ru, wmscelicom, migfull.app) */
import { ApiClient } from './client';
import type {
    FfBoxOverridePayload,
    FfBoxPack,
    FfBulkArchivePayload,
    FfBulkArchiveResult,
    FfBulkCreateRequestPayload,
    FfBulkCreateResult,
    FfCreateAssemblyResult,
    FfCreateFormResponse,
    FfCreateRequestPayload,
    FfGuidBarcodeRow,
    FfLinkCandidatesResponse,
    FfLinkPayload,
    FfNomenclatureOption,
    FfOverviewResponse,
    FfPushAssemblyResult,
    FfRepackCandidatesOut,
    FfRequestDetail,
    FfRequestKind,
    FfRequestRow,
    FfStatusEvent,
    FfStocksResponse,
    FfSyncResult,
    FfSyncRun,
    FfUnlinkedAssembly,
    FulfillmentConnectPayload,
    FulfillmentStatus,
} from '@/types/api';

export function addFulfillmentMethods(api: ApiClient) {
    return {
        // ─── Connection ──────────────────────────────────────────────
        getFulfillmentStatus(warehouseId: number) {
            return api.request<FulfillmentStatus>('GET', `/api/v1/warehouse/${warehouseId}/fulfillment/status`);
        },
        connectFulfillment(warehouseId: number, payload: FulfillmentConnectPayload) {
            return api.request<FulfillmentStatus>('POST', `/api/v1/warehouse/${warehouseId}/fulfillment/connect`, payload);
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

        // ─── Сопоставление короб→россыпь ─────────────────────────────
        getFulfillmentBoxPacks(warehouseId: number) {
            return api.request<FfBoxPack[]>('GET', `/api/v1/warehouse/${warehouseId}/fulfillment/box-packs`);
        },
        searchFulfillmentNomenclature(warehouseId: number, q: string) {
            const params = new URLSearchParams();
            if (q) params.set('q', q);
            const qs = params.toString();
            return api.request<FfNomenclatureOption[]>('GET', `/api/v1/warehouse/${warehouseId}/fulfillment/box-packs/nomenclature-search${qs ? `?${qs}` : ''}`);
        },
        setFulfillmentBoxOverride(warehouseId: number, boxBarcode: string, payload: FfBoxOverridePayload) {
            return api.request<FfBoxPack>('PUT', `/api/v1/warehouse/${warehouseId}/fulfillment/box-packs/${encodeURIComponent(boxBarcode)}/override`, payload);
        },
        deleteFulfillmentBoxOverride(warehouseId: number, boxBarcode: string) {
            return api.request<FfBoxPack | null>('DELETE', `/api/v1/warehouse/${warehouseId}/fulfillment/box-packs/${encodeURIComponent(boxBarcode)}/override`);
        },

        // ─── Ручной ШК по guid (товар ФФ без штрихкода в карточке) ────
        setFfGuidBarcode(warehouseId: number, productGuid: string, payload: { barcode: string; note?: string | null }) {
            return api.request<FfGuidBarcodeRow>('PUT', `/api/v1/warehouse/${warehouseId}/fulfillment/guid-barcodes/${encodeURIComponent(productGuid)}`, payload);
        },
        deleteFfGuidBarcode(warehouseId: number, productGuid: string) {
            return api.request<void>('DELETE', `/api/v1/warehouse/${warehouseId}/fulfillment/guid-barcodes/${encodeURIComponent(productGuid)}`);
        },

        // ─── Overview (сводная «Заявки ФФ» по всем складам) ─────────
        getFfOverview(params?: { kind?: FfRequestKind; warehouse_id?: number; only_unlinked?: boolean }) {
            const query = new URLSearchParams();
            if (params?.kind) query.set('kind', params.kind);
            if (params?.warehouse_id != null) query.set('warehouse_id', String(params.warehouse_id));
            if (params?.only_unlinked) query.set('only_unlinked', 'true');
            const qs = query.toString();
            return api.request<FfOverviewResponse>('GET', `/api/v1/warehouse/fulfillment/overview${qs ? `?${qs}` : ''}`);
        },

        // ─── Requests (заявки ФФ: сборка / приёмки) ─────────────────
        getFulfillmentRequests(warehouseId: number, kind?: FfRequestKind, showArchived?: boolean) {
            const params = new URLSearchParams();
            if (kind) params.set('kind', kind);
            if (showArchived) params.set('show_archived', 'true');
            const qs = params.toString();
            return api.request<FfRequestRow[]>('GET', `/api/v1/warehouse/${warehouseId}/fulfillment/requests${qs ? `?${qs}` : ''}`);
        },
        getFfRequestDetail(warehouseId: number, ffRequestId: number) {
            return api.request<FfRequestDetail>('GET', `/api/v1/warehouse/${warehouseId}/fulfillment/requests/${ffRequestId}/detail`);
        },

        // ─── История синхронизации (журнал смены статусов) ───────────
        getFfStatusHistory(warehouseId: number, params?: { kind?: FfRequestKind; ffRequestId?: number }) {
            const query = new URLSearchParams();
            if (params?.kind) query.set('kind', params.kind);
            if (params?.ffRequestId != null) query.set('ff_request_id', String(params.ffRequestId));
            const qs = query.toString();
            return api.request<FfStatusEvent[]>('GET', `/api/v1/warehouse/${warehouseId}/fulfillment/status-history${qs ? `?${qs}` : ''}`);
        },
        // ─── Журнал синхронизаций (когда были последние обновления зеркала) ──
        getFfSyncRuns(warehouseId: number) {
            return api.request<FfSyncRun[]>('GET', `/api/v1/warehouse/${warehouseId}/fulfillment/sync-runs`);
        },
        // ─── Наши заявки сборки без привязки ФФ (реверс-линк из заявки ФФ) ──
        getFfUnlinkedAssemblies(warehouseId: number) {
            return api.request<FfUnlinkedAssembly[]>('GET', `/api/v1/warehouse/${warehouseId}/fulfillment/unlinked-assemblies`);
        },
        getFfLinkCandidates(warehouseId: number, ffRequestId: number) {
            return api.request<FfLinkCandidatesResponse>('GET', `/api/v1/warehouse/${warehouseId}/fulfillment/requests/${ffRequestId}/link-candidates`);
        },
        linkFulfillmentRequest(warehouseId: number, ffRequestId: number, payload: FfLinkPayload) {
            return api.request<FfRequestRow>('POST', `/api/v1/warehouse/${warehouseId}/fulfillment/requests/${ffRequestId}/link`, payload);
        },
        unlinkFulfillmentRequest(warehouseId: number, ffRequestId: number) {
            return api.request<FfRequestRow>('DELETE', `/api/v1/warehouse/${warehouseId}/fulfillment/requests/${ffRequestId}/link`);
        },
        // ─── Ручная пара «вскрытие коробов» (migfull: возврат ↔ поступление) ──
        getFfRepackCandidates(warehouseId: number, ffRequestId: number) {
            return api.request<FfRepackCandidatesOut>('GET', `/api/v1/warehouse/${warehouseId}/fulfillment/requests/${ffRequestId}/repack-candidates`);
        },
        linkFfRepackPair(warehouseId: number, ffRequestId: number, submissionId: number) {
            return api.request<FfRequestRow>('POST', `/api/v1/warehouse/${warehouseId}/fulfillment/requests/${ffRequestId}/repack-link`, { submission_id: submissionId });
        },
        unlinkFfRepackPair(warehouseId: number, ffRequestId: number) {
            return api.request<FfRequestRow>('DELETE', `/api/v1/warehouse/${warehouseId}/fulfillment/requests/${ffRequestId}/repack-link`);
        },
        archiveFulfillmentRequest(warehouseId: number, ffRequestId: number) {
            return api.request<FfRequestRow>('POST', `/api/v1/warehouse/${warehouseId}/fulfillment/requests/${ffRequestId}/archive`);
        },
        unarchiveFulfillmentRequest(warehouseId: number, ffRequestId: number) {
            return api.request<FfRequestRow>('DELETE', `/api/v1/warehouse/${warehouseId}/fulfillment/requests/${ffRequestId}/archive`);
        },
        createAssemblyFromFf(warehouseId: number, ffRequestId: number) {
            return api.request<FfCreateAssemblyResult>('POST', `/api/v1/warehouse/${warehouseId}/fulfillment/requests/${ffRequestId}/create-assembly`);
        },
        /** Создать заявку на ФФ (skladbot тип 851) из нашей заявки на сборку. */
        getFfCreateForm(warehouseId: number, assemblyRequestId: number) {
            return api.request<FfCreateFormResponse>('GET', `/api/v1/warehouse/${warehouseId}/fulfillment/assembly/${assemblyRequestId}/create-form`);
        },
        createFfRequestFromAssembly(warehouseId: number, assemblyRequestId: number, payload: FfCreateRequestPayload) {
            return api.request<FfPushAssemblyResult>('POST', `/api/v1/warehouse/${warehouseId}/fulfillment/assembly/${assemblyRequestId}/create-request`, payload);
        },
        /** Массово создать заявки на ФФ из выбранных сборок (склад МП/дата выгрузки — по каждой). */
        bulkCreateFfRequests(warehouseId: number, payload: FfBulkCreateRequestPayload) {
            return api.request<FfBulkCreateResult>('POST', `/api/v1/warehouse/${warehouseId}/fulfillment/assembly/bulk-create-requests`, payload);
        },
        /** Массово убрать/вернуть заявки ФФ в локальный архив. */
        bulkArchiveFulfillmentRequests(warehouseId: number, payload: FfBulkArchivePayload) {
            return api.request<FfBulkArchiveResult>('POST', `/api/v1/warehouse/${warehouseId}/fulfillment/requests/bulk-archive`, payload);
        },
    };
}

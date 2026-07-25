/** WB FBS API methods — склады продавца, трансляция остатков, задания, поставки. */
import { ApiClient } from './client';
import type {
    FbsActionResult,
    FbsLinkCreatePayload,
    FbsModeInfo,
    FbsOffice,
    FbsOrderListResponse,
    FbsOrderStats,
    FbsOverrideSetPayload,
    FbsReconcileApplyPayload,
    FbsReconcileOut,
    FbsStickerRequestPayload,
    FbsSticker,
    FbsStockPreview,
    FbsStockPush,
    FbsStockPushPayload,
    FbsOrder,
    FbsPickList,
    FbsSupply,
    FbsSupplyAddOrdersPayload,
    FbsSupplyBarcode,
    FbsSupplyBulkCreatePayload,
    FbsSupplyBulkResult,
    FbsSupplyCreatePayload,
    FbsSupplyPlan,
    FbsWarehouse,
    FbsWarehouseCreatePayload,
    FbsWarehouseRenamePayload,
    FbsWarehouseSettingsPayload,
} from '@/types/api';

/** Фильтры списка сборочных заданий (GET /fbs/orders). */
export interface FbsOrderFilters {
    status?: string;
    supplyId?: string;
    wbWarehouseId?: number;
    dateFrom?: string;
    dateTo?: string;
    limit?: number;
    offset?: number;
}

function buildOrderQuery(f: FbsOrderFilters): string {
    const q = new URLSearchParams();
    if (f.status) q.set('status', f.status);
    if (f.supplyId) q.set('supply_id', f.supplyId);
    if (f.wbWarehouseId != null) q.set('wb_warehouse_id', String(f.wbWarehouseId));
    if (f.dateFrom) q.set('date_from', f.dateFrom);
    if (f.dateTo) q.set('date_to', f.dateTo);
    if (f.limit != null) q.set('limit', String(f.limit));
    if (f.offset != null) q.set('offset', String(f.offset));
    return q.toString();
}

export function addFbsMethods(api: ApiClient) {
    return {
        // ─── Режим контура ───────────────────────────────────────────────────
        /** Режим FBS: можно ли писать в WB и на каком хосте мы сейчас работаем. */
        getFbsMode() {
            return api.request<FbsModeInfo>('GET', '/api/v1/fbs/mode');
        },

        // ─── Склады продавца WB ──────────────────────────────────────────────
        getFbsOffices() {
            return api.request<FbsOffice[]>('GET', '/api/v1/fbs/offices');
        },
        getFbsWarehouses() {
            return api.request<FbsWarehouse[]>('GET', '/api/v1/fbs/warehouses');
        },
        syncFbsWarehouses() {
            return api.request<FbsActionResult>('POST', '/api/v1/fbs/warehouses/sync');
        },
        createFbsWarehouse(body: FbsWarehouseCreatePayload) {
            return api.request<FbsActionResult>('POST', '/api/v1/fbs/warehouses', body);
        },
        renameFbsWarehouse(wbWarehouseId: number, body: FbsWarehouseRenamePayload) {
            return api.request<FbsActionResult>('PUT', `/api/v1/fbs/warehouses/${wbWarehouseId}`, body);
        },
        updateFbsWarehouseSettings(wbWarehouseId: number, body: FbsWarehouseSettingsPayload) {
            return api.request<FbsWarehouse>('PATCH', `/api/v1/fbs/warehouses/${wbWarehouseId}/settings`, body);
        },
        linkFbsWarehouse(body: FbsLinkCreatePayload) {
            return api.request<FbsActionResult>('POST', '/api/v1/fbs/links', body);
        },
        unlinkFbsWarehouse(linkId: number) {
            return api.request<FbsActionResult>('DELETE', `/api/v1/fbs/links/${linkId}`);
        },

        // ─── Остатки ─────────────────────────────────────────────────────────
        getFbsStockPreview(wbWarehouseId: number) {
            const q = new URLSearchParams({ wb_warehouse_id: String(wbWarehouseId) });
            return api.request<FbsStockPreview>('GET', `/api/v1/fbs/stock/preview?${q.toString()}`);
        },
        /** Асинхронный триггер: ответ не ждёт похода в WB — прогресс смотрим в /fbs/stock/pushes. */
        pushFbsStocks(body: FbsStockPushPayload) {
            return api.request<FbsActionResult>('POST', '/api/v1/fbs/stock/push', body);
        },
        getFbsStockPushes(limit = 50) {
            const q = new URLSearchParams({ limit: String(limit) });
            return api.request<FbsStockPush[]>('GET', `/api/v1/fbs/stock/pushes?${q.toString()}`);
        },
        /**
         * Первичная сверка склада: спрашиваем у WB фактические остатки по всей
         * нашей номенклатуре и сравниваем с расчётом. ЧТЕНИЕ WB — кабинет не
         * меняется, поэтому доступна в любом режиме контура.
         */
        getFbsStockReconcile(wbWarehouseId: number) {
            const q = new URLSearchParams({ wb_warehouse_id: String(wbWarehouseId) });
            return api.request<FbsReconcileOut>('GET', `/api/v1/fbs/stock/reconcile?${q.toString()}`);
        },
        /** Обнулить в WB позиции, которыми мы не управляем. ЗАПИСЬ — гейтится режимом. */
        applyFbsStockReconcile(body: FbsReconcileApplyPayload) {
            return api.request<FbsActionResult>('POST', '/api/v1/fbs/stock/reconcile', body);
        },

        // ─── Потоварная замена количества ────────────────────────────────────
        // Ручное количество живёт в НАШЕЙ базе и в WB ничего не пишет — доступно
        // в любом режиме контура, гасить его по write_enabled нельзя.
        /**
         * Задать / снять ручное количество для товаров склада продавца.
         * qty = 0 — не отдавать, qty > 0 — потолок, qty = null — снять.
         */
        setFbsStockOverride(body: FbsOverrideSetPayload) {
            return api.request<FbsActionResult>('POST', '/api/v1/fbs/stock/override', body);
        },

        // ─── Сборочные задания ───────────────────────────────────────────────
        getFbsOrders(f: FbsOrderFilters = {}) {
            const q = buildOrderQuery(f);
            return api.request<FbsOrderListResponse>('GET', `/api/v1/fbs/orders${q ? `?${q}` : ''}`);
        },
        /** Сводка заказов за период: выручка, разрезы, доля в объёме воронки. */
        getFbsOrderStats(f: { dateFrom?: string; dateTo?: string; wbWarehouseId?: number } = {}) {
            const q = new URLSearchParams();
            if (f.dateFrom) q.set('date_from', f.dateFrom);
            if (f.dateTo) q.set('date_to', f.dateTo);
            if (f.wbWarehouseId) q.set('wb_warehouse_id', String(f.wbWarehouseId));
            const qs = q.toString();
            return api.request<FbsOrderStats>('GET', `/api/v1/fbs/orders/stats${qs ? `?${qs}` : ''}`);
        },
        syncFbsOrders() {
            return api.request<FbsActionResult>('POST', '/api/v1/fbs/orders/sync');
        },
        /** Стикеры приходят base64 — файл собирается на клиенте. */
        getFbsStickers(body: FbsStickerRequestPayload) {
            return api.request<FbsSticker[]>('POST', '/api/v1/fbs/orders/stickers', body);
        },
        cancelFbsOrder(wbOrderId: number) {
            return api.request<FbsActionResult>('PATCH', `/api/v1/fbs/orders/${wbOrderId}/cancel`);
        },

        // ─── Поставки ────────────────────────────────────────────────────────
        getFbsSupplies(opts: { done?: boolean; limit?: number; offset?: number } = {}) {
            const q = new URLSearchParams();
            if (opts.done != null) q.set('done', String(opts.done));
            if (opts.limit != null) q.set('limit', String(opts.limit));
            if (opts.offset != null) q.set('offset', String(opts.offset));
            const qs = q.toString();
            return api.request<FbsSupply[]>('GET', `/api/v1/fbs/supplies${qs ? `?${qs}` : ''}`);
        },
        createFbsSupply(body: FbsSupplyCreatePayload) {
            return api.request<FbsActionResult>('POST', '/api/v1/fbs/supplies', body);
        },
        syncFbsSupplies() {
            return api.request<FbsActionResult>('POST', '/api/v1/fbs/supplies/sync');
        },
        /**
         * Предпросмотр разбиения выделенных заданий на поставки.
         * Ничего не пишет в WB — доступен в любом режиме контура.
         */
        planFbsSupplies(orderIds: number[]) {
            return api.request<FbsSupplyPlan>('POST', '/api/v1/fbs/supplies/plan', { order_ids: orderIds });
        },
        /** Создать поставки по плану: одна на каждую группу «склад + габарит». */
        createFbsSuppliesBulk(body: FbsSupplyBulkCreatePayload) {
            return api.request<FbsSupplyBulkResult>('POST', '/api/v1/fbs/supplies/bulk', body);
        },
        /**
         * Задания конкретной поставки — разворот строки на вкладке «Поставки».
         * Ответ терпим и к голому списку, и к обёртке `{items}`: строка поставки
         * не должна ломаться из-за формы ответа.
         */
        async getFbsSupplyOrders(wbSupplyId: string): Promise<FbsOrder[]> {
            const res = await api.request<FbsOrder[] | { items?: FbsOrder[] }>(
                'GET', `/api/v1/fbs/supplies/${encodeURIComponent(wbSupplyId)}/orders`,
            );
            if (Array.isArray(res)) return res;
            return res?.items ?? [];
        },
        /**
         * Лист подбора поставки — что и сколько снять со склада, агрегат ПО
         * ТОВАРУ. Чтение: доступен в любом режиме контура.
         */
        getFbsPickList(wbSupplyId: string) {
            return api.request<FbsPickList>(
                'GET', `/api/v1/fbs/supplies/${encodeURIComponent(wbSupplyId)}/pick-list`,
            );
        },
        addOrdersToFbsSupply(wbSupplyId: string, body: FbsSupplyAddOrdersPayload) {
            return api.request<FbsActionResult>(
                'PATCH', `/api/v1/fbs/supplies/${encodeURIComponent(wbSupplyId)}/orders`, body,
            );
        },
        deliverFbsSupply(wbSupplyId: string) {
            return api.request<FbsActionResult>(
                'PATCH', `/api/v1/fbs/supplies/${encodeURIComponent(wbSupplyId)}/deliver`,
            );
        },
        deleteFbsSupply(wbSupplyId: string) {
            return api.request<FbsActionResult>(
                'DELETE', `/api/v1/fbs/supplies/${encodeURIComponent(wbSupplyId)}`,
            );
        },
        /** QR поставки — доступен только после передачи (deliver). */
        getFbsSupplyBarcode(wbSupplyId: string, type = 'png') {
            const q = new URLSearchParams({ type });
            return api.request<FbsSupplyBarcode>(
                'GET', `/api/v1/fbs/supplies/${encodeURIComponent(wbSupplyId)}/barcode?${q.toString()}`,
            );
        },
    };
}

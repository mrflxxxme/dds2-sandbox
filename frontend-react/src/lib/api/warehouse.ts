/** Warehouse API methods */
import { ApiClient } from './client';
import type {
    AssemblyHistoryEntry,
    AssemblyListResponse,
    AssemblyRequest,
    AssemblyRequestCreate,
    AssemblyRequestUpdate,
    DefectBulkOperation,
    DefectBulkResponse,
    DefectOperation,
    DeliveryTimesResponse,
    DeliveryTimesUpdate,
    FboSyncResult,
    InboundReceipt,
    LogisticsAnalyticsResponse,
    OutboundShipment,
    RefreshFromFboResponse,
    StockAdjustment,
    StockMovement,
    StockSummaryRow,
    UnifiedStockRow,
    StockTransfer,
    Warehouse,
    WarehouseStockRow,
    WbFboSupply,
    WbFboSupplyItem,
    WbFboSupplyListResponse,
    MessageResponse,
} from '@/types/api';

export function addWarehouseMethods(api: ApiClient) {
    return {
        // ─── Warehouses CRUD ─────────────────────────────────────────
        getWarehouses() { return api.request<Warehouse[]>('GET', '/api/v1/warehouse'); },
        createWarehouse(data: Partial<Warehouse>) { return api.request<Warehouse>('POST', '/api/v1/warehouse', data); },
        updateWarehouse(id: number, data: Partial<Warehouse>) { return api.request<Warehouse>('PUT', `/api/v1/warehouse/${id}`, data); },
        deleteWarehouse(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/warehouse/${id}`); },
        reorderWarehouses(items: { id: number; sort_order: number }[]) {
            return api.request<MessageResponse>('PUT', '/api/v1/warehouse/reorder', { items });
        },

        // ─── Stock ───────────────────────────────────────────────────
        getExpectedVehicles(warehouseId: number) { return api.request<any[]>('GET', `/api/v1/warehouse/${warehouseId}/expected-vehicles`); },
        getWarehouseStock(warehouseId: number) { return api.request<WarehouseStockRow[]>('GET', `/api/v1/warehouse/${warehouseId}/stock`); },
        getStockMovements(warehouseId: number, limit = 200) { return api.request<StockMovement[]>('GET', `/api/v1/warehouse/${warehouseId}/movements?limit=${limit}`); },
        getStockSummary() { return api.request<StockSummaryRow[]>('GET', '/api/v1/warehouse/stock/summary'); },
        getUnifiedStock(groupBy?: string, brand?: string, includeForecast?: boolean) {
            const params = new URLSearchParams();
            if (groupBy) params.set('group_by', groupBy);
            if (brand) params.set('brand', brand);
            if (includeForecast) params.set('include_forecast', 'true');
            const qs = params.toString();
            return api.request<UnifiedStockRow[]>('GET', `/api/v1/warehouse/stock/unified${qs ? `?${qs}` : ''}`);
        },
        updateCostPrice(stockId: number, costPrice: number) { return api.request<WarehouseStockRow>('PUT', `/api/v1/warehouse/stock/${stockId}/cost-price`, { cost_price: costPrice }); },

        // ─── Inbound Receipts ────────────────────────────────────────
        getReceipts(warehouseId: number) { return api.request<InboundReceipt[]>('GET', `/api/v1/warehouse/${warehouseId}/receipts`); },
        createReceipt(warehouseId: number, data: { planned_date?: string; comment?: string; tags?: string; items: { barcode: string; expected_qty: number; actual_qty?: number }[] }) {
            return api.request<InboundReceipt>('POST', `/api/v1/warehouse/${warehouseId}/receipts`, data);
        },
        getReceipt(receiptId: number) { return api.request<InboundReceipt>('GET', `/api/v1/warehouse/receipts/${receiptId}`); },
        updateReceipt(receiptId: number, data: Record<string, unknown>) { return api.request<InboundReceipt>('PUT', `/api/v1/warehouse/receipts/${receiptId}`, data); },
        acceptReceipt(receiptId: number, actualQuantities?: { item_id: number; actual_qty: number }[]) {
            return api.request<InboundReceipt>('POST', `/api/v1/warehouse/receipts/${receiptId}/accept`, actualQuantities || null);
        },
        cancelReceipt(receiptId: number) { return api.request<InboundReceipt>('POST', `/api/v1/warehouse/receipts/${receiptId}/cancel`); },

        // ─── Outbound Shipments ──────────────────────────────────────
        getShipments(warehouseId: number) { return api.request<OutboundShipment[]>('GET', `/api/v1/warehouse/${warehouseId}/shipments`); },
        createShipment(warehouseId: number, data: { destination?: string; comment?: string; items: { barcode: string; quantity: number }[] }) {
            return api.request<OutboundShipment>('POST', `/api/v1/warehouse/${warehouseId}/shipments`, data);
        },
        getShipment(shipmentId: number) { return api.request<OutboundShipment>('GET', `/api/v1/warehouse/shipments/${shipmentId}`); },
        shipShipment(shipmentId: number) { return api.request<OutboundShipment>('POST', `/api/v1/warehouse/shipments/${shipmentId}/ship`); },
        deliverShipment(shipmentId: number) { return api.request<OutboundShipment>('POST', `/api/v1/warehouse/shipments/${shipmentId}/deliver`); },
        cancelShipment(shipmentId: number) { return api.request<OutboundShipment>('POST', `/api/v1/warehouse/shipments/${shipmentId}/cancel`); },

        // ─── Stock Transfers ─────────────────────────────────────────
        getTransfers(inTransitOnly = false) { return api.request<StockTransfer[]>('GET', `/api/v1/warehouse/transfers?in_transit=${inTransitOnly}`); },
        createTransfer(data: { from_warehouse_id: number; to_warehouse_id: number; comment?: string; is_defect?: boolean; defect_reason?: string; items: { barcode: string; quantity: number }[] }) {
            return api.request<StockTransfer>('POST', '/api/v1/warehouse/transfers', data);
        },
        sendTransfer(transferId: number) { return api.request<StockTransfer>('POST', `/api/v1/warehouse/transfers/${transferId}/send`); },
        completeTransfer(transferId: number) { return api.request<StockTransfer>('POST', `/api/v1/warehouse/transfers/${transferId}/complete`); },

        // ─── Defects ─────────────────────────────────────────────────
        getDefectStock(warehouseId: number) { return api.request<WarehouseStockRow[]>('GET', `/api/v1/warehouse/${warehouseId}/defects`); },
        markDefect(warehouseId: number, data: DefectOperation) { return api.request<MessageResponse>('POST', `/api/v1/warehouse/${warehouseId}/defects/mark`, data); },
        receiveDefect(warehouseId: number, data: DefectOperation) { return api.request<MessageResponse>('POST', `/api/v1/warehouse/${warehouseId}/defects/receive`, data); },
        writeoffDefect(warehouseId: number, data: DefectOperation) { return api.request<MessageResponse>('POST', `/api/v1/warehouse/${warehouseId}/defects/writeoff`, data); },
        recoverDefect(warehouseId: number, data: DefectOperation) { return api.request<MessageResponse>('POST', `/api/v1/warehouse/${warehouseId}/defects/recover`, data); },
        markDefectBulk(warehouseId: number, data: DefectBulkOperation) {
            return api.request<DefectBulkResponse>('POST', `/api/v1/warehouse/${warehouseId}/defects/mark-bulk`, data);
        },
        receiveDefectBulk(warehouseId: number, data: DefectBulkOperation) {
            return api.request<DefectBulkResponse>('POST', `/api/v1/warehouse/${warehouseId}/defects/receive-bulk`, data);
        },
        writeoffDefectBulk(warehouseId: number, data: DefectBulkOperation) {
            return api.request<DefectBulkResponse>('POST', `/api/v1/warehouse/${warehouseId}/defects/writeoff-bulk`, data);
        },
        recoverDefectBulk(warehouseId: number, data: DefectBulkOperation) {
            return api.request<DefectBulkResponse>('POST', `/api/v1/warehouse/${warehouseId}/defects/recover-bulk`, data);
        },
        deleteDefectMovement(warehouseId: number, movementId: number) {
            return api.request<MessageResponse>('DELETE', `/api/v1/warehouse/${warehouseId}/defects/movements/${movementId}`);
        },
        getDefectSummary() { return api.request<WarehouseStockRow[]>('GET', '/api/v1/warehouse/defects/summary'); },
        getDefectReceipts(warehouseId: number) { return api.request<InboundReceipt[]>('GET', `/api/v1/warehouse/${warehouseId}/defect-receipts`); },
        getDefectShipments(warehouseId: number) { return api.request<OutboundShipment[]>('GET', `/api/v1/warehouse/${warehouseId}/defect-shipments`); },

        // ─── Adjustments ─────────────────────────────────────────────
        createAdjustment(warehouseId: number, data: { barcode: string; delta: number; reason: string }) {
            return api.request<StockAdjustment>('POST', `/api/v1/warehouse/${warehouseId}/adjustment`, data);
        },

        // ─── Delivery Times ──────────────────────────────────────────
        getDeliveryTimes(warehouseId: number) {
            return api.request<DeliveryTimesResponse>('GET', `/api/v1/warehouse/${warehouseId}/delivery-times`);
        },
        updateDeliveryTimes(warehouseId: number, data: DeliveryTimesUpdate) {
            return api.request<DeliveryTimesResponse>('PUT', `/api/v1/warehouse/${warehouseId}/delivery-times`, data);
        },

        // ─── FBO Supplies ──────────────────────────────────────────────
        getFboWarehouses() {
            return api.request<string[]>('GET', '/api/v1/warehouse/fbo-supplies/warehouses');
        },
        getFboSupplies(params?: {
            search?: string; status?: string; warehouse?: string; date_from?: string; date_to?: string;
            sort_by?: string; sort_order?: string; limit?: number; offset?: number;
            exclude_with_assembly?: boolean;
        }) {
            const query = new URLSearchParams();
            if (params) {
                Object.entries(params).forEach(([k, v]) => {
                    if (v !== undefined && v !== null && v !== '') query.set(k, String(v));
                });
            }
            const qs = query.toString();
            return api.request<WbFboSupplyListResponse>('GET', `/api/v1/warehouse/fbo-supplies${qs ? `?${qs}` : ''}`);
        },
        getFboSupplyItems(supplyId: number, refresh?: boolean) {
            const qs = refresh ? '?refresh=true' : '';
            return api.request<WbFboSupplyItem[]>('GET', `/api/v1/warehouse/fbo-supplies/${supplyId}/items${qs}`);
        },
        syncFboSupplies() {
            return api.request<FboSyncResult>('POST', '/api/v1/warehouse/fbo-supplies/sync');
        },
        syncFboStatuses() {
            return api.request<FboSyncResult>('POST', '/api/v1/warehouse/fbo-supplies/sync-statuses');
        },
        linkFboSupply(supplyId: number, outboundShipmentId: number) {
            return api.request<WbFboSupply>('POST', `/api/v1/warehouse/fbo-supplies/${supplyId}/link`, { outbound_shipment_id: outboundShipmentId });
        },
        unlinkFboSupply(supplyId: number) {
            return api.request<WbFboSupply>('DELETE', `/api/v1/warehouse/fbo-supplies/${supplyId}/link`);
        },

        // ─── WB Warehouse Names ─────────────────────────────────────────
        getWbWarehouseNames() {
            return api.request<string[]>('GET', '/api/v1/warehouse/assembly/wb-warehouses');
        },

        // ─── Assembly Requests ──────────────────────────────────────────
        getAssemblyRequests(params?: {
            warehouse_id?: number; status?: string; search?: string;
            date_from?: string; date_to?: string; brand?: string;
            limit?: number; offset?: number;
        }) {
            const query = new URLSearchParams();
            if (params) {
                Object.entries(params).forEach(([k, v]) => {
                    if (v !== undefined && v !== null && v !== '') query.set(k, String(v));
                });
            }
            const qs = query.toString();
            return api.request<AssemblyListResponse>('GET', `/api/v1/warehouse/assembly${qs ? `?${qs}` : ''}`);
        },
        createAssemblyRequest(data: AssemblyRequestCreate) {
            return api.request<AssemblyRequest>('POST', '/api/v1/warehouse/assembly', data);
        },
        getAssemblyRequest(id: number) {
            return api.request<AssemblyRequest>('GET', `/api/v1/warehouse/assembly/${id}`);
        },
        updateAssemblyRequest(id: number, data: AssemblyRequestUpdate) {
            return api.request<AssemblyRequest>('PUT', `/api/v1/warehouse/assembly/${id}`, data);
        },
        startAssembly(id: number) {
            return api.request<AssemblyRequest>('POST', `/api/v1/warehouse/assembly/${id}/start`);
        },
        markAssemblyReady(id: number) {
            return api.request<AssemblyRequest>('POST', `/api/v1/warehouse/assembly/${id}/ready`);
        },
        assignVehicle(id: number, data: {
            vehicle_info: string;
            vehicle_brand: string;
            driver_phone: string;
            pickup_date: string;
            pickup_time_slot: string;
            pickup_cost: number;
            delivery_date: string;
        }) {
            return api.request<AssemblyRequest>('POST', `/api/v1/warehouse/assembly/${id}/assign-vehicle`, data);
        },
        shipAssembly(id: number) {
            return api.request<AssemblyRequest>('POST', `/api/v1/warehouse/assembly/${id}/ship`);
        },
        unassignVehicle(id: number) {
            return api.request<AssemblyRequest>('POST', `/api/v1/warehouse/assembly/${id}/unassign-vehicle`);
        },
        cancelAssembly(id: number) {
            return api.request<AssemblyRequest>('POST', `/api/v1/warehouse/assembly/${id}/cancel`);
        },
        assignVehicleBulk(data: {
            vehicle_info: string;
            vehicle_brand: string;
            driver_phone: string;
            items: Array<{
                request_id: number;
                pickup_date: string;
                pickup_time_slot: string;
                pickup_cost: number;
                delivery_date: string;
            }>;
        }) {
            return api.request<AssemblyRequest[]>('POST', '/api/v1/warehouse/assembly/assign-vehicle-bulk', data);
        },
        shipBulk(ids: number[]) {
            return api.request<AssemblyRequest[]>('POST', '/api/v1/warehouse/assembly/ship-bulk', { ids });
        },
        refreshFromFbo(id: number) {
            return api.request<RefreshFromFboResponse>('POST', `/api/v1/warehouse/assembly/${id}/refresh-from-fbo`);
        },
        getAssemblyHistory(id: number) {
            return api.request<AssemblyHistoryEntry[]>('GET', `/api/v1/warehouse/assembly/${id}/history`);
        },
        getShipmentAnalytics(params?: { date_from?: string; date_to?: string; warehouse_ids?: string; brands?: string }) {
            const query = new URLSearchParams();
            if (params) {
                Object.entries(params).forEach(([k, v]) => {
                    if (v !== undefined && v !== null && v !== '') query.set(k, String(v));
                });
            }
            const qs = query.toString();
            return api.request<LogisticsAnalyticsResponse>('GET', `/api/v1/warehouse/assembly/shipments/analytics${qs ? `?${qs}` : ''}`);
        },
    };
}

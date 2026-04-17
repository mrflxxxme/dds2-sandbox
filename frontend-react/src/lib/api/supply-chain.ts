/** Supply Chain API methods */
import { ApiClient } from './client';
import type {
    FactoryOrder,
    FactoryOrderCreate,
    FactoryOrderHistory,
    FactoryOrderItem,
    FactoryOrderItemUpdate,
    FactoryOrderStatus,
    VehicleStatusUpdate,
    VehicleCreate,
    VehicleUpdateData,
    VehicleSchema,
    VehicleCostSummary,
    VehiclePriceResyncPreview,
    VehiclePriceResyncApplyResult,
    AvailableItemGroup,
    SupplyChainOverview,
    SplitItem,
    MessageResponse,
    VehicleDocument,
    VehicleStatusHistoryEntry,
    Supplier,
    SupplierCatalogResponse,
    ShipmentMatrixResponse,
} from '@/types/api';

export function addSupplyChainMethods(api: ApiClient) {
    return {
        // ─── Factory Orders ─────────────────────────────────────────
        getFactoryOrders() {
            return api.request<FactoryOrder[]>('GET', '/api/v1/supply-chain/factory-orders');
        },
        getFactoryOrder(id: number) {
            return api.request<FactoryOrder>('GET', `/api/v1/supply-chain/factory-orders/${id}`);
        },
        createFactoryOrder(data: FactoryOrderCreate) {
            return api.request<FactoryOrder>('POST', '/api/v1/supply-chain/factory-orders', data);
        },
        updateFactoryOrder(id: number, data: Partial<FactoryOrderCreate>) {
            return api.request<FactoryOrder>('PUT', `/api/v1/supply-chain/factory-orders/${id}`, data);
        },
        deleteFactoryOrder(id: number) {
            return api.request<MessageResponse>('DELETE', `/api/v1/supply-chain/factory-orders/${id}`);
        },

        // ─── Factory Order Items ────────────────────────────────────
        addFactoryOrderItems(orderId: number, items: Omit<FactoryOrderItem, 'id' | 'factory_order_id' | 'assigned_qty' | 'remaining_qty'>[]) {
            return api.request<{ ok: boolean; added: number }>('POST', `/api/v1/supply-chain/factory-orders/${orderId}/items`, items);
        },

        bulkUpdateItemSpecs(orderId: number, updates: { barcode: string; box_size?: string; pcs_per_box?: number; weight_kg?: number }[]) {
            return api.request<{ ok: boolean; updated: number; not_found: string[] }>('PUT', `/api/v1/supply-chain/factory-orders/${orderId}/items/bulk-specs`, updates);
        },
        updateFactoryOrderItem(orderId: number, itemId: number, data: FactoryOrderItemUpdate) {
            return api.request<FactoryOrderItem>('PUT', `/api/v1/supply-chain/factory-orders/${orderId}/items/${itemId}`, data);
        },
        deleteFactoryOrderItem(orderId: number, itemId: number) {
            return api.request<{ ok: boolean }>('DELETE', `/api/v1/supply-chain/factory-orders/${orderId}/items/${itemId}`);
        },

        // ─── Mix Groups ────────────────────────────────────────────
        setMixGroup(orderId: number, items: { id: number; pcs_per_box: number }[], boxSize: string) {
            return api.request<{ mix_group_id: string; item_ids: number[]; box_size: string }>('POST', `/api/v1/supply-chain/factory-orders/${orderId}/mix-group`, { items, box_size: boxSize });
        },
        removeMixGroup(orderId: number, mixGroupId: string) {
            return api.request<{ ok: boolean; removed_items: number }>('DELETE', `/api/v1/supply-chain/factory-orders/${orderId}/mix-group/${mixGroupId}`);
        },

        // ─── Split to Vehicles ──────────────────────────────────────
        splitToVehicles(orderId: number, assignments: SplitItem[]) {
            return api.request<MessageResponse>('POST', `/api/v1/supply-chain/factory-orders/${orderId}/split-to-vehicles`, { assignments });
        },

        // ─── Factory Order History & Status ────────────────────────
        getFactoryOrderHistory(orderId: number) {
            return api.request<FactoryOrderHistory[]>('GET', `/api/v1/supply-chain/factory-orders/${orderId}/history`);
        },
        updateFactoryOrderStatus(orderId: number, status: FactoryOrderStatus) {
            return api.request<FactoryOrder>('PUT', `/api/v1/supply-chain/factory-orders/${orderId}/status`, { status });
        },

        // ─── Vehicles (CostOrders with supply chain context) ────────
        getVehicles() {
            return api.request<VehicleSchema[]>('GET', '/api/v1/supply-chain/vehicles');
        },
        getVehicle(orderNo: string) {
            // Use query-param endpoint for order_no with slashes (server decodes %2F before routing)
            if (orderNo.includes('/')) {
                return api.request<VehicleSchema>('GET', `/api/v1/supply-chain/vehicles/find?order_no=${encodeURIComponent(orderNo)}`);
            }
            return api.request<VehicleSchema>('GET', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}`);
        },
        createVehicle(data: VehicleCreate) {
            return api.request<VehicleSchema>('POST', '/api/v1/supply-chain/vehicles', data);
        },
        updateVehicle(orderNo: string, data: VehicleUpdateData) {
            return api.request<VehicleSchema>('PUT', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}`, data);
        },
        updateVehicleStatus(orderNo: string, data: VehicleStatusUpdate) {
            return api.request<Record<string, unknown>>('PUT', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/status`, data);
        },
        recalcVehicle(orderNo: string) {
            return api.request<VehicleCostSummary>('POST', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/recalc`);
        },
        addItemsToVehicle(orderNo: string, items: { factory_order_item_id: number; qty: number }[]) {
            return api.request<{ ok: boolean; added: number }>('POST', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/items`, { items });
        },
        removeItemFromVehicle(orderNo: string, itemId: number) {
            return api.request<{ ok: boolean }>('DELETE', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/items/${itemId}`);
        },
        clearAllVehicleItems(orderNo: string) {
            return api.request<{ ok: boolean; removed: number }>('DELETE', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/items`);
        },
        previewVehiclePriceResync(orderNo: string) {
            return api.request<VehiclePriceResyncPreview>('GET', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/price-resync/preview`);
        },
        applyVehiclePriceResync(orderNo: string) {
            return api.request<VehiclePriceResyncApplyResult>('POST', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/price-resync`);
        },
        bulkUpdateFactoryItemPrices(items: { factory_order_item_id: number; new_price_cny: string | number }[]) {
            return api.request<{ updated: number; not_found: number[] }>('PUT', '/api/v1/supply-chain/factory-orders/items/bulk-price', { items });
        },
        deleteVehicle(orderNo: string) {
            if (orderNo.includes('/')) {
                return api.request<{ ok: boolean }>('DELETE', `/api/v1/supply-chain/vehicles/find?order_no=${encodeURIComponent(orderNo)}`);
            }
            return api.request<{ ok: boolean }>('DELETE', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}`);
        },
        getAvailableItems() {
            return api.request<AvailableItemGroup[]>('GET', '/api/v1/supply-chain/vehicles/available-items');
        },

        // ─── Vehicle Documents ──────────────────────────────────────
        getVehicleDocuments(orderNo: string) {
            return api.request<VehicleDocument[]>('GET', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/documents`);
        },
        uploadVehicleDocument(orderNo: string, file: File, docType: string, note?: string) {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('doc_type', docType);
            if (note) formData.append('note', note);
            return api.uploadFormData<VehicleDocument>(`/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/documents`, formData);
        },
        async downloadVehicleDocument(orderNo: string, docId: number) {
            const headers: Record<string, string> = {};
            const token = api.getToken();
            if (token) headers['Authorization'] = `Bearer ${token}`;
            const projectId = api.getProjectId();
            if (projectId) headers['X-Project-Id'] = String(projectId);
            const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || ''}/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/documents/${docId}/download`, { headers });
            if (!res.ok) throw new Error('Ошибка скачивания');
            return res.blob();
        },
        deleteVehicleDocument(orderNo: string, docId: number) {
            return api.request<{ ok: boolean }>('DELETE', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/documents/${docId}`);
        },

        // ─── Vehicle History ────────────────────────────────────────
        getVehicleHistory(orderNo: string) {
            return api.request<VehicleStatusHistoryEntry[]>('GET', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/history`);
        },

        // ─── Overview ───────────────────────────────────────────────
        getSupplyChainOverview() {
            return api.request<SupplyChainOverview>('GET', '/api/v1/supply-chain/overview');
        },

        // ─── Suppliers ──────────────────────────────────────────────
        getSuppliers() {
            return api.request<Supplier[]>('GET', '/api/v1/supply-chain/suppliers');
        },
        getSupplier(id: number) {
            return api.request<Supplier>('GET', `/api/v1/supply-chain/suppliers/${id}`);
        },
        createSupplier(data: Partial<Supplier>) {
            return api.request<Supplier>('POST', '/api/v1/supply-chain/suppliers', data);
        },
        updateSupplier(id: number, data: Partial<Supplier>) {
            return api.request<Supplier>('PUT', `/api/v1/supply-chain/suppliers/${id}`, data);
        },
        deleteSupplier(id: number) {
            return api.request<MessageResponse>('DELETE', `/api/v1/supply-chain/suppliers/${id}`);
        },
        getSupplierCatalog(id: number) {
            return api.request<SupplierCatalogResponse>('GET', `/api/v1/supply-chain/suppliers/${id}/catalog`);
        },
        getShipmentMatrix(id: number) {
            return api.request<ShipmentMatrixResponse>('GET', `/api/v1/supply-chain/suppliers/${id}/shipment-matrix`);
        },

        // ─── Vehicles by query (for order_no with slashes) ──────────
        getVehicleByQuery(orderNo: string) {
            return api.request<VehicleSchema>('GET', `/api/v1/supply-chain/vehicles/find?order_no=${encodeURIComponent(orderNo)}`);
        },
        deleteVehicleByQuery(orderNo: string) {
            return api.request<{ ok: boolean }>('DELETE', `/api/v1/supply-chain/vehicles/find?order_no=${encodeURIComponent(orderNo)}`);
        },
    };
}

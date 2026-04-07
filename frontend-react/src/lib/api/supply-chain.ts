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
    AvailableItemGroup,
    SupplyChainOverview,
    SplitItem,
    MessageResponse,
    VehicleDocument,
    VehicleStatusHistoryEntry,
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

        updateFactoryOrderItem(orderId: number, itemId: number, data: FactoryOrderItemUpdate) {
            return api.request<FactoryOrderItem>('PUT', `/api/v1/supply-chain/factory-orders/${orderId}/items/${itemId}`, data);
        },
        deleteFactoryOrderItem(orderId: number, itemId: number) {
            return api.request<{ ok: boolean }>('DELETE', `/api/v1/supply-chain/factory-orders/${orderId}/items/${itemId}`);
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
    };
}

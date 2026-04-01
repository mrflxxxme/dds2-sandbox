/** Supply Chain API methods */
import { ApiClient } from './client';
import type {
    FactoryOrder,
    FactoryOrderCreate,
    FactoryOrderItem,
    VehicleStatusUpdate,
    VehicleCreate,
    VehicleUpdateData,
    VehicleSchema,
    VehicleCostSummary,
    AvailableItemGroup,
    SupplyChainOverview,
    SplitItem,
    MessageResponse,
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

        // ─── Split to Vehicles ──────────────────────────────────────
        splitToVehicles(orderId: number, assignments: SplitItem[]) {
            return api.request<MessageResponse>('POST', `/api/v1/supply-chain/factory-orders/${orderId}/split-to-vehicles`, { assignments });
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

        // ─── Overview ───────────────────────────────────────────────
        getSupplyChainOverview() {
            return api.request<SupplyChainOverview>('GET', '/api/v1/supply-chain/overview');
        },
    };
}

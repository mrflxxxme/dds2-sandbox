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
    VehicleItemUpdate,
    FactoryQtyExceededDetail,
    PostShipmentItemsRequest,
    PostShipmentItemsResponse,
    AddUnorderedItemsRequest,
    AddUnorderedItemsResponse,
    SupplyProject,
    SupplyProjectCreate,
    SupplyProjectUpdate,
    MergeOrdersResult,
    SupplierDebtOverview,
    SupplierFinance,
    AutoDistributeResult,
} from '@/types/api';
import { FactoryQtyExceededError } from '@/types/api';

const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

/**
 * Parse a JSON-stringified 422 detail back to FactoryQtyExceededDetail.
 *
 * Background: `client.ts` strips structured object detail and rethrows as
 * `new Error(JSON.stringify(detail))`. This helper recovers it for typed
 * error handling on the supply-chain side without touching client.ts (shared).
 *
 * Returns null if the message is not a parseable drift-confirm detail.
 */
export function parseFactoryQtyExceededDetail(message: string): FactoryQtyExceededDetail | null {
    if (!message || message[0] !== '{') return null;
    try {
        const parsed = JSON.parse(message) as Partial<FactoryQtyExceededDetail>;
        if (parsed && parsed.error === 'exceeds_factory_qty' && typeof parsed.foi_id === 'number') {
            return parsed as FactoryQtyExceededDetail;
        }
    } catch {
        // not JSON — pass through
    }
    return null;
}

export function addSupplyChainMethods(api: ApiClient) {
    return {
        // ─── Factory Orders ─────────────────────────────────────────
        getFactoryOrders() {
            return api.request<FactoryOrder[]>('GET', '/api/v1/supply-chain/factory-orders');
        },
        getSupplierDebtOverview() {
            return api.request<SupplierDebtOverview>('GET', '/api/v1/supply-chain/debt-overview');
        },
        getSupplierFinance(supplierId: number) {
            return api.request<SupplierFinance>('GET', `/api/v1/supply-chain/suppliers/${supplierId}/finance`);
        },
        linkSupplierPayment(supplierId: number, transactionId: number) {
            return api.request<{ linked: boolean; transaction_id: number; counterparty_id: number }>(
                'POST', `/api/v1/supply-chain/suppliers/${supplierId}/link-payment`, { transaction_id: transactionId });
        },
        unlinkSupplierPayment(supplierId: number, transactionId: number) {
            return api.request<{ unlinked: boolean; transaction_id: number }>(
                'POST', `/api/v1/supply-chain/suppliers/${supplierId}/unlink-payment`, { transaction_id: transactionId });
        },
        addSupplierIdentifier(supplierId: number, kind: 'CONTRACT' | 'ACCOUNT' | 'INN', value: string, currency?: string) {
            return api.request<SupplierFinance>(
                'POST', `/api/v1/supply-chain/suppliers/${supplierId}/identifiers`, { kind, value, currency: currency ?? null });
        },
        deleteSupplierIdentifier(supplierId: number, identifierId: number) {
            return api.request<SupplierFinance>(
                'DELETE', `/api/v1/supply-chain/suppliers/${supplierId}/identifiers/${identifierId}`);
        },
        assignPaymentMachine(supplierId: number, transactionId: number, orderNo: string | null) {
            return api.request<SupplierFinance>(
                'POST', `/api/v1/supply-chain/suppliers/${supplierId}/assign-machine`, { transaction_id: transactionId, order_no: orderNo });
        },
        assignPaymentsMachineBulk(supplierId: number, transactionIds: number[], orderNo: string | null) {
            return api.request<SupplierFinance>(
                'POST', `/api/v1/supply-chain/suppliers/${supplierId}/assign-machine-bulk`, { transaction_ids: transactionIds, order_no: orderNo });
        },
        setMachinePlan(supplierId: number, orderNo: string, remainingDueDate: string | null) {
            return api.request<SupplierFinance>(
                'POST', `/api/v1/supply-chain/suppliers/${supplierId}/machine-plan`, { order_no: orderNo, remaining_due_date: remainingDueDate });
        },
        autoDistributeMachines(supplierId: number) {
            return api.request<AutoDistributeResult>(
                'POST', `/api/v1/supply-chain/suppliers/${supplierId}/auto-distribute`);
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
        archiveFactoryOrder(id: number, archived: boolean) {
            return api.request<FactoryOrder>('PUT', `/api/v1/supply-chain/factory-orders/${id}/archive`, { is_archived: archived });
        },
        setFactoryOrderProject(id: number, supplyProjectId: number | null) {
            return api.request<FactoryOrder>('PUT', `/api/v1/supply-chain/factory-orders/${id}`, { supply_project_id: supplyProjectId });
        },
        mergeFactoryOrders(targetId: number, sourceIds: number[]) {
            return api.request<MergeOrdersResult>('POST', '/api/v1/supply-chain/factory-orders/merge', { target_id: targetId, source_ids: sourceIds });
        },

        // ─── Supply Projects (grouping) ─────────────────────────────
        getSupplyProjects() {
            return api.request<SupplyProject[]>('GET', '/api/v1/supply-chain/projects');
        },
        createSupplyProject(data: SupplyProjectCreate) {
            return api.request<SupplyProject>('POST', '/api/v1/supply-chain/projects', data);
        },
        updateSupplyProject(id: number, data: SupplyProjectUpdate) {
            return api.request<SupplyProject>('PUT', `/api/v1/supply-chain/projects/${id}`, data);
        },
        deleteSupplyProject(id: number) {
            return api.request<MessageResponse>('DELETE', `/api/v1/supply-chain/projects/${id}`);
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
        addItemsToVehicle(
            orderNo: string,
            items: {
                factory_order_item_id: number;
                qty: number;
                box_size_override?: string | null;
                pcs_per_box_override?: number | null;
                mode?: 'strict' | 'extend_plan';
            }[],
        ) {
            return api.request<{ ok: boolean; added: number }>('POST', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/items`, { items });
        },
        addUnorderedItemsToVehicle(orderNo: string, payload: AddUnorderedItemsRequest) {
            return api.request<AddUnorderedItemsResponse>('POST', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/unordered-items`, payload);
        },
        async addPostShipmentItems(
            orderNo: string,
            payload: PostShipmentItemsRequest,
        ): Promise<PostShipmentItemsResponse> {
            try {
                return await api.request<PostShipmentItemsResponse>(
                    'POST',
                    `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/post-shipment-items`,
                    payload,
                );
            } catch (e: unknown) {
                if (e instanceof Error) {
                    const parsed = parseFactoryQtyExceededDetail(e.message);
                    if (parsed) {
                        throw new FactoryQtyExceededError(parsed);
                    }
                }
                throw e;
            }
        },
        removeItemFromVehicle(orderNo: string, itemId: number) {
            return api.request<{ ok: boolean }>('DELETE', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/items/${itemId}`);
        },
        updateVehicleItemQty(orderNo: string, itemId: number, qty: number) {
            return api.request<{ ok: boolean; item_id: number }>('PATCH', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/items/${itemId}`, { qty });
        },
        async updateVehicleItem(
            orderNo: string,
            itemId: number,
            payload: VehicleItemUpdate,
        ): Promise<{ ok: true; item_id: number; extended_by: number; noop?: boolean }> {
            try {
                return await api.request<{ ok: true; item_id: number; extended_by: number; noop?: boolean }>(
                    'PATCH',
                    `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/items/${itemId}`,
                    payload,
                );
            } catch (e: unknown) {
                // client.ts converts structured 422 detail to `Error(JSON.stringify(detail))`.
                // Try to parse it back to a typed error so the UI can render the drift confirm row.
                if (e instanceof Error) {
                    const parsed = parseFactoryQtyExceededDetail(e.message);
                    if (parsed) {
                        throw new FactoryQtyExceededError(parsed);
                    }
                }
                throw e;
            }
        },
        clearAllVehicleItems(orderNo: string) {
            return api.request<{ ok: boolean; removed: number }>('DELETE', `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/items`);
        },
        bulkRemoveVehicleItems(orderNo: string, itemIds: number[]) {
            // Одним вызовом вместо N×DELETE — иначе 30/мин rate_limit_write
            // обрывает массовое удаление на середине ("Слишком много запросов").
            return api.request<{ ok: boolean; removed: number; skipped_not_found: number; errors: { item_id: number; error: string }[] }>(
                'POST',
                `/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/items/bulk-delete`,
                { item_ids: itemIds },
            );
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
            const res = await fetch(`${API_URL}/api/v1/supply-chain/vehicles/${encodeURIComponent(orderNo)}/documents/${docId}/download`, { headers });
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

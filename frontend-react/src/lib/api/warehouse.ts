/** Warehouse API methods */
import { ApiClient } from './client';
import type {
    AcceptanceCheckRequest,
    AcceptanceCheckResponse,
    AcceptanceLimitsResponse,
    SupplyAcceptanceSlotsResponse,
    AssemblyDraft,
    AssemblyDraftCommitResponse,
    AssemblyDraftRow,
    BarcodeEligibilityResponse,
    CommitSupply,
    AssemblyDraftCreate,
    AssemblyDraftUnitRef,
    AssemblyDraftUpdate,
    AssemblyFlowAnalyticsResponse,
    LinkAnomaliesResponse,
    StockDistributionResponse,
    StockDistributionHistoryResponse,
    HandedUnitItem,
    AssemblyAttempt,
    AssemblyHistoryEntry,
    AssemblyListResponse,
    AssemblyRequest,
    AssemblyRequestCreate,
    AssemblyRequestUpdate,
    AssemblyReturnPayload,
    CreatedAssemblyGroup,
    FfMismatchDetail,
    BoxMultiplicityBulkRequest,
    BoxMultiplicityBatchListResponse,
    BoxMultiplicityBatchRevertResponse,
    BoxMultiplicityBulkResponse,
    BoxMultiplicityChangesResponse,
    BoxMultiplicityPatch,
    BoxMultiplicityPerWarehousePatch,
    BoxMultiplicityResponse,
    BoxMultiplicityRow,
    BoxMultiplicitySourcesResponse,
    DefectBulkOperation,
    DefectBulkResponse,
    DefectMarkCancelResponse,
    DefectMarkOperation,
    DefectOperation,
    DeliveryTimesResponse,
    DeliveryTimesUpdate,
    FboAuditListResponse,
    FboAuditResponse,
    FboAuditRevertResponse,
    FboPartialSummary,
    FboReassignCandidatesResponse,
    FboReassignRequest,
    FboReassignResponse,
    FboReturnRequest,
    FboReturnResponse,
    FboSyncResult,
    InboundReceipt,
    CostForecastResponse,
    LogisticsAnalyticsResponse,
    LogisticsShipmentListResponse,
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
        setWarehouseCounterparty(id: number, data: { inn: string | null; name: string | null }) {
            return api.request<Warehouse>('PATCH', `/api/v1/warehouse/${id}/counterparty`, data);
        },
        deleteWarehouse(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/warehouse/${id}`); },
        reorderWarehouses(items: { id: number; sort_order: number }[]) {
            return api.request<MessageResponse>('PUT', '/api/v1/warehouse/reorder', { items });
        },

        // ─── WB Acceptance check ─────────────────────────────────────
        /** POST /warehouse/acceptance-check — live WB API check + redistribute closed warehouses. */
        checkWbAcceptance(body: AcceptanceCheckRequest, force = false) {
            const url = force
                ? '/api/v1/warehouse/acceptance-check?force=true'
                : '/api/v1/warehouse/acceptance-check';
            return api.request<AcceptanceCheckResponse>('POST', url, body);
        },
        /** GET /warehouse/acceptance-limits — сводные лимиты на сдачу (календарь дат × тип упаковки). */
        getWbAcceptanceLimits(warehouse?: string, force = false) {
            const qs = new URLSearchParams();
            if (warehouse) qs.set('warehouse', warehouse);
            if (force) qs.set('force', 'true');
            const q = qs.toString();
            return api.request<AcceptanceLimitsResponse>('GET', `/api/v1/warehouse/acceptance-limits${q ? `?${q}` : ''}`);
        },
        /** GET /warehouse/acceptance-slots — слоты сдачи по активным заявкам (поставка → календарь приёмки её склада). */
        getSupplyAcceptanceSlots(force = false) {
            const q = force ? '?force=true' : '';
            return api.request<SupplyAcceptanceSlotsResponse>('GET', `/api/v1/warehouse/acceptance-slots${q}`);
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
            return api.request<InboundReceipt>('POST', `/api/v1/warehouse/receipts/${receiptId}/accept`, actualQuantities ?? null);
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
        getTransfers(inTransitOnly = false, warehouseId?: number) {
            const qs = new URLSearchParams({ in_transit: String(inTransitOnly) });
            if (warehouseId !== undefined) qs.set('warehouse_id', String(warehouseId));
            return api.request<StockTransfer[]>('GET', `/api/v1/warehouse/transfers?${qs.toString()}`);
        },
        createTransfer(data: { from_warehouse_id: number; to_warehouse_id: number; comment?: string; is_defect?: boolean; defect_reason?: string; items: { barcode: string; quantity: number }[] }) {
            return api.request<StockTransfer>('POST', '/api/v1/warehouse/transfers', data);
        },
        sendTransfer(transferId: number) { return api.request<StockTransfer>('POST', `/api/v1/warehouse/transfers/${transferId}/send`); },
        completeTransfer(transferId: number) { return api.request<StockTransfer>('POST', `/api/v1/warehouse/transfers/${transferId}/complete`); },
        cancelTransfer(transferId: number) { return api.request<MessageResponse>('DELETE', `/api/v1/warehouse/transfers/${transferId}`); },

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
        getDefectMarkOperations(warehouseId: number) {
            return api.request<DefectMarkOperation[]>('GET', `/api/v1/warehouse/${warehouseId}/defect-mark-operations`);
        },
        getDefectMarkOperation(operationId: number) {
            return api.request<DefectMarkOperation>('GET', `/api/v1/warehouse/defect-mark-operations/${operationId}`);
        },
        cancelDefectMarkOperation(operationId: number) {
            return api.request<DefectMarkCancelResponse>('POST', `/api/v1/warehouse/defect-mark-operations/${operationId}/cancel`);
        },

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
            search?: string; status?: string; warehouse?: string;
            source_warehouse_id?: number;
            date_from?: string; date_to?: string;
            sort_by?: string; sort_order?: string; limit?: number; offset?: number;
            exclude_with_assembly?: boolean;
            without_assembly?: boolean;
            partial_only?: boolean;
            excess_only?: boolean;
            archived_view?: boolean;
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
        getFboSuppliesSummary(params?: {
            date_from?: string; date_to?: string;
            warehouse?: string; status?: string; archived_view?: boolean;
        }) {
            const query = new URLSearchParams();
            if (params) {
                Object.entries(params).forEach(([k, v]) => {
                    if (v !== undefined && v !== null && v !== '' && v !== false) query.set(k, String(v));
                });
            }
            const qs = query.toString();
            return api.request<{ total: number; accepted: number; accepted_without_assembly: number; accepted_partial: number; accepted_excess: number }>(
                'GET',
                `/api/v1/warehouse/fbo-supplies/summary${qs ? `?${qs}` : ''}`,
            );
        },
        setFboSupplyArchive(supplyId: number, isArchived: boolean | null) {
            return api.request<WbFboSupply>(
                'PATCH',
                `/api/v1/warehouse/fbo-supplies/${supplyId}/archive`,
                { is_archived: isArchived },
            );
        },
        restoreLinkedFromArchive() {
            return api.request<{ restored: number }>(
                'POST',
                '/api/v1/warehouse/fbo-supplies/archive/restore-linked',
                {},
            );
        },
        processFboExcess(supplyId: number, payload: {
            warehouse_id: number;
            items: { barcode: string; quantity: number }[];
            comment?: string;
        }) {
            return api.request<{ supply_id: number; shipment_id: number; shipment_number: string; total_qty: number }>(
                'POST',
                `/api/v1/warehouse/fbo-supplies/${supplyId}/excess`,
                payload,
            );
        },
        getFboPartialSummary(params?: {
            date_from?: string; date_to?: string;
            warehouse?: string; status?: string; archived_view?: boolean;
        }) {
            const query = new URLSearchParams();
            if (params) {
                Object.entries(params).forEach(([k, v]) => {
                    if (v !== undefined && v !== null && v !== '' && v !== false) query.set(k, String(v));
                });
            }
            const qs = query.toString();
            return api.request<FboPartialSummary>(
                'GET',
                `/api/v1/warehouse/fbo-supplies/partial-summary${qs ? `?${qs}` : ''}`,
            );
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
        createFboReturn(supplyId: number, payload: FboReturnRequest) {
            return api.request<FboReturnResponse>('POST', `/api/v1/warehouse/fbo-supplies/${supplyId}/return`, payload);
        },
        getFboReassignCandidates(supplyId: number) {
            return api.request<FboReassignCandidatesResponse>(
                'GET',
                `/api/v1/warehouse/fbo-supplies/${supplyId}/reassign/candidates`,
            );
        },
        reassignFboSupply(supplyId: number, payload: FboReassignRequest) {
            return api.request<FboReassignResponse>(
                'POST',
                `/api/v1/warehouse/fbo-supplies/${supplyId}/reassign`,
                payload,
            );
        },
        getFboSupplyAudit(supplyId: number) {
            return api.request<FboAuditResponse>(
                'GET',
                `/api/v1/warehouse/fbo-supplies/${supplyId}/audit`,
            );
        },
        getFboAuditList(params?: { action?: string; supply_wb_id?: string; limit?: number; offset?: number }) {
            const q = new URLSearchParams();
            if (params?.action) q.set('action', params.action);
            if (params?.supply_wb_id) q.set('supply_wb_id', params.supply_wb_id);
            if (params?.limit !== undefined) q.set('limit', String(params.limit));
            if (params?.offset !== undefined) q.set('offset', String(params.offset));
            const qs = q.toString();
            return api.request<FboAuditListResponse>(
                'GET',
                `/api/v1/warehouse/fbo-supplies/audit${qs ? '?' + qs : ''}`,
            );
        },
        revertFboAudit(auditId: number) {
            return api.request<FboAuditRevertResponse>(
                'POST',
                `/api/v1/warehouse/fbo-supplies/audit/${auditId}/revert`,
                {},
            );
        },

        // ─── WB Warehouse Names ─────────────────────────────────────────
        getWbWarehouseNames() {
            return api.request<string[]>('GET', '/api/v1/warehouse/assembly/wb-warehouses');
        },

        // ─── Assembly Requests ──────────────────────────────────────────
        getAssemblyRequests(params?: {
            warehouse_id?: number; counterparty_id?: number; draft_id?: number; status?: string; search?: string;
            date_from?: string; date_to?: string; brand?: string;
            ff_link?: 'none' | 'linked';
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
        /** Группы созданных заявок по черновику (IN_PROGRESS) — «Предпросмотр созданных». */
        getCreatedAssemblyGroups() {
            return api.request<CreatedAssemblyGroup[]>('GET', '/api/v1/warehouse/assembly/created-groups');
        },
        getAssemblyRequest(id: number) {
            return api.request<AssemblyRequest>('GET', `/api/v1/warehouse/assembly/${id}`);
        },
        getAssemblyFfMismatch(id: number) {
            return api.request<FfMismatchDetail>('GET', `/api/v1/warehouse/assembly/${id}/ff-mismatch`);
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
            carrier_inn?: string | null;
            carrier_name?: string | null;
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
        returnAssembly(id: number, payload?: AssemblyReturnPayload) {
            return api.request<AssemblyRequest>('POST', `/api/v1/warehouse/assembly/${id}/return`, payload ?? {});
        },
        reopenAssembly(id: number) {
            return api.request<AssemblyRequest>('POST', `/api/v1/warehouse/assembly/${id}/reopen`);
        },
        closeAssembly(id: number) {
            return api.request<AssemblyRequest>('POST', `/api/v1/warehouse/assembly/${id}/close`);
        },
        getAssemblyAttempts(id: number) {
            return api.request<AssemblyAttempt[]>('GET', `/api/v1/warehouse/assembly/${id}/attempts`);
        },
        deleteAssembly(id: number) {
            return api.request<void>('DELETE', `/api/v1/warehouse/assembly/${id}`);
        },
        assignVehicleBulk(data: {
            vehicle_info: string;
            vehicle_brand: string;
            driver_phone: string;
            carrier_inn?: string | null;
            carrier_name?: string | null;
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
        getShipmentAnalytics(params?: { date_from?: string; date_to?: string; warehouse_ids?: string; brands?: string; carrier_id?: number }) {
            const query = new URLSearchParams();
            if (params) {
                Object.entries(params).forEach(([k, v]) => {
                    if (v !== undefined && v !== null && v !== '') query.set(k, String(v));
                });
            }
            const qs = query.toString();
            return api.request<LogisticsAnalyticsResponse>('GET', `/api/v1/warehouse/assembly/shipments/analytics${qs ? `?${qs}` : ''}`);
        },
        /** Построчная история отправок за период (весь набор — клиентская сортировка по всему периоду). */
        getShipmentsList(params?: { date_from?: string; date_to?: string; warehouse_ids?: string; brands?: string; carrier_id?: number; dest_warehouse?: string }) {
            const query = new URLSearchParams();
            if (params) {
                Object.entries(params).forEach(([k, v]) => {
                    if (v !== undefined && v !== null && v !== '') query.set(k, String(v));
                });
            }
            const qs = query.toString();
            return api.request<LogisticsShipmentListResponse>('GET', `/api/v1/warehouse/assembly/shipments/list${qs ? `?${qs}` : ''}`);
        },
        /** Прогнозная модель ₽/паллета по истории отгрузок (для неназначенных заявок). */
        getCostForecast(lookbackDays?: number) {
            const qs = lookbackDays ? `?lookback_days=${lookbackDays}` : '';
            return api.request<CostForecastResponse>('GET', `/api/v1/warehouse/assembly/cost-forecast${qs}`);
        },
        /** Distinct «города сдачи» по заявкам (поставка → manual) — фильтр аналитики. */
        getAssemblyWbWarehouses() {
            return api.request<string[]>('GET', '/api/v1/warehouse/assembly/flow-analytics/wb-warehouses');
        },
        /** Анализ потока сборки: длительности этапов, переходы, аномалии, дневная динамика. */
        getAssemblyFlowAnalytics(params?: {
            date_from?: string; date_to?: string; warehouse_ids?: string; categories?: string;
            wb_warehouses?: string;
            assembly_threshold_days?: number; ship_threshold_days?: number; delivery_threshold_days?: number;
        }) {
            const query = new URLSearchParams();
            if (params) {
                Object.entries(params).forEach(([k, v]) => {
                    if (v !== undefined && v !== null && v !== '') query.set(k, String(v));
                });
            }
            const qs = query.toString();
            return api.request<AssemblyFlowAnalyticsResponse>('GET', `/api/v1/warehouse/assembly/flow-analytics${qs ? `?${qs}` : ''}`);
        },
        /** Вкладка «Связи и расхождения»: расхождение наполнения, несвязанные заявки, аномалии FBO. */
        getAssemblyLinkAnomalies(params?: { warehouse_ids?: string }) {
            const query = new URLSearchParams();
            if (params) {
                Object.entries(params).forEach(([k, v]) => {
                    if (v !== undefined && v !== null && v !== '') query.set(k, String(v));
                });
            }
            const qs = query.toString();
            return api.request<LinkAnomaliesResponse>('GET', `/api/v1/warehouse/assembly/flow-analytics/link-anomalies${qs ? `?${qs}` : ''}`);
        },
        /** Вкладка «Распределение остатков»: «где сейчас товар» (склад ФФ / в сборке / готово / в пути). */
        getAssemblyStockDistribution(params?: { warehouse_ids?: string; product_status?: string }) {
            const query = new URLSearchParams();
            if (params) {
                Object.entries(params).forEach(([k, v]) => {
                    if (v !== undefined && v !== null && v !== '') query.set(k, String(v));
                });
            }
            const qs = query.toString();
            return api.request<StockDistributionResponse>('GET', `/api/v1/warehouse/assembly/flow-analytics/stock-distribution${qs ? `?${qs}` : ''}`);
        },
        /** История распределения остатков по дням (накопительные снимки). */
        getAssemblyStockDistributionHistory(params?: { date_from?: string; date_to?: string; warehouse_ids?: string; product_status?: string }) {
            const query = new URLSearchParams();
            if (params) {
                Object.entries(params).forEach(([k, v]) => {
                    if (v !== undefined && v !== null && v !== '') query.set(k, String(v));
                });
            }
            const qs = query.toString();
            return api.request<StockDistributionHistoryResponse>('GET', `/api/v1/warehouse/assembly/flow-analytics/stock-distribution/history${qs ? `?${qs}` : ''}`);
        },

        // ─── Assembly Drafts ────────────────────────────────────────────
        listAssemblyDrafts() {
            return api.request<AssemblyDraft[]>('GET', '/api/v1/assembly/drafts');
        },
        getAssemblyDraft(id: number) {
            return api.request<AssemblyDraft>('GET', `/api/v1/assembly/drafts/${id}`);
        },
        createAssemblyDraft(payload: AssemblyDraftCreate) {
            return api.request<AssemblyDraft>('POST', '/api/v1/assembly/drafts', payload);
        },
        updateAssemblyDraft(id: number, payload: AssemblyDraftUpdate) {
            return api.request<AssemblyDraft>('PUT', `/api/v1/assembly/drafts/${id}`, payload);
        },
        deleteAssemblyDraft(id: number) {
            return api.request<void>('DELETE', `/api/v1/assembly/drafts/${id}`);
        },
        /** @param palletCounts map `"{ffId}::{wbName}::{pkg}" → паллет` — авто-проставляется
         *  в каждую создаваемую заявку (иначе бэкенд берёт плоский pallets_count черновика).
         *  @param supplies явные отгрузки ФФ→склад (режим «только целые паллеты») — заявки
         *  создаются ровно из них, минуя pro-rata. Иначе разбивка считается на бэке. */
        commitAssemblyDraft(
            id: number,
            packageType?: string,
            palletCounts?: Record<string, number>,
            supplies?: CommitSupply[],
        ) {
            const qs = new URLSearchParams();
            if (packageType) qs.set('package_type', packageType);
            const q = qs.toString();
            const hasPallets = palletCounts && Object.keys(palletCounts).length > 0;
            const hasSupplies = supplies && supplies.length > 0;
            const body = hasPallets || hasSupplies
                ? {
                    ...(hasPallets ? { pallet_counts: palletCounts } : {}),
                    ...(hasSupplies ? { supplies } : {}),
                }
                : undefined;
            return api.request<AssemblyDraftCommitResponse>(
                'POST',
                `/api/v1/assembly/drafts/${id}/commit${q ? `?${q}` : ''}`,
                body,
            );
        },
        /** «Передать на ФФ» — заморозить заявку-юнит (вырезать в handed_units). */
        handOffDraftUnit(id: number, unit: AssemblyDraftUnitRef) {
            return api.request<AssemblyDraft>('POST', `/api/v1/assembly/drafts/${id}/units/hand-off`, unit);
        },
        /** «Вернуть в черновик» — вернуть позиции замороженного юнита в rows. */
        revertDraftUnit(id: number, unit: AssemblyDraftUnitRef) {
            return api.request<AssemblyDraft>('POST', `/api/v1/assembly/drafts/${id}/units/revert`, unit);
        },
        /** «В сборку» — создать AssemblyRequest из замороженного юнита. */
        commitDraftUnit(id: number, unit: AssemblyDraftUnitRef) {
            return api.request<AssemblyDraftCommitResponse>('POST', `/api/v1/assembly/drafts/${id}/units/commit`, unit);
        },
        /** Заменить наполнение заявки-юнита (правка черновика → фиксация). */
        setDraftUnitItems(id: number, unit: AssemblyDraftUnitRef, items: HandedUnitItem[]) {
            return api.request<AssemblyDraft>('POST', `/api/v1/assembly/drafts/${id}/units/items`, { ...unit, items });
        },
        /** Удалить заявку-юнит целиком (товар остаётся на ФФ). */
        deleteDraftUnit(id: number, unit: AssemblyDraftUnitRef) {
            return api.request<AssemblyDraft>('POST', `/api/v1/assembly/drafts/${id}/units/delete`, unit);
        },
        /** «Сменить склад WB» — перенести заявку-юнит этого ФФ на другой WB-склад
         *  (сливается с черновиком-получателем по баркоду). */
        moveDraftUnit(id: number, unit: AssemblyDraftUnitRef, newTargetWbName: string) {
            return api.request<AssemblyDraft>('POST', `/api/v1/assembly/drafts/${id}/units/move`, { ...unit, new_target_wb_name: newTargetWbName });
        },
        /** Объединить N черновиков в один: суммирует (nm_id, pkg)-строки, union складов.
         *  Возвращает survivor (черновик с наибольшим числом строк). */
        mergeAssemblyDrafts(draftIds: number[]) {
            return api.request<AssemblyDraft>('POST', '/api/v1/assembly/drafts/merge', { draft_ids: draftIds });
        },
        /** Дописать строки в черновик («добавить из потребности» / по баркоду):
         *  мёржит по (nm_id, pkg), union складов; возвращает обновлённый черновик. */
        addAssemblyDraftRows(draftId: number, rows: AssemblyDraftRow[]) {
            return api.request<AssemblyDraft>('POST', `/api/v1/assembly/drafts/${draftId}/rows`, { rows });
        },
        /** Единственный «текущий» черновик проекта (синглтон): нет — создаёт пустой,
         *  несколько — объединяет в один. Единая страница «Сборка» зовёт на входе. */
        getOrCreateCurrentDraft() {
            return api.request<AssemblyDraft>('POST', '/api/v1/assembly/drafts/current');
        },
        /** Проверка приёмки WB по баркодам: типы упаковки + лимиты + остаток на ФФ. */
        getBarcodeEligibility(barcodes: string[]) {
            return api.request<BarcodeEligibilityResponse>('POST', '/api/v1/warehouse/barcode-eligibility', { barcodes });
        },

        // ─── Box-multiplicity (кратность коробки) ────────────────────────
        getBoxMultiplicity() {
            return api.request<BoxMultiplicityResponse>('GET', '/api/v1/warehouse/box-multiplicity');
        },
        /** Partial update — pass only fields you want to change. */
        patchBoxMultiplicity(nmId: number, patch: BoxMultiplicityPatch) {
            return api.request<BoxMultiplicityRow>(
                'PATCH',
                `/api/v1/warehouse/box-multiplicity/${nmId}`,
                patch,
            );
        },
        /** Convenience: set/clear manual ppb, leaves use-flag untouched. */
        setBoxMultiplicity(nmId: number, boxQtyOverride: number | null) {
            return api.request<BoxMultiplicityRow>(
                'PATCH',
                `/api/v1/warehouse/box-multiplicity/${nmId}`,
                { box_qty_override: boxQtyOverride } satisfies BoxMultiplicityPatch,
            );
        },
        /** Bulk paste-update by barcode (partial — только переданные поля). */
        bulkBoxMultiplicity(items: BoxMultiplicityBulkRequest['items']) {
            return api.request<BoxMultiplicityBulkResponse>(
                'POST',
                '/api/v1/warehouse/box-multiplicity/bulk',
                { items } satisfies BoxMultiplicityBulkRequest,
            );
        },
        /** Per-ФФ override кратности/флага. Может вернуть 409 если ФФ машинно-заблокирован. */
        patchPerWarehouseBoxMultiplicity(
            barcode: string, warehouseId: number, patch: BoxMultiplicityPerWarehousePatch,
        ) {
            return api.request<BoxMultiplicityRow>(
                'PATCH',
                `/api/v1/warehouse/box-multiplicity/per-warehouse/${encodeURIComponent(barcode)}/${warehouseId}`,
                patch,
            );
        },
        /** Drill-down: вся история снабжения SKU — машины + заказы на фабрику (read-only). */
        getBoxMultiplicitySources(barcode: string) {
            return api.request<BoxMultiplicitySourcesResponse>(
                'GET',
                `/api/v1/warehouse/box-multiplicity/sources/${encodeURIComponent(barcode)}`,
            );
        },
        /** Drill-down: история изменений кратности/размера SKU (SKU- и per-ФФ-уровень). */
        getBoxMultiplicityChanges(barcode: string) {
            return api.request<BoxMultiplicityChangesResponse>(
                'GET',
                `/api/v1/warehouse/box-multiplicity/changes/${encodeURIComponent(barcode)}`,
            );
        },
        /** Откат одного изменения. Возвращает обновлённую строку SKU; 409 если откат заблокирован машиной. */
        revertBoxMultiplicityChange(changeId: number) {
            return api.request<BoxMultiplicityRow>(
                'POST',
                `/api/v1/warehouse/box-multiplicity/changes/${changeId}/revert`,
                {},
            );
        },
        /** Список последних bulk-вставок проекта (для журнала истории). */
        listBoxMultiplicityBatches(limit = 50) {
            const params = new URLSearchParams({ limit: String(limit) });
            return api.request<BoxMultiplicityBatchListResponse>(
                'GET',
                `/api/v1/warehouse/box-multiplicity/changes/batches?${params}`,
            );
        },
        /** Откат всей bulk-операции (вставки) по batch_id. */
        revertBoxMultiplicityBatch(batchId: string) {
            return api.request<BoxMultiplicityBatchRevertResponse>(
                'POST',
                `/api/v1/warehouse/box-multiplicity/changes/batch/${encodeURIComponent(batchId)}/revert`,
                {},
            );
        },

        // ─── Gazelka ──────────────────────────────────────────────────────────
        getGazelkaConfig() {
            return api.request<import('@/types/api').GazelkaConfig>('GET', '/api/v1/gazelka/config');
        },
        getGazelkaDraft(assemblyId: number) {
            return api.request<import('@/types/api').GazelkaDraft>('GET', `/api/v1/gazelka/assembly/${assemblyId}/draft`);
        },
        sendToGazelka(assemblyId: number, body: import('@/types/api').GazelkaSendRequest) {
            return api.request<import('@/types/api').GazelkaSendResult>('POST', `/api/v1/gazelka/assembly/${assemblyId}/send`, body);
        },
        getGazelkaPlanned() {
            return api.request<import('@/types/api').GazelkaOrderList>('GET', '/api/v1/gazelka/planned');
        },
        getGazelkaActive() {
            return api.request<import('@/types/api').GazelkaOrderList>('GET', '/api/v1/gazelka/active');
        },
        getGazelkaEditDraft(planId: number) {
            return api.request<import('@/types/api').GazelkaEditDraft>('GET', `/api/v1/gazelka/order/${planId}/edit`);
        },
        saveGazelkaEdit(planId: number, body: import('@/types/api').GazelkaSendRequest) {
            return api.request<import('@/types/api').GazelkaSendResult>('POST', `/api/v1/gazelka/order/${planId}/edit`, body);
        },
        /** ТТН требует авторизацию — открываем через авторизованный fetch, затем blob-URL. */
        async openGazelkaTtn(planId: number): Promise<void> {
            const token = api.getToken();
            const res = await fetch(`/api/v1/gazelka/order/${planId}/ttn`, {
                headers: token ? { Authorization: `Bearer ${token}` } : {},
            });
            if (!res.ok) {
                const text = await res.text().catch(() => '');
                throw new Error(`Ошибка ТТН: ${res.status}${text ? ' — ' + text.slice(0, 200) : ''}`);
            }
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            window.open(url, '_blank');
        },
        getGazelkaMatchCandidates(search?: string) {
            const qs = new URLSearchParams();
            if (search) qs.set('search', search);
            const tail = qs.toString();
            return api.request<import('@/types/api').GazelkaMatchCandidate[]>(
                'GET',
                `/api/v1/gazelka/match-candidates${tail ? `?${tail}` : ''}`,
            );
        },
        matchGazelkaOrder(planId: number, assemblyId: number) {
            return api.request<import('@/types/api').GazelkaMatchResult>(
                'POST',
                `/api/v1/gazelka/order/${planId}/match`,
                { assembly_id: assemblyId },
            );
        },
        unmatchGazelkaOrder(planId: number) {
            return api.request<import('@/types/api').GazelkaUnmatchResult>(
                'DELETE',
                `/api/v1/gazelka/order/${planId}/match`,
            );
        },
    };
}

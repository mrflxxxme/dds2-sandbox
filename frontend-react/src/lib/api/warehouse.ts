/** Warehouse API methods */
import { ApiClient } from './client';
import type {
    AcceptanceCheckRequest,
    InTransitResponse,
    AcceptanceCheckResponse,
    AcceptanceLimitsResponse,
    SupplyAcceptanceSlotsResponse,
    AssemblyDraft,
    AssemblyDraftCommitResponse,
    AssemblyDraftRow,
    BarcodeEligibilityResponse,
    CommitSupply,
    AssemblyDraftClearResponse,
    DraftCategoryHistoryResponse,
    DraftsReservedResponse,
    ForecastResponse,
    AssemblyDraftCreate,
    AssemblyDraftUnitRef,
    AssemblyDraftUpdate,
    DraftHistoryResponse,
    DraftEventRevertResponse,
    AssemblyFlowAnalyticsResponse,
    LinkAnomaliesResponse,
    StockDistributionResponse,
    StockDistributionHistoryResponse,
    HandedUnitItem,
    AssemblyAttempt,
    AssemblyApplyWeightBulkResult,
    AssemblyBulkDeleteResult,
    AssemblyBulkStatus,
    AssemblyBulkStatusResult,
    AssemblyHistoryEntry,
    AssemblyPickupCostHistoryEntry,
    AssemblyListResponse,
    AssemblyRequest,
    AssemblyRequestCreate,
    AssemblyRequestUpdate,
    AssemblyReturnPayload,
    CreatedAssemblyGroup,
    FfMismatchDetail,
    PreDistVehicle,
    PreDistVehiclePool,
    PreDistributionCreate,
    PreDistributionCreateResult,
    PrebookingCreate,
    PrebookingCreateResult,
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
        getWarehouseStock(warehouseId: number, excludeAssemblyId?: number) {
            // excludeAssemblyId: экран редактирования сборки — её собственный резерв не вычитается из available
            const params = new URLSearchParams();
            if (excludeAssemblyId != null) params.set('exclude_assembly_id', String(excludeAssemblyId));
            const qs = params.toString();
            return api.request<WarehouseStockRow[]>('GET', `/api/v1/warehouse/${warehouseId}/stock${qs ? `?${qs}` : ''}`);
        },
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
            /** с exclude_with_assembly: исключать только поставки, занятые сборкой этого склада-источника (совместная поставка) */
            exclude_assembly_warehouse_id?: number;
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
            /** active — скрыть Принято ВБ/Закрыта/Отменена; archived — только их; all — все */
            view?: 'active' | 'archived' | 'all';
            joint_only?: boolean;
            /** Происхождение: pre_dist — из машины (🚚), prebooking — предзаявки (🅿️), plain — обычные */
            source?: 'pre_dist' | 'prebooking' | 'plain';
            /** Только заявки конкретной машины (CostOrder.id) */
            source_vehicle_id?: number;
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
        /** Машины с заявками сборки — опции фильтра «Источник» (свежие сверху). */
        getAssemblySourceVehicles() {
            return api.request<import('@/types/api').SourceVehicleOption[]>('GET', '/api/v1/warehouse/assembly/source-vehicles');
        },
        createAssemblyRequest(data: AssemblyRequestCreate) {
            return api.request<AssemblyRequest>('POST', '/api/v1/warehouse/assembly', data);
        },
        /** Объединить ≥2 СОЗДАННЫХ сборки одного склада·направления·упаковки (статус «В сборке»)
         *  в одну: позиции суммируются, ФФ-связи переносятся. Возвращает survivor. */
        mergeAssemblyRequests(requestIds: number[]) {
            return api.request<AssemblyRequest>('POST', '/api/v1/warehouse/assembly/merge', { request_ids: requestIds });
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
        /** Сохранить ручную раскладку коробов по паллетам; pallets=null → сброс к «авто». */
        updatePalletManifest(id: number, pallets: import('@/types/api').PalletBox[] | null) {
            return api.request<AssemblyRequest>(
                'PATCH',
                `/api/v1/warehouse/assembly/${id}/pallet-manifest`,
                { pallets } satisfies import('@/types/api').PalletManifestUpdate,
            );
        },
        /** Проставить расчётный вес товаров в ручной вес паллеты (÷ кол-во паллет). */
        applyGoodsWeight(id: number) {
            return api.request<AssemblyRequest>('POST', `/api/v1/warehouse/assembly/${id}/apply-goods-weight`);
        },
        /** Массовое авто-заполнение «Общего веса» (нетто + тара коробов) ОДНИМ запросом. Невалидные пропускаются с причиной. */
        applyGoodsWeightBulk(ids: number[]) {
            return api.request<AssemblyApplyWeightBulkResult>('POST', '/api/v1/warehouse/assembly/apply-goods-weight-bulk', { ids });
        },
        /** Скачать раскладку по паллетам (Excel): format=internal (кладовщик) | wb (загрузка в WB).
         *  Через requestBlob — авторизация + X-Project-Id + рефреш (сырой <a href> не несёт JWT). */
        async downloadPalletLayout(id: number, format: 'internal' | 'wb', filename: string): Promise<void> {
            const blob = await api.requestBlob(`/api/v1/warehouse/assembly/${id}/pallet-layout.xlsx?format=${format}`);
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        },
        /** Решение по предложенной ФФ правке состава: применить («approve») или отклонить («reject»). */
        assemblyFfReview(id: number, action: 'approve' | 'reject') {
            return api.request<AssemblyRequest>('POST', `/api/v1/warehouse/assembly/${id}/ff-review`, { action });
        },
        startAssembly(id: number) {
            return api.request<AssemblyRequest>('POST', `/api/v1/warehouse/assembly/${id}/start`);
        },
        markAssemblyReady(id: number) {
            return api.request<AssemblyRequest>('POST', `/api/v1/warehouse/assembly/${id}/ready`);
        },
        assignVehicle(id: number, data: {
            /** госномер машины (поле «Госномер») */
            vehicle_info: string;
            vehicle_brand: string;
            driver_phone: string;
            driver_first_name?: string | null;
            driver_last_name?: string | null;
            pickup_date: string;
            pickup_time_slot: string;
            pickup_cost: number;
            delivery_date: string;
            carrier_inn?: string | null;
            carrier_name?: string | null;
            /** Логистику оказывает склад забора: перевозчик = контрагент склада-источника. */
            logistics_by_warehouse?: boolean;
        }) {
            return api.request<AssemblyRequest>('POST', `/api/v1/warehouse/assembly/${id}/assign-vehicle`, data);
        },
        shipAssembly(id: number) {
            return api.request<AssemblyRequest>('POST', `/api/v1/warehouse/assembly/${id}/ship`);
        },
        // Отгрузка всей совместной FBO-поставки разом (все назначенные сборки поставки).
        shipJoint(id: number) {
            return api.request<AssemblyRequest>('POST', `/api/v1/warehouse/assembly/${id}/ship-joint`);
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
        /** Массовое удаление заявок, ещё не отгруженных на WB. Отгруженные пропускаются. */
        deleteAssemblyBulk(ids: number[]) {
            return api.request<AssemblyBulkDeleteResult>('POST', '/api/v1/warehouse/assembly/delete-bulk', { ids });
        },
        /** Массовый перевод заявок в статус (В сборке / Готово) ОДНИМ запросом —
         *  не съедает write-лимит, как поштучные смены. Невалидные пропускаются с причиной. */
        setAssemblyStatusBulk(ids: number[], status: AssemblyBulkStatus) {
            return api.request<AssemblyBulkStatusResult>('POST', '/api/v1/warehouse/assembly/status-bulk', { ids, status });
        },
        // ─── Предраспределение машины в пути ────────────────────────────────
        /** Машины (CostOrder CUSTOMS/DISPATCHED) — кандидаты на предраспределение. */
        getPreDistVehicles() {
            return api.request<PreDistVehicle[]>('GET', '/api/v1/warehouse/assembly/pre-distribution/vehicles');
        },
        /** Пул товаров машины (gross / уже разнесено / доступно) для раздачи по WB-складам. */
        getPreDistVehiclePool(vehicleId: number) {
            return api.request<PreDistVehiclePool>('GET', `/api/v1/warehouse/assembly/pre-distribution/vehicles/${vehicleId}/pool`);
        },
        /** Создать заявки-предраспределения (status PRE_DISTRIBUTED, без приёмки). */
        createPreDistribution(payload: PreDistributionCreate) {
            return api.request<PreDistributionCreateResult>('POST', '/api/v1/warehouse/assembly/pre-distribution', payload);
        },
        /** Ручной перевод предраспределённых заявок машины PRE_DISTRIBUTED→IN_PROGRESS. */
        advancePreDistribution(vehicleId: number) {
            return api.request<{ advanced: number }>('POST', `/api/v1/warehouse/assembly/pre-distribution/vehicles/${vehicleId}/advance`);
        },
        /** Создать заявки-предзаявки на моно (is_prebooking=True) из строк предброни. */
        createPrebooking(payload: PrebookingCreate) {
            return api.request<PrebookingCreateResult>('POST', '/api/v1/warehouse/assembly/prebooking', payload);
        },
        assignVehicleBulk(data: {
            /** госномер машины (поле «Госномер») */
            vehicle_info: string;
            vehicle_brand: string;
            driver_phone: string;
            driver_first_name?: string | null;
            driver_last_name?: string | null;
            carrier_inn?: string | null;
            carrier_name?: string | null;
            /** Логистику оказывает склад забора (резолвится по каждой заявке отдельно). */
            logistics_by_warehouse?: boolean;
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
        /** История изменений стоимости перевозки заявки (старая→новая + автор, ASM-785). */
        getAssemblyPickupCostHistory(id: number) {
            return api.request<AssemblyPickupCostHistoryEntry[]>('GET', `/api/v1/warehouse/assembly/${id}/pickup-cost-history`);
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
        /** Резерв стока другими черновиками: barcode → {ff_id → qty} (rows + prebook +
         *  handed_units всех не-удалённых черновиков, кроме exclude). Вычитается из
         *  доступного ФФ, чтобы параллельные черновики не планировали товар дважды. */
        getDraftsReserved(excludeDraftId?: number) {
            const q = new URLSearchParams();
            if (excludeDraftId != null) q.set('exclude_draft_id', String(excludeDraftId));
            const qs = q.toString();
            return api.request<DraftsReservedResponse>('GET', `/api/v1/assembly/drafts/reserved${qs ? `?${qs}` : ''}`);
        },
        /** Что уже едет/зарезервировано активными заявками по SKU (вкл. PRE_DISTRIBUTED) — reconcile черновика. */
        getAssemblyInTransit(nmIds: number[]) {
            const q = new URLSearchParams();
            q.set('nm_ids', nmIds.join(','));
            return api.request<InTransitResponse>('GET', `/api/v1/warehouse/assembly/in-transit?${q.toString()}`);
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
        /** «Очистить черновик»: сброс наполнения на сервере; для основного черновика
         *  дополнительно удаляет категорийные черновики проекта (кроме переданных
         *  на ФФ — их имена вернутся в kept_scoped). */
        clearAssemblyDraft(id: number) {
            return api.request<AssemblyDraftClearResponse>('POST', `/api/v1/assembly/drafts/${id}/clear`);
        },
        /** Почасовая история наполнения черновиков по категориям (вкладка «Динамика
         *  черновика» на «Анализ сборки»): точки старые → новые. */
        getDraftCategoryHistory(days: number) {
            return api.request<DraftCategoryHistoryResponse>('GET', `/api/v1/assembly/drafts/category-history?days=${days}`);
        },
        /** Ручной срез текущего часа (не ждать почасовую джобу). Идемпотентен. */
        snapshotDraftCategoryHistory() {
            return api.request<DraftCategoryHistoryResponse>('POST', '/api/v1/assembly/drafts/category-history/snapshot');
        },
        /** История изменений черновика (события дозабора/раскладки/создания заявок), новейшие первыми. */
        getDraftHistory(draftId: number) {
            return api.request<DraftHistoryResponse>('GET', `/api/v1/assembly/drafts/${draftId}/history`);
        },
        /** Откат одного события истории. 409 если откат недоступен (detail — причина). */
        revertDraftEvent(draftId: number, eventId: number) {
            return api.request<DraftEventRevertResponse>('POST', `/api/v1/assembly/drafts/${draftId}/history/${eventId}/revert`, {});
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
            sourceFfId?: number,
        ) {
            const qs = new URLSearchParams();
            if (packageType) qs.set('package_type', packageType);
            if (sourceFfId != null) qs.set('source_ff_id', String(sourceFfId));
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
        /** Прогноз загрузки WB-складов с учётом черновика: текущий остаток + входящая
         *  − продажи за lead-time → дни покрытия/светофор + индекс локализации до/после. */
        getDraftForecast(draftId: number) {
            return api.request<ForecastResponse>('GET', `/api/v1/assembly/drafts/${draftId}/forecast`);
        },
        /** Убрать SKU (неликвид) из черновика — удаляет строки + чистит handed-юниты. */
        removeAssemblyDraftRows(draftId: number, nmIds: number[]) {
            return api.request<AssemblyDraft>('POST', `/api/v1/assembly/drafts/${draftId}/remove-rows`, { nm_ids: nmIds });
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
        /** ТТН требует авторизацию + проектный контекст — через requestBlob (правильный base URL,
         *  Authorization, X-Project-Id, рефреш). Сырой fetch падал на проде «Failed to fetch». */
        async openGazelkaTtn(planId: number): Promise<void> {
            const blob = await api.requestBlob(`/api/v1/gazelka/order/${planId}/ttn`);
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

        // ─── Migfull-portal (ФФ «Натали») ──────────────────────────────────────
        migfullPortalConfig() {
            return api.request<import('@/types/api').MigfullPortalConfig>('GET', '/api/v1/migfull-portal/config');
        },
        migfullPortalDraft(assemblyId: number) {
            return api.request<import('@/types/api').MigfullDraftResponse>(
                'GET',
                `/api/v1/migfull-portal/assembly/${assemblyId}/draft`,
            );
        },
        /**
         * Отправка заявки в портал ФФ «Натали».
         * Повторная отправка без force_resend → backend отвечает HTTP 409. request()
         * схлопывает статус в текст ошибки, поэтому здесь тегируем 409 в .code='conflict',
         * чтобы модалка показала подтверждение и переслала с force_resend=true.
         */
        async migfullPortalSend(assemblyId: number, body: import('@/types/api').MigfullSendRequest) {
            try {
                return await api.request<import('@/types/api').MigfullSendResult>(
                    'POST',
                    `/api/v1/migfull-portal/assembly/${assemblyId}/send`,
                    body,
                );
            } catch (e) {
                const msg = e instanceof Error ? e.message : '';
                // Бэк на повторную отправку без force_resend отдаёт 409 с понятным detail.
                if (!body.force_resend && /уже\s+(отправ|созда)|already|409|конфликт/i.test(msg)) {
                    const err = new Error(msg || 'Заявка уже создавалась') as Error & { code?: string };
                    err.code = 'conflict';
                    throw err;
                }
                throw e;
            }
        },

        // ─── WB portal supply (занос заявки в кабинет WB) ────────────────
        wbSessionStatus() {
            return api.request<import('@/types/api').WbPortalStatus>('GET', '/api/v1/warehouse/assembly/wb-session/status');
        },
        wbSessionSet(authorizev3: string) {
            return api.request<import('@/types/api').WbPortalStatus>('POST', '/api/v1/warehouse/assembly/wb-session', { authorizev3 });
        },
        wbSupplyGetState(assemblyId: number) {
            return api.request<import('@/types/api').WbSupplyState>('GET', `/api/v1/warehouse/assembly/${assemblyId}/wb`);
        },
        wbSupplyCreatePreorder(assemblyId: number, packageType?: import('@/types/api').PackageType) {
            return api.request<import('@/types/api').WbSupplyState>('POST', `/api/v1/warehouse/assembly/${assemblyId}/wb/preorder`, { package_type: packageType ?? null });
        },
        wbSupplyPushGoods(assemblyId: number) {
            return api.request<import('@/types/api').WbSupplyState>('POST', `/api/v1/warehouse/assembly/${assemblyId}/wb/goods/push`);
        },
        wbSupplySyncSupply(assemblyId: number) {
            return api.request<import('@/types/api').WbSupplyState>('POST', `/api/v1/warehouse/assembly/${assemblyId}/wb/sync-supply`);
        },
        wbSupplySaveBoxes(assemblyId: number, body: import('@/types/api').WbBoxesUpdate) {
            return api.request<import('@/types/api').WbSupplyState>('PUT', `/api/v1/warehouse/assembly/${assemblyId}/wb/boxes`, body);
        },
        wbSupplyPushBoxes(assemblyId: number) {
            return api.request<import('@/types/api').WbSupplyState>('POST', `/api/v1/warehouse/assembly/${assemblyId}/wb/boxes/push`);
        },
        wbSupplySavePass(assemblyId: number, body: import('@/types/api').WbPassUpdate) {
            return api.request<import('@/types/api').WbSupplyState>('PUT', `/api/v1/warehouse/assembly/${assemblyId}/wb/pass`, body);
        },
        wbSupplyPushPass(assemblyId: number) {
            return api.request<import('@/types/api').WbSupplyState>('POST', `/api/v1/warehouse/assembly/${assemblyId}/wb/pass/push`);
        },
        wbSupplyDrivers(assemblyId: number) {
            return api.request<import('@/types/api').WbDriver[]>('GET', `/api/v1/warehouse/assembly/${assemblyId}/wb/drivers`);
        },
        // F1: bulk-синк живого WB-состояния всех заявок проекта (одним заходом в кабинет).
        wbSupplySyncAllStates() {
            return api.request<import('@/types/api').WbBulkSyncResult>('POST', '/api/v1/warehouse/assembly/wb/sync-states');
        },
        /** Фоновый батч-занос преордеров WB: по одной заявке с паузой ~10с (кабинет
         *  не любит частые заносы). Батч уже идёт → 409, тегируем `.code='conflict'`
         *  (как migfullPortalSend) — фронт подключается к статусу, а не матчит текст. */
        async wbSupplyBulkPreorderStart(assemblyIds: number[]) {
            try {
                return await api.request<import('@/types/api').WbBulkPreorderStatus>('POST', '/api/v1/warehouse/assembly/wb/bulk-preorder', { assembly_ids: assemblyIds });
            } catch (e) {
                const msg = e instanceof Error ? e.message : '';
                if (/уже\s+идёт|409/i.test(msg)) {
                    const err = new Error(msg || 'Батч уже идёт') as Error & { code?: string };
                    err.code = 'conflict';
                    throw err;
                }
                throw e;
            }
        },
        wbSupplyBulkPreorderStatus() {
            return api.request<import('@/types/api').WbBulkPreorderStatus>('GET', '/api/v1/warehouse/assembly/wb/bulk-preorder/status');
        },
        // Короба поставки из кабинета WB (с содержимым) — вкладка «Упаковка».
        wbSupplyCabinetBoxes(assemblyId: number) {
            return api.request<import('@/types/api').WbCabinetBoxes>('GET', `/api/v1/warehouse/assembly/${assemblyId}/wb/cabinet-boxes`);
        },
        // Существующий пропуск поставки из кабинета WB — вкладка «Пропуск».
        wbSupplyCabinetPass(assemblyId: number) {
            return api.request<import('@/types/api').WbCabinetPass>('GET', `/api/v1/warehouse/assembly/${assemblyId}/wb/cabinet-pass`);
        },
    };
}

import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addWarehouseMethods } from '@/lib/api/warehouse';

const localStorageMock = (() => {
    let store: Record<string, string> = {};
    return {
        getItem: (key: string) => store[key] ?? null,
        setItem: (key: string, value: string) => { store[key] = value; },
        removeItem: (key: string) => { delete store[key]; },
        clear: () => { store = {}; },
    };
})();
Object.defineProperty(global, 'window', { value: { document: {}, location: { href: '' } }, writable: true });
Object.defineProperty(global, 'localStorage', { value: localStorageMock, writable: true });

function makeApi() {
    const client = new ApiClient();
    return { client, ...addWarehouseMethods(client) };
}

function mockFetch(body: unknown) {
    return vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: true, status: 200, json: async () => body,
    } as Response);
}

afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

// ─── Warehouse CRUD ────────────────────────────────────────────────────────

describe('warehouse.getWarehouses', () => {
    it('GETs /api/v1/warehouse', async () => {
        const spy = mockFetch([{ id: 1, name: 'Основной склад', sort_order: 1 }]);
        const api = makeApi();
        const result = await api.getWarehouses();
        expect(result).toHaveLength(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/warehouse');
    });
});

describe('warehouse.createWarehouse', () => {
    it('POSTs warehouse data', async () => {
        const spy = mockFetch({ id: 2, name: 'Новый склад' });
        const api = makeApi();
        await api.createWarehouse({ name: 'Новый склад' });
        expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
        const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
        expect(body.name).toBe('Новый склад');
    });
});

describe('warehouse.updateWarehouse', () => {
    it('PUTs by id', async () => {
        const spy = mockFetch({ id: 1, name: 'Updated' });
        const api = makeApi();
        await api.updateWarehouse(1, { name: 'Updated' });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/1');
        expect((init as RequestInit).method).toBe('PUT');
    });
});

describe('warehouse.deleteWarehouse', () => {
    it('DELETEs by id', async () => {
        const spy = mockFetch({ message: 'deleted' });
        const api = makeApi();
        await api.deleteWarehouse(3);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/3');
        expect((init as RequestInit).method).toBe('DELETE');
    });
});

describe('warehouse.reorderWarehouses', () => {
    it('PUTs reorder payload', async () => {
        const spy = mockFetch({ message: 'ok' });
        const api = makeApi();
        await api.reorderWarehouses([{ id: 1, sort_order: 2 }, { id: 2, sort_order: 1 }]);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/reorder');
        expect((init as RequestInit).method).toBe('PUT');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.items).toHaveLength(2);
    });
});

// ─── Stock ────────────────────────────────────────────────────────────────

describe('warehouse.getWarehouseStock', () => {
    it('GETs stock for a warehouse', async () => {
        const spy = mockFetch([{ barcode: '111', qty: 50 }]);
        const api = makeApi();
        await api.getWarehouseStock(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/warehouse/1/stock');
    });
});

describe('warehouse.getStockMovements', () => {
    it('GETs movements with default limit 200', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getStockMovements(1);
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/1/movements');
        expect(url).toContain('limit=200');
    });

    it('accepts custom limit', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getStockMovements(1, 50);
        expect(spy.mock.calls[0][0]).toContain('limit=50');
    });
});

describe('warehouse.getUnifiedStock', () => {
    it('GETs without params', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getUnifiedStock();
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/stock/unified');
        expect(url).not.toContain('?');
    });

    it('includes group_by and brand when provided', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getUnifiedStock('brand', 'Nike');
        const [url] = spy.mock.calls[0];
        expect(url).toContain('group_by=brand');
        expect(url).toContain('brand=Nike');
    });

    it('includes include_forecast=true when set', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getUnifiedStock(undefined, undefined, true);
        expect(spy.mock.calls[0][0]).toContain('include_forecast=true');
    });
});

// ─── Receipts ─────────────────────────────────────────────────────────────

describe('warehouse.createReceipt', () => {
    it('POSTs receipt with items', async () => {
        const spy = mockFetch({ id: 1, status: 'pending' });
        const api = makeApi();
        await api.createReceipt(1, {
            planned_date: '2025-06-01',
            items: [{ barcode: '111', expected_qty: 10 }],
        });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/1/receipts');
        expect((init as RequestInit).method).toBe('POST');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.items).toHaveLength(1);
        expect(body.items[0].barcode).toBe('111');
    });
});

describe('warehouse.acceptReceipt body serialization (|| null → ?? null)', () => {
    let captured: { body: string | undefined }[];

    beforeEach(() => {
        captured = [];
        vi.stubGlobal('fetch', vi.fn((_url: string, init?: RequestInit) => {
            captured.push({ body: init?.body as string | undefined });
            return Promise.resolve({
                ok: true,
                status: 200,
                json: () => Promise.resolve({ id: 42, status: 'ACCEPTED' }),
            });
        }));
    });

    it('sends body with actual quantities array when provided', async () => {
        const api = makeApi();
        const quantities = [{ item_id: 1, actual_qty: 5 }, { item_id: 2, actual_qty: 3 }];
        await api.acceptReceipt(42, quantities);

        expect(captured).toHaveLength(1);
        expect(captured[0].body).toBe(JSON.stringify(quantities));
    });

    it('sends [] as body when empty array is passed (was lost as null before fix)', async () => {
        const api = makeApi();
        await api.acceptReceipt(42, []);
        expect(captured).toHaveLength(1);
        // After fix: [] ?? null → [] → JSON.stringify([]) === "[]"
        expect(captured[0].body).toBe('[]');
    });

    it('sends no body (undefined) when no argument passed', async () => {
        const api = makeApi();
        await api.acceptReceipt(42);
        expect(captured).toHaveLength(1);
        expect(captured[0].body).toBeUndefined();
    });

    it('sends no body when null is passed explicitly', async () => {
        const api = makeApi();
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        await api.acceptReceipt(42, null as any);
        expect(captured).toHaveLength(1);
        expect(captured[0].body).toBeUndefined();
    });
});

// ─── Transfers ────────────────────────────────────────────────────────────

describe('warehouse.getTransfers', () => {
    it('GETs transfers with in_transit=false by default', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getTransfers();
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/transfers');
        expect(url).toContain('in_transit=false');
    });

    it('passes in_transit=true when requested', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getTransfers(true);
        expect(spy.mock.calls[0][0]).toContain('in_transit=true');
    });

    it('передаёт срез Листа логиста: status + has_vehicle', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getTransfers(false, undefined, { status: 'DRAFT', hasVehicle: false });
        const url = spy.mock.calls[0][0] as string;
        expect(url).toContain('status=DRAFT');
        // has_vehicle=false обязан УЙТИ в запрос: наивная проверка `if (v)`
        // выбросила бы его и превратила «без машины» в «все подряд».
        expect(url).toContain('has_vehicle=false');
    });
});

describe('warehouse.createTransfer', () => {
    it('POSTs transfer data', async () => {
        const spy = mockFetch({ id: 1, status: 'pending' });
        const api = makeApi();
        await api.createTransfer({
            from_warehouse_id: 1,
            to_warehouse_id: 2,
            items: [{ barcode: '111', quantity: 5 }],
        });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/transfers');
        expect((init as RequestInit).method).toBe('POST');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.from_warehouse_id).toBe(1);
        expect(body.to_warehouse_id).toBe(2);
    });

    /**
     * Контракты СОЗДАНИЯ и НАЗНАЧЕНИЯ МАШИНЫ трактуют пустую единицу по-разному,
     * и перепутать их легко:
     *   • create (StockTransferCreate): shipped_as_boxes — обычный bool,
     *     дефолт false, пустое = «паллеты». Флаг обязан уйти ВСЕГДА;
     *   • assign-vehicle (TransferAssignVehicle): трёхзначный, null = «не
     *     трогать уже заданное» (иначе bulk превратил бы коробочный переезд
     *     в паллетный).
     * Тест держит обе стороны.
     */
    it('создание: коробочная единица уходит явно, пустая оценка — null', async () => {
        const spy = mockFetch({ id: 1, status: 'DRAFT' });
        const api = makeApi();
        await api.createTransfer({
            from_warehouse_id: 1,
            to_warehouse_id: 2,
            items: [{ barcode: '111', quantity: 5 }],
            pallets_count: null,
            pallet_weight_kg: null,
            shipped_as_boxes: true,
        });
        const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
        expect(body.shipped_as_boxes).toBe(true);
        expect(body.pallets_count).toBeNull();
        expect(body.pallet_weight_kg).toBeNull();
    });
});

describe('warehouse.assignTransferVehicle', () => {
    const vehicleBase = {
        vehicle_info: 'В874УА37',
        vehicle_brand: 'ГАЗ-330',
        driver_first_name: 'Дмитрий',
        driver_last_name: 'Крапива',
        driver_phone: '+79991234567',
        logistics_by_warehouse: false,
        carrier_inn: null,
        carrier_name: null,
        pickup_date: '2026-08-01',
        pickup_time_slot: '08:00-12:00',
        pickup_cost: 15000,
        delivery_date: '2026-08-02',
    };

    it('назначение: нетронутая единица уходит как null — «не трогать»', async () => {
        const spy = mockFetch({ id: 7 });
        const api = makeApi();
        await api.assignTransferVehicle(7, {
            ...vehicleBase,
            pallets_count: null,
            pallet_weight_kg: null,
            shipped_as_boxes: null,
        });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/transfers/7/assign-vehicle');
        const body = JSON.parse((init as RequestInit).body as string);
        // Именно null, а не false: false здесь означал бы «сделать паллетным».
        expect(body.shipped_as_boxes).toBeNull();
    });

    it('bulk шлёт {ids, payload} — реквизиты общие на всю пачку', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.assignTransferVehicleBulk([31, 32], {
            ...vehicleBase,
            pallets_count: null,
            pallet_weight_kg: null,
            shipped_as_boxes: null,
        });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/transfers/assign-vehicle-bulk');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.ids).toEqual([31, 32]);
        expect(body.payload.vehicle_info).toBe('В874УА37');
    });
});

// ─── FBO Supplies ─────────────────────────────────────────────────────────

describe('warehouse.getFboSupplies', () => {
    it('GETs without params', async () => {
        const spy = mockFetch({ items: [], total: 0 });
        const api = makeApi();
        await api.getFboSupplies();
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/fbo-supplies');
        expect(url).not.toContain('?');
    });

    it('builds query string from params', async () => {
        const spy = mockFetch({ items: [], total: 0 });
        const api = makeApi();
        await api.getFboSupplies({ status: 'active', limit: 20, offset: 0 });
        const [url] = spy.mock.calls[0];
        expect(url).toContain('status=active');
        expect(url).toContain('limit=20');
    });

    it('omits undefined params', async () => {
        const spy = mockFetch({ items: [], total: 0 });
        const api = makeApi();
        await api.getFboSupplies({ status: 'active', warehouse: undefined });
        const [url] = spy.mock.calls[0];
        expect(url).not.toContain('warehouse=');
    });
});

describe('warehouse.syncFboSupplies', () => {
    it('POSTs to sync endpoint', async () => {
        const spy = mockFetch({ synced: 10, updated: 5 });
        const api = makeApi();
        const result = await api.syncFboSupplies();
        expect(result.synced).toBe(10);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/fbo-supplies/sync');
        expect((init as RequestInit).method).toBe('POST');
    });
});

// ─── Assembly ─────────────────────────────────────────────────────────────

describe('warehouse.getAssemblyRequests', () => {
    it('GETs without params', async () => {
        const spy = mockFetch({ items: [], total: 0 });
        const api = makeApi();
        await api.getAssemblyRequests();
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/assembly');
        expect(url).not.toContain('?');
    });

    it('includes warehouse_id and status filters', async () => {
        const spy = mockFetch({ items: [], total: 0 });
        const api = makeApi();
        await api.getAssemblyRequests({ warehouse_id: 1, status: 'pending' });
        const [url] = spy.mock.calls[0];
        expect(url).toContain('warehouse_id=1');
        expect(url).toContain('status=pending');
    });
});

describe('warehouse.startAssembly / markAssemblyReady / shipAssembly / cancelAssembly', () => {
    it('startAssembly POSTs to start endpoint', async () => {
        const spy = mockFetch({ id: 1, status: 'in_progress' });
        const api = makeApi();
        await api.startAssembly(1);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/assembly/1/start');
        expect((init as RequestInit).method).toBe('POST');
    });

    it('markAssemblyReady POSTs to ready endpoint', async () => {
        const spy = mockFetch({ id: 1, status: 'ready' });
        const api = makeApi();
        await api.markAssemblyReady(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/warehouse/assembly/1/ready');
    });

    it('cancelAssembly POSTs to cancel endpoint', async () => {
        const spy = mockFetch({ id: 1, status: 'cancelled' });
        const api = makeApi();
        await api.cancelAssembly(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/warehouse/assembly/1/cancel');
    });
});

describe('warehouse.shipBulk', () => {
    it('POSTs ids array to ship-bulk endpoint', async () => {
        const spy = mockFetch([{ id: 1, status: 'shipped' }, { id: 2, status: 'shipped' }]);
        const api = makeApi();
        const result = await api.shipBulk([1, 2]);
        expect(result).toHaveLength(2);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/assembly/ship-bulk');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.ids).toEqual([1, 2]);
    });
});

// ─── Defects ──────────────────────────────────────────────────────────────

describe('warehouse.getDefectSummary', () => {
    it('GETs /api/v1/warehouse/defects/summary', async () => {
        const spy = mockFetch([{ barcode: '111', defect_qty: 3 }]);
        const api = makeApi();
        await api.getDefectSummary();
        expect(spy.mock.calls[0][0]).toContain('/api/v1/warehouse/defects/summary');
    });
});

describe('warehouse.markDefect', () => {
    it('POSTs defect operation', async () => {
        const spy = mockFetch({ message: 'ok' });
        const api = makeApi();
        await api.markDefect(1, { barcode: '111', quantity: 2, reason: 'Брак' });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/1/defects/mark');
        expect((init as RequestInit).method).toBe('POST');
    });
});

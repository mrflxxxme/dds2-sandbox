import { describe, it, expect, vi, afterEach } from 'vitest';
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

describe('warehouse.acceptReceipt', () => {
    it('POSTs accept with actual quantities', async () => {
        const spy = mockFetch({ id: 1, status: 'accepted' });
        const api = makeApi();
        await api.acceptReceipt(5, [{ item_id: 1, actual_qty: 9 }]);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/receipts/5/accept');
        expect((init as RequestInit).method).toBe('POST');
    });

    it('sends no body when no actual quantities provided (null is falsy — no JSON serialisation)', async () => {
        const spy = mockFetch({ id: 1, status: 'accepted' });
        const api = makeApi();
        await api.acceptReceipt(5);
        // The implementation passes `actualQuantities || null` to api.request.
        // api.request serialises body only when truthy: `body ? JSON.stringify(body) : undefined`.
        // null is falsy so body is undefined — no body sent in the request.
        const body = (spy.mock.calls[0][1] as RequestInit).body;
        expect(body).toBeUndefined();
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
        await api.markDefect(1, { barcode: '111', qty: 2, reason: 'Брак' });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/1/defects/mark');
        expect((init as RequestInit).method).toBe('POST');
    });
});

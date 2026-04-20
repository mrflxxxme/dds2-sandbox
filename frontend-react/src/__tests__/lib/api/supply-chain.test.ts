import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addSupplyChainMethods } from '@/lib/api/supply-chain';

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
    return { client, ...addSupplyChainMethods(client) };
}

function mockFetch(body: unknown) {
    return vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: true, status: 200, json: async () => body,
    } as Response);
}

afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

// ─── Factory Orders ────────────────────────────────────────────────────────

describe('supply-chain.getFactoryOrders', () => {
    it('GETs /api/v1/supply-chain/factory-orders', async () => {
        const spy = mockFetch([{ id: 1, order_no: 'FO-001', status: 'draft' }]);
        const api = makeApi();
        const result = await api.getFactoryOrders();
        expect(result).toHaveLength(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/supply-chain/factory-orders');
    });
});

describe('supply-chain.getFactoryOrder', () => {
    it('GETs by id', async () => {
        const spy = mockFetch({ id: 1, order_no: 'FO-001', status: 'draft' });
        const api = makeApi();
        await api.getFactoryOrder(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/supply-chain/factory-orders/1');
    });
});

describe('supply-chain.createFactoryOrder', () => {
    it('POSTs factory order data', async () => {
        const spy = mockFetch({ id: 2, order_no: 'FO-002', status: 'draft' });
        const api = makeApi();
        await api.createFactoryOrder({ order_no: 'FO-002', supplier_id: 1, currency: 'CNY' } as any);
        expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
        const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
        expect(body.order_no).toBe('FO-002');
    });
});

describe('supply-chain.updateFactoryOrder', () => {
    it('PUTs updated data by id', async () => {
        const spy = mockFetch({ id: 1, order_no: 'FO-001', status: 'confirmed' });
        const api = makeApi();
        await api.updateFactoryOrder(1, { status: 'confirmed' } as any);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/supply-chain/factory-orders/1');
        expect((init as RequestInit).method).toBe('PUT');
    });
});

describe('supply-chain.deleteFactoryOrder', () => {
    it('DELETEs by id', async () => {
        const spy = mockFetch({ message: 'deleted' });
        const api = makeApi();
        await api.deleteFactoryOrder(1);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/supply-chain/factory-orders/1');
        expect((init as RequestInit).method).toBe('DELETE');
    });
});

describe('supply-chain.updateFactoryOrderStatus', () => {
    it('PUTs status update', async () => {
        const spy = mockFetch({ id: 1, status: 'production' });
        const api = makeApi();
        await api.updateFactoryOrderStatus(1, 'production' as any);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/supply-chain/factory-orders/1/status');
        expect((init as RequestInit).method).toBe('PUT');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.status).toBe('production');
    });
});

// ─── Vehicles ─────────────────────────────────────────────────────────────

describe('supply-chain.getVehicles', () => {
    it('GETs /api/v1/supply-chain/vehicles', async () => {
        const spy = mockFetch([{ order_no: 'VH-001', status: 'pending' }]);
        const api = makeApi();
        const result = await api.getVehicles();
        expect(result).toHaveLength(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/supply-chain/vehicles');
    });
});

describe('supply-chain.getVehicle', () => {
    it('GETs by orderNo without slash', async () => {
        const spy = mockFetch({ order_no: 'VH-001' });
        const api = makeApi();
        await api.getVehicle('VH-001');
        expect(spy.mock.calls[0][0]).toContain('/api/v1/supply-chain/vehicles/VH-001');
    });

    it('uses find endpoint for orderNo with slash', async () => {
        const spy = mockFetch({ order_no: 'VH/001' });
        const api = makeApi();
        await api.getVehicle('VH/001');
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/supply-chain/vehicles/find');
        expect(url).toContain('order_no=VH%2F001');
    });
});

describe('supply-chain.deleteVehicle', () => {
    it('DELETEs by orderNo', async () => {
        const spy = mockFetch({ ok: true });
        const api = makeApi();
        await api.deleteVehicle('VH-001');
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/supply-chain/vehicles/VH-001');
        expect((init as RequestInit).method).toBe('DELETE');
    });

    it('uses find endpoint for orderNo with slash', async () => {
        const spy = mockFetch({ ok: true });
        const api = makeApi();
        await api.deleteVehicle('VH/2025/001');
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/supply-chain/vehicles/find');
        expect(url).toContain('order_no=VH%2F2025%2F001');
    });
});

describe('supply-chain.addItemsToVehicle', () => {
    it('POSTs items to vehicle', async () => {
        const spy = mockFetch({ ok: true, added: 2 });
        const api = makeApi();
        await api.addItemsToVehicle('VH-001', [
            { factory_order_item_id: 1, qty: 10 },
            { factory_order_item_id: 2, qty: 5 },
        ]);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/supply-chain/vehicles/VH-001/items');
        expect((init as RequestInit).method).toBe('POST');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.items).toHaveLength(2);
    });
});

describe('supply-chain.updateVehicleItemQty', () => {
    it('PATCHes item qty', async () => {
        const spy = mockFetch({ ok: true, item_id: 5 });
        const api = makeApi();
        await api.updateVehicleItemQty('VH-001', 5, 15);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/supply-chain/vehicles/VH-001/items/5');
        expect((init as RequestInit).method).toBe('PATCH');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.qty).toBe(15);
    });
});

describe('supply-chain.clearAllVehicleItems', () => {
    it('DELETEs all items from vehicle', async () => {
        const spy = mockFetch({ ok: true, removed: 5 });
        const api = makeApi();
        const result = await api.clearAllVehicleItems('VH-001');
        expect(result.removed).toBe(5);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/supply-chain/vehicles/VH-001/items');
        expect((init as RequestInit).method).toBe('DELETE');
    });
});

describe('supply-chain.recalcVehicle', () => {
    it('POSTs to recalc endpoint', async () => {
        const spy = mockFetch({ total_cny: 5000, total_rub: 450000 });
        const api = makeApi();
        await api.recalcVehicle('VH-001');
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/supply-chain/vehicles/VH-001/recalc');
        expect((init as RequestInit).method).toBe('POST');
    });
});

// ─── Vehicle Documents ─────────────────────────────────────────────────────

describe('supply-chain.uploadVehicleDocument', () => {
    it('calls uploadFormData with file, doc_type, and optional note', async () => {
        const api = makeApi();
        const uploadSpy = vi.spyOn(api.client, 'uploadFormData').mockResolvedValue({
            id: 1, doc_type: 'invoice', filename: 'inv.pdf',
        });

        const file = new File(['data'], 'invoice.pdf', { type: 'application/pdf' });
        await api.uploadVehicleDocument('VH-001', file, 'invoice', 'Invoice for FO-001');

        const [path, formData] = uploadSpy.mock.calls[0];
        expect(path).toContain('/api/v1/supply-chain/vehicles/VH-001/documents');
        expect(formData.get('file')).toBe(file);
        expect(formData.get('doc_type')).toBe('invoice');
        expect(formData.get('note')).toBe('Invoice for FO-001');
    });

    it('does not append note when not provided', async () => {
        const api = makeApi();
        const uploadSpy = vi.spyOn(api.client, 'uploadFormData').mockResolvedValue({ id: 1, doc_type: 'contract', filename: 'c.pdf' });

        const file = new File(['data'], 'contract.pdf');
        await api.uploadVehicleDocument('VH-001', file, 'contract');

        const [, formData] = uploadSpy.mock.calls[0];
        expect(formData.get('note')).toBeNull();
    });
});

// ─── Suppliers ────────────────────────────────────────────────────────────

describe('supply-chain.getSuppliers', () => {
    it('GETs /api/v1/supply-chain/suppliers', async () => {
        const spy = mockFetch([{ id: 1, name: 'Supplier A', country: 'CN' }]);
        const api = makeApi();
        const result = await api.getSuppliers();
        expect(result).toHaveLength(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/supply-chain/suppliers');
    });
});

describe('supply-chain.createSupplier', () => {
    it('POSTs supplier data', async () => {
        const spy = mockFetch({ id: 2, name: 'Supplier B' });
        const api = makeApi();
        await api.createSupplier({ name: 'Supplier B', country: 'CHINA' });
        expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
    });
});

describe('supply-chain.getSupplyChainOverview', () => {
    it('GETs /api/v1/supply-chain/overview', async () => {
        const spy = mockFetch({ active_orders: 5, in_transit: 2 });
        const api = makeApi();
        await api.getSupplyChainOverview();
        expect(spy.mock.calls[0][0]).toContain('/api/v1/supply-chain/overview');
    });
});

describe('supply-chain.splitToVehicles', () => {
    it('POSTs assignments to split endpoint', async () => {
        const spy = mockFetch({ message: 'ok' });
        const api = makeApi();
        await api.splitToVehicles(1, [{ vehicle_order_no: 'VH-001', factory_order_item_id: 1, qty: 5 } as any]);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/supply-chain/factory-orders/1/split-to-vehicles');
        expect((init as RequestInit).method).toBe('POST');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.assignments).toHaveLength(1);
    });
});

describe('supply-chain.bulkUpdateFactoryItemPrices', () => {
    it('PUTs bulk price update', async () => {
        const spy = mockFetch({ updated: 2, not_found: [] });
        const api = makeApi();
        const items = [
            { factory_order_item_id: 1, new_price_cny: '25.50' },
            { factory_order_item_id: 2, new_price_cny: 30 },
        ];
        const result = await api.bulkUpdateFactoryItemPrices(items);
        expect(result.updated).toBe(2);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/supply-chain/factory-orders/items/bulk-price');
        expect((init as RequestInit).method).toBe('PUT');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.items).toHaveLength(2);
    });
});

function makeStubApi(token: string | null = 'test-token', projectId: number | null = 42) {
    return {
        getToken: () => token,
        getProjectId: () => projectId,
        request: vi.fn(async () => ({})),
        uploadFormData: vi.fn(async () => ({})),
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any;
}

describe('downloadVehicleDocument', () => {
    beforeEach(() => {
        vi.unstubAllEnvs();
        vi.unstubAllGlobals();
        vi.stubEnv('NEXT_PUBLIC_API_URL', 'http://test-api');
    });

    it('uses module-level API_URL and sends auth headers', async () => {
        const mockBlob = new Blob(['data']);
        const mockFetch = vi.fn().mockResolvedValue({ ok: true, blob: async () => mockBlob });
        vi.stubGlobal('fetch', mockFetch);

        const stubApi = makeStubApi('my-token', 7);
        const sc = addSupplyChainMethods(stubApi);

        const result = await sc.downloadVehicleDocument('ORD-001', 5);

        expect(mockFetch).toHaveBeenCalledOnce();
        const [url, opts] = mockFetch.mock.calls[0];
        expect(url).toContain('/api/v1/supply-chain/vehicles/ORD-001/documents/5/download');
        expect(opts.headers['Authorization']).toBe('Bearer my-token');
        expect(opts.headers['X-Project-Id']).toBe('7');
        expect(result).toBe(mockBlob);
    });

    it('encodes orderNo with slashes in the URL', async () => {
        const mockFetch = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob([]) });
        vi.stubGlobal('fetch', mockFetch);

        const stubApi = makeStubApi('tok', 1);
        const sc = addSupplyChainMethods(stubApi);
        await sc.downloadVehicleDocument('ORD/2024/001', 3);

        const [url] = mockFetch.mock.calls[0];
        expect(url).toContain(encodeURIComponent('ORD/2024/001'));
    });

    it('throws when response is not ok', async () => {
        const mockFetch = vi.fn().mockResolvedValue({ ok: false });
        vi.stubGlobal('fetch', mockFetch);

        const stubApi = makeStubApi();
        const sc = addSupplyChainMethods(stubApi);

        await expect(sc.downloadVehicleDocument('ORD-002', 9)).rejects.toThrow('Ошибка скачивания');
    });

    it('omits Authorization and X-Project-Id headers when token and projectId are null', async () => {
        const mockFetch = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob([]) });
        vi.stubGlobal('fetch', mockFetch);

        const stubApi = makeStubApi(null, null);
        const sc = addSupplyChainMethods(stubApi);
        await sc.downloadVehicleDocument('ORD-003', 1);

        const [, opts] = mockFetch.mock.calls[0];
        expect(opts.headers['Authorization']).toBeUndefined();
        expect(opts.headers['X-Project-Id']).toBeUndefined();
    });
});

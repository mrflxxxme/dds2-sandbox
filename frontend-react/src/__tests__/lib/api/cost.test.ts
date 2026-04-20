import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addCostMethods } from '@/lib/api/cost';

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
    return { client, ...addCostMethods(client) };
}

function mockFetch(body: unknown) {
    return vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: true, status: 200, json: async () => body,
    } as Response);
}

afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

describe('cost.getCostOrders', () => {
    it('GETs /api/v1/cost/orders', async () => {
        const spy = mockFetch([{ id: 1, order_no: 'ORD-001' }]);
        const api = makeApi();
        const result = await api.getCostOrders();
        expect(result).toHaveLength(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/cost/orders');
    });
});

describe('cost.createCostOrder', () => {
    it('POSTs to /api/v1/cost/orders', async () => {
        const spy = mockFetch({ id: 2, order_no: 'ORD-002', status: 'draft' });
        const api = makeApi();
        await api.createCostOrder({ order_no: 'ORD-002', currency: 'CNY' });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/cost/orders');
        expect((init as RequestInit).method).toBe('POST');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.order_no).toBe('ORD-002');
    });
});

describe('cost.updateCostOrder', () => {
    it('PUTs with URL-encoded orderNo', async () => {
        const spy = mockFetch({ id: 1, order_no: 'ORD-001' });
        const api = makeApi();
        await api.updateCostOrder('ORD-001', { status: 'confirmed' });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/cost/orders/ORD-001');
        expect((init as RequestInit).method).toBe('PUT');
    });

    it('URL-encodes orderNo with special chars', async () => {
        const spy = mockFetch({ id: 1 });
        const api = makeApi();
        await api.updateCostOrder('ORD/001', {});
        const [url] = spy.mock.calls[0];
        expect(url).toContain('ORD%2F001');
    });
});

describe('cost.deleteCostOrder', () => {
    it('DELETEs by orderNo', async () => {
        const spy = mockFetch({ message: 'deleted' });
        const api = makeApi();
        await api.deleteCostOrder('ORD-001');
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/cost/orders/ORD-001');
        expect((init as RequestInit).method).toBe('DELETE');
    });
});

describe('cost.getCostOrderItems', () => {
    it('GETs items for a given orderNo', async () => {
        const spy = mockFetch([{ id: 1, barcode: '123456', qty: 10, price_cny: '25.50' }]);
        const api = makeApi();
        const result = await api.getCostOrderItems('ORD-001');
        expect(result).toHaveLength(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/cost/orders/ORD-001/items');
    });
});

describe('cost.getNomenclature', () => {
    it('GETs /api/v1/cost/nomenclature', async () => {
        const spy = mockFetch([{ nm_id: 1, barcode: '123', name: 'Product A' }]);
        const api = makeApi();
        const result = await api.getNomenclature();
        expect(result).toHaveLength(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/cost/nomenclature');
    });
});

describe('cost.getDutyRules', () => {
    it('GETs /api/v1/cost/duty_rules', async () => {
        const spy = mockFetch([{ id: 1, hs_code: '6109', rate_pct: 15 }]);
        const api = makeApi();
        const result = await api.getDutyRules();
        expect(result).toHaveLength(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/cost/duty_rules');
    });
});

describe('cost.addDutyRule', () => {
    it('POSTs duty rule data', async () => {
        const spy = mockFetch({ id: 2, hs_code: '6110', rate_pct: 12 });
        const api = makeApi();
        await api.addDutyRule({ hs_code: '6110', rate_pct: 12 });
        expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
    });
});

describe('cost.deleteDutyRule', () => {
    it('DELETEs by id', async () => {
        const spy = mockFetch({ message: 'ok' });
        const api = makeApi();
        await api.deleteDutyRule(3);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/cost/duty_rules/3');
        expect((init as RequestInit).method).toBe('DELETE');
    });
});

describe('cost.getVatRate / setVatRate', () => {
    it('GETs vat rate', async () => {
        const spy = mockFetch({ vat_rate: 20 });
        const api = makeApi();
        const result = await api.getVatRate();
        expect(result.vat_rate).toBe(20);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/cost/vat_rate');
    });

    it('PUTs vat rate', async () => {
        const spy = mockFetch({ status: 'ok', vat_rate: 15 });
        const api = makeApi();
        const result = await api.setVatRate(15);
        expect(result.vat_rate).toBe(15);
        const [, init] = spy.mock.calls[0];
        expect((init as RequestInit).method).toBe('PUT');
        expect(JSON.parse((init as RequestInit).body as string)).toEqual({ vat_rate: 15 });
    });
});

describe('cost.bulkUpdateNomenclatureArea', () => {
    it('PUTs bulk area update', async () => {
        const spy = mockFetch({ ok: true, updated: 2, not_found: [] });
        const api = makeApi();
        const items = [
            { barcode: '111', area_m2: 0.05 },
            { barcode: '222', area_m2: 0.07 },
        ];
        const result = await api.bulkUpdateNomenclatureArea(items);
        expect(result.updated).toBe(2);
        const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
        expect(body.items).toEqual(items);
    });
});

describe('cost.uploadCostFile', () => {
    it('calls uploadFormData with correct path and FormData', async () => {
        const api = makeApi();
        const uploadSpy = vi.spyOn(api.client, 'uploadFormData').mockResolvedValue({
            inserted: 10, unrecognized: 0,
        });

        const file = new File(['data'], 'cost.xlsx');
        await api.uploadCostFile('ORD-001', file);

        expect(uploadSpy).toHaveBeenCalledOnce();
        const [path, formData] = uploadSpy.mock.calls[0];
        expect(path).toBe('/api/v1/cost/orders/ORD-001/upload');
        expect(formData.get('file')).toBe(file);
    });

    it('URL-encodes orderNo with slashes in upload path', async () => {
        const api = makeApi();
        const uploadSpy = vi.spyOn(api.client, 'uploadFormData').mockResolvedValue({
            inserted: 5, unrecognized: 0,
        });

        const file = new File(['data'], 'cost.xlsx');
        await api.uploadCostFile('ORD/2025/001', file);

        const [path] = uploadSpy.mock.calls[0];
        expect(path).toContain('ORD%2F2025%2F001');
    });
});

describe('cost.generatePlan', () => {
    it('POSTs to generate_plan endpoint', async () => {
        const spy = mockFetch({ ok: true, order_no: 'ORD-001', payments_created: 3, plan: {} });
        const api = makeApi();
        const result = await api.generatePlan('ORD-001');
        expect(result.ok).toBe(true);
        expect(result.payments_created).toBe(3);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/cost/orders/ORD-001/generate_plan');
        expect((init as RequestInit).method).toBe('POST');
    });
});

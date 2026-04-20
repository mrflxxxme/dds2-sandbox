import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addFunnelMethods } from '@/lib/api/funnel';

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
    return { client, ...addFunnelMethods(client) };
}

function mockFetch(body: unknown) {
    return vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: true, status: 200, json: async () => body,
    } as Response);
}

afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

describe('funnel.syncFunnel', () => {
    it('POSTs with date_from and date_to', async () => {
        const spy = mockFetch({ ok: true, days_synced: 30 });
        const api = makeApi();
        const result = await api.syncFunnel('2025-01-01', '2025-01-31');
        expect(result.days_synced).toBe(30);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/funnel/sync');
        expect((init as RequestInit).method).toBe('POST');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.date_from).toBe('2025-01-01');
        expect(body.date_to).toBe('2025-01-31');
    });
});

describe('funnel.getFunnelData', () => {
    it('GETs /api/v1/funnel/data without params', async () => {
        const spy = mockFetch({ data: [], detailed: false, tax_rate: 0.06 });
        const api = makeApi();
        await api.getFunnelData();
        expect(spy.mock.calls[0][0]).toContain('/api/v1/funnel/data');
    });

    it('passes all optional params as query string', async () => {
        const spy = mockFetch({ data: [], detailed: true, tax_rate: 0.06 });
        const api = makeApi();
        await api.getFunnelData({
            date_from: '2025-01-01',
            date_to: '2025-01-31',
            brand: 'Nike',
            vendor_code: 'ART-1',
            group_by: 'day',
        });
        const [url] = spy.mock.calls[0];
        expect(url).toContain('date_from=2025-01-01');
        expect(url).toContain('date_to=2025-01-31');
        expect(url).toContain('brand=Nike');
        expect(url).toContain('vendor_code=ART-1');
        expect(url).toContain('group_by=day');
    });

    it('omits params that are not provided', async () => {
        const spy = mockFetch({ data: [], detailed: false, tax_rate: 0.06 });
        const api = makeApi();
        await api.getFunnelData({ date_from: '2025-01-01' });
        const [url] = spy.mock.calls[0];
        expect(url).not.toContain('brand=');
        expect(url).not.toContain('vendor_code=');
    });
});

describe('funnel.getFunnelSummary', () => {
    it('builds correct URL with all optional params', async () => {
        const spy = mockFetch({ total_revenue: 1000000 });
        const api = makeApi();
        await api.getFunnelSummary('2025-01-01', '2025-01-31', 'Nike', 'Одежда');
        const [url] = spy.mock.calls[0];
        expect(url).toContain('date_from=2025-01-01');
        expect(url).toContain('brand=Nike');
        expect(url).toContain('subject=%D0%9E%D0%B4%D0%B5%D0%B6%D0%B4%D0%B0');
    });
});

describe('funnel.setFunnelCost', () => {
    it('POSTs nm_id and cost_price', async () => {
        const spy = mockFetch({ message: 'ok' });
        const api = makeApi();
        await api.setFunnelCost(12345, 199.99);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/funnel/cost');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.nm_id).toBe(12345);
        expect(body.cost_price).toBe(199.99);
    });
});

describe('funnel.bulkSetFunnelCosts', () => {
    it('POSTs items array to /api/v1/funnel/costs/bulk', async () => {
        const spy = mockFetch({ saved: 2, not_found: [], errors: [] });
        const api = makeApi();
        const items = [
            { barcode: '111', cost_price: 150 },
            { barcode: '222', cost_price: 200, currency: 'RUB' },
        ];
        const result = await api.bulkSetFunnelCosts(items);
        expect(result.saved).toBe(2);
        const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
        expect(body.items).toEqual(items);
    });
});

describe('funnel.setFunnelTax / getFunnelTax', () => {
    it('GETs current tax rate', async () => {
        const spy = mockFetch({ tax_rate: 0.06 });
        const api = makeApi();
        const result = await api.getFunnelTax();
        expect(result.tax_rate).toBe(0.06);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/funnel/tax');
    });

    it('POSTs new tax rate', async () => {
        const spy = mockFetch({ message: 'ok' });
        const api = makeApi();
        await api.setFunnelTax(0.07);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/funnel/tax');
        expect((init as RequestInit).method).toBe('POST');
        expect(JSON.parse((init as RequestInit).body as string)).toEqual({ tax_rate: 0.07 });
    });
});

describe('funnel.getAnomalies', () => {
    it('builds query string from optional params', async () => {
        const spy = mockFetch({ anomalies: [] });
        const api = makeApi();
        await api.getAnomalies({ period_days: 14, brand: 'Nike', include_rf_stocks: true, min_orders: 5 });
        const [url] = spy.mock.calls[0];
        expect(url).toContain('period_days=14');
        expect(url).toContain('brand=Nike');
        expect(url).toContain('include_rf_stocks=true');
        expect(url).toContain('min_orders=5');
    });

    it('omits params that are falsy/undefined', async () => {
        const spy = mockFetch({ anomalies: [] });
        const api = makeApi();
        await api.getAnomalies({});
        const [url] = spy.mock.calls[0];
        expect(url).not.toContain('period_days');
        expect(url).not.toContain('brand');
    });
});

describe('funnel.getCapital', () => {
    it('builds URL with capital params', async () => {
        const spy = mockFetch({ items: [] });
        const api = makeApi();
        await api.getCapital({ period_days: 30, brand: 'Nike', group_by: 'sku', elasticity: 0.5 });
        const [url] = spy.mock.calls[0];
        expect(url).toContain('period_days=30');
        expect(url).toContain('brand=Nike');
        expect(url).toContain('group_by=sku');
        expect(url).toContain('elasticity=0.5');
    });
});

describe('funnel.uploadTariffs', () => {
    it('calls uploadFormData for tariffs file', async () => {
        const api = makeApi();
        const uploadSpy = vi.spyOn(api.client, 'uploadFormData').mockResolvedValue({
            ok: true, created: 5, updated: 0, errors: [],
        });

        const file = new File(['data'], 'tariffs.xlsx');
        await api.uploadTariffs(file);

        const [path, formData] = uploadSpy.mock.calls[0];
        expect(path).toBe('/api/v1/funnel/tariffs/upload');
        expect(formData.get('file')).toBe(file);
    });
});

describe('funnel.deleteTariff', () => {
    it('DELETEs tariff by id', async () => {
        const spy = mockFetch({ status: 'deleted' });
        const api = makeApi();
        const result = await api.deleteTariff(42);
        expect(result.status).toBe('deleted');
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/funnel/tariffs/42');
        expect((init as RequestInit).method).toBe('DELETE');
    });
});

describe('funnel.syncFunnelBg', () => {
    it('POSTs with dates as query params', async () => {
        const spy = mockFetch({ status: 'started' });
        const api = makeApi();
        const result = await api.syncFunnelBg('2025-01-01', '2025-01-31');
        expect(result.status).toBe('started');
        const [url] = spy.mock.calls[0];
        expect(url).toContain('date_from=2025-01-01');
        expect(url).toContain('date_to=2025-01-31');
    });
});

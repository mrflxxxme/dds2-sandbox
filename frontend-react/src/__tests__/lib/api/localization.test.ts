import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addLocalizationMethods } from '@/lib/api/localization';

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
    return { client, ...addLocalizationMethods(client) };
}

function mockFetch(body: unknown) {
    return vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: true, status: 200, json: async () => body,
    } as Response);
}

afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

describe('localization.getLocalizationSummary', () => {
    it('GETs /api/v1/localization/summary with date_from and date_to', async () => {
        const spy = mockFetch({
            localization_index: 1.93,
            irp_percent: 2.47,
            local_orders: 8421,
            non_local_orders: 1342,
            total_orders: 9763,
            loc_pct_overall: 86.25,
            articles_count: 154,
            articles_local_count: 87,
            articles_critical_count: 67,
            district_totals: [
                { district: 'central', label: 'Центральный', local: 4500, non_local: 600, total: 5100, local_pct: 88.24 },
                { district: 'volga', label: 'Приволжский', local: 1200, non_local: 200, total: 1400, local_pct: 85.71 },
            ],
        });
        const api = makeApi();
        const result = await api.getLocalizationSummary('2026-02-01', '2026-05-01');
        expect(result.localization_index).toBe(1.93);
        expect(result.irp_percent).toBe(2.47);
        expect(result.articles_local_count).toBe(87);
        expect(result.district_totals).toHaveLength(2);
        expect(result.district_totals[0].district).toBe('central');
        expect(result.district_totals[0].label).toBe('Центральный');

        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/localization/summary');
        expect(url).toContain('date_from=2026-02-01');
        expect(url).toContain('date_to=2026-05-01');
        expect((init as RequestInit).method).toBe('GET');
    });

    it('encodes special characters in dates correctly via URLSearchParams', async () => {
        const spy = mockFetch({
            localization_index: 1.0,
            irp_percent: 0,
            local_orders: 0,
            non_local_orders: 0,
            total_orders: 0,
            loc_pct_overall: 0,
            articles_count: 0,
            articles_local_count: 0,
            articles_critical_count: 0,
            district_totals: [],
        });
        const api = makeApi();
        await api.getLocalizationSummary('2026-04-30', '2026-05-05');
        const [url] = spy.mock.calls[0];
        // URLSearchParams uses standard encoding (no template literal interpolation hazards)
        expect(url).toMatch(/date_from=2026-04-30/);
        expect(url).toMatch(/date_to=2026-05-05/);
    });
});

describe('localization.getLocalizationSkus', () => {
    it('GETs /api/v1/localization/skus with date params', async () => {
        const spy = mockFetch([
            {
                nm_id: 12345678,
                vendor_code: 'ART-001',
                title: 'Футболка базовая',
                subject: 'Футболки',
                brand: 'BrandX',
                total: 250,
                local: 230,
                non_local: 20,
                loc_pct: 92.0,
                ktr: 0.85,
                krp: 8.0,
                contribution: 212.5,
                status: 'excellent',
                districts: [
                    { district: 'central', label: 'Центральный', local: 200, non_local: 10, total: 210, local_pct: 95.24 },
                    { district: 'volga', label: 'Приволжский', local: 30, non_local: 10, total: 40, local_pct: 75.0 },
                ],
            },
        ]);
        const api = makeApi();
        const result = await api.getLocalizationSkus('2026-02-01', '2026-05-01');
        expect(result).toHaveLength(1);
        expect(result[0].nm_id).toBe(12345678);
        expect(result[0].status).toBe('excellent');
        expect(result[0].districts).toHaveLength(2);
        expect(result[0].districts[0].district).toBe('central');
        expect(result[0].districts[1].local_pct).toBe(75.0);

        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/localization/skus');
        expect(url).toContain('date_from=2026-02-01');
        expect(url).toContain('date_to=2026-05-01');
        expect((init as RequestInit).method).toBe('GET');
    });

    it('returns empty array gracefully', async () => {
        mockFetch([]);
        const api = makeApi();
        const result = await api.getLocalizationSkus('2026-02-01', '2026-05-01');
        expect(result).toEqual([]);
    });

    it('accepts SKU rows with empty districts (no wb_orders sync yet)', async () => {
        mockFetch([
            {
                nm_id: 99,
                vendor_code: 'NEW-001',
                title: 'New',
                subject: 'X',
                brand: 'Y',
                total: 10,
                local: 5,
                non_local: 5,
                loc_pct: 50.0,
                ktr: 1.0,
                krp: 0,
                contribution: 10,
                status: 'neutral',
                districts: [],
            },
        ]);
        const api = makeApi();
        const result = await api.getLocalizationSkus('2026-02-01', '2026-05-01');
        expect(result[0].districts).toEqual([]);
    });
});

describe('localization.syncLocalizationNow', () => {
    it('POSTs /api/v1/localization/sync and returns counters', async () => {
        const spy = mockFetch({
            ok: true,
            total_fetched: 1342,
            inserted: 18,
            updated: 1324,
        });
        const api = makeApi();
        const res = await api.syncLocalizationNow();

        expect(res.ok).toBe(true);
        expect(res.total_fetched).toBe(1342);
        expect(res.inserted).toBe(18);
        expect(res.updated).toBe(1324);

        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/localization/sync');
        expect((init as RequestInit).method).toBe('POST');
        // POST body должен быть валидным JSON, не undefined
        expect((init as RequestInit).body).toBe(JSON.stringify({}));
    });

    it('propagates server errors as Error', async () => {
        vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: false,
            status: 500,
            json: async () => ({ detail: 'WB API временно недоступен' }),
        } as Response);
        const api = makeApi();
        await expect(api.syncLocalizationNow()).rejects.toThrow('WB API временно недоступен');
    });
});

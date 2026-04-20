import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addReportMethods } from '@/lib/api/reports';

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
    return { client, ...addReportMethods(client) };
}

function mockFetch(body: unknown) {
    return vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: true, status: 200, json: async () => body,
    } as Response);
}

afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

describe('reports.getDDS', () => {
    it('calls /api/v1/reports/dds_month with no params', async () => {
        const spy = mockFetch({ rows: [], totals: {} });
        const api = makeApi();
        await api.getDDS();
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/reports/dds_month');
    });

    it('passes date_from and date_to when provided', async () => {
        const spy = mockFetch({ rows: [], totals: {} });
        const api = makeApi();
        await api.getDDS({ start: '2025-01-01', end: '2025-01-31' });
        const [url] = spy.mock.calls[0];
        expect(url).toContain('date_from=2025-01-01');
        expect(url).toContain('date_to=2025-01-31');
    });

    it('omits date params when not provided', async () => {
        const spy = mockFetch({ rows: [], totals: {} });
        const api = makeApi();
        await api.getDDS({});
        const [url] = spy.mock.calls[0];
        expect(url).not.toContain('date_from');
        expect(url).not.toContain('date_to');
    });
});

describe('reports.getDashboardSummary', () => {
    it('calls without date params', async () => {
        const spy = mockFetch({ balance_rub: 0, balance_cny: 0, month_income: 0, month_expense: 0 });
        const api = makeApi();
        await api.getDashboardSummary();
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/reports/dashboard_summary');
    });

    it('passes dateFrom and dateTo as query params', async () => {
        const spy = mockFetch({ balance_rub: 1000 });
        const api = makeApi();
        await api.getDashboardSummary('2025-01-01', '2025-01-31');
        const [url] = spy.mock.calls[0];
        expect(url).toContain('date_from=2025-01-01');
        expect(url).toContain('date_to=2025-01-31');
    });
});

describe('reports.getBalance', () => {
    it('GETs /api/v1/reports/balance', async () => {
        const spy = mockFetch([{ account: 'RUB', account_name: 'Сбер', currency: 'RUB', balance: 50000 }]);
        const api = makeApi();
        const result = await api.getBalance();
        expect(result).toHaveLength(1);
        expect(result[0].account).toBe('RUB');
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/reports/balance');
    });
});

describe('reports.getFilteredTransactions', () => {
    it('includes required date params', async () => {
        const spy = mockFetch({ total: 0, items: [] });
        const api = makeApi();
        await api.getFilteredTransactions('2025-01-01', '2025-01-31');
        const [url] = spy.mock.calls[0];
        expect(url).toContain('date_from=2025-01-01');
        expect(url).toContain('date_to=2025-01-31');
    });

    it('includes optional cpKey and category when provided', async () => {
        const spy = mockFetch({ total: 5, items: [] });
        const api = makeApi();
        await api.getFilteredTransactions('2025-01-01', '2025-01-31', { cpKey: 'WB', category: 'Расходы' });
        const [url] = spy.mock.calls[0];
        expect(url).toContain('cp_key=WB');
        expect(url).toContain('category=%D0%A0%D0%B0%D1%81%D1%85%D0%BE%D0%B4%D1%8B');
    });

    it('omits optional params when not provided', async () => {
        const spy = mockFetch({ total: 0, items: [] });
        const api = makeApi();
        await api.getFilteredTransactions('2025-01-01', '2025-01-31', {});
        const [url] = spy.mock.calls[0];
        expect(url).not.toContain('cp_key');
        expect(url).not.toContain('category');
        expect(url).not.toContain('flow');
    });
});

describe('reports.getDDSPnL', () => {
    it('calls /api/v1/reports/dds_pnl with year param', async () => {
        const spy = mockFetch({ months: [] });
        const api = makeApi();
        await api.getDDSPnL(2025);
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/reports/dds_pnl');
        expect(url).toContain('year=2025');
    });
});

describe('reports.getOpiu', () => {
    it('calls with required date params', async () => {
        const spy = mockFetch({ rows: [] });
        const api = makeApi();
        await api.getOpiu('2025-01-01', '2025-01-31');
        const [url] = spy.mock.calls[0];
        expect(url).toContain('date_from=2025-01-01');
        expect(url).toContain('date_to=2025-01-31');
    });

    it('URL-encodes brand and article when provided', async () => {
        const spy = mockFetch({ rows: [] });
        const api = makeApi();
        await api.getOpiu('2025-01-01', '2025-01-31', 'My Brand', 'ART-123');
        const [url] = spy.mock.calls[0];
        expect(url).toContain('brand=My+Brand');
        expect(url).toContain('article=ART-123');
    });
});

describe('reports.getWbBdr', () => {
    it('calls /api/v1/reports/wb_bdr with date params', async () => {
        const spy = mockFetch({});
        const api = makeApi();
        await api.getWbBdr('2025-01-01', '2025-01-31');
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/reports/wb_bdr');
        expect(url).toContain('date_from=2025-01-01');
    });

    it('includes group_by when provided', async () => {
        const spy = mockFetch({});
        const api = makeApi();
        await api.getWbBdr('2025-01-01', '2025-01-31', undefined, undefined, 'brand');
        const [url] = spy.mock.calls[0];
        expect(url).toContain('group_by=brand');
    });
});

describe('reports.getCostHistory', () => {
    it('calls without params if none provided', async () => {
        const spy = mockFetch({ items: [] });
        const api = makeApi();
        await api.getCostHistory();
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/reports/cost_history');
        expect(url).not.toContain('?');
    });

    it('adds article and brand as query params', async () => {
        const spy = mockFetch({ items: [] });
        const api = makeApi();
        await api.getCostHistory('ART001', 'Nike');
        const [url] = spy.mock.calls[0];
        expect(url).toContain('article=ART001');
        expect(url).toContain('brand=Nike');
    });
});

describe('reports.getCostDna', () => {
    it('uses period_days=30 as default when no dates given', async () => {
        const spy = mockFetch({ categories: [] });
        const api = makeApi();
        await api.getCostDna();
        const [url] = spy.mock.calls[0];
        expect(url).toContain('period_days=30');
        expect(url).not.toContain('date_from');
    });

    it('uses date_from and date_to when both provided', async () => {
        const spy = mockFetch({ categories: [] });
        const api = makeApi();
        await api.getCostDna({ dateFrom: '2025-01-01', dateTo: '2025-01-31' });
        const [url] = spy.mock.calls[0];
        expect(url).toContain('date_from=2025-01-01');
        expect(url).toContain('date_to=2025-01-31');
        expect(url).not.toContain('period_days');
    });
});

describe('reports.uploadOrderCities', () => {
    it('calls uploadFormData with FormData containing the file', async () => {
        const api = makeApi();
        const uploadSpy = vi.spyOn(api.client, 'uploadFormData').mockResolvedValue({
            ok: true, total_mappings: 100, affected_rows: 50,
        });

        const file = new File(['data'], 'cities.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
        await api.uploadOrderCities(file);

        expect(uploadSpy).toHaveBeenCalledOnce();
        const [path, formData] = uploadSpy.mock.calls[0];
        expect(path).toBe('/api/upload-order-cities');
        expect(formData.get('file')).toBe(file);
    });
});

describe('reports.getStockAnalytics', () => {
    it('includes trend_days param', async () => {
        const spy = mockFetch({ items: [] });
        const api = makeApi();
        await api.getStockAnalytics(14);
        const [url] = spy.mock.calls[0];
        expect(url).toContain('trend_days=14');
    });

    it('includes optional brand and article when provided', async () => {
        const spy = mockFetch({});
        const api = makeApi();
        await api.getStockAnalytics(7, 'Electronics', 'Nike', 'ART-1');
        const [url] = spy.mock.calls[0];
        expect(url).toContain('subject=Electronics');
        expect(url).toContain('brand=Nike');
        expect(url).toContain('article=ART-1');
    });
});

function makeClient() {
    const requestMock = vi.fn().mockResolvedValue({});
    const client = { request: requestMock } as unknown as ApiClient;
    const methods = addReportMethods(client);
    return { methods, requestMock };
}

function capturedUrl(mock: ReturnType<typeof vi.fn>): string {
    return mock.mock.calls[0][1] as string;
}

describe('getOpiu URL encoding', () => {
    it('encodes brand with ampersand (H&M)', () => {
        const { methods, requestMock } = makeClient();
        methods.getOpiu('2024-01-01', '2024-01-31', 'H&M');
        const url = capturedUrl(requestMock);
        expect(url).toContain('brand=H%26M');
        expect(url).not.toContain('brand=H&M');
    });

    it('encodes article with space', () => {
        const { methods, requestMock } = makeClient();
        methods.getOpiu('2024-01-01', '2024-01-31', undefined, 'ABC 123');
        const url = capturedUrl(requestMock);
        // URLSearchParams encodes space as + (application/x-www-form-urlencoded)
        const decoded = decodeURIComponent(url.replace(/\+/g, ' '));
        expect(decoded).toContain('article=ABC 123');
    });

    it('encodes brand with plus sign (C++)', () => {
        const { methods, requestMock } = makeClient();
        methods.getOpiu('2024-01-01', '2024-01-31', 'C++');
        const url = capturedUrl(requestMock);
        // + must be encoded so it is not misinterpreted as a space on the server
        expect(url).not.toMatch(/brand=C\+\+(?:&|$)/);
        expect(url).toContain('brand=C%2B%2B');
    });

    it('omits optional params when not provided', () => {
        const { methods, requestMock } = makeClient();
        methods.getOpiu('2024-01-01', '2024-01-31');
        const url = capturedUrl(requestMock);
        expect(url).not.toContain('brand=');
        expect(url).not.toContain('article=');
        expect(url).toContain('date_from=2024-01-01');
        expect(url).toContain('date_to=2024-01-31');
    });
});

describe('getWbBdr URL encoding', () => {
    it('encodes brand with ampersand (H&M)', () => {
        const { methods, requestMock } = makeClient();
        methods.getWbBdr('2024-01-01', '2024-01-31', 'H&M');
        const url = capturedUrl(requestMock);
        expect(url).toContain('brand=H%26M');
        expect(url).not.toContain('brand=H&M');
    });

    it('encodes article with space', () => {
        const { methods, requestMock } = makeClient();
        methods.getWbBdr('2024-01-01', '2024-01-31', undefined, 'ABC 123');
        const url = capturedUrl(requestMock);
        const decoded = decodeURIComponent(url.replace(/\+/g, ' '));
        expect(decoded).toContain('article=ABC 123');
    });

    it('encodes brand with plus sign (C++)', () => {
        const { methods, requestMock } = makeClient();
        methods.getWbBdr('2024-01-01', '2024-01-31', 'C++');
        const url = capturedUrl(requestMock);
        expect(url).toContain('brand=C%2B%2B');
    });

    it('includes group_by when provided', () => {
        const { methods, requestMock } = makeClient();
        methods.getWbBdr('2024-01-01', '2024-01-31', undefined, undefined, 'week');
        const url = capturedUrl(requestMock);
        expect(url).toContain('group_by=week');
    });
});

describe('getOrderGeography URL encoding', () => {
    it('encodes brand with ampersand (H&M)', () => {
        const { methods, requestMock } = makeClient();
        methods.getOrderGeography('2024-01-01', '2024-01-31', 'H&M');
        const url = capturedUrl(requestMock);
        expect(url).toContain('brand=H%26M');
        expect(url).not.toContain('brand=H&M');
    });

    it('encodes article with space', () => {
        const { methods, requestMock } = makeClient();
        methods.getOrderGeography('2024-01-01', '2024-01-31', undefined, undefined, 'ABC 123');
        const url = capturedUrl(requestMock);
        const decoded = decodeURIComponent(url.replace(/\+/g, ' '));
        expect(decoded).toContain('article=ABC 123');
    });

    it('encodes brand with plus sign (C++)', () => {
        const { methods, requestMock } = makeClient();
        methods.getOrderGeography('2024-01-01', '2024-01-31', 'C++');
        const url = capturedUrl(requestMock);
        expect(url).toContain('brand=C%2B%2B');
    });

    it('encodes category with special chars', () => {
        const { methods, requestMock } = makeClient();
        methods.getOrderGeography('2024-01-01', '2024-01-31', undefined, 'Toys & Games');
        const url = capturedUrl(requestMock);
        expect(url).toContain('category=Toys+%26+Games');
    });
});

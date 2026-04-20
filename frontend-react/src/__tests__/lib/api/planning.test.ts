import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addPlanningMethods } from '@/lib/api/planning';

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
    return { client, ...addPlanningMethods(client) };
}

function mockFetch(body: unknown) {
    return vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: true, status: 200, json: async () => body,
    } as Response);
}

afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

describe('planning.getPlanningOrders', () => {
    it('GETs /api/v1/planning/orders', async () => {
        const spy = mockFetch([{ id: 1, order_no: 'ORD-001' }]);
        const api = makeApi();
        const result = await api.getPlanningOrders();
        expect(result).toHaveLength(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/planning/orders');
    });
});

describe('planning.createPlanningOrder', () => {
    it('POSTs to /api/v1/planning/orders', async () => {
        const spy = mockFetch({ id: 2, order_no: 'ORD-002' });
        const api = makeApi();
        await api.createPlanningOrder({ order_no: 2, order_amount: 50000 });
        expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
        const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
        expect(body.order_no).toBe(2);
    });
});

describe('planning.deletePlanningOrder', () => {
    it('DELETEs by id', async () => {
        const spy = mockFetch({ message: 'deleted' });
        const api = makeApi();
        await api.deletePlanningOrder(5);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/planning/orders/5');
        expect((init as RequestInit).method).toBe('DELETE');
    });
});

describe('planning.getPlanningPayments', () => {
    it('GETs /api/v1/planning/payments', async () => {
        const spy = mockFetch([{ id: 1, amount_rub: 10000, planned_date: '2025-06-01' }]);
        const api = makeApi();
        const result = await api.getPlanningPayments();
        expect(result).toHaveLength(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/planning/payments');
    });
});

describe('planning.markPaymentPaid', () => {
    it('POSTs to mark_paid endpoint', async () => {
        const spy = mockFetch({ message: 'ok' });
        const api = makeApi();
        await api.markPaymentPaid(7);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/planning/payments/7/mark_paid');
        expect((init as RequestInit).method).toBe('POST');
    });
});

describe('planning.getPlanningIncomes', () => {
    it('GETs /api/v1/planning/incomes', async () => {
        const spy = mockFetch([{ id: 1, amount: 100000 }]);
        const api = makeApi();
        await api.getPlanningIncomes();
        expect(spy.mock.calls[0][0]).toContain('/api/v1/planning/incomes');
    });
});

describe('planning.refreshWbForecast', () => {
    it('POSTs with default trend_days=7', async () => {
        const spy = mockFetch({ ok: true, daily_avg: 5000, weekly_total: 35000, weekday_pattern: {}, created: 7, forecast_days: 7 });
        const api = makeApi();
        const result = await api.refreshWbForecast();
        expect(result.ok).toBe(true);
        const [url] = spy.mock.calls[0];
        expect(url).toContain('trend_days=7');
    });

    it('uses custom trend_days', async () => {
        const spy = mockFetch({ ok: true, daily_avg: 4000, weekly_total: 28000, weekday_pattern: {}, created: 14, forecast_days: 14 });
        const api = makeApi();
        await api.refreshWbForecast(14);
        expect(spy.mock.calls[0][0]).toContain('trend_days=14');
    });
});

describe('planning.uploadWbPayouts', () => {
    it('calls uploadFormData with the file', async () => {
        const api = makeApi();
        const uploadSpy = vi.spyOn(api.client, 'uploadFormData').mockResolvedValue({
            ok: true, created: 3, updated: 1, skipped: 0,
        });

        const file = new File(['data'], 'payouts.xlsx');
        const result = await api.uploadWbPayouts(file);

        expect(result.created).toBe(3);
        const [path, formData] = uploadSpy.mock.calls[0];
        expect(path).toBe('/api/v1/planning/wb_payouts/upload');
        expect(formData.get('file')).toBe(file);
    });
});

describe('planning.uploadFtsPdf', () => {
    it('calls uploadFormData for FTS PDF', async () => {
        const api = makeApi();
        const uploadSpy = vi.spyOn(api.client, 'uploadFormData').mockResolvedValue({
            ok: true, created: 2, skipped: 0, parsed: [],
        });

        const file = new File(['pdf'], 'fts.pdf', { type: 'application/pdf' });
        await api.uploadFtsPdf(file);

        const [path, formData] = uploadSpy.mock.calls[0];
        expect(path).toBe('/api/v1/planning/customs_dt/upload_fts');
        expect(formData.get('file')).toBe(file);
    });
});

describe('planning.getCashflowDaily', () => {
    it('GETs /api/v1/planning/cashflow_daily', async () => {
        const spy = mockFetch([{ date: '2025-01-01', income: 100000, expense: 50000, balance: 50000 }]);
        const api = makeApi();
        const result = await api.getCashflowDaily();
        expect(result).toHaveLength(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/planning/cashflow_daily');
    });
});

describe('planning.getBrandPlans', () => {
    it('GETs brand plans with year param', async () => {
        const spy = mockFetch([{ id: 1, brand: 'Nike', year: 2025, month: 1, plan_amount: 500000 }]);
        const api = makeApi();
        const result = await api.getBrandPlans(2025);
        expect(result).toHaveLength(1);
        const [url] = spy.mock.calls[0];
        expect(url).toContain('year=2025');
    });
});

describe('planning.getPlanFactDaily', () => {
    it('builds URL with brand, year, month', async () => {
        const spy = mockFetch({ daily: [], totals: {} });
        const api = makeApi();
        await api.getPlanFactDaily('Nike', 2025, 1);
        const [url] = spy.mock.calls[0];
        expect(url).toContain('brand=Nike');
        expect(url).toContain('year=2025');
        expect(url).toContain('month=1');
    });

    it('includes year_to and month_to when different from from', async () => {
        const spy = mockFetch({ daily: [], totals: {} });
        const api = makeApi();
        await api.getPlanFactDaily('Nike', 2025, 1, 2025, 3);
        const [url] = spy.mock.calls[0];
        expect(url).toContain('year_to=2025');
        expect(url).toContain('month_to=3');
    });

    it('omits year_to and month_to when same as from', async () => {
        const spy = mockFetch({ daily: [], totals: {} });
        const api = makeApi();
        await api.getPlanFactDaily('Nike', 2025, 1, 2025, 1);
        const [url] = spy.mock.calls[0];
        expect(url).not.toContain('year_to');
        expect(url).not.toContain('month_to');
    });
});

describe('planning.getCandidateTransactions', () => {
    it('GETs without account param by default', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getCandidateTransactions();
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/planning/candidate_transactions');
        expect(url).not.toContain('account=');
    });

    it('includes account param when provided', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getCandidateTransactions('RUB-1');
        const [url] = spy.mock.calls[0];
        expect(url).toContain('account=RUB-1');
    });
});

describe('planning.createFactLink', () => {
    it('POSTs fact link data', async () => {
        const spy = mockFetch({ ok: true, id: 10 });
        const api = makeApi();
        await api.createFactLink({ payment_id: 1, txn_id: 'txn-001', amount_rub: 5000 });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/planning/fact_links');
        expect((init as RequestInit).method).toBe('POST');
    });
});

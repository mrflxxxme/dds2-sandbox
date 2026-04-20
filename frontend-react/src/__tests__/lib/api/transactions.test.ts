import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addTransactionMethods } from '@/lib/api/transactions';

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
    return { client, ...addTransactionMethods(client) };
}

function mockFetch(body: unknown) {
    return vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: true, status: 200, json: async () => body,
    } as Response);
}

afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

describe('transactions.searchTransactions', () => {
    it('POSTs to /api/v1/transactions/search', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.searchTransactions({ date_from: '2025-01-01', date_to: '2025-01-31' });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/transactions/search');
        expect((init as RequestInit).method).toBe('POST');
    });

    it('sends params as JSON body', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        const params = { date_from: '2025-01-01', account: 'RUB-1' };
        await api.searchTransactions(params);
        const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
        expect(body).toEqual(params);
    });

    it('sends empty object when called with no args', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.searchTransactions();
        const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
        expect(body).toEqual({});
    });
});

describe('transactions.getUnassigned', () => {
    it('GETs /api/v1/transactions/unassigned with default limit 500', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getUnassigned();
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/transactions/unassigned');
        expect(url).toContain('limit=500');
    });

    it('accepts custom limit', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getUnassigned(100);
        const [url] = spy.mock.calls[0];
        expect(url).toContain('limit=100');
    });
});

describe('transactions.getUnassignedGrouped', () => {
    it('GETs /api/v1/transactions/unassigned_grouped', async () => {
        const spy = mockFetch([{ cp_key: 'WB', count: 5, total: 100000 }]);
        const api = makeApi();
        const result = await api.getUnassignedGrouped();
        expect(result).toHaveLength(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/transactions/unassigned_grouped');
    });
});

describe('transactions.assignCategory', () => {
    it('POSTs category assignment data', async () => {
        const spy = mockFetch({ message: 'ok' });
        const api = makeApi();
        await api.assignCategory({
            txn_id: 'txn-001',
            cat_lvl1: 'Расходы',
            cat_lvl2: 'Маркетинг',
        });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/transactions/assign_category');
        expect((init as RequestInit).method).toBe('POST');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.txn_id).toBe('txn-001');
        expect(body.cat_lvl1).toBe('Расходы');
        expect(body.cat_lvl2).toBe('Маркетинг');
    });
});

describe('transactions.assignCategoryBulk', () => {
    it('POSTs to /api/v1/transactions/assign_category_bulk', async () => {
        const spy = mockFetch({ updated: 15 });
        const api = makeApi();
        const result = await api.assignCategoryBulk({
            cp_key: 'WB',
            counterparty: 'Wildberries',
            cat_lvl1: 'Расходы',
        });
        expect(result.updated).toBe(15);
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/transactions/assign_category_bulk');
    });
});

describe('transactions.assignCategoryByIds', () => {
    it('POSTs txn_ids array with category', async () => {
        const spy = mockFetch({ updated: 3 });
        const api = makeApi();
        await api.assignCategoryByIds({ txn_ids: ['t1', 't2', 't3'], cat_lvl1: 'Доходы' });
        const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
        expect(body.txn_ids).toEqual(['t1', 't2', 't3']);
        expect(body.cat_lvl1).toBe('Доходы');
    });
});

describe('transactions.auto-categorize flow', () => {
    it('getAutoCategorizeRules GETs the rules endpoint', async () => {
        const spy = mockFetch([{ id: 1, keyword: 'WB', direction: 'both', cat_lvl1: 'Расходы', cat_lvl2: null, priority: 1, is_active: true }]);
        const api = makeApi();
        const rules = await api.getAutoCategorizeRules();
        expect(rules).toHaveLength(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/transactions/auto_categorize/rules');
    });

    it('createAutoCategorizeRule POSTs new rule', async () => {
        const spy = mockFetch({ id: 2, keyword: 'Маркет', direction: 'expense', cat_lvl1: 'Реклама', cat_lvl2: null });
        const api = makeApi();
        await api.createAutoCategorizeRule({ keyword: 'Маркет', cat_lvl1: 'Реклама', direction: 'expense' });
        expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
    });

    it('deleteAutoCategorizeRule DELETEs by id', async () => {
        const spy = mockFetch({ ok: true });
        const api = makeApi();
        await api.deleteAutoCategorizeRule(5);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/rules/5');
        expect((init as RequestInit).method).toBe('DELETE');
    });

    it('previewAutoCategorize GETs the preview endpoint', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.previewAutoCategorize();
        expect(spy.mock.calls[0][0]).toContain('/api/v1/transactions/auto_categorize/preview');
    });

    it('applyAutoCategorize POSTs to the apply endpoint', async () => {
        const spy = mockFetch({ ok: true, updated: 10, details: [] });
        const api = makeApi();
        const result = await api.applyAutoCategorize();
        expect(result.updated).toBe(10);
        expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
    });
});

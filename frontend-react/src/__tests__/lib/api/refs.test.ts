import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addRefMethods } from '@/lib/api/refs';

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
    return { client, ...addRefMethods(client) };
}

function mockFetch(body: unknown) {
    return vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: true, status: 200, json: async () => body,
    } as Response);
}

afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

describe('refs.getAccounts', () => {
    it('GETs /api/v1/refs/accounts', async () => {
        const spy = mockFetch([{ id: 1, name: 'Сбер', currency: 'RUB', account_no: '123' }]);
        const api = makeApi();
        const result = await api.getAccounts();
        expect(result).toHaveLength(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/refs/accounts');
    });
});

describe('refs.upsertAccount', () => {
    it('POSTs account data', async () => {
        const spy = mockFetch({ id: 2, name: 'Тинькофф', currency: 'RUB' });
        const api = makeApi();
        await api.upsertAccount({ account: 'Тинькофф', currency: 'RUB' });
        expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
        expect(spy.mock.calls[0][0]).toContain('/api/v1/refs/accounts');
    });
});

describe('refs.deleteAccount', () => {
    it('DELETEs by id', async () => {
        const spy = mockFetch({ message: 'deleted' });
        const api = makeApi();
        await api.deleteAccount(3);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/refs/accounts/3');
        expect((init as RequestInit).method).toBe('DELETE');
    });
});

describe('refs.getCpCategories', () => {
    it('GETs /api/v1/refs/cp_categories', async () => {
        const spy = mockFetch([{ id: 1, cp_key: 'WB', cat_lvl1: 'Расходы', cat_lvl2: 'Комиссия' }]);
        const api = makeApi();
        await api.getCpCategories();
        expect(spy.mock.calls[0][0]).toContain('/api/v1/refs/cp_categories');
    });
});

describe('refs.getCategoryRef', () => {
    it('GETs /api/v1/refs/categories', async () => {
        const spy = mockFetch([{ id: 1, cat_lvl1: 'Расходы', cat_lvl2: 'Маркетинг', flow: 'expense' }]);
        const api = makeApi();
        await api.getCategoryRef();
        expect(spy.mock.calls[0][0]).toContain('/api/v1/refs/categories');
    });
});

describe('refs.addCategoryRef', () => {
    it('POSTs category ref data', async () => {
        const spy = mockFetch({ id: 2, cat_lvl1: 'Доходы', cat_lvl2: 'WB', flow: 'income' });
        const api = makeApi();
        await api.addCategoryRef({ cat_lvl1: 'Доходы', cat_lvl2: 'WB', direction: 'income' });
        expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
    });
});

describe('refs.getExcludedWarehouses / setExcludedWarehouses', () => {
    it('GETs excluded warehouse list', async () => {
        const spy = mockFetch(['Коледино', 'Электросталь']);
        const api = makeApi();
        const result = await api.getExcludedWarehouses();
        expect(result).toContain('Коледино');
        expect(spy.mock.calls[0][0]).toContain('/api/v1/refs/excluded-warehouses');
    });

    it('PUTs new exclusion list', async () => {
        const spy = mockFetch({ ok: true, excluded: ['Коледино'] });
        const api = makeApi();
        const result = await api.setExcludedWarehouses(['Коледино']);
        expect(result.excluded).toEqual(['Коледино']);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/refs/excluded-warehouses');
        expect((init as RequestInit).method).toBe('PUT');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.warehouses).toEqual(['Коледино']);
    });
});

describe('refs.getProductTags', () => {
    it('GETs /api/v1/refs/tags', async () => {
        const spy = mockFetch([{ id: 1, name: 'Бестселлер', color: '#ff0000' }]);
        const api = makeApi();
        await api.getProductTags();
        expect(spy.mock.calls[0][0]).toContain('/api/v1/refs/tags');
    });
});

describe('refs.updateProductTagMapping', () => {
    it('POSTs mapping payload', async () => {
        const spy = mockFetch({ message: 'ok' });
        const api = makeApi();
        await api.updateProductTagMapping({ nm_ids: [100, 200], add_tags: [1], remove_tags: [] });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/refs/tags/mapping');
        expect((init as RequestInit).method).toBe('POST');
    });
});

describe('refs.setProductStatus', () => {
    it('PATCHes product status', async () => {
        const spy = mockFetch({ message: 'ok' });
        const api = makeApi();
        await api.setProductStatus({ nm_id: 12345, status: 'active' });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/refs/product-statuses');
        expect((init as RequestInit).method).toBe('PATCH');
    });
});

describe('refs.bulkSetProductStatus', () => {
    it('POSTs bulk status update', async () => {
        const spy = mockFetch({ message: 'ok' });
        const api = makeApi();
        await api.bulkSetProductStatus({ nm_ids: [111, 222], status: 'inactive' });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/refs/product-statuses/bulk');
        expect((init as RequestInit).method).toBe('POST');
    });
});

describe('refs.setImtAlias', () => {
    it('PATCHes IMT alias', async () => {
        const spy = mockFetch({ message: 'ok' });
        const api = makeApi();
        await api.setImtAlias({ imt_id: 9999, name: 'Зимний ассортимент' });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/refs/imt-aliases');
        expect((init as RequestInit).method).toBe('PATCH');
    });
});

describe('refs.getOpeningBalances', () => {
    it('GETs opening balances', async () => {
        const spy = mockFetch([{ id: 1, account: 'RUB-1', currency: 'RUB', amount: 100000, date: '2025-01-01' }]);
        const api = makeApi();
        const result = await api.getOpeningBalances();
        expect(result).toHaveLength(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/refs/opening_balances');
    });
});

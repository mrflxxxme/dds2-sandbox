import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addCounterpartyMethods } from '@/lib/api/counterparty';

const localStorageMock = (() => {
    let store: Record<string, string> = {};
    return {
        getItem: (key: string) => store[key] ?? null,
        setItem: (key: string, value: string) => { store[key] = value; },
        removeItem: (key: string) => { delete store[key]; },
        clear: () => { store = {}; },
    };
})();

Object.defineProperty(global, 'window', {
    value: { document: {}, location: { href: '' } },
    writable: true,
});
Object.defineProperty(global, 'localStorage', { value: localStorageMock, writable: true });

function makeApi() {
    const client = new ApiClient();
    return { client, ...addCounterpartyMethods(client) };
}

describe('counterparty.listCounterparties', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('GETs without query string when no params provided', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ items: [], total: 0 }),
        } as Response);

        const api = makeApi();
        await api.listCounterparties();

        expect(fetchSpy).toHaveBeenCalledOnce();
        const [url] = fetchSpy.mock.calls[0];
        expect(url).toContain('/api/v1/counterparties');
        // No '?' when no params
        expect(String(url).endsWith('/counterparties')).toBe(true);
    });

    it('serializes all filters via URLSearchParams', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ items: [], total: 0 }),
        } as Response);

        const api = makeApi();
        await api.listCounterparties({
            type: 'SUPPLIER',
            q: 'ООО Рога',
            active_only: true,
            limit: 50,
            offset: 100,
        });

        const [url] = fetchSpy.mock.calls[0];
        const urlStr = String(url);
        expect(urlStr).toContain('type=SUPPLIER');
        // URLSearchParams encodes space as + and cyrillic as %XX
        expect(urlStr).toContain('q=');
        expect(urlStr).toContain('active_only=true');
        expect(urlStr).toContain('limit=50');
        expect(urlStr).toContain('offset=100');
    });

    it('omits falsy/missing params', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ items: [], total: 0 }),
        } as Response);

        const api = makeApi();
        await api.listCounterparties({ limit: 10 });

        const [url] = fetchSpy.mock.calls[0];
        const urlStr = String(url);
        expect(urlStr).toContain('limit=10');
        expect(urlStr).not.toContain('type=');
        expect(urlStr).not.toContain('q=');
        expect(urlStr).not.toContain('active_only=');
    });

    it('returns paginated list response', async () => {
        const mockRes = {
            items: [
                { id: 1, inn: '7704217370', name: 'OZON', primary_type: 'MARKETPLACE', secondary_types: null, kpp: null, contract_number: null, created_by_import: true, created_at: '2025-01-01T00:00:00', updated_at: null },
            ],
            total: 1,
        };
        vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => mockRes,
        } as Response);

        const api = makeApi();
        const res = await api.listCounterparties();

        expect(res.items).toHaveLength(1);
        expect(res.total).toBe(1);
        expect(res.items[0].inn).toBe('7704217370');
    });
});

describe('counterparty.getCounterparty', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('GETs /api/v1/counterparties/{id} with no date params', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({
                id: 42, name: 'Test', primary_type: 'OTHER',
                secondary_types: null, inn: null, kpp: null, contract_number: null,
                created_by_import: false, created_at: null, updated_at: null,
                stats_rub: { in_sum: 0, out_sum: 0, net: 0, tx_count: 0 },
                stats_cny: { in_sum: 0, out_sum: 0, net: 0, tx_count: 0 },
                linked_warehouses: [], linked_suppliers: [], active_loans: [], docs_count: 0,
            }),
        } as Response);

        const api = makeApi();
        await api.getCounterparty(42);

        const [url] = fetchSpy.mock.calls[0];
        expect(String(url)).toContain('/api/v1/counterparties/42');
    });

    it('passes date_from and date_to as query params', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({
                id: 1, name: 'X', primary_type: 'OTHER', secondary_types: null,
                inn: null, kpp: null, contract_number: null, created_by_import: false,
                created_at: null, updated_at: null,
                stats_rub: { in_sum: 0, out_sum: 0, net: 0, tx_count: 0 },
                stats_cny: { in_sum: 0, out_sum: 0, net: 0, tx_count: 0 },
                linked_warehouses: [], linked_suppliers: [], active_loans: [], docs_count: 0,
            }),
        } as Response);

        const api = makeApi();
        await api.getCounterparty(1, { date_from: '2025-01-01', date_to: '2025-12-31' });

        const [url] = fetchSpy.mock.calls[0];
        const urlStr = String(url);
        expect(urlStr).toContain('date_from=2025-01-01');
        expect(urlStr).toContain('date_to=2025-12-31');
    });
});

describe('counterparty.getCounterpartyTransactions', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('GETs /api/v1/counterparties/{id}/transactions', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ items: [], total: 0 }),
        } as Response);

        const api = makeApi();
        await api.getCounterpartyTransactions(42);

        const [url] = fetchSpy.mock.calls[0];
        expect(String(url)).toContain('/api/v1/counterparties/42/transactions');
    });

    it('serializes all filters via URLSearchParams', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ items: [], total: 0 }),
        } as Response);

        const api = makeApi();
        await api.getCounterpartyTransactions(7, {
            date_from: '2026-01-01',
            date_to: '2026-12-31',
            currency: 'RUB',
            limit: 500,
            offset: 100,
        });

        const [url] = fetchSpy.mock.calls[0];
        const urlStr = String(url);
        expect(urlStr).toContain('date_from=2026-01-01');
        expect(urlStr).toContain('date_to=2026-12-31');
        expect(urlStr).toContain('currency=RUB');
        expect(urlStr).toContain('limit=500');
        expect(urlStr).toContain('offset=100');
    });

    it('returns items and total', async () => {
        vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({
                items: [
                    {
                        id: 1, date: '2026-03-12T00:00:00', account: '40702810000000000001',
                        currency: 'RUB', income: 1000, expense: 0, purpose: 'Test',
                        event_type2: null, loan_payment_type: null, contract_number: null,
                    },
                ],
                total: 83,
            }),
        } as Response);

        const api = makeApi();
        const res = await api.getCounterpartyTransactions(1);
        expect(res.items).toHaveLength(1);
        expect(res.total).toBe(83);
        expect(res.items[0].purpose).toBe('Test');
    });
});

describe('counterparty.createCounterparty', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('POSTs counterparty data as JSON', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 201,
            json: async () => ({ id: 10, name: 'Новый', primary_type: 'SUPPLIER' }),
        } as Response);

        const api = makeApi();
        await api.createCounterparty({
            name: 'Новый',
            primary_type: 'SUPPLIER',
            inn: '1234567890',
        });

        const [url, init] = fetchSpy.mock.calls[0];
        expect(String(url)).toContain('/api/v1/counterparties');
        expect((init as RequestInit).method).toBe('POST');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.name).toBe('Новый');
        expect(body.primary_type).toBe('SUPPLIER');
        expect(body.inn).toBe('1234567890');
    });
});

describe('counterparty.updateCounterparty', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('PATCHes /api/v1/counterparties/{id} with partial data', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ id: 5, name: 'Upd' }),
        } as Response);

        const api = makeApi();
        await api.updateCounterparty(5, { notes: 'memo' });

        const [url, init] = fetchSpy.mock.calls[0];
        expect(String(url)).toContain('/api/v1/counterparties/5');
        expect((init as RequestInit).method).toBe('PATCH');
        expect(JSON.parse((init as RequestInit).body as string)).toEqual({ notes: 'memo' });
    });
});

describe('counterparty.deleteCounterparty', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('DELETEs /api/v1/counterparties/{id}', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 204,
            json: async () => ({}),
        } as Response);

        const api = makeApi();
        await api.deleteCounterparty(7);

        const [url, init] = fetchSpy.mock.calls[0];
        expect(String(url)).toContain('/api/v1/counterparties/7');
        expect((init as RequestInit).method).toBe('DELETE');
    });
});

describe('counterparty documents', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('listCounterpartyDocuments GETs correct path', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => [],
        } as Response);

        const api = makeApi();
        await api.listCounterpartyDocuments(3);

        const [url] = fetchSpy.mock.calls[0];
        expect(String(url)).toContain('/api/v1/counterparties/3/documents');
    });

    it('deleteCounterpartyDocument DELETEs correct path', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 204,
            json: async () => ({}),
        } as Response);

        const api = makeApi();
        await api.deleteCounterpartyDocument(3, 99);

        const [url, init] = fetchSpy.mock.calls[0];
        expect(String(url)).toContain('/api/v1/counterparties/3/documents/99');
        expect((init as RequestInit).method).toBe('DELETE');
    });

    it('uploadCounterpartyDocument uses FormData', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 201,
            json: async () => ({ id: 1, counterparty_id: 3, doc_type: 'CONTRACT', original_filename: 't.pdf', file_size: 100, mime_type: 'application/pdf', uploaded_at: '2025-01-01' }),
        } as Response);

        const api = makeApi();
        const file = new File(['hello'], 'test.pdf', { type: 'application/pdf' });
        await api.uploadCounterpartyDocument(3, file, 'CONTRACT');

        const [url, init] = fetchSpy.mock.calls[0];
        expect(String(url)).toContain('/api/v1/counterparties/3/documents');
        expect((init as RequestInit).method).toBe('POST');
        expect((init as RequestInit).body).toBeInstanceOf(FormData);
    });

    it('downloadCounterpartyDocument GETs the download endpoint and returns a Blob', async () => {
        const blob = new Blob(['pdf-bytes'], { type: 'application/pdf' });
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            blob: async () => blob,
        } as unknown as Response);

        const api = makeApi();
        const out = await api.downloadCounterpartyDocument(3, 99);

        const [url] = fetchSpy.mock.calls[0];
        expect(String(url)).toContain('/api/v1/counterparties/3/documents/99/download');
        expect(out).toBe(blob);
    });
});

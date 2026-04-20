import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addIntegrationMethods } from '@/lib/api/integrations';

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
    return { client, ...addIntegrationMethods(client) };
}

function mockFetch(body: unknown) {
    return vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: true, status: 200, json: async () => body,
    } as Response);
}

afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

describe('integrations.getIntegrationKeys', () => {
    it('GETs /api/v1/integrations/keys', async () => {
        const spy = mockFetch([
            { id: 1, service: 'wb', label: 'WB Main', key_preview: 'abc***xyz', is_active: true, created_at: '2025-01-01', last_sync_at: null },
        ]);
        const api = makeApi();
        const result = await api.getIntegrationKeys();
        expect(result).toHaveLength(1);
        expect(result[0].service).toBe('wb');
        expect(spy.mock.calls[0][0]).toContain('/api/v1/integrations/keys');
    });
});

describe('integrations.addIntegrationKey', () => {
    it('POSTs service, api_key, and optional label', async () => {
        const spy = mockFetch({ id: 2, service: 'wb', label: 'Test Key', key_preview: 'xxx***yyy', is_active: true, created_at: '2025-01-01', last_sync_at: null });
        const api = makeApi();
        await api.addIntegrationKey('wb', 'secret-wb-key', 'Test Key');
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/integrations/keys');
        expect((init as RequestInit).method).toBe('POST');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.service).toBe('wb');
        expect(body.api_key).toBe('secret-wb-key');
        expect(body.label).toBe('Test Key');
    });

    it('sends undefined label when not provided', async () => {
        const spy = mockFetch({ id: 3, service: 'wb', label: null, key_preview: 'abc', is_active: true, created_at: '2025-01-01', last_sync_at: null });
        const api = makeApi();
        await api.addIntegrationKey('wb', 'key-without-label');
        const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
        // label not provided -> undefined -> serialised as missing key in JSON
        expect(Object.keys(body)).not.toContain('label');
    });
});

describe('integrations.deleteIntegrationKey', () => {
    it('DELETEs by id', async () => {
        const spy = mockFetch({ message: 'deleted' });
        const api = makeApi();
        await api.deleteIntegrationKey(5);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/integrations/keys/5');
        expect((init as RequestInit).method).toBe('DELETE');
    });
});

describe('integrations.syncWb', () => {
    it('POSTs to sync WB with keyId and date_from', async () => {
        const spy = mockFetch({ ok: true, rows: 500 });
        const api = makeApi();
        const result = await api.syncWb(1, '2025-01-01');
        expect(result.ok).toBe(true);
        expect(result.rows).toBe(500);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/integrations/sync/wb/1');
        expect(url).toContain('date_from=2025-01-01');
        expect((init as RequestInit).method).toBe('POST');
    });
});

describe('integrations.getSyncLog', () => {
    it('GETs /api/v1/integrations/sync_log', async () => {
        const spy = mockFetch([
            {
                id: 1,
                service: 'wb',
                sync_type: 'funnel',
                status: 'success',
                started_at: '2025-01-01T10:00:00',
                finished_at: '2025-01-01T10:05:00',
                rows_fetched: 1000,
                rows_inserted: 950,
                error_msg: null,
            },
        ]);
        const api = makeApi();
        const result = await api.getSyncLog();
        expect(result).toHaveLength(1);
        expect(result[0].sync_type).toBe('funnel');
        expect(spy.mock.calls[0][0]).toContain('/api/v1/integrations/sync_log');
    });
});

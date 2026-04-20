import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addMonitoringMethods } from '@/lib/api/monitoring';

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
    return { client, ...addMonitoringMethods(client) };
}

function mockFetch(body: unknown) {
    return vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: true, status: 200, json: async () => body,
    } as Response);
}

afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

describe('monitoring.getMonitoringOverview', () => {
    it('GETs /api/v1/monitoring/overview', async () => {
        const spy = mockFetch({
            services: { wb: { status: 'ok', last_sync: '2025-01-01T00:00:00' } },
            alerts: [],
        });
        const api = makeApi();
        const result = await api.getMonitoringOverview();
        expect(result.services).toBeDefined();
        expect(spy.mock.calls[0][0]).toContain('/api/v1/monitoring/overview');
        expect((spy.mock.calls[0][1] as RequestInit).method).toBe('GET');
    });
});

describe('monitoring.getMonitoringSyncLog', () => {
    it('GETs /api/v1/monitoring/sync_log without params', async () => {
        const spy = mockFetch([
            { id: 1, service: 'wb', sync_type: 'funnel', status: 'success', started_at: '2025-01-01T10:00:00', finished_at: '2025-01-01T10:05:00', rows_fetched: 500, rows_inserted: 490, error_msg: null },
        ]);
        const api = makeApi();
        const result = await api.getMonitoringSyncLog();
        expect(result).toHaveLength(1);
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/monitoring/sync_log');
        // No query string when no params
        expect(url).not.toContain('?');
    });

    it('includes service filter when provided', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getMonitoringSyncLog({ service: 'wb' });
        const [url] = spy.mock.calls[0];
        expect(url).toContain('service=wb');
    });

    it('includes sync_type filter when provided', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getMonitoringSyncLog({ sync_type: 'funnel' });
        const [url] = spy.mock.calls[0];
        expect(url).toContain('sync_type=funnel');
    });

    it('includes status filter when provided', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getMonitoringSyncLog({ status: 'error' });
        const [url] = spy.mock.calls[0];
        expect(url).toContain('status=error');
    });

    it('includes limit and offset for pagination', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getMonitoringSyncLog({ limit: 50, offset: 100 });
        const [url] = spy.mock.calls[0];
        expect(url).toContain('limit=50');
        expect(url).toContain('offset=100');
    });

    it('combines multiple filters in query string', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getMonitoringSyncLog({ service: 'wb', status: 'success', limit: 20 });
        const [url] = spy.mock.calls[0];
        expect(url).toContain('service=wb');
        expect(url).toContain('status=success');
        expect(url).toContain('limit=20');
    });

    it('omits filters that are not provided', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getMonitoringSyncLog({ service: 'wb' });
        const [url] = spy.mock.calls[0];
        expect(url).not.toContain('sync_type=');
        expect(url).not.toContain('status=');
        expect(url).not.toContain('limit=');
        expect(url).not.toContain('offset=');
    });
});

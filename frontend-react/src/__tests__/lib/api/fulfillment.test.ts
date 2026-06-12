import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addFulfillmentMethods } from '@/lib/api/fulfillment';

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
    return { client, ...addFulfillmentMethods(client) };
}

function mockFetch(body: unknown) {
    return vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: true, status: 200, json: async () => body,
    } as Response);
}

afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

// ─── Requests list (kind / show_archived) ─────────────────────────────────

describe('fulfillment.getFulfillmentRequests', () => {
    it('GETs without query when no params', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getFulfillmentRequests(5);
        const [url] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/5/fulfillment/requests');
        expect(url).not.toContain('?');
    });

    it('includes kind when provided', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getFulfillmentRequests(5, 'assembly');
        expect(spy.mock.calls[0][0]).toContain('kind=assembly');
    });

    it('includes show_archived=true only when true', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getFulfillmentRequests(5, 'inbound', true);
        const [url] = spy.mock.calls[0];
        expect(url).toContain('kind=inbound');
        expect(url).toContain('show_archived=true');
    });

    it('omits show_archived when false', async () => {
        const spy = mockFetch([]);
        const api = makeApi();
        await api.getFulfillmentRequests(5, 'assembly', false);
        expect(spy.mock.calls[0][0]).not.toContain('show_archived');
    });
});

// ─── Link candidates ──────────────────────────────────────────────────────

describe('fulfillment.getFfLinkCandidates', () => {
    it('GETs link-candidates for the request', async () => {
        const spy = mockFetch({
            kind: 'assembly', ff_number: 'FF-1', ff_total_qty: 100,
            composition_available: true, candidates: [],
        });
        const api = makeApi();
        const r = await api.getFfLinkCandidates(5, 42);
        expect(r.composition_available).toBe(true);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/5/fulfillment/requests/42/link-candidates');
        expect((init as RequestInit).method).toBe('GET');
    });
});

// ─── Archive / unarchive ──────────────────────────────────────────────────

describe('fulfillment.archiveFulfillmentRequest / unarchiveFulfillmentRequest', () => {
    it('archive POSTs to /archive', async () => {
        const spy = mockFetch({ id: 42, local_archived: true });
        const api = makeApi();
        const r = await api.archiveFulfillmentRequest(5, 42);
        expect(r.local_archived).toBe(true);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/5/fulfillment/requests/42/archive');
        expect((init as RequestInit).method).toBe('POST');
    });

    it('unarchive DELETEs /archive', async () => {
        const spy = mockFetch({ id: 42, local_archived: false });
        const api = makeApi();
        const r = await api.unarchiveFulfillmentRequest(5, 42);
        expect(r.local_archived).toBe(false);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/5/fulfillment/requests/42/archive');
        expect((init as RequestInit).method).toBe('DELETE');
    });
});

// ─── Create assembly from FF ──────────────────────────────────────────────

describe('fulfillment.createAssemblyFromFf', () => {
    it('POSTs to /create-assembly and returns the result', async () => {
        const spy = mockFetch({
            request: { id: 42, assembly_request_id: 7 },
            assembly_request_id: 7,
            assembly_number: 'ASM-7',
            items_created: 3,
            skipped_barcodes: ['111'],
        });
        const api = makeApi();
        const r = await api.createAssemblyFromFf(5, 42);
        expect(r.assembly_number).toBe('ASM-7');
        expect(r.skipped_barcodes).toEqual(['111']);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/warehouse/5/fulfillment/requests/42/create-assembly');
        expect((init as RequestInit).method).toBe('POST');
    });
});

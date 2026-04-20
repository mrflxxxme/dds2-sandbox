import { describe, it, expect, vi, beforeEach } from 'vitest';
import { addSupplyChainMethods } from '@/lib/api/supply-chain';

// Minimal ApiClient stub
function makeApi(token: string | null = 'test-token', projectId: number | null = 42) {
    const api = {
        getToken: () => token,
        getProjectId: () => projectId,
        request: vi.fn(async () => ({})),
        uploadFormData: vi.fn(async () => ({})),
    };
    return api as any;
}

describe('downloadVehicleDocument', () => {
    beforeEach(() => {
        vi.unstubAllEnvs();
        vi.unstubAllGlobals();
        vi.stubEnv('NEXT_PUBLIC_API_URL', 'http://test-api');
    });

    it('uses module-level API_URL and sends auth headers', async () => {
        const mockBlob = new Blob(['data']);
        const mockFetch = vi.fn().mockResolvedValue({ ok: true, blob: async () => mockBlob });
        vi.stubGlobal('fetch', mockFetch);

        const api = makeApi('my-token', 7);
        const sc = addSupplyChainMethods(api);

        const result = await sc.downloadVehicleDocument('ORD-001', 5);

        expect(mockFetch).toHaveBeenCalledOnce();
        const [url, opts] = mockFetch.mock.calls[0];
        expect(url).toContain('/api/v1/supply-chain/vehicles/ORD-001/documents/5/download');
        expect(opts.headers['Authorization']).toBe('Bearer my-token');
        expect(opts.headers['X-Project-Id']).toBe('7');
        expect(result).toBe(mockBlob);
    });

    it('encodes orderNo with slashes in the URL', async () => {
        const mockFetch = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob([]) });
        vi.stubGlobal('fetch', mockFetch);

        const api = makeApi('tok', 1);
        const sc = addSupplyChainMethods(api);
        await sc.downloadVehicleDocument('ORD/2024/001', 3);

        const [url] = mockFetch.mock.calls[0];
        expect(url).toContain(encodeURIComponent('ORD/2024/001'));
    });

    it('throws when response is not ok', async () => {
        const mockFetch = vi.fn().mockResolvedValue({ ok: false });
        vi.stubGlobal('fetch', mockFetch);

        const api = makeApi();
        const sc = addSupplyChainMethods(api);

        await expect(sc.downloadVehicleDocument('ORD-002', 9)).rejects.toThrow('Ошибка скачивания');
    });

    it('omits Authorization and X-Project-Id headers when token and projectId are null', async () => {
        const mockFetch = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob([]) });
        vi.stubGlobal('fetch', mockFetch);

        const api = makeApi(null, null);
        const sc = addSupplyChainMethods(api);
        await sc.downloadVehicleDocument('ORD-003', 1);

        const [, opts] = mockFetch.mock.calls[0];
        expect(opts.headers['Authorization']).toBeUndefined();
        expect(opts.headers['X-Project-Id']).toBeUndefined();
    });
});

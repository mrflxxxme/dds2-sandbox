import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';

// Mock localStorage for browser-like environment
const localStorageMock = (() => {
    let store: Record<string, string> = {};
    return {
        getItem: (key: string) => store[key] ?? null,
        setItem: (key: string, value: string) => { store[key] = value; },
        removeItem: (key: string) => { delete store[key]; },
        clear: () => { store = {}; },
    };
})();

// Patch window so isBrowser check passes
Object.defineProperty(global, 'window', {
    value: {
        document: {},
        location: { href: '' },
    },
    writable: true,
});
Object.defineProperty(global, 'localStorage', {
    value: localStorageMock,
    writable: true,
});

function makeClient(): ApiClient {
    return new ApiClient();
}

function mockFetchOk(body: unknown, status = 200): ReturnType<typeof vi.fn> {
    return vi.fn().mockResolvedValue({
        ok: true,
        status,
        json: async () => body,
    });
}

function mockFetchError(status: number, body: unknown = {}): ReturnType<typeof vi.fn> {
    return vi.fn().mockResolvedValue({
        ok: false,
        status,
        json: async () => body,
    });
}

describe('ApiClient — token management', () => {
    let client: ApiClient;

    beforeEach(() => {
        localStorageMock.clear();
        client = makeClient();
    });

    it('getToken returns null when localStorage is empty', () => {
        expect(client.getToken()).toBeNull();
    });

    it('setToken stores token and getToken retrieves it', () => {
        client.setToken('test-token-123');
        expect(client.getToken()).toBe('test-token-123');
    });

    it('setRefreshToken stores and getRefreshToken retrieves it', () => {
        client.setRefreshToken('refresh-abc');
        expect(client.getRefreshToken()).toBe('refresh-abc');
    });

    it('clearToken removes all auth keys', () => {
        client.setToken('tok');
        client.setRefreshToken('ref');
        client.setProjectId(42);
        client.clearToken();
        expect(client.getToken()).toBeNull();
        expect(client.getRefreshToken()).toBeNull();
        expect(client.getProjectId()).toBeNull();
    });

    it('isAuthenticated returns false without token', () => {
        expect(client.isAuthenticated()).toBe(false);
    });

    it('isAuthenticated returns true with token', () => {
        client.setToken('x');
        expect(client.isAuthenticated()).toBe(true);
    });

    it('getProjectId parses integer from localStorage', () => {
        client.setProjectId(7);
        expect(client.getProjectId()).toBe(7);
    });

    it('getProjectId returns null when not set', () => {
        expect(client.getProjectId()).toBeNull();
    });
});

describe('ApiClient.request — headers', () => {
    let client: ApiClient;

    beforeEach(() => {
        localStorageMock.clear();
        client = makeClient();
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('sends Authorization header when token is set', async () => {
        client.setToken('my-jwt-token');
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true,
            status: 200,
            json: async () => ({ data: 'ok' }),
        } as Response);

        await client.request('GET', '/api/v1/test');

        expect(fetchSpy).toHaveBeenCalledOnce();
        const [, init] = fetchSpy.mock.calls[0];
        expect((init as RequestInit).headers).toMatchObject({
            Authorization: 'Bearer my-jwt-token',
        });
    });

    it('does NOT send Authorization header when token is absent', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true,
            status: 200,
            json: async () => ({}),
        } as Response);

        await client.request('GET', '/api/v1/test');

        const [, init] = fetchSpy.mock.calls[0];
        const headers = (init as RequestInit).headers as Record<string, string>;
        expect(headers['Authorization']).toBeUndefined();
    });

    it('sends X-Project-Id header when project is set', async () => {
        client.setProjectId(5);
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true,
            status: 200,
            json: async () => ({}),
        } as Response);

        await client.request('GET', '/api/v1/test');

        const [, init] = fetchSpy.mock.calls[0];
        expect((init as RequestInit).headers).toMatchObject({
            'X-Project-Id': '5',
        });
    });

    it('sends Content-Type application/json', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true,
            status: 200,
            json: async () => ({}),
        } as Response);

        await client.request('GET', '/api/v1/test');

        const [, init] = fetchSpy.mock.calls[0];
        expect((init as RequestInit).headers).toMatchObject({
            'Content-Type': 'application/json',
        });
    });

    it('serialises body as JSON for POST', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true,
            status: 200,
            json: async () => ({}),
        } as Response);

        await client.request('POST', '/api/v1/test', { key: 'value' });

        const [, init] = fetchSpy.mock.calls[0];
        expect((init as RequestInit).body).toBe(JSON.stringify({ key: 'value' }));
    });

    it('sends no body when body param is absent', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true,
            status: 200,
            json: async () => ({}),
        } as Response);

        await client.request('GET', '/api/v1/test');

        const [, init] = fetchSpy.mock.calls[0];
        expect((init as RequestInit).body).toBeUndefined();
    });
});

describe('ApiClient.request — 401 + token refresh', () => {
    let client: ApiClient;

    beforeEach(() => {
        localStorageMock.clear();
        client = makeClient();
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('retries the original request after successful refresh', async () => {
        client.setToken('expired-token');
        client.setRefreshToken('valid-refresh');

        const fetchSpy = vi.spyOn(global, 'fetch')
            // First call — original request returns 401
            .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) } as Response)
            // Second call — refresh endpoint returns new token
            .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ access_token: 'new-token' }) } as Response)
            // Third call — retried original request succeeds
            .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ id: 42 }) } as Response);

        const result = await client.request<{ id: number }>('GET', '/api/v1/protected');

        expect(result).toEqual({ id: 42 });
        expect(fetchSpy).toHaveBeenCalledTimes(3);
        // The new token should be stored
        expect(client.getToken()).toBe('new-token');
    });

    it('clears tokens and throws Unauthorized when refresh also returns 401', async () => {
        client.setToken('expired');
        client.setRefreshToken('also-expired');

        vi.spyOn(global, 'fetch')
            // Original request
            .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) } as Response)
            // Refresh endpoint returns 401 (token invalid)
            .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) } as Response);

        await expect(client.request('GET', '/api/v1/protected')).rejects.toThrow('Unauthorized');
        expect(client.getToken()).toBeNull();
        expect(client.getRefreshToken()).toBeNull();
    });

    it('throws unavailability message when refresh network fails', async () => {
        client.setToken('expired');
        client.setRefreshToken('ref');

        vi.spyOn(global, 'fetch')
            // Original request returns 401
            .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) } as Response)
            // Refresh call throws (network error)
            .mockRejectedValueOnce(new Error('Network failure'));

        await expect(client.request('GET', '/api/v1/protected')).rejects.toThrow(
            'Сервер временно недоступен'
        );
        // Tokens must NOT be cleared on network errors (backend may be deploying)
        expect(client.getToken()).toBe('expired');
        expect(client.getRefreshToken()).toBe('ref');
    });

    it('throws immediately if no refresh token available on 401', async () => {
        client.setToken('expired');
        // No refresh token

        vi.spyOn(global, 'fetch')
            .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) } as Response);

        await expect(client.request('GET', '/api/v1/protected')).rejects.toThrow('Unauthorized');
        expect(client.getToken()).toBeNull();
    });
});

describe('ApiClient.request — error handling', () => {
    let client: ApiClient;

    beforeEach(() => {
        localStorageMock.clear();
        client = makeClient();
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('throws Error with detail string on 422', async () => {
        vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: false,
            status: 422,
            json: async () => ({ detail: 'Validation failed' }),
        } as Response);

        await expect(client.request('POST', '/api/v1/test', {})).rejects.toThrow('Validation failed');
    });

    it('throws joined message when detail is array of validation errors', async () => {
        vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: false,
            status: 422,
            json: async () => ({
                detail: [
                    { msg: 'field required', loc: ['body', 'name'] },
                    { msg: 'value is not a valid integer' },
                ],
            }),
        } as Response);

        await expect(client.request('POST', '/api/v1/test', {})).rejects.toThrow(
            'field required; value is not a valid integer'
        );
    });

    it('throws generic Error N on non-ok response without detail', async () => {
        vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: false,
            status: 500,
            json: async () => ({}),
        } as Response);

        await expect(client.request('GET', '/api/v1/test')).rejects.toThrow('Error 500');
    });

    it('returns parsed JSON on 200 OK', async () => {
        vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true,
            status: 200,
            json: async () => ({ result: 'success' }),
        } as Response);

        const result = await client.request<{ result: string }>('GET', '/api/v1/test');
        expect(result).toEqual({ result: 'success' });
    });
});

describe('ApiClient.uploadFormData', () => {
    let client: ApiClient;

    beforeEach(() => {
        localStorageMock.clear();
        client = makeClient();
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('sends POST with FormData and auth headers', async () => {
        client.setToken('upload-token');
        client.setProjectId(3);

        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true,
            status: 200,
            json: async () => ({ inserted: 5 }),
        } as Response);

        const formData = new FormData();
        formData.append('file', new Blob(['data'], { type: 'text/csv' }), 'test.csv');

        const result = await client.uploadFormData<{ inserted: number }>('/api/v1/upload', formData);

        expect(result).toEqual({ inserted: 5 });
        expect(fetchSpy).toHaveBeenCalledOnce();
        const [, init] = fetchSpy.mock.calls[0];
        expect((init as RequestInit).method).toBe('POST');
        expect((init as RequestInit).body).toBe(formData);
        expect((init as RequestInit).headers).toMatchObject({
            Authorization: 'Bearer upload-token',
            'X-Project-Id': '3',
        });
        // Content-Type must NOT be set for FormData (browser sets multipart boundary)
        const headers = (init as RequestInit).headers as Record<string, string>;
        expect(headers['Content-Type']).toBeUndefined();
    });

    it('throws on upload error with detail string', async () => {
        vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: false,
            status: 400,
            json: async () => ({ detail: 'Invalid file format' }),
        } as Response);

        const formData = new FormData();
        await expect(
            client.uploadFormData('/api/v1/upload', formData)
        ).rejects.toThrow('Invalid file format');
    });

    it('throws on upload error with array detail', async () => {
        vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: false,
            status: 422,
            json: async () => ({
                detail: [{ loc: ['body', 'file'], msg: 'field required' }],
            }),
        } as Response);

        const formData = new FormData();
        await expect(
            client.uploadFormData('/api/v1/upload', formData)
        ).rejects.toThrow('body.file: field required');
    });

    it('attaches HTTP status to the thrown error (for caller classification)', async () => {
        vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: false,
            status: 413,
            json: async () => ({ detail: 'Файл слишком большой' }),
        } as Response);

        const formData = new FormData();
        await client
            .uploadFormData('/api/v1/upload', formData)
            .then(() => { throw new Error('should have thrown'); })
            .catch((e: Error & { status?: number }) => {
                expect(e.message).toBe('Файл слишком большой');
                expect(e.status).toBe(413);
            });
    });

    it('does NOT retry by default — one attempt on 5xx', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: false,
            status: 500,
            json: async () => ({}),
        } as Response);

        const formData = new FormData();
        await expect(
            client.uploadFormData('/api/v1/upload', formData)
        ).rejects.toThrow();
        expect(fetchSpy).toHaveBeenCalledOnce();
    });

    it('retries transient 5xx and succeeds on a later attempt', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch')
            .mockResolvedValueOnce({ ok: false, status: 502, json: async () => ({}) } as Response)
            .mockResolvedValueOnce({ ok: false, status: 503, json: async () => ({}) } as Response)
            .mockResolvedValueOnce({ ok: true, status: 201, json: async () => ({ id: 7 }) } as Response);

        const formData = new FormData();
        const result = await client.uploadFormData<{ id: number }>(
            '/api/v1/upload', formData, { retries: 3, retryDelayMs: 0 },
        );
        expect(result).toEqual({ id: 7 });
        expect(fetchSpy).toHaveBeenCalledTimes(3);
    });

    it('retries a network-level fetch rejection (connection reset during deploy)', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch')
            .mockRejectedValueOnce(new TypeError('Failed to fetch'))
            .mockResolvedValueOnce({ ok: true, status: 201, json: async () => ({ id: 9 }) } as Response);

        const formData = new FormData();
        const result = await client.uploadFormData<{ id: number }>(
            '/api/v1/upload', formData, { retries: 2, retryDelayMs: 0 },
        );
        expect(result).toEqual({ id: 9 });
        expect(fetchSpy).toHaveBeenCalledTimes(2);
    });

    it('does NOT retry a 4xx — client error is not transient', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: false,
            status: 415,
            json: async () => ({ detail: 'Unsupported' }),
        } as Response);

        const formData = new FormData();
        await expect(
            client.uploadFormData('/api/v1/upload', formData, { retries: 3, retryDelayMs: 0 })
        ).rejects.toThrow('Unsupported');
        expect(fetchSpy).toHaveBeenCalledOnce();
    });

    it('gives up after exhausting retries and throws with status', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: false,
            status: 500,
            json: async () => ({}),
        } as Response);

        const formData = new FormData();
        await client
            .uploadFormData('/api/v1/upload', formData, { retries: 2, retryDelayMs: 0 })
            .then(() => { throw new Error('should have thrown'); })
            .catch((e: Error & { status?: number }) => {
                expect(e.status).toBe(500);
            });
        expect(fetchSpy).toHaveBeenCalledTimes(3); // 1 initial + 2 retries
    });
});

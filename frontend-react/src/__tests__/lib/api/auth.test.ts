import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addAuthMethods } from '@/lib/api/auth';

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
    return { client, ...addAuthMethods(client) };
}

describe('auth.login', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('POSTs to /api/v1/auth/login with username and password', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ access_token: 'tok123', token_type: 'bearer' }),
        } as Response);

        const api = makeApi();
        const result = await api.login('admin', 'secret');

        expect(result.access_token).toBe('tok123');
        expect(fetchSpy).toHaveBeenCalledOnce();
        const [url, init] = fetchSpy.mock.calls[0];
        expect(url).toContain('/api/v1/auth/login');
        expect((init as RequestInit).method).toBe('POST');
        expect(JSON.parse((init as RequestInit).body as string)).toEqual({ username: 'admin', password: 'secret' });
    });

    it('throws on 401 invalid credentials', async () => {
        vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: false, status: 401,
            json: async () => ({ detail: 'Invalid credentials' }),
        } as Response);

        const api = makeApi();
        await expect(api.login('user', 'wrong')).rejects.toThrow('Unauthorized');
    });
});

describe('auth.register', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('POSTs registration data and returns token', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ access_token: 'new-tok', token_type: 'bearer' }),
        } as Response);

        const api = makeApi();
        const result = await api.register({ username: 'newuser', password: 'pass123', email: 'u@test.com' });

        expect(result.access_token).toBe('new-tok');
        const [url, init] = fetchSpy.mock.calls[0];
        expect(url).toContain('/api/v1/auth/register');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.username).toBe('newuser');
        expect(body.email).toBe('u@test.com');
    });
});

describe('auth.getProfile', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('GETs /api/v1/auth/me and returns user profile', async () => {
        vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({
                id: 1, username: 'admin', email: 'admin@test.com',
                first_name: 'Admin', last_name: null, is_active: true, created_at: '2025-01-01T00:00:00',
            }),
        } as Response);

        const api = makeApi();
        const profile = await api.getProfile();

        expect(profile.id).toBe(1);
        expect(profile.username).toBe('admin');
    });
});

describe('auth.updateProfile', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('PUTs profile data to /api/v1/auth/me', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ id: 1, username: 'admin', email: 'new@test.com' }),
        } as Response);

        const api = makeApi();
        await api.updateProfile({ email: 'new@test.com', first_name: 'John' });

        const [url, init] = fetchSpy.mock.calls[0];
        expect(url).toContain('/api/v1/auth/me');
        expect((init as RequestInit).method).toBe('PUT');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.email).toBe('new@test.com');
        expect(body.first_name).toBe('John');
    });
});

describe('auth.changePassword', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('POSTs old and new password to /api/v1/auth/change_password', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ message: 'Password changed' }),
        } as Response);

        const api = makeApi();
        await api.changePassword('oldpass', 'newpass');

        const [url, init] = fetchSpy.mock.calls[0];
        expect(url).toContain('/api/v1/auth/change_password');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.old_password).toBe('oldpass');
        expect(body.new_password).toBe('newpass');
    });
});

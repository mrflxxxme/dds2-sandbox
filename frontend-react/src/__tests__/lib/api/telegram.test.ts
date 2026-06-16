import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addTelegramMethods } from '@/lib/api/telegram';

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
    return { client, ...addTelegramMethods(client) };
}

function mockFetch(body: unknown) {
    return vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: true, status: 200, json: async () => body,
    } as Response);
}

afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

describe('telegram.getTelegramLink', () => {
    it('POSTs to /api/v1/telegram/link and returns link response', async () => {
        const spy = mockFetch({ deep_link_url: 'https://t.me/dds_bot?start=abc123' });
        const api = makeApi();
        const result = await api.getTelegramLink();
        expect(result.deep_link_url).toContain('t.me');
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/telegram/link');
        expect((init as RequestInit).method).toBe('POST');
    });
});

describe('telegram.getTelegramChats', () => {
    it('GETs /api/v1/telegram/chats', async () => {
        const spy = mockFetch([
            { id: 1, chat_id: 123456, brand: 'My Chat', notify_enabled: true, created_at: '2025-01-01' },
        ]);
        const api = makeApi();
        const result = await api.getTelegramChats();
        expect(result).toHaveLength(1);
        expect(result[0].brand).toBe('My Chat');
        expect(spy.mock.calls[0][0]).toContain('/api/v1/telegram/chats');
    });
});

describe('telegram.deleteTelegramChat', () => {
    it('DELETEs binding by id', async () => {
        const spy = mockFetch({ message: 'deleted' });
        const api = makeApi();
        await api.deleteTelegramChat(7);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/telegram/chats/7');
        expect((init as RequestInit).method).toBe('DELETE');
    });
});

describe('telegram.toggleTelegramNotify', () => {
    it('PATCHes notify setting to enabled=true', async () => {
        const spy = mockFetch({ message: 'ok' });
        const api = makeApi();
        await api.toggleTelegramNotify(3, true);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/telegram/chats/3/notify');
        expect((init as RequestInit).method).toBe('PATCH');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.enabled).toBe(true);
    });

    it('PATCHes notify setting to enabled=false', async () => {
        const spy = mockFetch({ message: 'ok' });
        const api = makeApi();
        await api.toggleTelegramNotify(3, false);
        const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
        expect(body.enabled).toBe(false);
    });
});

describe('telegram.toggleTelegramFfNotify', () => {
    it('PATCHes ff-notify setting to enabled=true', async () => {
        const spy = mockFetch({ message: 'ok' });
        const api = makeApi();
        await api.toggleTelegramFfNotify(3, true);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/telegram/chats/3/ff-notify');
        expect((init as RequestInit).method).toBe('PATCH');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.enabled).toBe(true);
    });

    it('PATCHes ff-notify setting to enabled=false', async () => {
        const spy = mockFetch({ message: 'ok' });
        const api = makeApi();
        await api.toggleTelegramFfNotify(3, false);
        const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
        expect(body.enabled).toBe(false);
    });
});

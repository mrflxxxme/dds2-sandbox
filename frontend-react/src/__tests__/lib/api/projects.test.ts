import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addProjectMethods } from '@/lib/api/projects';

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
    return { client, ...addProjectMethods(client) };
}

function mockFetch(body: unknown) {
    return vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: true, status: 200, json: async () => body,
    } as Response);
}

afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

describe('projects.getProjects', () => {
    it('GETs /api/v1/projects', async () => {
        const spy = mockFetch([{ id: 1, name: 'My Shop', slug: 'my-shop', owner_id: 1, created_at: '2025-01-01', members_count: 2 }]);
        const api = makeApi();
        const result = await api.getProjects();
        expect(result).toHaveLength(1);
        expect(result[0].slug).toBe('my-shop');
        expect(spy.mock.calls[0][0]).toContain('/api/v1/projects');
    });
});

describe('projects.getProject', () => {
    it('GETs by slug', async () => {
        const spy = mockFetch({ id: 1, name: 'My Shop', slug: 'my-shop', owner_id: 1, created_at: '2025-01-01' });
        const api = makeApi();
        const result = await api.getProject('my-shop');
        expect(result.slug).toBe('my-shop');
        expect(spy.mock.calls[0][0]).toContain('/api/v1/projects/my-shop');
    });
});

describe('projects.createProject', () => {
    it('POSTs name to /api/v1/projects', async () => {
        const spy = mockFetch({ id: 2, name: 'New Shop', slug: 'new-shop' });
        const api = makeApi();
        await api.createProject('New Shop');
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/projects');
        expect((init as RequestInit).method).toBe('POST');
        expect(JSON.parse((init as RequestInit).body as string)).toEqual({ name: 'New Shop' });
    });
});

describe('projects.deleteProject', () => {
    it('DELETEs by slug', async () => {
        const spy = mockFetch({ message: 'deleted' });
        const api = makeApi();
        await api.deleteProject('my-shop');
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/projects/my-shop');
        expect((init as RequestInit).method).toBe('DELETE');
    });
});

describe('projects.getMembers', () => {
    it('GETs members list for a project', async () => {
        const spy = mockFetch([
            { id: 1, user_id: 1, username: 'admin', email: 'a@test.com', first_name: null, last_name: null, joined_at: '2025-01-01' },
        ]);
        const api = makeApi();
        const result = await api.getMembers('my-shop');
        expect(result).toHaveLength(1);
        expect(spy.mock.calls[0][0]).toContain('/api/v1/projects/my-shop/members');
    });
});

describe('projects.inviteByEmail', () => {
    it('POSTs invite with URL-encoded email', async () => {
        const spy = mockFetch({ id: 1, email: 'user@test.com', invite_token: 'abc123', status: 'pending', created_at: '2025-01-01', accepted_at: null });
        const api = makeApi();
        await api.inviteByEmail('my-shop', 'user@test.com');
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/projects/my-shop/invite');
        expect(url).toContain('email=user%40test.com');
        expect((init as RequestInit).method).toBe('POST');
    });
});

describe('projects.acceptInvite', () => {
    it('POSTs to invite accept endpoint', async () => {
        const spy = mockFetch({ message: 'accepted' });
        const api = makeApi();
        await api.acceptInvite('token-xyz');
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/projects/invite/accept/token-xyz');
        expect((init as RequestInit).method).toBe('POST');
    });
});

describe('projects.removeMember', () => {
    it('DELETEs member by userId', async () => {
        const spy = mockFetch({ message: 'removed' });
        const api = makeApi();
        await api.removeMember('my-shop', 42);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/projects/my-shop/members/42');
        expect((init as RequestInit).method).toBe('DELETE');
    });
});

describe('projects.updateMemberRole', () => {
    it('PUTs role and pages', async () => {
        const spy = mockFetch({ message: 'updated' });
        const api = makeApi();
        await api.updateMemberRole('my-shop', 5, 'editor', ['reports', 'transactions']);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/projects/my-shop/members/5/role');
        expect((init as RequestInit).method).toBe('PUT');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.role).toBe('editor');
        expect(body.pages).toEqual(['reports', 'transactions']);
    });
});

describe('projects.getMyPermissions', () => {
    it('GETs permissions for current user in project', async () => {
        const spy = mockFetch({ role: 'admin', pages: ['*'], can_invite: true });
        const api = makeApi();
        const result = await api.getMyPermissions('my-shop');
        expect(result.role).toBe('admin');
        expect(spy.mock.calls[0][0]).toContain('/api/v1/projects/my-shop/my-permissions');
    });
});

describe('projects.updateTelegramUsername', () => {
    it('PUTs telegram username', async () => {
        const spy = mockFetch(undefined);
        const api = makeApi();
        await api.updateTelegramUsername('my-shop', 5, 'myuser');
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/projects/my-shop/members/5/telegram-username');
        expect((init as RequestInit).method).toBe('PUT');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.telegram_username).toBe('myuser');
    });

    it('sends null to clear telegram username', async () => {
        const spy = mockFetch(undefined);
        const api = makeApi();
        await api.updateTelegramUsername('my-shop', 5, null);
        const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
        expect(body.telegram_username).toBeNull();
    });
});

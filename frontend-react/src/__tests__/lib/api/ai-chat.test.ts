import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addAiChatMethods } from '@/lib/api/ai-chat';

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
    return { client, ...addAiChatMethods(client) };
}

function mockFetch(body: unknown, ok = true, status = 200) {
    return vi.spyOn(global, 'fetch').mockResolvedValue({
        ok, status, json: async () => body,
    } as Response);
}

afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

describe('ai-chat.listConversations', () => {
    it('GETs /api/v1/ai/conversations', async () => {
        const spy = mockFetch([
            { id: 1, title: 'Test Chat', brand: 'Nike', created_at: '2025-01-01', updated_at: '2025-01-01' },
        ]);
        const api = makeApi();
        const result = await api.listConversations();
        expect(result).toHaveLength(1);
        expect(result[0].title).toBe('Test Chat');
        expect(spy.mock.calls[0][0]).toContain('/api/v1/ai/conversations');
    });
});

describe('ai-chat.createConversation', () => {
    it('POSTs with brand and title', async () => {
        const spy = mockFetch({ id: 2, title: 'New Chat', brand: 'Adidas', created_at: '2025-01-01', updated_at: '2025-01-01' });
        const api = makeApi();
        await api.createConversation({ brand: 'Adidas', title: 'New Chat' });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/ai/conversations');
        expect((init as RequestInit).method).toBe('POST');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.brand).toBe('Adidas');
        expect(body.title).toBe('New Chat');
    });
});

describe('ai-chat.getConversation', () => {
    it('fetches conversation and messages in parallel, returning both', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch')
            // First call — conversation
            .mockResolvedValueOnce({
                ok: true, status: 200,
                json: async () => ({ id: 1, title: 'Chat 1', brand: null, created_at: '2025-01-01', updated_at: '2025-01-01' }),
            } as Response)
            // Second call — messages
            .mockResolvedValueOnce({
                ok: true, status: 200,
                json: async () => [
                    { id: 1, conversation_id: 1, role: 'user', content: 'Hello', created_at: '2025-01-01' },
                ],
            } as Response);

        const api = makeApi();
        const result = await api.getConversation(1);

        expect(result.conversation.id).toBe(1);
        expect(result.messages).toHaveLength(1);
        expect(result.messages[0].content).toBe('Hello');
        expect(fetchSpy).toHaveBeenCalledTimes(2);
        expect(fetchSpy.mock.calls[0][0]).toContain('/api/v1/ai/conversations/1');
        expect(fetchSpy.mock.calls[1][0]).toContain('/api/v1/ai/conversations/1/messages');
    });
});

describe('ai-chat.updateConversation', () => {
    it('PATCHes conversation data', async () => {
        const spy = mockFetch({ id: 1, title: 'Updated Title', brand: 'Nike', created_at: '2025-01-01', updated_at: '2025-01-02' });
        const api = makeApi();
        await api.updateConversation(1, { title: 'Updated Title' });
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/ai/conversations/1');
        expect((init as RequestInit).method).toBe('PATCH');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.title).toBe('Updated Title');
    });
});

describe('ai-chat.deleteConversation', () => {
    it('DELETEs conversation by id', async () => {
        const spy = mockFetch(null);
        const api = makeApi();
        await api.deleteConversation(5);
        const [url, init] = spy.mock.calls[0];
        expect(url).toContain('/api/v1/ai/conversations/5');
        expect((init as RequestInit).method).toBe('DELETE');
    });
});

describe('ai-chat.sendMessageStream', () => {
    it('POSTs message to conversation and returns Response', async () => {
        const mockResponse = {
            ok: true, status: 200,
            json: async () => ({}),
            body: null,
        } as unknown as Response;

        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue(mockResponse);

        const client = new ApiClient();
        client.setToken('stream-token');
        client.setProjectId(2);
        const api = { client, ...addAiChatMethods(client) };

        const result = await api.sendMessageStream(1, 'Hello AI');

        expect(result).toBe(mockResponse);
        const [url, init] = fetchSpy.mock.calls[0];
        expect(url).toContain('/api/v1/ai/conversations/1/messages');
        expect((init as RequestInit).method).toBe('POST');
        const headers = (init as RequestInit).headers as Record<string, string>;
        expect(headers['Authorization']).toBe('Bearer stream-token');
        expect(headers['X-Project-Id']).toBe('2');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.content).toBe('Hello AI');
        expect(body.files).toEqual([]);
    });

    it('sends files array when provided', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200, json: async () => ({}),
        } as Response);

        const client = new ApiClient();
        const api = { client, ...addAiChatMethods(client) };

        const files = [{ name: 'report.xlsx', type: 'excel' as const, size: 1024, content: 'base64data' }];
        await api.sendMessageStream(1, 'Analyze this', files);

        const body = JSON.parse((fetchSpy.mock.calls[0][1] as RequestInit).body as string);
        expect(body.files).toEqual(files);
    });

    it('throws on non-ok response', async () => {
        vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: false, status: 500,
            json: async () => ({ detail: 'AI service error' }),
        } as Response);

        const client = new ApiClient();
        const api = { client, ...addAiChatMethods(client) };

        await expect(api.sendMessageStream(1, 'Hi')).rejects.toThrow('AI service error');
    });
});

describe('ai-chat.uploadAiFile', () => {
    it('calls uploadFormData for AI file upload', async () => {
        const client = new ApiClient();
        const uploadSpy = vi.spyOn(client, 'uploadFormData').mockResolvedValue({
            file_id: 'f-001', name: 'report.xlsx', type: 'excel', size: 2048,
        });
        const api = { client, ...addAiChatMethods(client) };

        const file = new File(['data'], 'report.xlsx');
        const result = await api.uploadAiFile(file);

        expect(result.file_id).toBe('f-001');
        const [path, formData] = uploadSpy.mock.calls[0];
        expect(path).toBe('/api/v1/ai/upload');
        expect(formData.get('file')).toBe(file);
    });
});

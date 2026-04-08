/** AI Chat API methods */
import { ApiClient } from './client';
import type {
    AiConversation,
    AiMessage,
    AiFileUploadResponse,
} from '@/types/api';

const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

export function addAiChatMethods(api: ApiClient) {
    return {
        // ─── Conversations ─────────────────────────────────────────
        listConversations() {
            return api.request<AiConversation[]>('GET', '/api/v1/ai/conversations');
        },
        async getConversation(id: number): Promise<{ conversation: AiConversation; messages: AiMessage[] }> {
            const [conversation, messages] = await Promise.all([
                api.request<AiConversation>('GET', `/api/v1/ai/conversations/${id}`),
                api.request<AiMessage[]>('GET', `/api/v1/ai/conversations/${id}/messages`),
            ]);
            return { conversation, messages };
        },
        createConversation(data: { brand?: string; title?: string }) {
            return api.request<AiConversation>('POST', '/api/v1/ai/conversations', data);
        },
        updateConversation(id: number, data: { title?: string; brand?: string }) {
            return api.request<AiConversation>('PATCH', `/api/v1/ai/conversations/${id}`, data);
        },
        deleteConversation(id: number) {
            return api.request<void>('DELETE', `/api/v1/ai/conversations/${id}`);
        },

        // ─── Messages (SSE streaming) ──────────────────────────────
        /**
         * Send a message and get a ReadableStream of SSE events.
         * Returns raw Response so the caller can read the stream.
         */
        async sendMessageStream(
            conversationId: number,
            content: string,
            files?: { name: string; type: 'excel' | 'image'; size: number; content: string }[],
        ): Promise<Response> {
            const headers: Record<string, string> = {
                'Content-Type': 'application/json',
            };
            const token = api.getToken();
            if (token) headers['Authorization'] = `Bearer ${token}`;
            const projectId = api.getProjectId();
            if (projectId) headers['X-Project-Id'] = String(projectId);

            const res = await fetch(`${API_URL}/api/v1/ai/conversations/${conversationId}/messages`, {
                method: 'POST',
                headers,
                body: JSON.stringify({ content, files: files || [] }),
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `Error ${res.status}`);
            }

            return res;
        },

        // ─── File Upload ───────────────────────────────────────────
        uploadAiFile(file: File) {
            const formData = new FormData();
            formData.append('file', file);
            return api.uploadFormData<AiFileUploadResponse>('/api/v1/ai/upload', formData);
        },
    };
}

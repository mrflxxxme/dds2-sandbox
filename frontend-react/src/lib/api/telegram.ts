/** Telegram bot API methods */
import { ApiClient } from './client';
import type { TelegramChatBinding, TelegramLinkResponse, MessageResponse } from '@/types/api';

export function addTelegramMethods(api: ApiClient) {
    return {
        getTelegramLink() {
            return api.request<TelegramLinkResponse>('POST', '/api/v1/telegram/link');
        },
        getTelegramChats() {
            return api.request<TelegramChatBinding[]>('GET', '/api/v1/telegram/chats');
        },
        deleteTelegramChat(bindingId: number) {
            return api.request<MessageResponse>('DELETE', `/api/v1/telegram/chats/${bindingId}`);
        },
        toggleTelegramNotify(bindingId: number, enabled: boolean) {
            return api.request<MessageResponse>('PATCH', `/api/v1/telegram/chats/${bindingId}/notify`, { enabled });
        },
    };
}

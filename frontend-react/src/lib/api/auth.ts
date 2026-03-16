/** Auth API methods */
import { ApiClient } from './client';
import type { UserProfile, MessageResponse } from '@/types/api';

export function addAuthMethods(api: ApiClient) {
    return {
        login(username: string, password: string) {
            return api.request<{ access_token: string; token_type: string }>(
                'POST', '/api/v1/auth/login', { username, password }
            );
        },
        register(data: { username: string; password: string; email?: string; first_name?: string; last_name?: string }) {
            return api.request<{ access_token: string; token_type: string }>(
                'POST', '/api/v1/auth/register', data
            );
        },
        getProfile() {
            return api.request<{
                id: number; username: string; email: string | null;
                first_name: string | null; last_name: string | null;
                is_active: boolean; created_at: string;
            }>('GET', '/api/v1/auth/me');
        },
        updateProfile(data: { email?: string; first_name?: string; last_name?: string }) {
            return api.request<UserProfile>('PUT', '/api/v1/auth/me', data);
        },
        changePassword(old_password: string, new_password: string) {
            return api.request<MessageResponse>('POST', '/api/v1/auth/change_password', { old_password, new_password });
        },
    };
}

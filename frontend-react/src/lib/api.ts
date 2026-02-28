/**
 * DDS API Client — typed HTTP client with JWT auth.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

class ApiClient {
    private getToken(): string | null {
        if (typeof window === 'undefined') return null;
        return localStorage.getItem('dds_token');
    }

    setToken(token: string) {
        localStorage.setItem('dds_token', token);
    }

    clearToken() {
        localStorage.removeItem('dds_token');
        localStorage.removeItem('dds_project_id');
    }

    isAuthenticated(): boolean {
        return !!this.getToken();
    }

    getProjectId(): number | null {
        if (typeof window === 'undefined') return null;
        const pid = localStorage.getItem('dds_project_id');
        return pid ? parseInt(pid) : null;
    }

    setProjectId(id: number) {
        localStorage.setItem('dds_project_id', String(id));
    }

    private async request<T>(
        method: string,
        path: string,
        body?: unknown,
    ): Promise<T> {
        const headers: Record<string, string> = {
            'Content-Type': 'application/json',
        };

        const token = this.getToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;

        const projectId = this.getProjectId();
        if (projectId) headers['X-Project-Id'] = String(projectId);

        const res = await fetch(`${API_URL}${path}`, {
            method,
            headers,
            body: body ? JSON.stringify(body) : undefined,
        });

        if (res.status === 401) {
            this.clearToken();
            if (typeof window !== 'undefined') {
                window.location.href = '/login';
            }
            throw new Error('Unauthorized');
        }

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || err.error?.message || `Error ${res.status}`);
        }

        return res.json();
    }

    // Auth
    login(username: string, password: string) {
        return this.request<{ access_token: string; token_type: string }>(
            'POST', '/api/v1/auth/login', { username, password }
        );
    }

    register(data: { username: string; password: string; email?: string; first_name?: string; last_name?: string }) {
        return this.request<{ access_token: string; token_type: string }>(
            'POST', '/api/v1/auth/register', data
        );
    }

    getProfile() {
        return this.request<{
            id: number; username: string; email: string | null;
            first_name: string | null; last_name: string | null;
            is_active: boolean; created_at: string;
        }>('GET', '/api/v1/auth/me');
    }

    updateProfile(data: { email?: string; first_name?: string; last_name?: string }) {
        return this.request<any>('PUT', '/api/v1/auth/me', data);
    }

    changePassword(old_password: string, new_password: string) {
        return this.request<any>('POST', '/api/v1/auth/change_password', { old_password, new_password });
    }

    // Projects
    getProjects() {
        return this.request<Array<{
            id: number; name: string; slug: string; owner_id: number;
            created_at: string; members_count: number;
        }>>('GET', '/api/v1/projects');
    }

    createProject(name: string) {
        return this.request<any>('POST', '/api/v1/projects', { name });
    }

    deleteProject(slug: string) {
        return this.request<any>('DELETE', `/api/v1/projects/${slug}`);
    }

    // Team
    getMembers(slug: string) {
        return this.request<Array<{
            id: number; user_id: number; username: string;
            email: string | null; first_name: string | null;
            last_name: string | null; joined_at: string;
        }>>('GET', `/api/v1/projects/${slug}/members`);
    }

    inviteByEmail(slug: string, email: string) {
        return this.request<any>('POST', `/api/v1/projects/${slug}/invite?email=${encodeURIComponent(email)}`);
    }

    getInviteLink(slug: string) {
        return this.request<{ invite_token: string; link: string }>('GET', `/api/v1/projects/${slug}/invite-link`);
    }

    getInvites(slug: string) {
        return this.request<Array<{
            id: number; email: string | null; invite_token: string;
            status: string; created_at: string; accepted_at: string | null;
        }>>('GET', `/api/v1/projects/${slug}/invites`);
    }

    removeMember(slug: string, userId: number) {
        return this.request<any>('DELETE', `/api/v1/projects/${slug}/members/${userId}`);
    }

    acceptInvite(token: string) {
        return this.request<any>('POST', `/api/v1/projects/invite/accept/${token}`);
    }

    // Integrations
    getIntegrationKeys() {
        return this.request<Array<{
            id: number; service: string; label: string | null;
            encrypted_key: string; is_active: boolean;
            created_at: string; last_sync_at: string | null;
        }>>('GET', '/api/v1/integrations/keys');
    }

    addIntegrationKey(service: string, api_key: string, label?: string) {
        return this.request<any>('POST', '/api/v1/integrations/keys', { service, api_key, label });
    }

    deleteIntegrationKey(id: number) {
        return this.request<any>('DELETE', `/api/v1/integrations/keys/${id}`);
    }

    syncWb(keyId: number, dateFrom: string) {
        return this.request<any>('POST', `/api/v1/integrations/sync/wb/${keyId}?date_from=${dateFrom}`);
    }

    getSyncLog() {
        return this.request<Array<{
            id: number; service: string; sync_type: string; status: string;
            started_at: string; finished_at: string | null;
            rows_fetched: number; rows_inserted: number; error_msg: string | null;
        }>>('GET', '/api/v1/integrations/sync_log');
    }

    // Reports
    getBalance() {
        return this.request<Array<{
            account: string; account_name: string | null;
            currency: string; balance: number;
        }>>('GET', '/api/v1/reports/balance');
    }

    getDDS(params: { start?: string; end?: string } = {}) {
        const q = new URLSearchParams();
        if (params.start) q.set('start', params.start);
        if (params.end) q.set('end', params.end);
        return this.request<any>('GET', `/api/v1/reports/dds?${q}`);
    }

    getTransactions(params: { start?: string; end?: string; account?: string } = {}) {
        const q = new URLSearchParams();
        if (params.start) q.set('start', params.start);
        if (params.end) q.set('end', params.end);
        if (params.account) q.set('account', params.account);
        return this.request<any>('GET', `/api/v1/reports/transactions?${q}`);
    }

    getOrders() {
        return this.request<any>('GET', '/api/v1/refs/orders');
    }

    // Upload
    async uploadFile(file: File, sourceType: string): Promise<any> {
        const formData = new FormData();
        formData.append('file', file);

        const headers: Record<string, string> = {};
        const token = this.getToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;
        const projectId = this.getProjectId();
        if (projectId) headers['X-Project-Id'] = String(projectId);

        const res = await fetch(`${API_URL}/api/v1/import/${sourceType}`, {
            method: 'POST',
            headers,
            body: formData,
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `Error ${res.status}`);
        }
        return res.json();
    }
}

export const api = new ApiClient();

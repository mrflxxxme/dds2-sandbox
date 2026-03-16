/**
 * DDS API Client — Core: auth state, token management, HTTP request helper.
 *
 * Domain-specific methods are in separate files under lib/api/.
 * This module re-exports the fully assembled `api` singleton.
 */

import type {
    TokenResponse, UserProfile, Project, ProjectMember, ProjectInvite,
    Transaction, UnassignedGroupRow, Account, CounterpartyCategory,
    CategoryRef, BalanceRow, DdsMonthRow, CashflowDailyRow, PlannedPayment,
    PlannedIncome, WbPayout, CostOrder, CostOrderItem, Nomenclature,
    DutyRule, IntegrationKey, SyncLog, FunnelDayRow, FunnelSummary,
    MessageResponse, ImportResult, Order, LeadTime, OrderGeographyResponse,
} from '@/types/api';

const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

export class ApiClient {
    private _isRefreshing = false;

    getToken(): string | null {
        if (typeof window === 'undefined') return null;
        return localStorage.getItem('dds_token');
    }

    setToken(token: string) {
        localStorage.setItem('dds_token', token);
    }

    getRefreshToken(): string | null {
        if (typeof window === 'undefined') return null;
        return localStorage.getItem('dds_refresh_token');
    }

    setRefreshToken(token: string) {
        localStorage.setItem('dds_refresh_token', token);
    }

    clearToken() {
        localStorage.removeItem('dds_token');
        localStorage.removeItem('dds_refresh_token');
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

    /**
     * Try to refresh the access token.
     * Returns: 'ok' if refreshed, 'invalid' if token is rejected (must logout),
     *          'unavailable' if backend is unreachable (keep tokens, retry later).
     */
    private async tryRefresh(): Promise<'ok' | 'invalid' | 'unavailable'> {
        const refreshToken = this.getRefreshToken();
        if (!refreshToken || this._isRefreshing) return 'invalid';

        this._isRefreshing = true;
        try {
            const res = await fetch(`${API_URL}/api/v1/auth/refresh`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_token: refreshToken }),
            });
            if (res.ok) {
                const data = await res.json();
                this.setToken(data.access_token);
                if (data.refresh_token) this.setRefreshToken(data.refresh_token);
                return 'ok';
            }
            // Explicit 401/403 from /refresh → token is genuinely invalid
            return 'invalid';
        } catch {
            // Network error (backend down during deploy) — don't invalidate tokens
            return 'unavailable';
        }
        finally { this._isRefreshing = false; }
    }

    async request<T>(
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

        let res = await fetch(`${API_URL}${path}`, {
            method,
            headers,
            body: body ? JSON.stringify(body) : undefined,
        });

        // On 401 — try refresh before giving up
        if (res.status === 401) {
            const refreshResult = await this.tryRefresh();
            if (refreshResult === 'ok') {
                // Retry the original request with new token
                headers['Authorization'] = `Bearer ${this.getToken()}`;
                res = await fetch(`${API_URL}${path}`, {
                    method,
                    headers,
                    body: body ? JSON.stringify(body) : undefined,
                });
            } else if (refreshResult === 'unavailable') {
                // Backend temporarily down (deploy) — don't clear tokens
                throw new Error('Сервер временно недоступен. Попробуйте через минуту.');
            }
            if (res.status === 401) {
                this.clearToken();
                if (typeof window !== 'undefined') {
                    window.location.href = '/login';
                }
                throw new Error('Unauthorized');
            }
        }

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            const detail = err.detail ?? err.error?.message;
            if (typeof detail === 'string') throw new Error(detail);
            if (Array.isArray(detail)) throw new Error(detail.map((d: any) => d.msg || JSON.stringify(d)).join('; '));
            throw new Error(typeof detail === 'object' ? JSON.stringify(detail) : `Error ${res.status}`);
        }

        return res.json();
    }

    /**
     * Helper for FormData uploads — shared by all upload methods.
     */
    async uploadFormData<T>(path: string, formData: FormData): Promise<T> {
        const headers: Record<string, string> = {};
        const token = this.getToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;
        const projectId = this.getProjectId();
        if (projectId) headers['X-Project-Id'] = String(projectId);

        const res = await fetch(`${API_URL}${path}`, {
            method: 'POST', headers, body: formData,
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            const detail = err.detail;
            if (typeof detail === 'string') throw new Error(detail);
            if (Array.isArray(detail)) throw new Error(detail.map((d: any) => `${d.loc ? d.loc.join('.') + ': ' : ''}${d.msg || JSON.stringify(d)}`).join('; '));
            throw new Error(JSON.stringify(detail) || `Error ${res.status}`);
        }
        return res.json();
    }
}

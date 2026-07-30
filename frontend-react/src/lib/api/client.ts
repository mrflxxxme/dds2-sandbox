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

// Node.js 22+ has a built-in localStorage that throws without --localstorage-file.
// Check for browser environment more reliably than just `typeof window`.
const isBrowser = typeof window !== 'undefined' && typeof window.document !== 'undefined';

function safeGetItem(key: string): string | null {
    if (!isBrowser) return null;
    try { return localStorage.getItem(key); } catch { return null; }
}

function safeSetItem(key: string, value: string): void {
    if (!isBrowser) return;
    try { localStorage.setItem(key, value); } catch { /* noop */ }
}

function safeRemoveItem(key: string): void {
    if (!isBrowser) return;
    try { localStorage.removeItem(key); } catch { /* noop */ }
}

export class ApiClient {
    private _refreshPromise: Promise<'ok' | 'invalid' | 'unavailable'> | null = null;

    getToken(): string | null {
        return safeGetItem('dds_token');
    }

    setToken(token: string) {
        safeSetItem('dds_token', token);
    }

    getRefreshToken(): string | null {
        return safeGetItem('dds_refresh_token');
    }

    setRefreshToken(token: string) {
        safeSetItem('dds_refresh_token', token);
    }

    clearToken() {
        safeRemoveItem('dds_token');
        safeRemoveItem('dds_refresh_token');
        safeRemoveItem('dds_project_id');
    }

    isAuthenticated(): boolean {
        return !!this.getToken();
    }

    /** Decode the JWT payload of the current access token (no signature check). */
    private decodeToken(): Record<string, unknown> | null {
        const token = this.getToken();
        if (!token) return null;
        const parts = token.split('.');
        if (parts.length !== 3) return null;
        try {
            let b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
            const pad = b64.length % 4;
            if (pad) b64 += '='.repeat(4 - pad);
            return JSON.parse(atob(b64)) as Record<string, unknown>;
        } catch {
            return null;
        }
    }

    /**
     * True when the stored token belongs to an external fulfillment account
     * (`ext` claim). Such a token is allowed ONLY at /api/v1/ff/* by the backend
     * choke-point middleware — on the main portal it 403s everything.
     */
    isExternalToken(): boolean {
        return !!this.decodeToken()?.ext;
    }

    /**
     * Guard for the main-portal client: it serves ONLY main endpoints, which the
     * backend blocks for external accounts. If an `ext` token lands here (stale
     * session, or an FF operator who used the main login form) the requests are
     * doomed — they 403, and cross-origin they fail CORS and never surface a
     * readable status, leaving the page stuck on "Failed to fetch". Bounce to the
     * FF portal before firing anything. Returns true when it redirected so callers
     * abort.
     *
     * Deliberately does NOT clearToken(): the dashboard fires many requests in
     * parallel, and clearing here makes the in-flight siblings see a tokenless
     * state, 401, and redirect to /login — racing over our /ff redirect. Leaving
     * the (inert, main-portal-blocked) token lets every sibling bounce to /ff
     * identically. To use the main app, an admin logs in at /login, overwriting it.
     */
    private bounceExternalToFf(): boolean {
        if (!this.isExternalToken()) return false;
        if (typeof window !== 'undefined') window.location.href = '/ff';
        return true;
    }

    getProjectId(): number | null {
        const pid = safeGetItem('dds_project_id');
        return pid ? parseInt(pid) : null;
    }

    setProjectId(id: number) {
        safeSetItem('dds_project_id', String(id));
    }

    /**
     * Try to refresh the access token.
     * All concurrent callers share the same refresh promise to avoid race conditions.
     * Returns: 'ok' if refreshed, 'invalid' if token is rejected (must logout),
     *          'unavailable' if backend is unreachable (keep tokens, retry later).
     */
    private async tryRefresh(): Promise<'ok' | 'invalid' | 'unavailable'> {
        const refreshToken = this.getRefreshToken();
        if (!refreshToken) return 'invalid';

        // If a refresh is already in progress, wait for the same result
        if (this._refreshPromise) return this._refreshPromise;

        this._refreshPromise = this._doRefresh(refreshToken);
        try {
            return await this._refreshPromise;
        } finally {
            this._refreshPromise = null;
        }
    }

    private async _doRefresh(refreshToken: string): Promise<'ok' | 'invalid' | 'unavailable'> {
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
    }

    async request<T>(
        method: string,
        path: string,
        body?: unknown,
    ): Promise<T> {
        if (this.bounceExternalToFf()) {
            throw new Error('Внешний аккаунт фулфилмента: доступ только к ФФ-порталу');
        }

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
            // Structured payload (sc17/sc18): backend возвращает error.payload как объект — сериализуем в JSON
            // чтобы вызывающий мог распарсить через JSON.parse(e.message).
            const payload = err.error?.payload;
            if (payload && typeof payload === 'object') {
                throw this.httpError(JSON.stringify(payload), res.status);
            }
            const detail = err.detail ?? err.error?.message;
            if (typeof detail === 'string') throw this.httpError(detail, res.status);
            if (Array.isArray(detail)) throw this.httpError(detail.map((d: any) => d.msg || JSON.stringify(d)).join('; '), res.status);
            if (detail && typeof detail === 'object') {
                // Структурированный detail (напр. 409-гейт настроек FBS-склада):
                // message остаётся JSON-строкой — существующие потребители,
                // читающие только .message, не ломаются; сам объект едет полем
                // error.detail для тех, кому нужен code и цифры, а не текст.
                throw this.httpError(JSON.stringify(detail), res.status, detail);
            }
            throw this.httpError(`Error ${res.status}`, res.status);
        }

        // 204 No Content / пустое тело (DELETE-эндпоинты): res.json() на пустоте
        // кидал «Unexpected end of JSON input», хотя операция прошла успешно.
        if (res.status === 204) return undefined as T;
        return res.json().catch(() => undefined as T);
    }

    /**
     * GET, возвращающий Blob (для скачивания/печати: ТТН, выгрузки).
     * Зеркалит request(): base URL (API_URL), Authorization, X-Project-Id, рефреш на 401.
     * Сырой fetch без этого ломается на проде (api на отдельном origin + нужен проектный контекст).
     */
    async requestBlob(path: string): Promise<Blob> {
        if (this.bounceExternalToFf()) {
            throw new Error('Внешний аккаунт фулфилмента: доступ только к ФФ-порталу');
        }

        const headers: Record<string, string> = {};
        const token = this.getToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;
        const projectId = this.getProjectId();
        if (projectId) headers['X-Project-Id'] = String(projectId);

        let res = await fetch(`${API_URL}${path}`, { headers });

        if (res.status === 401) {
            const refreshResult = await this.tryRefresh();
            if (refreshResult === 'ok') {
                headers['Authorization'] = `Bearer ${this.getToken()}`;
                res = await fetch(`${API_URL}${path}`, { headers });
            } else if (refreshResult === 'unavailable') {
                throw new Error('Сервер временно недоступен. Попробуйте через минуту.');
            }
            if (res.status === 401) {
                this.clearToken();
                if (typeof window !== 'undefined') window.location.href = '/login';
                throw new Error('Unauthorized');
            }
        }

        if (!res.ok) {
            const text = await res.text().catch(() => '');
            throw new Error(`Ошибка загрузки (${res.status})${text ? ': ' + text.slice(0, 200) : ''}`);
        }

        return res.blob();
    }

    /**
     * Helper for FormData uploads — shared by all upload methods.
     *
     * `opts.retries` включает авто-повтор при ТРАНЗИЕНТНОМ сбое — обрыве соединения
     * (fetch reject) или 5xx (напр. backend/nginx пересобираются во время деплоя →
     * in-flight запрос ловит 502/reset). 4xx НЕ ретраятся — это проблема самого файла.
     * Брошенная ошибка несёт `.status` (HTTP-код; 0 = сетевой обрыв) для классификации в UI.
     */
    async uploadFormData<T>(
        path: string,
        formData: FormData,
        opts?: { retries?: number; retryDelayMs?: number; timeoutMs?: number },
    ): Promise<T> {
        if (this.bounceExternalToFf()) {
            throw new Error('Внешний аккаунт фулфилмента: доступ только к ФФ-порталу');
        }

        const retries = opts?.retries ?? 0;
        const retryDelayMs = opts?.retryDelayMs ?? 1000;
        const timeoutMs = opts?.timeoutMs;

        const headers: Record<string, string> = {};
        const token = this.getToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;
        const projectId = this.getProjectId();
        if (projectId) headers['X-Project-Id'] = String(projectId);

        for (let attempt = 0; ; attempt++) {
            let res: Response;
            try {
                res = await this.fetchWithTimeout(`${API_URL}${path}`, { method: 'POST', headers, body: formData }, timeoutMs);
            } catch (netErr) {
                // Таймаут (AbortController) — терминальный, НЕ ретраим: сервер всё ещё
                // молотит в фоне, повтор только копит нагрузку (прод-инцидент со скан-счётом
                // 2026-07-23: залипший разбор держал DB-сессию → исчерпание пула).
                if ((netErr as { name?: string })?.name === 'AbortError') {
                    const e = new Error(
                        `Превышено время ожидания ответа сервера (${Math.round((timeoutMs ?? 0) / 1000)} с). Попробуйте ещё раз или заполните реквизиты вручную.`,
                    ) as Error & { status?: number };
                    e.status = 0;
                    throw e;
                }
                // Сетевой обрыв (напр. соединение сброшено при пересборке контейнеров) — транзиентно.
                if (attempt < retries) { await this.sleep(retryDelayMs); continue; }
                const e = netErr instanceof Error ? netErr : new Error('Ошибка сети');
                (e as Error & { status?: number }).status = 0;
                throw e;
            }

            // На 401 — рефреш токена и повтор (зеркалит request()/requestBlob()). Без этого
            // долгая форма (распознавание+подбор+выбор файла) → протухший токен → сырой Error 401.
            if (res.status === 401) {
                const refreshResult = await this.tryRefresh();
                if (refreshResult === 'ok') {
                    headers['Authorization'] = `Bearer ${this.getToken()}`;
                    res = await this.fetchWithTimeout(`${API_URL}${path}`, { method: 'POST', headers, body: formData }, timeoutMs);
                } else if (refreshResult === 'unavailable') {
                    throw new Error('Сервер временно недоступен. Попробуйте через минуту.');
                }
                if (res.status === 401) {
                    this.clearToken();
                    if (typeof window !== 'undefined') window.location.href = '/login';
                    throw new Error('Unauthorized');
                }
            }

            if (res.ok) return res.json();

            // 5xx — транзиентный серверный сбой (деплой/перезапуск): ретраим, если есть попытки.
            if (res.status >= 500 && attempt < retries) {
                await this.sleep(retryDelayMs);
                continue;
            }

            const err = await res.json().catch(() => ({}));
            const detail = err.detail;
            const message =
                typeof detail === 'string' ? detail
                : Array.isArray(detail) ? detail.map((d: any) => `${d.loc ? d.loc.join('.') + ': ' : ''}${d.msg || JSON.stringify(d)}`).join('; ')
                : (JSON.stringify(detail) || `Error ${res.status}`);
            const e = new Error(message) as Error & { status?: number };
            e.status = res.status;
            throw e;
        }
    }

    /**
     * `fetch` с опциональным таймаутом через AbortController. Без `timeoutMs` —
     * обычный fetch (поведение не меняется). При срабатывании таймаута реджектит
     * с `AbortError`, который вызывающий трактует как терминальный (без ретрая).
     */
    private async fetchWithTimeout(url: string, init: RequestInit, timeoutMs?: number): Promise<Response> {
        if (!timeoutMs) return fetch(url, init);
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        try {
            return await fetch(url, { ...init, signal: controller.signal });
        } finally {
            clearTimeout(timer);
        }
    }

    /**
     * Ошибка HTTP с сохранённым кодом ответа. Текст не меняется — существующие
     * вызывающие, читающие `.message`, не ломаются; `.status` — добавка для тех,
     * кому важен КОД, а не текст (напр. 403 = «вкладка не для вас», не «ошибка»).
     * `.detail` — структурированный detail бэка (объект), когда он был объектом:
     * потребитель опознаёт ошибку по `detail.code`, а не парсит message.
     * Зеркалит `uploadFormData()`, которое так делало и раньше.
     */
    private httpError(
        message: string, status: number, detail?: unknown,
    ): Error & { status: number; detail?: unknown } {
        const e = new Error(message) as Error & { status: number; detail?: unknown };
        e.status = status;
        if (detail !== undefined) e.detail = detail;
        return e;
    }

    private sleep(ms: number): Promise<void> {
        return new Promise(resolve => setTimeout(resolve, ms));
    }
}

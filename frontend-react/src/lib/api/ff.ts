/**
 * Fulfillment-operator portal API client (route group `(ff)`).
 *
 * Self-contained on purpose — it does NOT reuse the main `ApiClient` instance
 * because FF endpoints aggregate cross-project server-side and must NEVER send
 * the `X-Project-Id` header. Token STORAGE is ALSO separate: its own localStorage
 * keys (`dds_ff_token` / `dds_ff_refresh_token`), distinct from the main app's
 * `dds_token`. Sharing the key meant an FF login (external operator) clobbered an
 * admin's main-portal session and vice versa — and the new ext-isolation
 * middleware then 403'd every main API call, hanging the dashboard.
 *
 * 401 handling: try a single refresh; on failure clear tokens and redirect to
 * `/ff/login`. Non-OK responses throw with the server-provided message.
 */

import type {
    FfMe,
    FfAssemblyRow,
    FfAssemblyListResponse,
    FfAcceptanceRow,
    FfAcceptanceListResponse,
    FfAcceptItemInput,
    FfReadyInput,
    FfStockListResponse,
    FfAssemblyStatus,
    FfAcceptanceStatus,
} from '@/types/ff';

const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

const TOKEN_KEY = 'dds_ff_token';
const REFRESH_KEY = 'dds_ff_refresh_token';

const isBrowser =
    typeof window !== 'undefined' && typeof window.document !== 'undefined';

function getItem(key: string): string | null {
    if (!isBrowser) return null;
    try {
        return localStorage.getItem(key);
    } catch {
        return null;
    }
}

function setItem(key: string, value: string): void {
    if (!isBrowser) return;
    try {
        localStorage.setItem(key, value);
    } catch {
        /* noop */
    }
}

function removeItem(key: string): void {
    if (!isBrowser) return;
    try {
        localStorage.removeItem(key);
    } catch {
        /* noop */
    }
}

function getToken(): string | null {
    return getItem(TOKEN_KEY);
}

function getRefreshToken(): string | null {
    return getItem(REFRESH_KEY);
}

export function ffIsAuthenticated(): boolean {
    return !!getToken();
}

export function ffLogout(): void {
    removeItem(TOKEN_KEY);
    removeItem(REFRESH_KEY);
    if (isBrowser) window.location.href = '/ff/login';
}

let refreshPromise: Promise<'ok' | 'invalid' | 'unavailable'> | null = null;

async function doRefresh(refreshToken: string): Promise<'ok' | 'invalid' | 'unavailable'> {
    try {
        const res = await fetch(`${API_URL}/api/v1/auth/refresh`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (res.ok) {
            const data = await res.json();
            setItem(TOKEN_KEY, data.access_token);
            if (data.refresh_token) setItem(REFRESH_KEY, data.refresh_token);
            return 'ok';
        }
        return 'invalid';
    } catch {
        return 'unavailable';
    }
}

async function tryRefresh(): Promise<'ok' | 'invalid' | 'unavailable'> {
    const refreshToken = getRefreshToken();
    if (!refreshToken) return 'invalid';
    if (refreshPromise) return refreshPromise;
    refreshPromise = doRefresh(refreshToken);
    try {
        return await refreshPromise;
    } finally {
        refreshPromise = null;
    }
}

/** Thrown for non-OK responses; carries the HTTP status so callers can branch. */
export class FfApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
        super(message);
        this.name = 'FfApiError';
        this.status = status;
    }
}

async function extractError(res: Response): Promise<string> {
    const err = await res.json().catch(() => ({} as Record<string, unknown>));
    const detail = (err as { detail?: unknown; error?: { message?: unknown } }).detail
        ?? (err as { error?: { message?: unknown } }).error?.message;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        return detail
            .map((d) => {
                if (d && typeof d === 'object' && typeof (d as { msg?: unknown }).msg === 'string') {
                    return (d as { msg: string }).msg;
                }
                return JSON.stringify(d);
            })
            .join('; ');
    }
    if (detail && typeof detail === 'object') return JSON.stringify(detail);
    return `Ошибка ${res.status}`;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
    // IMPORTANT: never send X-Project-Id for FF endpoints.

    const fetchOnce = () =>
        fetch(`${API_URL}${path}`, {
            method,
            headers,
            body: body ? JSON.stringify(body) : undefined,
        });

    let res = await fetchOnce();

    if (res.status === 401) {
        const result = await tryRefresh();
        if (result === 'ok') {
            const fresh = getToken();
            if (fresh) headers['Authorization'] = `Bearer ${fresh}`;
            res = await fetchOnce();
        } else if (result === 'unavailable') {
            throw new FfApiError('Сервер временно недоступен. Попробуйте через минуту.', 503);
        }
        if (res.status === 401) {
            removeItem(TOKEN_KEY);
            removeItem(REFRESH_KEY);
            if (isBrowser) window.location.href = '/ff/login';
            throw new FfApiError('Требуется вход', 401);
        }
    }

    if (!res.ok) {
        throw new FfApiError(await extractError(res), res.status);
    }

    return res.json() as Promise<T>;
}

function buildQuery(params: Record<string, string | number | undefined | null>): string {
    const sp = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
        if (value === undefined || value === null || value === '') continue;
        sp.set(key, String(value));
    }
    const qs = sp.toString();
    return qs ? `?${qs}` : '';
}

// ── Auth ─────────────────────────────────────────────────────────────────────

export async function ffLogin(
    username: string,
    password: string,
): Promise<{ access_token: string; refresh_token?: string; token_type: string }> {
    const res = await request<{ access_token: string; refresh_token?: string; token_type: string }>(
        'POST',
        '/api/v1/auth/login',
        { username, password },
    );
    setItem(TOKEN_KEY, res.access_token);
    if (res.refresh_token) setItem(REFRESH_KEY, res.refresh_token);
    return res;
}

export function ffMe(): Promise<FfMe> {
    return request<FfMe>('GET', '/api/v1/ff/me');
}

// ── Assemblies ─────────────────────────────────────────────────────────────────

export function ffListAssemblies(opts: {
    status?: FfAssemblyStatus | '';
    warehouse_id?: number | null;
    limit?: number;
    offset?: number;
} = {}): Promise<FfAssemblyListResponse> {
    const query = buildQuery({
        status: opts.status || undefined,
        warehouse_id: opts.warehouse_id ?? undefined,
        limit: opts.limit ?? 50,
        offset: opts.offset ?? 0,
    });
    return request<FfAssemblyListResponse>('GET', `/api/v1/ff/assemblies${query}`);
}

export function ffStartAssembly(id: number): Promise<FfAssemblyRow> {
    return request<FfAssemblyRow>('POST', `/api/v1/ff/assemblies/${id}/start`);
}

export function ffReadyAssembly(id: number, body: FfReadyInput): Promise<FfAssemblyRow> {
    return request<FfAssemblyRow>('POST', `/api/v1/ff/assemblies/${id}/ready`, body);
}

export function ffShipAssembly(id: number): Promise<FfAssemblyRow> {
    return request<FfAssemblyRow>('POST', `/api/v1/ff/assemblies/${id}/ship`);
}

// ── Acceptances ──────────────────────────────────────────────────────────────

export function ffListAcceptances(opts: {
    status?: FfAcceptanceStatus | '';
    warehouse_id?: number | null;
    limit?: number;
    offset?: number;
} = {}): Promise<FfAcceptanceListResponse> {
    const query = buildQuery({
        status: opts.status || undefined,
        warehouse_id: opts.warehouse_id ?? undefined,
        limit: opts.limit ?? 50,
        offset: opts.offset ?? 0,
    });
    return request<FfAcceptanceListResponse>('GET', `/api/v1/ff/acceptances${query}`);
}

export function ffStartAcceptance(id: number): Promise<FfAcceptanceRow> {
    return request<FfAcceptanceRow>('POST', `/api/v1/ff/acceptances/${id}/start`);
}

export function ffAcceptAcceptance(
    id: number,
    items: FfAcceptItemInput[],
): Promise<FfAcceptanceRow> {
    return request<FfAcceptanceRow>('POST', `/api/v1/ff/acceptances/${id}/accept`, { items });
}

// ── Stock ────────────────────────────────────────────────────────────────────

export function ffListStock(opts: {
    warehouse_id?: number | null;
    barcode?: string;
    limit?: number;
    offset?: number;
} = {}): Promise<FfStockListResponse> {
    const query = buildQuery({
        warehouse_id: opts.warehouse_id ?? undefined,
        barcode: opts.barcode || undefined,
        limit: opts.limit ?? 100,
        offset: opts.offset ?? 0,
    });
    return request<FfStockListResponse>('GET', `/api/v1/ff/stock${query}`);
}

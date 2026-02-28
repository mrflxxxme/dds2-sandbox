/**
 * DDS API Client — typed fetch wrapper with JWT auth.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

interface ApiError {
    error: { code: string; message: string; details?: unknown[] };
}

let _token: string | null = null;

export function setToken(token: string | null) {
    _token = token;
    if (token) {
        if (typeof window !== "undefined") localStorage.setItem("dds_token", token);
    } else {
        if (typeof window !== "undefined") localStorage.removeItem("dds_token");
    }
}

export function getToken(): string | null {
    if (_token) return _token;
    if (typeof window !== "undefined") {
        _token = localStorage.getItem("dds_token");
    }
    return _token;
}

async function apiFetch<T>(
    path: string,
    options: RequestInit = {},
): Promise<T> {
    const token = getToken();
    const headers: Record<string, string> = {
        ...(options.headers as Record<string, string>),
    };
    if (token) {
        headers["Authorization"] = `Bearer ${token}`;
    }
    if (!(options.body instanceof FormData)) {
        headers["Content-Type"] = "application/json";
    }

    const res = await fetch(`${API_BASE}${path}`, { ...options, headers });

    if (!res.ok) {
        let body: ApiError | null = null;
        try {
            body = await res.json();
        } catch {
            /* empty */
        }
        const msg = body?.error?.message || `HTTP ${res.status}`;
        throw new Error(msg);
    }

    if (res.status === 204) return {} as T;
    return res.json();
}

// ─── Auth ────────────────────────────────────────────────────────────────────

export interface TokenResponse {
    access_token: string;
    token_type: string;
}

export async function login(
    username: string,
    password: string,
): Promise<TokenResponse> {
    const body = new URLSearchParams({ username, password });
    const data = await apiFetch<TokenResponse>("/api/v1/auth/login", {
        method: "POST",
        body,
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });
    setToken(data.access_token);
    return data;
}

export function logout() {
    setToken(null);
}

// ─── Generic typed requests ─────────────────────────────────────────────────

export const api = {
    get: <T>(path: string) => apiFetch<T>(path),
    post: <T>(path: string, body?: unknown) =>
        apiFetch<T>(path, { method: "POST", body: JSON.stringify(body) }),
    put: <T>(path: string, body?: unknown) =>
        apiFetch<T>(path, { method: "PUT", body: JSON.stringify(body) }),
    delete: <T>(path: string) => apiFetch<T>(path, { method: "DELETE" }),
    upload: <T>(path: string, file: File, fieldName = "file") => {
        const form = new FormData();
        form.append(fieldName, file);
        return apiFetch<T>(path, { method: "POST", body: form });
    },
};

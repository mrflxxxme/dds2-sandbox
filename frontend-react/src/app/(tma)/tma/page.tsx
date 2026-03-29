'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { getTelegramWebApp, getTelegramInitData, getTelegramUser, haptic } from '@/lib/telegram';
import { api } from '@/lib/api';

/**
 * TMA Entry Page — auth via initData, then pick project and redirect to dashboard.
 *
 * FIX #3: Uses direct fetch for /tma/auth instead of api.request()
 * to avoid automatic redirect to /login on 401.
 */

interface TmaProject {
    id: number;
    name: string;
    slug: string;
}

type PageState = 'loading' | 'not_linked' | 'pick_project' | 'error';

const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

/** Wait for Telegram WebApp SDK to initialize (up to 3s) */
function waitForTelegramSdk(timeout = 3000): Promise<ReturnType<typeof getTelegramWebApp>> {
    return new Promise((resolve) => {
        const twa = getTelegramWebApp();
        if (twa) { resolve(twa); return; }

        const start = Date.now();
        const interval = setInterval(() => {
            const twa = getTelegramWebApp();
            if (twa || Date.now() - start > timeout) {
                clearInterval(interval);
                resolve(twa);
            }
        }, 50);
    });
}

export default function TmaEntryPage() {
    const router = useRouter();
    const [state, setState] = useState<PageState>('loading');
    const [error, setError] = useState('');
    const [projects, setProjects] = useState<TmaProject[]>([]);
    const [userName, setUserName] = useState('');

    const authenticate = useCallback(async () => {
        // Wait for Telegram SDK to load (beforeInteractive doesn't work in route group layouts)
        const twa = await waitForTelegramSdk();
        if (!twa) {
            setError('Откройте приложение через Telegram');
            setState('error');
            return;
        }

        twa.ready();
        twa.expand();

        const initData = getTelegramInitData();
        if (!initData) {
            setError('Не удалось получить данные Telegram');
            setState('error');
            return;
        }

        const user = getTelegramUser();
        if (user) {
            setUserName(user.first_name);
        }

        try {
            // FIX #3: Direct fetch instead of api.request() to avoid 401 -> /login redirect
            const res = await fetch(`${API_URL}/api/v1/tma/auth`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ init_data: initData }),
            });

            if (!res.ok) {
                if (res.status === 403 || res.status === 401) {
                    setState('not_linked');
                    return;
                }
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `Ошибка ${res.status}`);
            }

            const data = await res.json();

            // Save JWT tokens
            api.setToken(data.access_token);
            if (data.refresh_token) {
                api.setRefreshToken(data.refresh_token);
            }

            // Load projects
            const projectsData = await api.request<TmaProject[]>('GET', '/api/v1/tma/projects');

            if (!projectsData || projectsData.length === 0) {
                setState('not_linked');
                return;
            }

            if (projectsData.length === 1) {
                // Single project — go directly to dashboard
                selectProject(projectsData[0]);
                return;
            }

            // Multiple projects — let user pick
            setProjects(projectsData);
            setState('pick_project');
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Ошибка авторизации');
            setState('error');
        }
    }, []);

    const selectProject = useCallback((project: TmaProject) => {
        haptic('light');
        api.setProjectId(project.id);
        localStorage.setItem('dds_project_name', project.name);
        router.replace(`/tma/${project.slug}`);
    }, [router]);

    useEffect(() => {
        authenticate();
    }, [authenticate]);

    if (state === 'loading') {
        return (
            <div className="tma-onboarding">
                <div className="tma-spinner" />
                <p style={{ marginTop: 16, color: 'var(--tma-hint)', fontSize: 14 }}>
                    Авторизация...
                </p>
            </div>
        );
    }

    if (state === 'not_linked') {
        return (
            <div className="tma-onboarding">
                <div className="tma-onboarding-icon">🔗</div>
                <div className="tma-onboarding-title">
                    Аккаунт не привязан
                </div>
                <div className="tma-onboarding-subtitle">
                    Привяжите Telegram в настройках DDS:
                    Настройки → Telegram → Привязать
                </div>
            </div>
        );
    }

    if (state === 'error') {
        return (
            <div className="tma-onboarding">
                <div className="tma-onboarding-icon">⚠️</div>
                <div className="tma-onboarding-title">Ошибка</div>
                <div className="tma-onboarding-error">{error}</div>
                <button
                    className="tma-btn tma-btn-primary"
                    style={{ marginTop: 20 }}
                    onClick={() => {
                        setState('loading');
                        setError('');
                        authenticate();
                    }}
                >
                    Повторить
                </button>
            </div>
        );
    }

    // pick_project state
    return (
        <div className="tma-onboarding">
            <div className="tma-onboarding-icon">📊</div>
            <div className="tma-onboarding-title">
                {userName ? `Привет, ${userName}!` : 'Выберите проект'}
            </div>
            <div className="tma-onboarding-subtitle">
                Выберите проект для просмотра
            </div>
            <div className="tma-project-list">
                {projects.map((p) => (
                    <button
                        key={p.id}
                        className="tma-project-btn"
                        onClick={() => selectProject(p)}
                    >
                        <div className="tma-project-icon">
                            {p.name.charAt(0).toUpperCase()}
                        </div>
                        <div>
                            <div className="tma-project-name">{p.name}</div>
                            <div className="tma-project-slug">{p.slug}</div>
                        </div>
                    </button>
                ))}
            </div>
        </div>
    );
}

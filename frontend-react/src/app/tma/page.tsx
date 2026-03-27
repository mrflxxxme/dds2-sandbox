'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { getTelegramInitData, getTelegramWebApp, isTelegramMiniApp } from '@/lib/telegram';

interface TmaProject {
    id: number;
    name: string;
    slug: string;
}

export default function TmaIndexPage() {
    const router = useRouter();
    const [state, setState] = useState<'loading' | 'not_linked' | 'pick_project' | 'error'>('loading');
    const [projects, setProjects] = useState<TmaProject[]>([]);
    const [errorMsg, setErrorMsg] = useState('');

    useEffect(() => {
        const tg = getTelegramWebApp();
        if (tg) {
            tg.ready();
            tg.expand();
        }

        authenticate();
    }, []);

    async function authenticate() {
        const initData = getTelegramInitData();

        if (!initData) {
            // Not in Telegram — check if already authenticated
            if (api.isAuthenticated()) {
                loadProjects();
                return;
            }
            setState('error');
            setErrorMsg('Откройте приложение через Telegram');
            return;
        }

        try {
            const res = await api.request<{
                access_token: string;
                refresh_token: string | null;
                user: { id: number; username: string; first_name: string; projects: TmaProject[] };
            }>('POST', '/api/v1/tma/auth', { init_data: initData });

            api.setToken(res.access_token);
            if (res.refresh_token) api.setRefreshToken(res.refresh_token);

            const userProjects = res.user.projects;
            if (userProjects.length === 1) {
                // Auto-select single project
                selectProject(userProjects[0]);
                return;
            }

            setProjects(userProjects);
            setState('pick_project');
        } catch (e: any) {
            const detail = e.message || '';
            if (detail.includes('account_not_linked')) {
                setState('not_linked');
            } else {
                setState('error');
                setErrorMsg(detail || 'Ошибка авторизации');
            }
        }
    }

    async function loadProjects() {
        try {
            const res = await api.request<TmaProject[]>('GET', '/api/v1/tma/projects');
            if (res.length === 1) {
                selectProject(res[0]);
                return;
            }
            setProjects(res);
            setState('pick_project');
        } catch {
            setState('error');
            setErrorMsg('Не удалось загрузить проекты');
        }
    }

    function selectProject(project: TmaProject) {
        api.setProjectId(project.id);
        if (typeof window !== 'undefined') {
            localStorage.setItem('dds_project_slug', project.slug);
            localStorage.setItem('dds_project_name', project.name);
        }
        router.replace(`/tma/${project.slug}`);
    }

    // ─── Loading ───
    if (state === 'loading') {
        return (
            <div className="tma-center">
                <div className="tma-spinner" />
            </div>
        );
    }

    // ─── Not linked ───
    if (state === 'not_linked') {
        return (
            <div className="tma-onboarding">
                <div className="tma-onboarding-icon">🔗</div>
                <div className="tma-onboarding-title">Привяжите аккаунт</div>
                <div className="tma-onboarding-text">
                    Чтобы пользоваться аналитикой, нужно связать Telegram с вашим аккаунтом DDS
                </div>
                <div className="tma-onboarding-steps">
                    <div className="tma-onboarding-step">
                        <span className="tma-step-num">1</span>
                        <span>Откройте DDS в браузере и перейдите в Настройки проекта</span>
                    </div>
                    <div className="tma-onboarding-step">
                        <span className="tma-step-num">2</span>
                        <span>Найдите раздел «Telegram-бот» и нажмите «Привязать»</span>
                    </div>
                    <div className="tma-onboarding-step">
                        <span className="tma-step-num">3</span>
                        <span>Перейдите по ссылке в Telegram и отправьте /start боту</span>
                    </div>
                    <div className="tma-onboarding-step">
                        <span className="tma-step-num">4</span>
                        <span>Вернитесь сюда — приложение заработает автоматически</span>
                    </div>
                </div>
                <button className="tma-btn tma-btn-primary" onClick={() => { setState('loading'); authenticate(); }}>
                    Проверить снова
                </button>
            </div>
        );
    }

    // ─── Error ───
    if (state === 'error') {
        return (
            <div className="tma-onboarding">
                <div className="tma-onboarding-icon">😕</div>
                <div className="tma-onboarding-title">Что-то пошло не так</div>
                <div className="tma-onboarding-text">{errorMsg}</div>
                <button className="tma-btn tma-btn-secondary" onClick={() => { setState('loading'); authenticate(); }}>
                    Попробовать снова
                </button>
            </div>
        );
    }

    // ─── Project picker ───
    return (
        <div>
            <div style={{ padding: '24px 16px 8px' }}>
                <div className="tma-page-title">Выберите проект</div>
            </div>
            <div className="tma-project-list">
                {projects.map(p => (
                    <div key={p.id} className="tma-project-item" onClick={() => selectProject(p)}>
                        <div className="tma-project-icon">
                            {p.name.charAt(0).toUpperCase()}
                        </div>
                        <div className="tma-project-name">{p.name}</div>
                    </div>
                ))}
            </div>
        </div>
    );
}

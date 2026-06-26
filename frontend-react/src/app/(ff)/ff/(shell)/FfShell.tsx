'use client';

import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import Link from 'next/link';
import { ffIsAuthenticated, ffMe, ffLogout, FfApiError } from '@/lib/api/ff';

const NAV = [
    { label: 'Приёмки', href: '/ff/acceptances' },
    { label: 'Заявки на сборку', href: '/ff/assemblies' },
    { label: 'Остатки', href: '/ff/stock' },
];

type GateState = 'checking' | 'ok' | 'denied' | 'error';

/**
 * Shell for the FF portal sections: fixed glass top bar + sticky pill nav +
 * auth/role gate. Auth: unauthenticated → redirect to /ff/login. On mount calls
 * ffMe(); a 403 means the user is not a fulfillment operator → access-denied.
 */
export default function FfShell({ children }: { children: React.ReactNode }) {
    const pathname = usePathname();
    const router = useRouter();
    const [state, setState] = useState<GateState>('checking');
    const [errorMsg, setErrorMsg] = useState('');

    useEffect(() => {
        if (!ffIsAuthenticated()) {
            router.replace('/ff/login');
            return;
        }
        const controller = new AbortController();
        setState('checking');
        ffMe()
            .then(() => {
                if (controller.signal.aborted) return;
                setState('ok');
            })
            .catch((err: unknown) => {
                if (controller.signal.aborted) return;
                if (err instanceof FfApiError && err.status === 403) {
                    setState('denied');
                } else if (err instanceof FfApiError && err.status === 401) {
                    // ffMe already redirected to /ff/login.
                    setState('checking');
                } else {
                    setErrorMsg(err instanceof Error ? err.message : 'Ошибка загрузки');
                    setState('error');
                }
            });
        return () => controller.abort();
    }, [router]);

    if (state === 'checking') {
        return (
            <div className="ff-shell">
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Загрузка…
                </div>
            </div>
        );
    }

    if (state === 'denied') {
        return (
            <div className="ff-shell">
                <div className="glass-card">
                    <h1 className="page-title">Нет доступа</h1>
                    <p className="page-subtitle">Доступ только для фулфилмента.</p>
                    <button className="btn btn-danger" onClick={ffLogout} style={{ marginTop: 16 }}>
                        Выйти
                    </button>
                </div>
            </div>
        );
    }

    if (state === 'error') {
        return (
            <div className="ff-shell">
                <div className="glass-card">
                    <div style={{ color: 'var(--color-danger)', marginBottom: 16 }}>{errorMsg}</div>
                    <div style={{ display: 'flex', gap: 8 }}>
                        <button className="btn btn-secondary btn-sm" onClick={() => router.refresh()}>
                            Повторить
                        </button>
                        <button className="btn btn-danger btn-sm" onClick={ffLogout}>
                            Выйти
                        </button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <>
            <header className="ff-topbar glass-card" style={{ padding: '10px 16px', borderRadius: 0 }}>
                <span className="page-title" style={{ fontSize: 20 }}>ФФ-портал</span>
                <button className="btn btn-secondary btn-sm" onClick={ffLogout}>
                    Выйти
                </button>
            </header>

            <div className="ff-shell">
                <nav className="ff-nav">
                    {NAV.map((item) => {
                        const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
                        return (
                            <Link
                                key={item.href}
                                href={item.href}
                                className={`sc-status-pill${active ? ' sc-status-pill-active' : ''}`}
                            >
                                {item.label}
                            </Link>
                        );
                    })}
                </nav>
                {children}
            </div>
        </>
    );
}

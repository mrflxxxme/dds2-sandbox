'use client';

import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import Link from 'next/link';
import { ffIsAuthenticated, ffMe, ffLogout, FfApiError } from '@/lib/api/ff';

const NAV = [
    { label: 'Приёмки', icon: '📥', href: '/ff/acceptances' },
    { label: 'Заявки', icon: '📦', href: '/ff/assemblies' },
    { label: 'Остатки', icon: '🏷️', href: '/ff/stock' },
];

type GateState = 'checking' | 'ok' | 'denied' | 'error';

/**
 * Shell for the FF portal sections: top bar + bottom nav + auth/role gate.
 * Auth: unauthenticated → redirect to /ff/login. On mount calls ffMe();
 * a 403 means the user is not a fulfillment operator → access-denied screen.
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
                <div className="ff-card">
                    <div className="ff-muted">Загрузка…</div>
                </div>
            </div>
        );
    }

    if (state === 'denied') {
        return (
            <div className="ff-shell">
                <div className="ff-card">
                    <div className="ff-section-title">Нет доступа</div>
                    <p className="ff-section-sub">Доступ только для фулфилмента.</p>
                    <button className="btn btn-danger" onClick={ffLogout} style={{ width: '100%' }}>
                        Выйти
                    </button>
                </div>
            </div>
        );
    }

    if (state === 'error') {
        return (
            <div className="ff-shell">
                <div className="ff-error">
                    <span>{errorMsg}</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => router.refresh()}>
                        Повторить
                    </button>
                    <button className="btn btn-danger btn-sm" onClick={ffLogout}>
                        Выйти
                    </button>
                </div>
            </div>
        );
    }

    return (
        <>
            <header className="ff-topbar">
                <span className="ff-topbar-title">ФФ-портал</span>
                <button className="btn btn-secondary btn-sm" onClick={ffLogout}>
                    Выйти
                </button>
            </header>

            <div className="ff-shell">{children}</div>

            <nav className="ff-bottom-nav">
                {NAV.map((item) => {
                    const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
                    return (
                        <Link
                            key={item.href}
                            href={item.href}
                            className={`ff-nav-item${active ? ' active' : ''}`}
                        >
                            <span className="ff-nav-icon">{item.icon}</span>
                            <span>{item.label}</span>
                        </Link>
                    );
                })}
            </nav>
        </>
    );
}

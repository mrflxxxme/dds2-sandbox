'use client';

import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import Link from 'next/link';
import {
    lenderIsAuthenticated,
    lenderMe,
    lenderLogout,
    LenderApiError,
} from '@/lib/api/lender';

const NAV: { label: string; href: string; icon: React.ReactNode }[] = [
    {
        label: 'Мои займы',
        href: '/lender',
        icon: (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12V7H5a2 2 0 0 1 0-4h14v4" />
                <path d="M3 5v14a2 2 0 0 0 2 2h16v-5" />
                <path d="M18 12a2 2 0 0 0 0 4h4v-4Z" />
            </svg>
        ),
    },
];

type GateState = 'checking' | 'ok' | 'denied' | 'error';

/**
 * Shell for the lender portal: desktop left sidebar (mirrors the main app's
 * layout) + auth/role gate. Auth: unauthenticated → redirect to /lender/login.
 * On mount calls lenderMe(); a 403 means the user is not a lender account →
 * access-denied. Sidebar collapses to a top bar under ~900px (see
 * .lender-sidebar rule in lender.css); desktop is primary.
 */
export default function LenderShell({ children }: { children: React.ReactNode }) {
    const pathname = usePathname();
    const router = useRouter();
    const [state, setState] = useState<GateState>('checking');
    const [errorMsg, setErrorMsg] = useState('');
    const [displayName, setDisplayName] = useState('');

    useEffect(() => {
        if (!lenderIsAuthenticated()) {
            router.replace('/lender/login');
            return;
        }
        const controller = new AbortController();
        setState('checking');
        lenderMe()
            .then((me) => {
                if (controller.signal.aborted) return;
                setDisplayName(me.display_name || me.username);
                setState('ok');
            })
            .catch((err: unknown) => {
                if (controller.signal.aborted) return;
                if (err instanceof LenderApiError && err.status === 403) {
                    setState('denied');
                } else if (err instanceof LenderApiError && err.status === 401) {
                    // lenderMe already redirected to /lender/login.
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
            <main className="main-content lender-main">
                <div className="lender-feed">
                    <div className="lender-skeleton" />
                    <div className="lender-skeleton" />
                    <div className="lender-skeleton" />
                </div>
            </main>
        );
    }

    if (state === 'denied') {
        return (
            <main className="main-content lender-main">
                <div className="glass-card">
                    <h1 className="page-title">Нет доступа</h1>
                    <p className="page-subtitle">Доступ только для займодавцев.</p>
                    <button className="btn btn-danger" onClick={lenderLogout} style={{ marginTop: 16 }}>
                        Выйти
                    </button>
                </div>
            </main>
        );
    }

    if (state === 'error') {
        return (
            <main className="main-content lender-main">
                <div className="glass-card">
                    <h1 className="page-title">Что-то пошло не так</h1>
                    <p className="page-subtitle" style={{ color: 'var(--color-danger)' }}>{errorMsg}</p>
                    <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
                        <button className="btn btn-secondary" onClick={() => router.refresh()}>
                            Повторить
                        </button>
                        <button className="btn btn-danger" onClick={lenderLogout}>
                            Выйти
                        </button>
                    </div>
                </div>
            </main>
        );
    }

    return (
        <div>
            <aside className="sidebar lender-sidebar">
                <div className="sidebar-logo">DDS · Займы</div>

                <nav className="sidebar-nav">
                    {NAV.map((item) => {
                        const active =
                            pathname === item.href ||
                            (item.href === '/lender'
                                ? pathname.startsWith('/lender/loans')
                                : pathname.startsWith(`${item.href}/`));
                        return (
                            <Link
                                key={item.href}
                                href={item.href}
                                className={`sidebar-link${active ? ' active' : ''}`}
                            >
                                {item.icon}
                                <span>{item.label}</span>
                            </Link>
                        );
                    })}
                </nav>

                <div className="sidebar-user">
                    <div className="sidebar-avatar">{(displayName.charAt(0) || 'З').toUpperCase()}</div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 14, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {displayName || 'Займодавец'}
                        </div>
                    </div>
                    <button className="btn btn-secondary btn-sm" onClick={lenderLogout}>
                        Выйти
                    </button>
                </div>
            </aside>

            <main className="main-content lender-main">
                {children}
            </main>
        </div>
    );
}

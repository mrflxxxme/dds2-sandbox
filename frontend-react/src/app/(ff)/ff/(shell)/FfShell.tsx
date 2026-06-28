'use client';

import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import Link from 'next/link';
import {
    ffIsAuthenticated,
    ffMe,
    ffLogout,
    ffListAssemblies,
    ffListAcceptances,
    FfApiError,
} from '@/lib/api/ff';
import type { FfAssemblyStatus } from '@/types/ff';

/** Which nav items show an action-needed counter (keyed by href). */
type CountKey = 'acceptances' | 'assemblies';

const NAV: { label: string; href: string; countKey?: CountKey; icon: React.ReactNode }[] = [
    {
        label: 'Приёмки',
        href: '/ff/acceptances',
        countKey: 'acceptances',
        icon: (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
                <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
                <line x1="12" y1="22.08" x2="12" y2="12" />
            </svg>
        ),
    },
    {
        label: 'Заявки на сборку',
        href: '/ff/assemblies',
        countKey: 'assemblies',
        icon: (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="7" height="7" />
                <rect x="14" y="3" width="7" height="7" />
                <rect x="14" y="14" width="7" height="7" />
                <rect x="3" y="14" width="7" height="7" />
            </svg>
        ),
    },
    {
        label: 'Остатки',
        href: '/ff/stock',
        icon: (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20 7L12 3 4 7v10l8 4 8-4V7z" />
                <path d="M4 7l8 4 8-4" />
                <line x1="12" y1="11" x2="12" y2="21" />
            </svg>
        ),
    },
];

type GateState = 'checking' | 'ok' | 'denied' | 'error';

/**
 * Shell for the FF portal sections: desktop left sidebar (mirrors the main app's
 * `(main)/p/[slug]/layout.tsx`) + auth/role gate. Auth: unauthenticated →
 * redirect to /ff/login. On mount calls ffMe(); a 403 means the user is not a
 * fulfillment operator → access-denied. Sidebar collapses to a top bar under
 * ~900px (see .ff-sidebar media rule in ff.css); desktop is primary.
 */
export default function FfShell({ children }: { children: React.ReactNode }) {
    const pathname = usePathname();
    const router = useRouter();
    const [state, setState] = useState<GateState>('checking');
    const [errorMsg, setErrorMsg] = useState('');
    const [operator, setOperator] = useState('');
    const [counts, setCounts] = useState<Partial<Record<CountKey, number>>>({});

    useEffect(() => {
        if (!ffIsAuthenticated()) {
            router.replace('/ff/login');
            return;
        }
        const controller = new AbortController();
        setState('checking');
        ffMe()
            .then((me) => {
                if (controller.signal.aborted) return;
                setOperator(me.username);
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

    // Lightweight action-needed totals for the nav badges. Fetched only after the
    // gate passes; never blocks the gate. On error the count is simply omitted.
    useEffect(() => {
        if (state !== 'ok') return;
        let cancelled = false;
        // Backend accepts a comma-separated status list; it's not a single enum
        // member, so widen to the param's union type for this multi-status filter.
        ffListAssemblies({ status: 'PENDING,IN_PROGRESS' as FfAssemblyStatus, limit: 1 })
            .then((res) => {
                if (!cancelled) setCounts((c) => ({ ...c, assemblies: res.total }));
            })
            .catch(() => {
                /* omit the badge on error */
            });
        ffListAcceptances({ status: 'EXPECTED', limit: 1 })
            .then((res) => {
                if (!cancelled) setCounts((c) => ({ ...c, acceptances: res.total }));
            })
            .catch(() => {
                /* omit the badge on error */
            });
        return () => {
            cancelled = true;
        };
    }, [state]);

    if (state === 'checking') {
        return (
            <main className="main-content ff-main">
                <div className="ff-feed">
                    <div className="ff-skeleton" />
                    <div className="ff-skeleton" />
                    <div className="ff-skeleton" />
                </div>
            </main>
        );
    }

    if (state === 'denied') {
        return (
            <main className="main-content ff-main">
                <div className="glass-card">
                    <h1 className="page-title">Нет доступа</h1>
                    <p className="page-subtitle">Доступ только для фулфилмента.</p>
                    <button className="btn btn-danger" onClick={ffLogout} style={{ marginTop: 16 }}>
                        Выйти
                    </button>
                </div>
            </main>
        );
    }

    if (state === 'error') {
        return (
            <main className="main-content ff-main">
                <div className="glass-card">
                    <h1 className="page-title">Что-то пошло не так</h1>
                    <p className="page-subtitle" style={{ color: 'var(--color-danger)' }}>{errorMsg}</p>
                    <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
                        <button className="btn btn-secondary" onClick={() => router.refresh()}>
                            Повторить
                        </button>
                        <button className="btn btn-danger" onClick={ffLogout}>
                            Выйти
                        </button>
                    </div>
                </div>
            </main>
        );
    }

    return (
        <div>
            <aside className="sidebar ff-sidebar">
                <div className="sidebar-logo">DDS · ФФ</div>

                <nav className="sidebar-nav">
                    {NAV.map((item) => {
                        const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
                        const count = item.countKey ? counts[item.countKey] : undefined;
                        return (
                            <Link
                                key={item.href}
                                href={item.href}
                                className={`sidebar-link${active ? ' active' : ''}`}
                            >
                                {item.icon}
                                <span>{item.label}</span>
                                {count != null && count > 0 && (
                                    <span className="ff-nav-count">{count}</span>
                                )}
                            </Link>
                        );
                    })}
                </nav>

                <div className="sidebar-user">
                    <div className="sidebar-avatar">{(operator.charAt(0) || 'Ф').toUpperCase()}</div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 14, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {operator || 'Оператор'}
                        </div>
                    </div>
                    <button className="btn btn-secondary btn-sm" onClick={ffLogout}>
                        Выйти
                    </button>
                </div>
            </aside>

            <main className="main-content ff-main">
                {children}
            </main>
        </div>
    );
}

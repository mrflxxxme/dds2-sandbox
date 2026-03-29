'use client';

import { useEffect, useCallback, useRef } from 'react';
import { usePathname, useRouter, useParams } from 'next/navigation';
import { getTelegramWebApp, haptic } from '@/lib/telegram';

/**
 * Slug layout — bottom navigation + Telegram BackButton handling.
 *
 * FIX #6: BackButton cleanup with abort flag for async operations.
 */

interface NavItem {
    label: string;
    icon: string;
    path: string;
}

export default function TmaSlugLayout({ children }: { children: React.ReactNode }) {
    const pathname = usePathname();
    const router = useRouter();
    const params = useParams();
    const slug = params.slug as string;
    const abortedRef = useRef(false);

    const navItems: NavItem[] = [
        { label: 'Главная', icon: '📊', path: `/tma/${slug}` },
        { label: 'Склад', icon: '📦', path: `/tma/${slug}/warehouse` },
        { label: 'P&L', icon: '📈', path: `/tma/${slug}/pnl` },
        { label: 'AI', icon: '💬', path: `/tma/${slug}/chat` },
    ];

    const isSubPage = pathname !== `/tma/${slug}`;

    // BackButton handling with proper cleanup (FIX #6)
    useEffect(() => {
        abortedRef.current = false;
        const twa = getTelegramWebApp();
        if (!twa) return;

        const handleBack = () => {
            if (abortedRef.current) return;
            haptic('light');
            router.back();
        };

        if (isSubPage) {
            twa.BackButton.show();
            twa.BackButton.onClick(handleBack);
        } else {
            twa.BackButton.hide();
        }

        return () => {
            abortedRef.current = true;
            twa.BackButton.offClick(handleBack);
            twa.BackButton.hide();
        };
    }, [isSubPage, router]);

    // Signal ready to Telegram
    useEffect(() => {
        const twa = getTelegramWebApp();
        if (twa) {
            twa.ready();
        }
    }, []);

    const handleNavClick = useCallback((path: string) => {
        haptic('selection');
        router.push(path);
    }, [router]);

    return (
        <>
            <div style={{ paddingBottom: 72 }}>
                {children}
            </div>
            <nav className="tma-bottom-nav">
                {navItems.map((item) => {
                    const isActive = pathname === item.path;
                    return (
                        <button
                            key={item.path}
                            className={`tma-nav-item${isActive ? ' active' : ''}`}
                            onClick={() => handleNavClick(item.path)}
                            style={{ border: 'none', background: 'none', cursor: 'pointer' }}
                        >
                            <span className="tma-nav-icon">{item.icon}</span>
                            <span>{item.label}</span>
                        </button>
                    );
                })}
            </nav>
        </>
    );
}

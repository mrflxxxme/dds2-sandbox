'use client';
import { useEffect, useState } from 'react';
import { useParams, usePathname, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { getTelegramWebApp } from '@/lib/telegram';
import Link from 'next/link';

const navItems = [
    { href: '', icon: '📊', label: 'Главная' },
    { href: '/pnl', icon: '📋', label: 'P&L' },
    { href: '/chat', icon: '💬', label: 'AI' },
];

export default function TmaProjectLayout({ children }: { children: React.ReactNode }) {
    const params = useParams();
    const pathname = usePathname();
    const router = useRouter();
    const slug = params.slug as string;
    const [ready, setReady] = useState(false);

    useEffect(() => {
        // Sync project
        if (!api.isAuthenticated()) {
            router.replace('/tma');
            return;
        }

        api.getProject(slug).then(p => {
            api.setProjectId(p.id);
            setReady(true);
        }).catch(() => {
            router.replace('/tma');
        });

        // Telegram BackButton
        const tg = getTelegramWebApp();
        if (tg) {
            tg.BackButton.show();
            const handleBack = () => {
                const base = `/tma/${slug}`;
                if (pathname === base || pathname === base + '/') {
                    tg.BackButton.hide();
                    router.replace('/tma');
                } else {
                    router.back();
                }
            };
            tg.BackButton.onClick(handleBack);
            return () => tg.BackButton.offClick(handleBack);
        }
    }, [slug, pathname]);

    if (!ready) {
        return (
            <div className="tma-center">
                <div className="tma-spinner" />
            </div>
        );
    }

    const isActive = (href: string) => {
        const full = `/tma/${slug}${href}`;
        if (href === '') return pathname === full || pathname === full + '/';
        return pathname.startsWith(full);
    };

    return (
        <>
            {children}
            <nav className="tma-nav">
                {navItems.map(item => (
                    <Link
                        key={item.href}
                        href={`/tma/${slug}${item.href}`}
                        className={`tma-nav-item ${isActive(item.href) ? 'active' : ''}`}
                    >
                        <span className="tma-nav-icon">{item.icon}</span>
                        <span>{item.label}</span>
                    </Link>
                ))}
            </nav>
        </>
    );
}

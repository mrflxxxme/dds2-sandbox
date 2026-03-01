'use client';
import { useEffect, useState } from 'react';
import { useParams, usePathname } from 'next/navigation';
import { api } from '@/lib/api';
import Link from 'next/link';

const navItems = [
    { href: '', label: 'Дашборд', icon: '📊' },
    { href: '/import', label: 'Импорт выписок', icon: '📥' },
    { href: '/txn', label: 'Операции', icon: '💳' },
    { href: '/inbox', label: 'INBOX / Неразнесённые', icon: '🔴' },
    { href: '/reports', label: 'Отчёты', icon: '📈' },
    { href: '/planning', label: 'Планирование', icon: '📦' },
    { href: '/cost', label: 'Себестоимость', icon: '🧮' },
    { href: '/funnel', label: 'Воронка продаж', icon: '📊' },
    { href: '/refs', label: 'Справочники', icon: '📋' },
];

const settingsItems = [
    { href: '/settings', label: 'Настройки', icon: '⚙️' },
    { href: '/team', label: 'Команда', icon: '👥' },
];

export default function ProjectLayout({ children }: { children: React.ReactNode }) {
    const params = useParams();
    const pathname = usePathname();
    const slug = params.slug as string;
    const [username, setUsername] = useState('');
    const [projectName, setProjectName] = useState('');

    useEffect(() => {
        if (!api.isAuthenticated()) { window.location.href = '/login'; return; }
        // Resolve slug to project_id and sync localStorage
        api.getProject(slug).then(p => {
            api.setProjectId(p.id);
            localStorage.setItem('dds_project_slug', p.slug);
            localStorage.setItem('dds_project_name', p.name);
            setProjectName(p.name);
        }).catch(() => {
            setProjectName(slug);
        });
        api.getProfile().then(u => setUsername(u.username)).catch(() => { });
    }, [slug]);

    const isActive = (href: string) => {
        const full = `/p/${slug}${href}`;
        return pathname === full;
    };

    return (
        <div>
            <aside className="sidebar">
                <Link href="/projects" className="sidebar-logo" style={{ display: 'block', textDecoration: 'none' }}>DDS</Link>

                <div style={{ padding: '0 12px 12px' }}>
                    <div className="project-selector" onClick={() => window.location.href = '/projects'}>
                        <span style={{ fontSize: 14 }}>📁</span>
                        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{projectName}</span>
                        <span style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>⌄</span>
                    </div>
                </div>

                <nav className="sidebar-nav">
                    <div className="sidebar-section">Данные</div>
                    {navItems.map(item => (
                        <Link key={item.href} href={`/p/${slug}${item.href}`}
                            className={`sidebar-link ${isActive(item.href) ? 'active' : ''}`}>
                            <span>{item.icon}</span>
                            <span>{item.label}</span>
                        </Link>
                    ))}

                    <div className="sidebar-section" style={{ marginTop: 8 }}>Управление</div>
                    {settingsItems.map(item => (
                        <Link key={item.href} href={`/p/${slug}${item.href}`}
                            className={`sidebar-link ${isActive(item.href) ? 'active' : ''}`}>
                            <span>{item.icon}</span>
                            <span>{item.label}</span>
                        </Link>
                    ))}
                </nav>

                <div className="sidebar-user">
                    <div className="sidebar-avatar">{username.charAt(0).toUpperCase()}</div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 14, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{username}</div>
                    </div>
                    <Link href="/profile" style={{ color: 'var(--color-text-dim)', fontSize: 16, textDecoration: 'none' }} title="Профиль">⚙</Link>
                </div>
            </aside>

            <main className="main-content">
                {children}
            </main>
        </div>
    );
}

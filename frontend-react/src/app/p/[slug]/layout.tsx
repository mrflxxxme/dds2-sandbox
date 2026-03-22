'use client';
import { useEffect, useState } from 'react';
import { useParams, usePathname } from 'next/navigation';
import { api } from '@/lib/api';
import Link from 'next/link';

const dashboardItem = { href: '', label: 'Дашборд', icon: '📊' };

const navGroups = [
    {
        title: 'Финансы',
        items: [
            { href: '/import', label: 'Импорт документов', icon: '📥' },
            { href: '/txn', label: 'Операции', icon: '💳' },
            { href: '/inbox', label: 'INBOX — Неразнесённые', icon: '🔴' },
            { href: '/reports', label: 'Отчёты', icon: '📈' },
            { href: '/cost', label: 'Себестоимость', icon: '💰' },
            { href: '/refs', label: 'Справочники', icon: '📋' },
        ],
    },
    {
        title: 'Склад',
        items: [
            { href: '/warehouse', label: 'Склады', icon: '🏢' },
            { href: '/warehouse/assembly', label: 'Заявки на сборку', icon: '📋' },
            { href: '/warehouse/logistics', label: 'Лист логиста', icon: '🚛' },
            { href: '/warehouse/stock', label: 'Сводные остатки', icon: '📦' },
            { href: '/warehouse/fbo-supplies', label: 'Поставки FBO', icon: '📮' },
            { href: '/warehouse/wb-stocks', label: 'Остатки WB', icon: '🏭' },
            { href: '/warehouse/analytics', label: 'Аналитика остатков', icon: '📊' },
        ],
    },
    {
        title: 'Заказы',
        items: [
            { href: '/planning', label: 'Планирование', icon: '📦' },
            { href: '/container-loader', label: 'Загрузка контейнера', icon: '🚛' },
        ],
    },
    {
        title: 'Продажи',
        items: [
            { href: '/funnel', label: 'Воронка продаж', icon: '📊' },
            { href: '/trends', label: 'Метрики и тренды', icon: '📈' },
            { href: '/order-geography', label: 'Куда заказывают', icon: '🗺️' },
            { href: '/opiu', label: 'ОПИУ', icon: '📋' },
            { href: '/plan-fact', label: 'План-Факт', icon: '🎯' },
        ],
    },
    {
        title: 'Настройки',
        items: [
            { href: '/settings', label: 'Настройка проекта', icon: '⚙️' },
            { href: '/team', label: 'Команда', icon: '👥' },
        ],
    },
];

export default function ProjectLayout({ children }: { children: React.ReactNode }) {
    const params = useParams();
    const pathname = usePathname();
    const slug = params.slug as string;
    const [username, setUsername] = useState('');
    const [projectName, setProjectName] = useState('');
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
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
        if (!mounted) return false;
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
                    {/* Дашборд — отдельно, без группы */}
                    <Link href={`/p/${slug}`}
                        className={`sidebar-link ${isActive('') ? 'active' : ''}`}>
                        <span>{dashboardItem.icon}</span>
                        <span>{dashboardItem.label}</span>
                    </Link>

                    {navGroups.map(group => (
                        <div key={group.title}>
                            <div className="sidebar-section" style={{ marginTop: 8 }}>{group.title}</div>
                            {group.items.map(item => (
                                <Link key={item.href} href={`/p/${slug}${item.href}`}
                                    className={`sidebar-link ${isActive(item.href) ? 'active' : ''}`}>
                                    <span>{item.icon}</span>
                                    <span>{item.label}</span>
                                </Link>
                            ))}
                        </div>
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

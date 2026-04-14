'use client';
import { useEffect, useState } from 'react';
import { useParams, usePathname } from 'next/navigation';
import { api } from '@/lib/api';
import { usePermissions } from '@/lib/hooks/usePermissions';
import Link from 'next/link';

const dashboardItem = { href: '', label: 'Дашборд', icon: '📊' };

const navGroups = [
    {
        title: 'Финансы',
        section: 'finance',
        items: [
            { href: '/import', label: 'Импорт документов', icon: '📥', pageKey: 'import' },
            { href: '/txn', label: 'Операции', icon: '💳', pageKey: 'txn' },
            { href: '/inbox', label: 'INBOX — Неразнесённые', icon: '🔴', pageKey: 'inbox' },
            { href: '/reports', label: 'Отчёты', icon: '📈', pageKey: 'reports' },
            { href: '/cost', label: 'Себестоимость', icon: '💰', pageKey: 'cost' },
            { href: '/reports/cost-dna', label: 'ДНК себестоимости', icon: '🧬', pageKey: 'reports' },
            { href: '/refs', label: 'Справочники', icon: '📋', pageKey: 'refs' },
        ],
    },
    {
        title: 'Склад',
        section: 'warehouse',
        items: [
            { href: '/warehouse', label: 'Склады', icon: '🏢', pageKey: 'warehouse' },
            { href: '/warehouse/assembly', label: 'Заявки на сборку', icon: '📋', pageKey: 'assembly' },
            { href: '/warehouse/logistics', label: 'Лист логиста', icon: '🚛', pageKey: 'logistics' },
            { href: '/warehouse/stock', label: 'Сводные остатки', icon: '📦', pageKey: 'stocks' },
            { href: '/warehouse/fbo-supplies', label: 'Поставки FBO', icon: '📮', pageKey: 'fbo' },
            { href: '/warehouse/wb-stocks', label: 'Остатки WB', icon: '🏭', pageKey: 'stocks' },
            { href: '/warehouse/analytics', label: 'Аналитика остатков', icon: '📊', pageKey: 'stock-analytics' },
        ],
    },
    {
        title: 'Заказы',
        section: 'orders',
        items: [
            { href: '/planning', label: 'Планирование', icon: '📦', pageKey: 'planning' },
            { href: '/supply-chain', label: 'Поставки', icon: '🚚', pageKey: 'supply-chain' },
            { href: '/container-loader', label: 'Загрузка контейнера', icon: '🚛', pageKey: 'container' },
        ],
    },
    {
        title: 'Продажи',
        section: 'sales',
        items: [
            { href: '/funnel', label: 'Воронка продаж', icon: '📊', pageKey: 'funnel' },
            { href: '/trends', label: 'Метрики и тренды', icon: '📈', pageKey: 'trends' },
            { href: '/order-geography', label: 'Куда заказывают', icon: '🗺️', pageKey: 'geography' },
            { href: '/opiu', label: 'ОПИУ', icon: '📋', pageKey: 'opiu' },
            { href: '/plan-fact', label: 'План-Факт', icon: '🎯', pageKey: 'plan-fact' },
        ],
    },
    {
        title: 'AI',
        section: 'ai',
        items: [
            { href: '/ai-chat', label: 'AI-ассистент', icon: '🤖', pageKey: 'ai-chat' },
        ],
    },
    {
        title: 'Настройки',
        section: 'settings',
        items: [
            { href: '/monitoring', label: 'Мониторинг', icon: '📡', pageKey: 'monitoring' },
            { href: '/settings', label: 'Настройка проекта', icon: '⚙️', pageKey: 'project-settings' },
            { href: '/team', label: 'Команда', icon: '👥', pageKey: 'team' },
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
    const { canAccess, canManage, loading: permLoading } = usePermissions();

    useEffect(() => {
        setMounted(true);
        if (!api.isAuthenticated()) { window.location.href = '/login'; return; }
        // Resolve slug to project_id and sync localStorage
        api.getProject(slug).then(p => {
            api.setProjectId(p.id);
            try { localStorage.setItem('dds_project_slug', p.slug); } catch { /* SSR */ }
            try { localStorage.setItem('dds_project_name', p.name); } catch { /* SSR */ }
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

    // Filter nav groups based on permissions
    const filteredGroups = navGroups.map(group => {
        const visibleItems = group.items.filter(item => canAccess(item.pageKey));
        return { ...group, items: visibleItems };
    }).filter(group => group.items.length > 0);

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
                    {/* Дашборд — отдельно, без группы, всегда виден */}
                    <Link href={`/p/${slug}`}
                        className={`sidebar-link ${isActive('') ? 'active' : ''}`}>
                        <span>{dashboardItem.icon}</span>
                        <span>{dashboardItem.label}</span>
                    </Link>

                    {filteredGroups.map(group => (
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

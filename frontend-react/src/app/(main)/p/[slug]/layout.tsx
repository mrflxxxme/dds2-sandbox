'use client';
import { useEffect, useState } from 'react';
import { useParams, usePathname } from 'next/navigation';
import { api } from '@/lib/api';
import { usePermissions } from '@/lib/hooks/usePermissions';
import ChangelogBell from '@/components/ChangelogBell';
import Link from 'next/link';

const dashboardItem = { href: '', label: 'Дашборд' };

// Минималистичные монохромные иконки секций (line-стиль, currentColor)
const Icon = ({ children }: { children: React.ReactNode }) => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">{children}</svg>
);

const dashboardIcon = (
    <Icon><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /></Icon>
);

const sectionIcons: Record<string, React.ReactNode> = {
    finance: <Icon><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4" /><path d="M3 5v14a2 2 0 0 0 2 2h16v-5" /><path d="M18 12a2 2 0 0 0 0 4h4v-4Z" /></Icon>,
    refs: <Icon><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></Icon>,
    warehouse: <Icon><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" /><path d="M3.3 7 12 12l8.7-5" /><path d="M12 22V12" /></Icon>,
    orders: <Icon><circle cx="8" cy="21" r="1" /><circle cx="19" cy="21" r="1" /><path d="M2 2h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57L23 6H5.12" /></Icon>,
    sales: <Icon><polyline points="22 7 13.5 15.5 8.5 10.5 2 17" /><polyline points="16 7 22 7 22 13" /></Icon>,
    ai: <Icon><path d="M12 3 13.9 8.1 19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" /><path d="M19 15l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z" /></Icon>,
    settings: <Icon><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></Icon>,
};

// Минималистичные иконки пунктов навигации (ключ — href)
const itemIcons: Record<string, React.ReactNode> = {
    // Финансы
    '/import': <Icon><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" x2="12" y1="15" y2="3" /></Icon>,
    '/txn': <Icon><rect width="20" height="14" x="2" y="5" rx="2" /><line x1="2" x2="22" y1="10" y2="10" /></Icon>,
    '/inbox': <Icon><polyline points="22 12 16 12 14 15 10 15 8 12 2 12" /><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" /></Icon>,
    '/reports': <Icon><path d="M3 3v18h18" /><path d="m19 9-5 5-4-4-3 3" /></Icon>,
    '/cost': <Icon><rect width="20" height="12" x="2" y="6" rx="2" /><circle cx="12" cy="12" r="2" /><path d="M6 12h.01" /><path d="M18 12h.01" /></Icon>,
    '/reports/cost-dna': <Icon><path d="M22 12h-4l-3 9L9 3l-3 9H2" /></Icon>,
    // Справочники
    '/refs/counterparty': <Icon><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></Icon>,
    '/refs/loans': <Icon><circle cx="8" cy="8" r="6" /><path d="M18.09 10.37A6 6 0 1 1 10.34 18" /><path d="M7 6h1v4" /><path d="m16.71 13.88.7.71-2.82 2.82" /></Icon>,
    '/refs': <Icon><rect width="8" height="4" x="8" y="2" rx="1" /><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" /><path d="M12 11h4" /><path d="M12 16h4" /><path d="M8 11h.01" /><path d="M8 16h.01" /></Icon>,
    // Склад
    '/warehouse': <Icon><path d="M22 8.35V20a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8.35A2 2 0 0 1 3.26 6.5l8-3.2a2 2 0 0 1 1.48 0l8 3.2A2 2 0 0 1 22 8.35Z" /><path d="M6 18h12" /><path d="M6 14h12" /><path d="M6 10h12" /></Icon>,
    '/warehouse/assembly': <Icon><rect width="8" height="4" x="8" y="2" rx="1" /><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" /><path d="m9 14 2 2 4-4" /></Icon>,
    '/warehouse/assembly/distribute': <Icon><path d="M5.5 8.5 9 12l-3.5 3.5L2 12z" /><path d="m12 2 3.5 3.5L12 9 8.5 5.5z" /><path d="M18.5 8.5 22 12l-3.5 3.5L15 12z" /><path d="m12 15 3.5 3.5L12 22l-3.5-3.5z" /></Icon>,
    '/warehouse/assembly/analytics': <Icon><line x1="10" x2="14" y1="2" y2="2" /><line x1="12" x2="15" y1="14" y2="11" /><circle cx="12" cy="14" r="8" /></Icon>,
    '/warehouse/ff-requests': <Icon><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></Icon>,
    '/warehouse/logistics': <Icon><path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2" /><path d="M15 18H9" /><path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.62l-3.48-4.35A1 1 0 0 0 18.52 8H14" /><circle cx="17" cy="18" r="2" /><circle cx="7" cy="18" r="2" /></Icon>,
    '/payments': <Icon><rect width="20" height="14" x="2" y="5" rx="2" /><line x1="2" x2="22" y1="10" y2="10" /></Icon>,
    '/warehouse/acceptance-slots': <Icon><path d="M21 7.5V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h3.5" /><path d="M16 2v4" /><path d="M8 2v4" /><path d="M3 10h5" /><circle cx="17.5" cy="17.5" r="3.5" /><path d="M17.5 16v1.5l1 1" /></Icon>,
    '/warehouse/ff-invoices': <Icon><path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z" /><path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8" /><path d="M12 17.5v-11" /></Icon>,
    '/warehouse/stock': <Icon><path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z" /><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65" /><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65" /></Icon>,
    '/warehouse/fbo-supplies': <Icon><path d="m16 16 2 2 4-4" /><path d="M21 10V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l2-1.14" /><path d="M3.3 7 12 12l8.7-5" /><path d="M12 22V12" /></Icon>,
    '/warehouse/acceptance-limits': <Icon><path d="M8 2v4" /><path d="M16 2v4" /><rect width="18" height="18" x="3" y="4" rx="2" /><path d="M3 10h18" /></Icon>,
    '/warehouse/wb-returns': <Icon><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" /><path d="M3 3v5h5" /></Icon>,
    '/warehouse/wb-stocks': <Icon><path d="M2 20a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8l-7 5V8l-7 5V4a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z" /><path d="M17 18h1" /><path d="M12 18h1" /><path d="M7 18h1" /></Icon>,
    '/warehouse/analytics': <Icon><line x1="12" x2="12" y1="20" y2="10" /><line x1="18" x2="18" y1="20" y2="4" /><line x1="6" x2="6" y1="20" y2="16" /></Icon>,
    '/warehouse/speed': <Icon><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z" /><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z" /><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0" /><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5" /></Icon>,
    '/barcode-labels': <Icon><path d="M3 5v14" /><path d="M8 5v14" /><path d="M12 5v14" /><path d="M17 5v14" /><path d="M21 5v14" /></Icon>,
    // Заказы
    '/planning': <Icon><path d="m3 17 2 2 4-4" /><path d="m3 7 2 2 4-4" /><path d="M13 6h8" /><path d="M13 12h8" /><path d="M13 18h8" /></Icon>,
    '/supply-chain': <Icon><path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2" /><path d="M15 18H9" /><path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.62l-3.48-4.35A1 1 0 0 0 18.52 8H14" /><circle cx="17" cy="18" r="2" /><circle cx="7" cy="18" r="2" /></Icon>,
    '/container-loader': <Icon><rect width="20" height="12" x="2" y="7" rx="1" /><path d="M6 7v12" /><path d="M10 7v12" /><path d="M14 7v12" /><path d="M18 7v12" /></Icon>,
    // Продажи
    '/funnel': <Icon><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" /></Icon>,
    '/ads-manager': <Icon><path d="m3 11 18-5v12L3 14v-3z" /><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6" /></Icon>,
    '/ab-tests': <Icon><rect width="8" height="12" x="2" y="6" rx="2" /><rect width="8" height="12" x="14" y="6" rx="2" /><path d="M10 12h4" /></Icon>,
    '/pricing': <Icon><path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z" /><circle cx="7.5" cy="7.5" r=".5" fill="currentColor" /></Icon>,
    '/trends': <Icon><path d="M14.828 14.828 21 21" /><path d="M21 16v5h-5" /><path d="m21 3-9 9-4-4-6 6" /><path d="M21 8V3h-5" /></Icon>,
    '/order-geography': <Icon><path d="M14.106 5.553a2 2 0 0 0 1.788 0l3.659-1.83A1 1 0 0 1 21 4.619v12.764a1 1 0 0 1-.553.894l-4.553 2.277a2 2 0 0 1-1.788 0l-4.212-2.106a2 2 0 0 0-1.788 0l-3.659 1.83A1 1 0 0 1 3 19.381V6.618a1 1 0 0 1 .553-.894l4.553-2.277a2 2 0 0 1 1.788 0z" /><path d="M15 5.764v15" /><path d="M9 3.236v15" /></Icon>,
    '/localization': <Icon><path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0" /><circle cx="12" cy="10" r="3" /></Icon>,
    '/opiu': <Icon><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z" /><path d="M14 2v4a2 2 0 0 0 2 2h4" /><path d="M16 13H8" /><path d="M16 17H8" /><path d="M10 9H8" /></Icon>,
    '/plan-fact': <Icon><circle cx="12" cy="12" r="10" /><circle cx="12" cy="12" r="6" /><circle cx="12" cy="12" r="2" /></Icon>,
    // AI
    '/ai-chat': <Icon><path d="M12 8V4H8" /><rect width="16" height="12" x="4" y="8" rx="2" /><path d="M2 14h2" /><path d="M20 14h2" /><path d="M15 13v2" /><path d="M9 13v2" /></Icon>,
    '/vibecoding': <Icon><polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" /><line x1="14" x2="10" y1="4" y2="20" /></Icon>,
    // Настройки
    '/monitoring': <Icon><path d="M4 11a9 9 0 0 1 9 9" /><path d="M4 4a16 16 0 0 1 16 16" /><circle cx="5" cy="19" r="1" /></Icon>,
    '/raw-data': <Icon><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M3 5v14a9 3 0 0 0 18 0V5" /><path d="M3 12a9 3 0 0 0 18 0" /></Icon>,
    '/settings': <Icon><line x1="21" x2="14" y1="4" y2="4" /><line x1="10" x2="3" y1="4" y2="4" /><line x1="21" x2="12" y1="12" y2="12" /><line x1="8" x2="3" y1="12" y2="12" /><line x1="21" x2="16" y1="20" y2="20" /><line x1="12" x2="3" y1="20" y2="20" /><line x1="14" x2="14" y1="2" y2="6" /><line x1="8" x2="8" y1="10" y2="14" /><line x1="16" x2="16" y1="18" y2="22" /></Icon>,
    '/team': <Icon><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></Icon>,
};

interface NavItem {
    href: string;
    label: string;
    icon: string;
    pageKey: string;
    /**
     * Пункт виден только вайбкодеру (строка в vibe_authors), а НЕ по роли в проекте.
     * Роль тут не гейт: клиент-селлер — owner своего проекта и прошёл бы canAccess.
     */
    requiresVibecoder?: boolean;
}

const navGroups: { title: string; section: string; items: NavItem[] }[] = [
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
        ],
    },
    {
        title: 'Справочники',
        section: 'refs',
        items: [
            { href: '/refs/counterparty', label: 'Контрагенты', icon: '👥', pageKey: 'refs' },
            { href: '/loans', label: 'Займы', icon: '💸', pageKey: 'refs' },
            { href: '/refs', label: 'Счета и категории', icon: '📋', pageKey: 'refs' },
        ],
    },
    {
        title: 'Склад',
        section: 'warehouse',
        items: [
            { href: '/warehouse', label: 'Склады', icon: '🏢', pageKey: 'warehouse' },
            { href: '/warehouse/assembly', label: 'Заявки на сборку', icon: '📋', pageKey: 'assembly' },
            { href: '/warehouse/assembly/distribute', label: 'Сборка', icon: '🧩', pageKey: 'assembly' },
            { href: '/warehouse/assembly/analytics', label: 'Анализ сборки', icon: '⏱️', pageKey: 'assembly-analytics' },
            { href: '/warehouse/ff-requests', label: 'Заявки ФФ', icon: '🔗', pageKey: 'assembly' },
            { href: '/warehouse/logistics', label: 'Лист логиста', icon: '🚛', pageKey: 'logistics' },
            { href: '/payments', label: 'Оплаты', icon: '💳', pageKey: 'logistics' },
            { href: '/warehouse/ff-invoices', label: 'Счета ФФ', icon: '🧾', pageKey: 'logistics' },
            { href: '/warehouse/acceptance-slots', label: 'Слоты сдачи', icon: '🗓️', pageKey: 'logistics' },
            { href: '/warehouse/stock', label: 'Сводные остатки', icon: '📦', pageKey: 'stocks' },
            { href: '/warehouse/fbo-supplies', label: 'Поставки FBO', icon: '📮', pageKey: 'fbo' },
            { href: '/warehouse/acceptance-limits', label: 'Лимиты приёмки', icon: '📅', pageKey: 'fbo' },
            { href: '/warehouse/wb-returns', label: 'Возвраты на ПВЗ', icon: '↩️', pageKey: 'warehouse' },
            { href: '/warehouse/wb-stocks', label: 'Остатки WB', icon: '🏭', pageKey: 'stocks' },
            { href: '/warehouse/fbs', label: 'FBS Wildberries', icon: '🛒', pageKey: 'fbs' },
            { href: '/measurements', label: 'Замеры', icon: '📐', pageKey: 'measurements' },
            { href: '/warehouse/analytics', label: 'Аналитика остатков', icon: '📊', pageKey: 'stock-analytics' },
            { href: '/warehouse/speed', label: 'Приоритет складов', icon: '🚀', pageKey: 'stock-analytics' },
            { href: '/barcode-labels', label: 'Генератор ШК', icon: '🏷️', pageKey: 'barcode-labels' },
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
            { href: '/reviews', label: 'Отзывы', icon: '⭐', pageKey: 'reviews' },
            { href: '/ads-manager', label: 'Управление рекламой', icon: '📢', pageKey: 'ads-manager' },
            { href: '/card-exchange', label: 'Биржа карточек', icon: '🃏', pageKey: 'card-exchange' },
            { href: '/ab-tests', label: 'АБ-тесты фото', icon: '🧪', pageKey: 'ab-tests' },
            { href: '/pricing', label: 'Ценообразование', icon: '💲', pageKey: 'funnel' },
            { href: '/trends', label: 'Метрики и тренды', icon: '📈', pageKey: 'trends' },
            { href: '/order-geography', label: 'Куда заказывают', icon: '🗺️', pageKey: 'geography' },
            { href: '/localization', label: 'Индекс локализации', icon: '📍', pageKey: 'geography' },
            { href: '/opiu', label: 'ОПИУ', icon: '📋', pageKey: 'opiu' },
            { href: '/plan-fact', label: 'План-Факт', icon: '🎯', pageKey: 'plan-fact' },
        ],
    },
    {
        title: 'AI',
        section: 'ai',
        items: [
            { href: '/ai-chat', label: 'AI-ассистент', icon: '🤖', pageKey: 'ai-chat' },
            { href: '/vibecoding', label: 'Вайбкодинг', icon: '🤖', pageKey: 'vibecoding', requiresVibecoder: true },
        ],
    },
    {
        title: 'Настройки',
        section: 'settings',
        items: [
            { href: '/monitoring', label: 'Мониторинг', icon: '📡', pageKey: 'monitoring' },
            { href: '/raw-data', label: 'Сырые данные', icon: '🗄️', pageKey: 'raw-data' },
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
    // Пока slug не разрешён в project_id, дочерние страницы рендерить нельзя:
    // они успевают сходить в API со СТАРЫМ проектом из localStorage и показать
    // чужие (или пустые) данные. Гонка вылезала при переходе между проектами
    // по прямой ссылке — раздел открывался пустым, хотя данные на месте.
    const [projectReady, setProjectReady] = useState(false);
    const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
    const [isVibecoder, setIsVibecoder] = useState(false);
    const { canAccess, canManage, loading: permLoading } = usePermissions();

    useEffect(() => {
        setMounted(true);
        if (!api.isAuthenticated()) { window.location.href = '/login'; return; }
        // Resolve slug to project_id and sync localStorage
        setProjectReady(false);
        api.getProject(slug).then(p => {
            api.setProjectId(p.id);
            try { localStorage.setItem('dds_project_slug', p.slug); } catch { /* SSR */ }
            try { localStorage.setItem('dds_project_name', p.name); } catch { /* SSR */ }
            setProjectName(p.name);
        }).catch(() => {
            setProjectName(slug);
        }).finally(() => {
            setProjectReady(true);
        });
        api.getProfile().then(u => setUsername(u.username)).catch(() => { });
    }, [slug]);

    // Вайбкодер? Вкладка «Вайбкодинг» — внутренняя телеметрия репозитория, её не должны
    // видеть внешние пользователи и клиенты-селлеры. Стартуем с false: пункт появляется
    // только на явное «да», сбой/молчание бэкенда вкладку НЕ открывает.
    useEffect(() => {
        let cancelled = false;
        api.getIsVibecoder().then(flag => { if (!cancelled) setIsVibecoder(flag); });
        return () => { cancelled = true; };
    }, []);

    // Раскрытие секций сайдбара — переживает переходы и перезагрузку
    useEffect(() => {
        try {
            const raw = localStorage.getItem('dds_sidebar_collapsed');
            if (raw) setCollapsed(JSON.parse(raw));
        } catch { /* SSR / битый JSON */ }
    }, []);

    const toggleSection = (section: string) => {
        setCollapsed(prev => {
            const next = { ...prev, [section]: !prev[section] };
            try { localStorage.setItem('dds_sidebar_collapsed', JSON.stringify(next)); } catch { /* SSR */ }
            return next;
        });
    };

    const isActive = (href: string) => {
        if (!mounted) return false;
        const full = `/p/${slug}${href}`;
        return pathname === full;
    };

    // Filter nav groups based on permissions
    // Вайбкодерский пункт гейтится СПИСКОМ ДОСТУПА (vibe_authors), а не проектными
    // правами: у этих данных нет project_id, и роль в проекте про них ничего не знает —
    // permissions.pages никогда не перечислит 'vibecoding', а owner-селлер прошёл бы
    // canAccess насквозь. Поэтому для таких пунктов is-vibecoder ЗАМЕНЯЕТ canAccess.
    const filteredGroups = navGroups.map(group => {
        const visibleItems = group.items.filter(item =>
            item.requiresVibecoder ? isVibecoder : canAccess(item.pageKey),
        );
        return { ...group, items: visibleItems };
    }).filter(group => group.items.length > 0);

    return (
        <div>
            <aside className="sidebar">
                <div className="sidebar-top">
                    <Link href="/projects" className="sidebar-logo" style={{ display: 'block', textDecoration: 'none' }}>DDS</Link>
                    <ChangelogBell slug={slug} />
                </div>

                <div style={{ padding: '0 12px 12px' }}>
                    <div className="project-selector" onClick={() => window.location.href = '/projects'}>
                        <span style={{ fontSize: 14 }}>📁</span>
                        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{projectName}</span>
                        <span style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>⌄</span>
                    </div>
                </div>

                <nav className="sidebar-nav">
                    {/* Дашборд — отдельно, без группы, но по праву 'dashboard' */}
                    {canAccess('dashboard') && (
                        <Link href={`/p/${slug}`}
                            className={`sidebar-link ${isActive('') ? 'active' : ''}`}>
                            <span className="sidebar-link-icon">{dashboardIcon}</span>
                            <span>{dashboardItem.label}</span>
                        </Link>
                    )}

                    {filteredGroups.map(group => {
                        const isCollapsed = !!collapsed[group.section];
                        return (
                            <div key={group.title}>
                                <button type="button" className="sidebar-section-btn"
                                    onClick={() => toggleSection(group.section)}
                                    aria-expanded={!isCollapsed}>
                                    <span className="sidebar-section-icon">{sectionIcons[group.section]}</span>
                                    <span className="sidebar-section-label">{group.title}</span>
                                    <span className={`sidebar-section-chevron ${isCollapsed ? 'collapsed' : ''}`}>⌄</span>
                                </button>
                                {!isCollapsed && group.items.map(item => (
                                    <Link key={item.href} href={`/p/${slug}${item.href}`}
                                        className={`sidebar-link nested ${isActive(item.href) ? 'active' : ''}`}>
                                        <span className="sidebar-link-icon">{itemIcons[item.href] ?? item.icon}</span>
                                        <span>{item.label}</span>
                                    </Link>
                                ))}
                            </div>
                        );
                    })}
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
                {projectReady
                    ? children
                    : (
                        <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                            Загрузка проекта…
                        </div>
                    )}
            </main>
        </div>
    );
}

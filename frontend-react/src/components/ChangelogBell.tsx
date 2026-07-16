'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { usePathname } from 'next/navigation';
import Link from 'next/link';
import { CHANGELOG, type ChangelogEntry, type ChangelogKind } from '@/lib/changelog';

const LS_KEY = 'dds_changelog_last_seen';
const PANEL_WIDTH = 380;
const VIEWPORT_GAP = 12;

const KIND_LABEL: Record<ChangelogKind, string> = {
    feature: 'Новое',
    improvement: 'Улучшение',
    fix: 'Исправлено',
};

const KIND_BADGE: Record<ChangelogKind, string> = {
    feature: 'badge-info',
    improvement: 'badge-secondary',
    fix: 'badge-success',
};

/** «Сегодня» / «Вчера» / «15 июля» — заголовок группы. */
function groupLabel(date: string): string {
    const today = new Date();
    const d = new Date(`${date}T00:00:00`);
    const dayDiff = Math.round(
        (new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime() - d.getTime()) / 86400000,
    );
    if (dayDiff === 0) return 'Сегодня';
    if (dayDiff === 1) return 'Вчера';
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
}

function groupByDate(entries: ChangelogEntry[]): { date: string; items: ChangelogEntry[] }[] {
    const groups: { date: string; items: ChangelogEntry[] }[] = [];
    for (const entry of entries) {
        const last = groups[groups.length - 1];
        if (last && last.date === entry.date) last.items.push(entry);
        else groups.push({ date: entry.date, items: [entry] });
    }
    return groups;
}

export default function ChangelogBell({ slug }: { slug: string }) {
    const [open, setOpen] = useState(false);
    const [hydrated, setHydrated] = useState(false);
    const [lastSeenId, setLastSeenId] = useState<string | null>(null);
    const [expandedId, setExpandedId] = useState<string | null>(null);
    const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
    /** Снимок непрочитанного на момент открытия — точки не должны гаснуть под курсором. */
    const [freshIds, setFreshIds] = useState<Set<string>>(new Set());
    const btnRef = useRef<HTMLButtonElement>(null);
    const panelRef = useRef<HTMLDivElement>(null);
    const pathname = usePathname();

    useEffect(() => {
        setHydrated(true);
        try {
            setLastSeenId(localStorage.getItem(LS_KEY));
        } catch { /* SSR */ }
    }, []);

    const unreadCount = useMemo(() => {
        if (!hydrated) return 0; // до гидратации бейдж не рисуем — иначе мигает
        if (!lastSeenId) return CHANGELOG.length;
        const idx = CHANGELOG.findIndex(e => e.id === lastSeenId);
        return idx === -1 ? CHANGELOG.length : idx;
    }, [hydrated, lastSeenId]);

    const place = useCallback(() => {
        const rect = btnRef.current?.getBoundingClientRect();
        if (!rect) return;
        const maxHeight = window.innerHeight * 0.7;
        // Панель уходит вправо от сайдбара и вниз от колокольчика, упираясь в края экрана.
        const left = Math.min(rect.right + 8, window.innerWidth - PANEL_WIDTH - VIEWPORT_GAP);
        const top = Math.min(rect.top, window.innerHeight - maxHeight - VIEWPORT_GAP);
        setPos({ left: Math.max(left, VIEWPORT_GAP), top: Math.max(top, VIEWPORT_GAP) });
    }, []);

    const openPanel = () => {
        setFreshIds(new Set(CHANGELOG.slice(0, unreadCount).map(e => e.id)));
        place();
        setOpen(true);
        const newest = CHANGELOG[0]?.id;
        if (newest) {
            setLastSeenId(newest);
            try { localStorage.setItem(LS_KEY, newest); } catch { /* SSR */ }
        }
    };

    // Клик мимо панели и Escape закрывают; скролл/ресайз — пересчёт позиции.
    useEffect(() => {
        if (!open) return;
        const onDown = (e: MouseEvent) => {
            const t = e.target as Node;
            if (panelRef.current?.contains(t) || btnRef.current?.contains(t)) return;
            setOpen(false);
        };
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        window.addEventListener('resize', place);
        window.addEventListener('scroll', place, true);
        return () => {
            document.removeEventListener('mousedown', onDown);
            document.removeEventListener('keydown', onKey);
            window.removeEventListener('resize', place);
            window.removeEventListener('scroll', place, true);
        };
    }, [open, place]);

    // Панель живёт в layout и переживает переход — закрываем её ПОСЛЕ навигации.
    // Закрытие прямо в onClick ссылки убирает <a> из DOM раньше, чем Next обработает клик.
    useEffect(() => { setOpen(false); }, [pathname]);

    const groups = useMemo(() => groupByDate(CHANGELOG), []);

    return (
        <>
            <button
                ref={btnRef}
                type="button"
                className="changelog-bell-btn"
                aria-label="Обновления проекта"
                aria-expanded={open}
                title="Обновления проекта"
                onClick={() => (open ? setOpen(false) : openPanel())}
            >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                    strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
                    <path d="M10.268 21a2 2 0 0 0 3.464 0" />
                    <path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326" />
                </svg>
                {unreadCount > 0 && (
                    <span className="changelog-bell-count">{unreadCount > 99 ? '99+' : unreadCount}</span>
                )}
            </button>

            {open && pos && createPortal(
                <div
                    ref={panelRef}
                    className="changelog-panel animate-in"
                    style={{ left: pos.left, top: pos.top }}
                    role="dialog"
                    aria-label="Обновления проекта"
                >
                    <div className="changelog-panel-head">
                        <div className="changelog-panel-title">Обновления</div>
                        <div className="changelog-panel-sub">Что нового в DDS</div>
                    </div>

                    <div className="changelog-panel-body">
                        {groups.length === 0 && (
                            <div className="changelog-empty">Пока никаких обновлений</div>
                        )}
                        {groups.map(group => (
                            <div key={group.date}>
                                <div className="changelog-group-label">{groupLabel(group.date)}</div>
                                {group.items.map(entry => {
                                    const expanded = expandedId === entry.id;
                                    return (
                                        <div key={entry.id} className={`changelog-item ${expanded ? 'expanded' : ''}`}>
                                            <button
                                                type="button"
                                                className="changelog-item-head"
                                                aria-expanded={expanded}
                                                onClick={() => setExpandedId(expanded ? null : entry.id)}
                                            >
                                                <div className="changelog-item-main">
                                                    <div className="changelog-item-meta">
                                                        <span className={`badge ${KIND_BADGE[entry.kind]}`}>{KIND_LABEL[entry.kind]}</span>
                                                        <span className="changelog-item-section">{entry.section}</span>
                                                    </div>
                                                    <div className="changelog-item-title">{entry.title}</div>
                                                </div>
                                                {freshIds.has(entry.id) && <span className="changelog-item-dot" aria-label="Новое" />}
                                            </button>

                                            {expanded && (
                                                <div className="changelog-item-body">
                                                    <p className="changelog-item-text">{entry.summary}</p>
                                                    <p className="changelog-item-impact">
                                                        <span className="changelog-item-impact-label">На что влияет: </span>
                                                        {entry.impact}
                                                    </p>
                                                    <Link
                                                        href={`/p/${slug}${entry.href}`}
                                                        className="btn btn-secondary btn-sm changelog-item-link"
                                                    >
                                                        Открыть «{entry.section}»
                                                    </Link>
                                                </div>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        ))}
                    </div>
                </div>,
                document.body,
            )}
        </>
    );
}

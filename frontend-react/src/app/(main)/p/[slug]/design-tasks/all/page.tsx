'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import PageHeader from '@/components/PageHeader';
import Toast from '@/components/Toast';
import WbThumb from '@/components/WbThumb';
import { DESIGN_STATUS_BADGE, DESIGN_STATUS_LABEL, DESIGN_WORK_TYPE_LABEL } from '@/lib/design';
import type { DesignTaskListItem } from '@/types/api';
import DesignTabs from '../components/DesignTabs';

/** Размер страницы = кап бэка (limit ≤ 200); догрузка — через offset. */
const PAGE_SIZE = 200;

/**
 * Сквозной экран по всем брендам: GET /all-projects (только проекты членства,
 * скоуп считает бэк — §6.1). Переход по строке — в деталку слага её проекта.
 */
export default function DesignAllProjectsPage() {
    const params = useParams<{ slug: string }>();
    const router = useRouter();

    const [tasks, setTasks] = useState<DesignTaskListItem[]>([]);
    /** project_name → slug (из списка проектов пользователя) для перехода в деталку. */
    const [slugByProject, setSlugByProject] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [toast, setToast] = useState<string | null>(null);
    /** Выдача упёрлась в кап страницы — есть что догружать. */
    const [hasMore, setHasMore] = useState(false);
    const [loadingMore, setLoadingMore] = useState(false);
    const mountedRef = useRef(true);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [rows, projects] = await Promise.all([
                api.listDesignTasksAllProjects({ limit: PAGE_SIZE, offset: 0 }),
                api.getProjects(),
            ]);
            if (!mountedRef.current) return;
            setTasks(rows);
            setHasMore(rows.length === PAGE_SIZE);
            const map: Record<string, string> = {};
            for (const p of projects) map[p.name] = p.slug;
            setSlugByProject(map);
        } catch (e) {
            if (!mountedRef.current) return;
            setError(e instanceof Error ? e.message : 'Не удалось загрузить задачи по брендам');
        } finally {
            if (mountedRef.current) setLoading(false);
        }
    }, []);

    /** Догрузка следующей страницы по offset (порядок выдачи бэка детерминирован). */
    const loadMore = useCallback(async () => {
        setLoadingMore(true);
        try {
            const rows = await api.listDesignTasksAllProjects({ limit: PAGE_SIZE, offset: tasks.length });
            if (!mountedRef.current) return;
            setTasks((prev) => [...prev, ...rows]);
            setHasMore(rows.length === PAGE_SIZE);
        } catch (e) {
            if (!mountedRef.current) return;
            setToast(e instanceof Error ? e.message : 'Не удалось догрузить задачи');
        } finally {
            if (mountedRef.current) setLoadingMore(false);
        }
    }, [tasks.length]);

    useEffect(() => {
        mountedRef.current = true;
        void load();
        return () => { mountedRef.current = false; };
    }, [load]);

    const sorted = useMemo(
        () => [...tasks].sort((a, b) => {
            const pa = a.project_name ?? '';
            const pb = b.project_name ?? '';
            if (pa !== pb) return pa.localeCompare(pb, 'ru');
            if (a.due_date && b.due_date) return a.due_date.localeCompare(b.due_date);
            if (a.due_date) return -1;
            if (b.due_date) return 1;
            return a.number.localeCompare(b.number);
        }),
        [tasks],
    );

    const openTask = useCallback((t: DesignTaskListItem) => {
        const slug = t.project_name ? slugByProject[t.project_name] : undefined;
        if (!slug) {
            setToast('Не удалось определить проект задачи — откройте её из его раздела');
            return;
        }
        router.push(`/p/${slug}/design-tasks/${t.id}`);
    }, [router, slugByProject]);

    const cell = { padding: '12px 16px' } as const;

    return (
        <PageGuard page="design-tasks">
            <PageHeader
                title="🌐 Дизайн карточек — все бренды"
                subtitle="Сквозной список задач по всем проектам вашего членства"
                actions={<DesignTabs slug={params.slug} active="all" />}
            />

            {loading && (
                <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка…</div>
            )}
            {error && !loading && (
                <div className="glass-card" style={{ color: 'var(--color-danger)' }}>
                    {error} <button className="btn btn-sm btn-secondary" onClick={() => void load()}>Повторить</button>
                </div>
            )}
            {!loading && !error && sorted.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: 48 }}>
                    Задач по вашим проектам пока нет.
                </div>
            )}
            {!loading && !error && sorted.length > 0 && (
                <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid var(--color-border)', color: 'var(--color-text-muted)', fontSize: 12 }}>
                                <th style={{ ...cell, textAlign: 'left' }}>Проект</th>
                                <th style={{ ...cell, textAlign: 'left' }}>Номер</th>
                                <th style={{ ...cell, textAlign: 'left' }}>Товар</th>
                                <th style={{ ...cell, textAlign: 'left' }}>Тип</th>
                                <th style={{ ...cell, textAlign: 'left' }}>Исполнитель</th>
                                <th style={{ ...cell, textAlign: 'left' }}>Срок</th>
                                <th style={{ ...cell, textAlign: 'right' }}>Версий</th>
                                <th style={{ ...cell, textAlign: 'left' }}>Статус</th>
                            </tr>
                        </thead>
                        <tbody>
                            {sorted.map((t) => (
                                <tr
                                    key={`${t.project_name ?? ''}-${t.id}`}
                                    onClick={() => openTask(t)}
                                    style={{ borderBottom: '1px solid var(--color-border)', cursor: 'pointer' }}
                                >
                                    <td style={{ ...cell, fontWeight: 600, whiteSpace: 'nowrap' }}>{t.project_name ?? '—'}</td>
                                    <td style={{ ...cell, whiteSpace: 'nowrap' }}>{t.number}</td>
                                    <td style={cell}>
                                        <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                                            <WbThumb nmId={t.nm_id} size={32} height={42} />
                                            <span>{t.title}</span>
                                            {t.is_urgent && <span className="badge badge-danger" style={{ fontSize: 10 }}>Срочно</span>}
                                        </span>
                                    </td>
                                    <td style={{ ...cell, whiteSpace: 'nowrap' }}>{DESIGN_WORK_TYPE_LABEL[t.work_type]}</td>
                                    <td style={{ ...cell, whiteSpace: 'nowrap' }}>{t.assignee_name ?? '—'}</td>
                                    <td style={{ ...cell, whiteSpace: 'nowrap', color: t.is_overdue ? 'var(--color-danger)' : undefined, fontWeight: t.is_overdue ? 600 : undefined }}>
                                        {formatDate(t.due_date)}
                                    </td>
                                    <td style={{ ...cell, textAlign: 'right' }}>{formatNumber(t.versions_count, 0)}</td>
                                    <td style={cell}>
                                        <span className={`badge ${DESIGN_STATUS_BADGE[t.status]}`}>{DESIGN_STATUS_LABEL[t.status]}</span>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    <div
                        style={{
                            display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                            padding: '12px 16px', borderTop: '1px solid var(--color-border)',
                            fontSize: 13, color: 'var(--color-text-muted)',
                        }}
                    >
                        <span>
                            Показано задач: {formatNumber(sorted.length, 0)}
                            {hasMore && ' — список усечён'}
                        </span>
                        {hasMore && (
                            <button className="btn btn-sm btn-secondary" onClick={() => void loadMore()} disabled={loadingMore}>
                                {loadingMore ? 'Загрузка…' : 'Показать ещё'}
                            </button>
                        )}
                    </div>
                </div>
            )}

            {toast && <Toast message={toast} type="error" onClose={() => setToast(null)} />}
        </PageGuard>
    );
}

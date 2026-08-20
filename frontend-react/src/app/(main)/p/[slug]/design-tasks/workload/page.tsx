'use client';

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import PageHeader from '@/components/PageHeader';
import { DESIGN_STATUS_BADGE, DESIGN_STATUS_LABEL } from '@/lib/design';
import type { DesignTaskListItem, DesignTaskStatus, DesignWorkloadRow } from '@/types/api';
import DesignTabs from '../components/DesignTabs';
import { useDesignBoardPermissions } from '../components/useDesignBoardPermissions';

/**
 * Зеркало DESIGN_ACTIVE_STATUSES бэка (workload.py): ON_HOLD и терминалы — вне
 * загрузки. Уходит в ЗАПРОС (не в клиентский фильтр): кап limit применяется на
 * сервере ДО фильтра, иначе разворот строки не сходится со счётчиком «Активные».
 */
const ACTIVE_STATUSES: readonly DesignTaskStatus[] = [
    'NEW', 'ASSIGNED', 'IN_PROGRESS', 'REVIEW', 'REVISION',
];

/** Кап выдачи списка (бэк: limit ≤ 200) — при достижении показываем усечение. */
const LIST_LIMIT = 200;

/** Светофор по просрочке (спек F5): 0 — зелёный, 1–2 — жёлтый, 3+ — красный. */
function trafficColor(overdue: number): string {
    if (overdue === 0) return 'var(--color-success)';
    if (overdue <= 2) return 'var(--color-warning)';
    return 'var(--color-danger)';
}

const UNASSIGNED_KEY = -1;

/** Экран «Загрузка команды»: GET /workload + строка «Не назначено» + разворот в задачи. */
export default function DesignWorkloadPage() {
    const params = useParams<{ slug: string }>();
    const boardPerms = useDesignBoardPermissions();
    const router = useRouter();

    const [rows, setRows] = useState<DesignWorkloadRow[]>([]);
    /** NEW (=всегда без исполнителя) — строка «Не назначено» (спек F5). */
    const [unassigned, setUnassigned] = useState<DesignTaskListItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [expanded, setExpanded] = useState<number | null>(null);
    /** user_id → активные задачи исполнителя (кэш разворота). */
    const [tasksByUser, setTasksByUser] = useState<Record<number, DesignTaskListItem[]>>({});
    const [expandLoading, setExpandLoading] = useState(false);
    const [expandError, setExpandError] = useState<string | null>(null);
    const mountedRef = useRef(true);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [wl, newTasks] = await Promise.all([
                api.getDesignWorkload(),
                // Не назначено: только NEW. ASSIGNED без исполнителя невозможен —
                // гвард state.py (→ASSIGNED требует исполнителя), а снятие с ASSIGNED
                // уводит задачу обратно в NEW (crud.assign), очищая assignee.
                api.listDesignTasks({ status: ['NEW'], limit: LIST_LIMIT }),
            ]);
            if (!mountedRef.current) return;
            setRows(wl);
            setUnassigned(newTasks.filter((t) => !t.assignee_name));
        } catch (e) {
            if (!mountedRef.current) return;
            setError(e instanceof Error ? e.message : 'Не удалось загрузить загрузку команды');
        } finally {
            if (mountedRef.current) setLoading(false);
        }
    }, []);

    useEffect(() => {
        mountedRef.current = true;
        void load();
        return () => { mountedRef.current = false; };
    }, [load]);

    const toggleExpand = useCallback(async (userId: number) => {
        setExpandError(null);
        if (expanded === userId) {
            setExpanded(null);
            return;
        }
        setExpanded(userId);
        if (userId === UNASSIGNED_KEY || tasksByUser[userId]) return;
        setExpandLoading(true);
        try {
            // Активные статусы — в запрос: кап сервер применяет ДО фильтра.
            const res = await api.listDesignTasks({
                assignee_user_id: userId,
                status: [...ACTIVE_STATUSES],
                limit: LIST_LIMIT,
            });
            if (!mountedRef.current) return;
            setTasksByUser((prev) => ({ ...prev, [userId]: res }));
        } catch (e) {
            if (!mountedRef.current) return;
            setExpandError(e instanceof Error ? e.message : 'Не удалось загрузить задачи исполнителя');
        } finally {
            if (mountedRef.current) setExpandLoading(false);
        }
    }, [expanded, tasksByUser]);

    const unassignedNearestDue = useMemo(() => {
        const dues = unassigned.map((t) => t.due_date).filter((d): d is string => !!d).sort();
        return dues[0] ?? null;
    }, [unassigned]);

    const openTask = useCallback(
        (taskId: number) => router.push(`/p/${params.slug}/design-tasks/${taskId}`),
        [router, params.slug],
    );

    const cell = { padding: '12px 16px' } as const;

    const renderTaskRows = (tasks: DesignTaskListItem[]) => (
        <tr>
            <td colSpan={6} style={{ padding: '0 16px 12px', background: 'var(--color-bg-hover)' }}>
                {expandLoading && <div style={{ padding: 12, color: 'var(--color-text-muted)', fontSize: 13 }}>Загрузка…</div>}
                {expandError && <div style={{ padding: 12, color: 'var(--color-danger)', fontSize: 13 }}>{expandError}</div>}
                {!expandLoading && !expandError && tasks.length === 0 && (
                    <div style={{ padding: 12, color: 'var(--color-text-muted)', fontSize: 13 }}>Активных задач нет.</div>
                )}
                {!expandLoading && !expandError && tasks.map((t) => (
                    <div
                        key={t.id}
                        onClick={() => openTask(t.id)}
                        style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 4px', cursor: 'pointer', borderBottom: '1px solid var(--color-border)', fontSize: 13 }}
                    >
                        <span style={{ fontWeight: 600, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>{t.number}</span>
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{t.title}</span>
                        {t.is_urgent && <span className="badge badge-danger" style={{ fontSize: 10 }}>Срочно</span>}
                        <span className={`badge ${DESIGN_STATUS_BADGE[t.status]}`} style={{ fontSize: 10 }}>{DESIGN_STATUS_LABEL[t.status]}</span>
                        <span style={{ whiteSpace: 'nowrap', color: t.is_overdue ? 'var(--color-danger)' : 'var(--color-text-muted)', fontWeight: t.is_overdue ? 600 : 400 }}>
                            {formatDate(t.due_date)}
                        </span>
                    </div>
                ))}
                {!expandLoading && !expandError && tasks.length >= LIST_LIMIT && (
                    <div style={{ padding: '8px 4px', color: 'var(--color-text-muted)', fontSize: 12 }}>
                        Показаны первые {formatNumber(LIST_LIMIT, 0)} — уточните фильтр в списке задач.
                    </div>
                )}
            </td>
        </tr>
    );

    return (
        <PageGuard page="design-tasks">
            <PageHeader
                title="👥 Дизайн карточек — загрузка"
                subtitle="Активные задачи по исполнителям: просрочка, правки, ближайший срок"
                actions={<DesignTabs slug={params.slug} active="workload" canManageRefs={boardPerms.can_manage_refs} />}
            />

            {loading && (
                <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка…</div>
            )}
            {error && !loading && (
                <div className="glass-card" style={{ color: 'var(--color-danger)' }}>
                    {error} <button className="btn btn-sm btn-secondary" onClick={() => void load()}>Повторить</button>
                </div>
            )}
            {!loading && !error && rows.length === 0 && unassigned.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: 48 }}>
                    Активных задач в команде нет — загрузка появится после назначения заявок.
                </div>
            )}
            {!loading && !error && (rows.length > 0 || unassigned.length > 0) && (
                <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid var(--color-border)', color: 'var(--color-text-muted)', fontSize: 12 }}>
                                <th style={{ ...cell, textAlign: 'left' }}>Исполнитель</th>
                                <th style={{ ...cell, textAlign: 'right' }}>Активные</th>
                                <th style={{ ...cell, textAlign: 'right' }}>Просрочено</th>
                                <th style={{ ...cell, textAlign: 'right' }}>В правках</th>
                                <th style={{ ...cell, textAlign: 'left' }}>Ближайший срок</th>
                                <th style={{ ...cell, textAlign: 'center' }}>Светофор</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((r) => (
                                <Fragment key={r.user_id}>
                                    <tr
                                        onClick={() => void toggleExpand(r.user_id)}
                                        style={{ borderBottom: '1px solid var(--color-border)', cursor: 'pointer' }}
                                    >
                                        <td style={{ ...cell, fontWeight: 600 }}>
                                            <span style={{ marginRight: 8, display: 'inline-block', width: 12, color: 'var(--color-text-muted)' }}>
                                                {expanded === r.user_id ? '▾' : '▸'}
                                            </span>
                                            {r.user_name}
                                        </td>
                                        <td style={{ ...cell, textAlign: 'right' }}>{formatNumber(r.active_tasks, 0)}</td>
                                        <td style={{ ...cell, textAlign: 'right', color: r.overdue > 0 ? 'var(--color-danger)' : undefined, fontWeight: r.overdue > 0 ? 600 : 400 }}>
                                            {formatNumber(r.overdue, 0)}
                                        </td>
                                        <td style={{ ...cell, textAlign: 'right' }}>{formatNumber(r.in_revision, 0)}</td>
                                        <td style={cell}>{formatDate(r.nearest_due)}</td>
                                        <td style={{ ...cell, textAlign: 'center' }}>
                                            <span
                                                title={`Просрочено: ${formatNumber(r.overdue, 0)}`}
                                                style={{ display: 'inline-block', width: 12, height: 12, borderRadius: '50%', background: trafficColor(r.overdue) }}
                                            />
                                        </td>
                                    </tr>
                                    {expanded === r.user_id && renderTaskRows(tasksByUser[r.user_id] ?? [])}
                                </Fragment>
                            ))}
                            {unassigned.length > 0 && (
                                <>
                                    <tr
                                        onClick={() => void toggleExpand(UNASSIGNED_KEY)}
                                        style={{ borderBottom: '1px solid var(--color-border)', cursor: 'pointer' }}
                                    >
                                        <td style={{ ...cell, fontWeight: 600, color: 'var(--color-text-muted)' }}>
                                            <span style={{ marginRight: 8, display: 'inline-block', width: 12 }}>
                                                {expanded === UNASSIGNED_KEY ? '▾' : '▸'}
                                            </span>
                                            Не назначено
                                        </td>
                                        <td
                                            style={{ ...cell, textAlign: 'right' }}
                                            title={unassigned.length >= LIST_LIMIT ? `Показаны первые ${LIST_LIMIT} — задач без исполнителя больше` : undefined}
                                        >
                                            {formatNumber(unassigned.length, 0)}{unassigned.length >= LIST_LIMIT ? '+' : ''}
                                        </td>
                                        <td style={{ ...cell, textAlign: 'right', color: unassigned.some((t) => t.is_overdue) ? 'var(--color-danger)' : undefined }}>
                                            {formatNumber(unassigned.filter((t) => t.is_overdue).length, 0)}
                                        </td>
                                        <td style={{ ...cell, textAlign: 'right' }}>—</td>
                                        <td style={cell}>{formatDate(unassignedNearestDue)}</td>
                                        <td style={{ ...cell, textAlign: 'center' }}>
                                            <span
                                                title="Задачи без исполнителя"
                                                style={{ display: 'inline-block', width: 12, height: 12, borderRadius: '50%', background: trafficColor(unassigned.filter((t) => t.is_overdue).length) }}
                                            />
                                        </td>
                                    </tr>
                                    {expanded === UNASSIGNED_KEY && renderTaskRows(unassigned)}
                                </>
                            )}
                        </tbody>
                    </table>
                </div>
            )}
        </PageGuard>
    );
}

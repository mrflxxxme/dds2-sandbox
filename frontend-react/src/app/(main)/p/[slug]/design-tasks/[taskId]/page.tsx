'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDate, formatDateTime } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import PageHeader from '@/components/PageHeader';
import Toast from '@/components/Toast';
import WbThumb from '@/components/WbThumb';
import { usePermissions } from '@/lib/hooks/usePermissions';
import {
    DESIGN_COMPLEXITY_LABEL,
    DESIGN_STATUS_BADGE,
    DESIGN_STATUS_LABEL,
    DESIGN_WORK_TYPE_LABEL,
} from '@/lib/design';
import type { DesignTaskDetail, DesignTaskStatus } from '@/types/api';
import MaterialsCard from '../components/MaterialsCard';
import SubmissionsCard from '../components/SubmissionsCard';
import CommentsCard from '../components/CommentsCard';
import EventsCard from '../components/EventsCard';
import ReasonModal from '../components/ReasonModal';
import SubmitWorkModal from '../components/SubmitWorkModal';
import AssignModal from '../components/AssignModal';
import EditTaskModal from '../components/EditTaskModal';

type ModalState =
    | { kind: 'submit' }
    | { kind: 'return'; subId: number }
    | { kind: 'assign' }
    | { kind: 'edit' }
    | { kind: 'status'; to: DesignTaskStatus; required: boolean }
    | { kind: 'cancel' }
    | null;

export default function DesignTaskDetailPage() {
    const params = useParams<{ slug: string; taskId: string }>();
    const router = useRouter();
    const taskId = Number(params.taskId);
    const { canManage } = usePermissions();

    const [task, setTask] = useState<DesignTaskDetail | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const [modal, setModal] = useState<ModalState>(null);
    const [memberNames, setMemberNames] = useState<Record<number, string>>({});
    const mountedRef = useRef(true);
    const viewedRef = useRef(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const t = await api.getDesignTask(taskId);
            if (mountedRef.current) setTask(t);
        } catch (e) {
            if (mountedRef.current) setError(e instanceof Error ? e.message : 'Не удалось загрузить задачу');
        } finally {
            if (mountedRef.current) setLoading(false);
        }
    }, [taskId]);

    useEffect(() => {
        mountedRef.current = true;
        void load();
        api.getMembers(params.slug)
            .then((rows) => {
                if (!mountedRef.current) return;
                const map: Record<number, string> = {};
                for (const m of rows) {
                    map[m.user_id] = [m.first_name, m.last_name].filter(Boolean).join(' ') || m.username;
                }
                setMemberNames(map);
            })
            .catch(() => { /* имена по id — best-effort */ });
        return () => { mountedRef.current = false; };
    }, [load, params.slug]);

    // Р5: при открытии деталки лидом — POST /viewed (идемпотентно), снимает красную метку.
    useEffect(() => {
        if (!task || viewedRef.current) return;
        if (!canManage() || task.viewed_by_lead_at) return;
        viewedRef.current = true;
        api.markDesignTaskViewed(task.id)
            .then((t) => { if (mountedRef.current) setTask(t); })
            .catch(() => { /* best-effort */ });
    }, [task, canManage]);

    const userName = useCallback(
        (id: number | null) => (id == null ? '—' : memberNames[id] ?? `#${id}`),
        [memberNames],
    );

    const errText = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

    /** Все мутации возвращают DesignTaskDetail — просто кладём его в состояние. */
    const apply = useCallback(async (fn: () => Promise<DesignTaskDetail>, okMsg?: string) => {
        try {
            const t = await fn();
            if (!mountedRef.current) return false;
            setTask(t);
            if (okMsg) setToast({ type: 'success', message: okMsg });
            return true;
        } catch (e) {
            if (mountedRef.current) setToast({ type: 'error', message: errText(e, 'Операция отклонена') });
            return false;
        }
    }, []);

    const changeStatus = useCallback(
        (to: DesignTaskStatus, comment?: string) =>
            apply(() => api.changeDesignTaskStatus(taskId, { to_status: to, comment: comment?.trim() ? comment.trim() : null })),
        [apply, taskId],
    );

    const pendingSub = useMemo(
        () => task?.submissions.find((s) => s.verdict === 'PENDING') ?? null,
        [task],
    );

    if (loading) {
        return (
            <PageGuard page="design-tasks">
                <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-muted)' }}>Загрузка…</div>
            </PageGuard>
        );
    }
    if (error || !task) {
        return (
            <PageGuard page="design-tasks">
                <div className="glass-card" style={{ color: 'var(--color-danger)' }}>
                    {error ?? 'Задача не найдена'}{' '}
                    <button className="btn btn-sm btn-secondary" onClick={() => void load()}>Повторить</button>{' '}
                    <button className="btn btn-sm btn-secondary" onClick={() => router.push(`/p/${params.slug}/design-tasks`)}>← К доске</button>
                </div>
            </PageGuard>
        );
    }

    const perms = task.permissions;
    const status = task.status;

    // Кнопки строго по данным бэка (§6.9): allowed_transitions — целевые статусы,
    // куда ТЕКУЩИЙ пользователь реально может перевести задачу (матрица прав бэка,
    // amendment M3). Спец-кнопки (взять/сдать/вердикт/отложить) покрывают свои
    // переходы ниже — из generic-списка их прячем.
    const allowed = new Set<DesignTaskStatus>(task.allowed_transitions);
    const covered = new Set<DesignTaskStatus>(['ON_HOLD', 'CANCELLED']);
    if (perms.can_take && status === 'ASSIGNED') covered.add('IN_PROGRESS');
    if (perms.can_verdict && pendingSub) { covered.add('ACCEPTED'); covered.add('REVISION'); }
    if (perms.can_submit && (status === 'IN_PROGRESS' || status === 'REVISION')) covered.add('REVIEW');
    const genericTargets = status === 'ON_HOLD'
        ? []
        : task.allowed_transitions.filter((t) => !covered.has(t));

    return (
        <PageGuard page="design-tasks">
            <PageHeader
                title={`${task.number} · ${task.title}`}
                subtitle={`Создана ${formatDateTime(task.created_at)} · автор: ${task.author_name ?? userName(task.author_user_id)}`}
                actions={
                    <button className="btn btn-secondary btn-sm" onClick={() => router.push(`/p/${params.slug}/design-tasks`)}>
                        ← К доске
                    </button>
                }
            />

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 16, alignItems: 'start' }}>
                {/* ── Левая колонка ── */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                    <div className="glass-card">
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
                            <span className={`badge ${DESIGN_STATUS_BADGE[status]}`}>{DESIGN_STATUS_LABEL[status]}</span>
                            {task.is_urgent && <span className="badge badge-danger">Срочно</span>}
                            {task.is_outsourced && <span className="badge badge-secondary">Аутсорс</span>}
                            {status === 'NEW' && !task.viewed_by_lead_at && (
                                <span className="badge badge-danger" title="Ведущий ещё не просмотрел">Не просмотрено</span>
                            )}
                        </div>
                        <div style={{ fontSize: 14, whiteSpace: 'pre-wrap', marginBottom: 12 }}>{task.description}</div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 20px', fontSize: 13, color: 'var(--color-muted)' }}>
                            <span>Тип: <b style={{ color: 'var(--color-text)' }}>{DESIGN_WORK_TYPE_LABEL[task.work_type]}</b></span>
                            <span>Сложность: <b style={{ color: 'var(--color-text)' }}>{DESIGN_COMPLEXITY_LABEL[task.complexity]}</b></span>
                            <span>
                                Срок:{' '}
                                {/* Просрочка — сравнением ДАТ (YYYY-MM-DD, UTC), не Date-моментов: иначе краснеет на сутки раньше is_overdue бэка (due_date < today). */}
                                <b style={{ color: task.due_date && task.due_date < new Date().toISOString().slice(0, 10) && status !== 'ACCEPTED' && status !== 'CANCELLED' ? 'var(--color-danger)' : 'var(--color-text)' }}>
                                    {formatDate(task.due_date)}
                                </b>
                            </span>
                            <span>Исполнитель: <b style={{ color: 'var(--color-text)' }}>{task.assignee_name ?? userName(task.assignee_user_id)}</b></span>
                        </div>
                        <div style={{ marginTop: 12 }}>
                            <a className="btn btn-secondary btn-sm" href={task.sheet_url} target="_blank" rel="noopener noreferrer">
                                📋 Открыть ТЗ-таблицу
                            </a>
                        </div>
                    </div>

                    <div className="glass-card">
                        <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 12px' }}>Товар</h3>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                            <WbThumb nmId={task.nm_id} size={48} height={62} />
                            <span style={{ fontSize: 14 }}>
                                {task.title}
                                <span style={{ display: 'block', fontSize: 12, color: 'var(--color-muted)' }}>
                                    {task.nm_id != null ? `Артикул ${task.nm_id}` : 'Товар не привязан'}
                                </span>
                            </span>
                            {task.nm_id == null && perms.can_edit && (
                                <button className="btn btn-sm btn-secondary" style={{ marginLeft: 'auto' }} onClick={() => setModal({ kind: 'edit' })}>
                                    Привязать товар
                                </button>
                            )}
                        </div>
                    </div>

                    <MaterialsCard
                        task={task}
                        onChanged={() => void load()}
                        onError={(m) => setToast({ type: 'error', message: m })}
                    />

                    <EventsCard task={task} />
                </div>

                {/* ── Правая колонка ── */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                    <div className="glass-card">
                        <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 12px' }}>Действия</h3>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                            {perms.can_take && status === 'ASSIGNED' && (
                                <button className="btn btn-primary btn-sm" onClick={() => void changeStatus('IN_PROGRESS')}>
                                    ▶ Взять в работу
                                </button>
                            )}
                            {perms.can_submit && (
                                <button className="btn btn-primary btn-sm" onClick={() => setModal({ kind: 'submit' })}>
                                    📤 Сдать работу
                                </button>
                            )}
                            {perms.can_verdict && pendingSub && (
                                <>
                                    <button
                                        className="btn btn-success btn-sm"
                                        onClick={() => void apply(
                                            () => api.setDesignVerdict(taskId, pendingSub.id, { verdict: 'ACCEPTED' }),
                                            'Версия принята',
                                        )}
                                    >
                                        ✅ Принять
                                    </button>
                                    <button className="btn btn-danger btn-sm" onClick={() => setModal({ kind: 'return', subId: pendingSub.id })}>
                                        ↩ Вернуть
                                    </button>
                                </>
                            )}
                            {perms.can_assign && (
                                <button className="btn btn-secondary btn-sm" onClick={() => setModal({ kind: 'assign' })}>
                                    👤 {task.assignee_user_id == null ? 'Назначить' : 'Переназначить'}
                                </button>
                            )}
                            {perms.can_hold && status !== 'ON_HOLD' && (
                                <button className="btn btn-secondary btn-sm" onClick={() => setModal({ kind: 'status', to: 'ON_HOLD', required: false })}>
                                    ⏸ Отложить
                                </button>
                            )}
                            {status === 'ON_HOLD' && allowed.has(task.held_from_status ?? 'NEW') && (
                                <button
                                    className="btn btn-primary btn-sm"
                                    onClick={() => void changeStatus(task.held_from_status ?? 'NEW')}
                                >
                                    ▶ Вернуть из отложенных ({DESIGN_STATUS_LABEL[task.held_from_status ?? 'NEW']})
                                </button>
                            )}
                            {genericTargets.map((t) => (
                                <button
                                    key={t}
                                    className="btn btn-secondary btn-sm"
                                    onClick={() => {
                                        if (t === 'REVISION') setModal({ kind: 'status', to: t, required: true });
                                        else void changeStatus(t);
                                    }}
                                >
                                    → {DESIGN_STATUS_LABEL[t]}
                                </button>
                            ))}
                            {perms.can_edit && (
                                <button className="btn btn-secondary btn-sm" onClick={() => setModal({ kind: 'edit' })}>
                                    ✏️ Редактировать
                                </button>
                            )}
                            {perms.can_cancel && (
                                <button className="btn btn-danger btn-sm" onClick={() => setModal({ kind: 'cancel' })}>
                                    ✖ Отменить задачу
                                </button>
                            )}
                            {perms.can_delete && (
                                <button
                                    className="btn btn-danger btn-sm"
                                    onClick={() => {
                                        if (!window.confirm(`Удалить заявку ${task.number}?`)) return;
                                        api.deleteDesignTask(taskId)
                                            .then(() => router.push(`/p/${params.slug}/design-tasks`))
                                            .catch((e) => setToast({ type: 'error', message: errText(e, 'Не удалось удалить') }));
                                    }}
                                >
                                    🗑 Удалить
                                </button>
                            )}
                        </div>
                    </div>

                    <SubmissionsCard task={task} userName={userName} onError={(m) => setToast({ type: 'error', message: m })} />
                    <CommentsCard task={task} onChanged={() => void load()} onError={(m) => setToast({ type: 'error', message: m })} />
                </div>
            </div>

            {/* ── Модалки ── */}
            {modal?.kind === 'submit' && (
                <SubmitWorkModal
                    taskId={taskId}
                    onDone={(t) => { setTask(t); setModal(null); setToast({ type: 'success', message: 'Версия отправлена на проверку' }); }}
                    onClose={() => setModal(null)}
                />
            )}
            {modal?.kind === 'return' && (
                <ReasonModal
                    title="Вернуть на доработку"
                    label="Причина возврата"
                    required
                    submitLabel="Вернуть в правки"
                    placeholder="Что нужно исправить"
                    onSubmit={async (reason) => {
                        const ok = await apply(() => api.setDesignVerdict(taskId, modal.subId, { verdict: 'REJECTED', verdict_comment: reason }));
                        if (ok) setModal(null);
                    }}
                    onClose={() => setModal(null)}
                />
            )}
            {modal?.kind === 'assign' && (
                <AssignModal
                    slug={params.slug}
                    currentAssigneeId={task.assignee_user_id}
                    onAssign={async (userId) => {
                        const ok = await apply(() => api.assignDesignTask(taskId, { assignee_user_id: userId }));
                        if (ok) setModal(null);
                    }}
                    onClose={() => setModal(null)}
                />
            )}
            {modal?.kind === 'edit' && (
                <EditTaskModal
                    task={task}
                    onSaved={(t) => { setTask(t); setModal(null); setToast({ type: 'success', message: 'Сохранено' }); }}
                    onClose={() => setModal(null)}
                />
            )}
            {modal?.kind === 'status' && (
                <ReasonModal
                    title={`Переход: ${DESIGN_STATUS_LABEL[modal.to]}`}
                    label={modal.required ? 'Причина' : 'Комментарий (необязательно)'}
                    required={modal.required}
                    submitLabel="Подтвердить"
                    onSubmit={async (reason) => {
                        const ok = await changeStatus(modal.to, reason);
                        if (ok) setModal(null);
                    }}
                    onClose={() => setModal(null)}
                />
            )}
            {modal?.kind === 'cancel' && (
                <ReasonModal
                    title={`Отменить заявку ${task.number}`}
                    label="Причина отмены"
                    required
                    submitLabel="Отменить задачу"
                    onSubmit={async (reason) => {
                        const ok = await changeStatus('CANCELLED', reason);
                        if (ok) setModal(null);
                    }}
                    onClose={() => setModal(null)}
                />
            )}

            {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
        </PageGuard>
    );
}

'use client';

import { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import PageHeader from '@/components/PageHeader';
import Toast from '@/components/Toast';
import { usePermissions } from '@/lib/hooks/usePermissions';
import {
    applyOptimisticMove,
    buildBoardColumns,
    findTaskColumn,
    type BoardColumns,
    type DesignBoardStatus,
} from '@/lib/design';
import type { DesignBoardResponse } from '@/types/api';
import BoardView from './components/BoardView';
import ListView from './components/ListView';
import HoldCancelledOverlay from './components/HoldCancelledOverlay';
import ReasonModal from './components/ReasonModal';

interface PendingMove {
    taskId: number;
    toStatus: DesignBoardStatus;
    beforeTaskId: number | null;
}

function DesignTasksPageInner() {
    const params = useParams<{ slug: string }>();
    const router = useRouter();
    const searchParams = useSearchParams();
    const { canEdit, canManage } = usePermissions();

    const view = searchParams.get('view') === 'list' ? 'list' : 'board';

    const [columns, setColumns] = useState<BoardColumns | null>(null);
    const [counts, setCounts] = useState<DesignBoardResponse['counts']>({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const [overlay, setOverlay] = useState<'ON_HOLD' | 'CANCELLED' | null>(null);
    // Модалка причины ПЕРЕД move: dnd/кнопка в «Правки» требует комментарий (контракт: 400 без него).
    const [pendingMove, setPendingMove] = useState<PendingMove | null>(null);
    const mountedRef = useRef(true);

    const load = useCallback(async (quiet = false) => {
        if (!quiet) {
            setLoading(true);
            setError(null);
        }
        try {
            const res = await api.getDesignBoard();
            if (!mountedRef.current) return;
            setColumns(buildBoardColumns(res));
            setCounts(res.counts);
        } catch (e) {
            if (!mountedRef.current) return;
            if (!quiet) setError(e instanceof Error ? e.message : 'Не удалось загрузить доску');
        } finally {
            if (mountedRef.current && !quiet) setLoading(false);
        }
    }, []);

    useEffect(() => {
        mountedRef.current = true;
        if (view === 'board') void load();
        return () => { mountedRef.current = false; };
    }, [load, view]);

    const openTask = useCallback(
        (taskId: number) => router.push(`/p/${params.slug}/design-tasks/${taskId}`),
        [router, params.slug],
    );

    const setView = (v: 'board' | 'list') => {
        router.replace(`/p/${params.slug}/design-tasks${v === 'list' ? '?view=list' : ''}`);
    };

    /** Оптимистичный move (Р4): применяем локально → POST /move → на 4xx откат + toast бэка + refetch. */
    const doMove = useCallback(
        async (taskId: number, toStatus: DesignBoardStatus, beforeTaskId: number | null, comment?: string) => {
            if (!columns) return;
            const snapshot = columns;
            const mv = applyOptimisticMove(columns, taskId, toStatus, beforeTaskId);
            if (!mv || !mv.changed) return;
            setColumns(mv.columns);
            try {
                await api.moveDesignTask(taskId, {
                    to_status: toStatus,
                    after_task_id: mv.afterTaskId,
                    comment: comment?.trim() ? comment.trim() : null,
                });
                void load(true);
            } catch (e) {
                if (!mountedRef.current) return;
                setColumns(snapshot);
                setToast({ type: 'error', message: e instanceof Error ? e.message : 'Перемещение отклонено' });
                void load(true);
            }
        },
        [columns, load],
    );

    const onMoveRequest = useCallback(
        (taskId: number, toStatus: DesignBoardStatus, beforeTaskId: number | null) => {
            if (!columns) return;
            const fromStatus = findTaskColumn(columns, taskId);
            if (toStatus === 'REVISION' && fromStatus !== 'REVISION') {
                setPendingMove({ taskId, toStatus, beforeTaskId });
                return;
            }
            void doMove(taskId, toStatus, beforeTaskId);
        },
        [columns, doMove],
    );

    const onHoldCount = counts.ON_HOLD ?? 0;
    const cancelledCount = counts.CANCELLED ?? 0;

    return (
        <PageGuard page="design-tasks">
            <PageHeader
                title="🎨 Дизайн карточек"
                subtitle="Задачи на инфографику: заявка → назначение → работа → версии сдач → приёмка"
                actions={
                    <>
                        <div style={{ display: 'flex', gap: 4 }}>
                            <button
                                className={`btn btn-sm ${view === 'board' ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setView('board')}
                            >
                                Доска
                            </button>
                            <button
                                className={`btn btn-sm ${view === 'list' ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setView('list')}
                            >
                                Список
                            </button>
                        </div>
                        {canEdit() && (
                            <button className="btn btn-primary btn-sm" onClick={() => router.push(`/p/${params.slug}/design-tasks/new`)}>
                                + Новая заявка
                            </button>
                        )}
                    </>
                }
            />

            {view === 'list' ? (
                <ListView slug={params.slug} />
            ) : (
                <>
                    <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                        <button className="btn btn-sm btn-secondary" onClick={() => setOverlay('ON_HOLD')}>
                            ⏸ Отложенные ({formatNumber(onHoldCount, 0)})
                        </button>
                        <button className="btn btn-sm btn-secondary" onClick={() => setOverlay('CANCELLED')}>
                            ✖ Отменённые ({formatNumber(cancelledCount, 0)})
                        </button>
                    </div>

                    {loading && (
                        <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-muted)' }}>Загрузка…</div>
                    )}
                    {error && !loading && (
                        <div className="glass-card" style={{ color: 'var(--color-danger)' }}>
                            {error} <button className="btn btn-sm btn-secondary" onClick={() => void load()}>Повторить</button>
                        </div>
                    )}
                    {!loading && !error && columns && (
                        Object.values(columns).every((c) => c.length === 0) && onHoldCount === 0 && cancelledCount === 0 ? (
                            <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-muted)', padding: 48 }}>
                                Заявок ещё нет. Создайте первую: «+ Новая заявка».
                            </div>
                        ) : (
                            <BoardView
                                columns={columns}
                                canReorder={canManage()}
                                onOpen={openTask}
                                onMoveRequest={onMoveRequest}
                            />
                        )
                    )}
                </>
            )}

            {overlay && (
                <HoldCancelledOverlay status={overlay} onOpen={openTask} onClose={() => setOverlay(null)} />
            )}

            {pendingMove && (
                <ReasonModal
                    title="Возврат в «Правки»"
                    label="Причина возврата"
                    required
                    submitLabel="Вернуть в правки"
                    placeholder="Что нужно исправить"
                    onSubmit={async (reason) => {
                        const mv = pendingMove;
                        setPendingMove(null);
                        await doMove(mv.taskId, mv.toStatus, mv.beforeTaskId, reason);
                    }}
                    onClose={() => setPendingMove(null)}
                />
            )}

            {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
        </PageGuard>
    );
}

export default function DesignTasksPage() {
    // useSearchParams требует Suspense-границу при статической генерации.
    return (
        <Suspense fallback={<div style={{ padding: 40, color: 'var(--color-muted)' }}>Загрузка…</div>}>
            <DesignTasksPageInner />
        </Suspense>
    );
}

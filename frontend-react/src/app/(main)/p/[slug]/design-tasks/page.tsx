'use client';

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import PageGuard from '@/components/PageGuard';
import PageHeader from '@/components/PageHeader';
import Toast from '@/components/Toast';
import {
    DESIGN_BOARD_STATUSES,
    DESIGN_STATUS_LABEL,
    applyOptimisticMove,
    buildBoardColumns,
    findTaskColumn,
    type BoardColumns,
    type DesignBoardStatus,
} from '@/lib/design';
import type { DesignBoardPermissions, DesignBoardResponse, DesignTaskListItem } from '@/types/api';
import BoardView from './components/BoardView';
import ListView from './components/ListView';
import BoardFilters, {
    EMPTY_BOARD_FILTERS,
    applyBoardFilters,
    type BoardFilterState,
    type BoardScope,
} from './components/BoardFilters';
import FlatTaskList from './components/FlatTaskList';
import ReasonModal from './components/ReasonModal';
import TaskCalendarModal from './components/TaskCalendarModal';
import DesignTabs from './components/DesignTabs';
import StatsPanel from './components/StatsPanel';

interface PendingMove {
    taskId: number;
    toStatus: DesignBoardStatus;
    beforeTaskId: number | null;
}

/** До ответа /board кнопок нет: права считает бэк (§6.9), роль фронт не проверяет. */
const NO_BOARD_PERMS: DesignBoardPermissions = { can_create: false, can_reorder: false };

function DesignTasksPageInner() {
    const params = useParams<{ slug: string }>();
    const router = useRouter();
    const searchParams = useSearchParams();

    const view = searchParams.get('view') === 'list' ? 'list' : 'board';

    const [columns, setColumns] = useState<BoardColumns | null>(null);
    const [counts, setCounts] = useState<DesignBoardResponse['counts']>({});
    const [perms, setPerms] = useState<DesignBoardPermissions>(NO_BOARD_PERMS);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const [filters, setFilters] = useState<BoardFilterState>(EMPTY_BOARD_FILTERS);
    /**
     * Кэш наборов вне доски (Р21). Отложенные и отменённые на доске не лежат —
     * это ДРУГОЙ набор данных, а не фильтр: первое переключение делает один
     * запрос, дальше набор переиспользуется. Мутация задачи чистит кэш.
     */
    const [offBoard, setOffBoard] = useState<Partial<Record<Exclude<BoardScope, 'board'>, DesignTaskListItem[]>>>({});
    const [scopeLoading, setScopeLoading] = useState(false);
    const [scopeError, setScopeError] = useState<string | null>(null);
    // Модалка причины ПЕРЕД move: dnd/кнопка в «Правки» требует комментарий (контракт: 400 без него).
    const [pendingMove, setPendingMove] = useState<PendingMove | null>(null);
    // Персональный календарь задачи: данные — из уже загруженной карточки доски, без запросов.
    const [calendarTask, setCalendarTask] = useState<DesignTaskListItem | null>(null);
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
            setPerms(res.permissions);
        } catch (e) {
            if (!mountedRef.current) return;
            if (!quiet) setError(e instanceof Error ? e.message : 'Не удалось загрузить доску');
        } finally {
            if (mountedRef.current && !quiet) setLoading(false);
        }
    }, []);

    useEffect(() => {
        mountedRef.current = true;
        // В режиме списка доска тоже запрашивается — тихо (без спиннера и баннера
        // ошибки): её ответ несёт permissions уровня доски, а «+ Новая заявка»
        // живёт в общей шапке обоих режимов. Права считает бэк (§6.9), роль фронт
        // не проверяет; сбой запроса оставляет флаги false — кнопки просто нет.
        void load(view !== 'board');
        return () => { mountedRef.current = false; };
    }, [load, view]);

    const openTask = useCallback(
        (taskId: number) => router.push(`/p/${params.slug}/design-tasks/${taskId}`),
        [router, params.slug],
    );

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
                // Мутация могла увести задачу из отложенных/отменённых — кэш наборов
                // вне доски протух, следующее переключение перезапросит его.
                setOffBoard({});
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

    // Ленивая догрузка набора вне доски: ровно один запрос на статус за сессию
    // страницы. Возврат на «На доске» и повторное переключение — без сети.
    useEffect(() => {
        const scope = filters.scope;
        if (scope === 'board' || offBoard[scope]) return;
        let alive = true;
        setScopeLoading(true);
        setScopeError(null);
        api.listDesignTasks({ status: [scope], limit: 200 })
            .then((rows) => { if (alive && mountedRef.current) setOffBoard((prev) => ({ ...prev, [scope]: rows })); })
            .catch((e) => { if (alive && mountedRef.current) setScopeError(e instanceof Error ? e.message : 'Не удалось загрузить'); })
            .finally(() => { if (alive && mountedRef.current) setScopeLoading(false); });
        return () => { alive = false; };
    }, [filters.scope, offBoard]);

    /** Задачи текущего набора до фильтров — из них же строится список исполнителей. */
    const scopeTasks = useMemo<DesignTaskListItem[]>(() => {
        if (filters.scope !== 'board') return offBoard[filters.scope] ?? [];
        return columns ? DESIGN_BOARD_STATUSES.flatMap((s) => columns[s]) : [];
    }, [filters.scope, offBoard, columns]);

    /** Колонки доски после клиентских фильтров — сеть не трогается (Р21). */
    const filteredColumns = useMemo<BoardColumns | null>(() => {
        if (!columns) return null;
        const out = {} as BoardColumns;
        for (const s of DESIGN_BOARD_STATUSES) out[s] = applyBoardFilters(columns[s], filters);
        return out;
    }, [columns, filters]);

    const filteredFlat = useMemo(
        () => (filters.scope === 'board' ? [] : applyBoardFilters(scopeTasks, filters)),
        [filters, scopeTasks],
    );

    return (
        <PageGuard page="design-tasks">
            <PageHeader
                title="🖌️ Дизайн карточек"
                subtitle="Задачи на инфографику: заявка → назначение → работа → версии сдач → приёмка"
                actions={
                    <>
                        <DesignTabs slug={params.slug} active={view} />
                        {perms.can_create && (
                            <button className="btn btn-primary btn-sm" onClick={() => router.push(`/p/${params.slug}/design-tasks/new`)}>
                                + Новая заявка
                            </button>
                        )}
                    </>
                }
            />

            {view === 'list' ? (
                <>
                    <ListView slug={params.slug} />
                    {/* Список задач первее всего: метрики опускаются ПОД таблицу
                        (в волне D уедут во вкладку «Аналитика»). */}
                    <StatsPanel />
                </>
            ) : (
                <>
                    <BoardFilters
                        value={filters}
                        onChange={setFilters}
                        tasks={scopeTasks}
                        onHoldCount={onHoldCount}
                        cancelledCount={cancelledCount}
                    />

                    {loading && (
                        <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка…</div>
                    )}
                    {error && !loading && (
                        <div className="glass-card" style={{ color: 'var(--color-danger)' }}>
                            {error} <button className="btn btn-sm btn-secondary" onClick={() => void load()}>Повторить</button>
                        </div>
                    )}

                    {!loading && !error && filters.scope !== 'board' && (
                        scopeLoading && !offBoard[filters.scope] ? (
                            <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка…</div>
                        ) : scopeError ? (
                            <div className="glass-card" style={{ color: 'var(--color-danger)' }}>{scopeError}</div>
                        ) : (
                            <FlatTaskList
                                tasks={filteredFlat}
                                emptyText={
                                    scopeTasks.length === 0
                                        ? `Нет задач в статусе «${DESIGN_STATUS_LABEL[filters.scope]}».`
                                        : 'Нет задач под выбранные фильтры.'
                                }
                                onOpen={openTask}
                            />
                        )
                    )}

                    {!loading && !error && filters.scope === 'board' && filteredColumns && columns && (
                        DESIGN_BOARD_STATUSES.every((s) => columns[s].length === 0) && onHoldCount === 0 && cancelledCount === 0 ? (
                            <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: 48 }}>
                                Заявок ещё нет. Создайте первую: «+ Новая заявка».
                            </div>
                        ) : (
                            <BoardView
                                columns={filteredColumns}
                                canReorder={perms.can_reorder}
                                onOpen={openTask}
                                onMoveRequest={onMoveRequest}
                                onOpenCalendar={setCalendarTask}
                            />
                        )
                    )}
                </>
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

            {calendarTask && (
                <TaskCalendarModal
                    task={calendarTask}
                    onClose={() => setCalendarTask(null)}
                    onOpenTask={() => { const id = calendarTask.id; setCalendarTask(null); openTask(id); }}
                />
            )}

            {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
        </PageGuard>
    );
}

export default function DesignTasksPage() {
    // useSearchParams требует Suspense-границу при статической генерации.
    return (
        <Suspense fallback={<div style={{ padding: 40, color: 'var(--color-text-muted)' }}>Загрузка…</div>}>
            <DesignTasksPageInner />
        </Suspense>
    );
}

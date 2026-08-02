'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import PageGuard from '@/components/PageGuard';
import PageHeader from '@/components/PageHeader';
import {
    DESIGN_STATUS_COLOR,
    UNASSIGNED_COLOR,
    buildAssigneeColors,
    buildMonthGrid,
    currentMonth,
    formatMonthTitle,
    shiftMonth,
    type CalendarColorMode,
    type DesignMemberLike,
} from '@/lib/designCalendar';
import type { DesignCalendarOut, DesignTaskListItem } from '@/types/api';
import DesignTabs from '../components/DesignTabs';
import MonthGrid from '../components/MonthGrid';
import DayPanel from '../components/DayPanel';

/** Календарь месяца по due_date (Р7): грид 7 колонок, раскраска по дизайнеру/статусу. */
export default function DesignCalendarPage() {
    const params = useParams<{ slug: string }>();
    const router = useRouter();

    const [month, setMonth] = useState(currentMonth);
    const [data, setData] = useState<DesignCalendarOut | null>(null);
    const [members, setMembers] = useState<DesignMemberLike[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [colorMode, setColorMode] = useState<CalendarColorMode>('designer');
    const [selectedDay, setSelectedDay] = useState<string | null>(null);
    const mountedRef = useRef(true);
    /** Поколение загрузки: abort предыдущей + сверка запрошенного месяца (learnings). */
    const abortRef = useRef<AbortController | null>(null);
    const monthRef = useRef(month);

    const load = useCallback(async (m: string) => {
        // Быстрое переключение стрелками: ответ месяца A не должен перезаписать B.
        abortRef.current?.abort();
        const controller = new AbortController();
        abortRef.current = controller;
        monthRef.current = m;
        const stale = () => controller.signal.aborted || m !== monthRef.current || !mountedRef.current;
        setLoading(true);
        setError(null);
        try {
            const res = await api.getDesignCalendar(m);
            if (stale()) return;
            setData(res);
        } catch (e) {
            if (stale()) return;
            setError(e instanceof Error ? e.message : 'Не удалось загрузить календарь');
        } finally {
            if (!stale()) setLoading(false);
        }
    }, []);

    useEffect(() => {
        mountedRef.current = true;
        void load(month);
        return () => {
            mountedRef.current = false;
            abortRef.current?.abort();
        };
    }, [load, month]);

    useEffect(() => {
        // Палитра по дизайнеру: user_id членов проекта (best-effort, фолбэк — серый).
        api.getMembers(params.slug)
            .then((rows) => { if (mountedRef.current) setMembers(rows); })
            .catch(() => { /* цвета останутся нейтральными */ });
    }, [params.slug]);

    const weeks = useMemo(() => buildMonthGrid(month), [month]);

    const tasksByDay = useMemo(() => {
        const map = new Map<string, DesignTaskListItem[]>();
        for (const t of data?.tasks ?? []) {
            if (!t.due_date) continue;
            const list = map.get(t.due_date);
            if (list) list.push(t);
            else map.set(t.due_date, [t]);
        }
        return map;
    }, [data]);

    const assigneeColors = useMemo(() => buildAssigneeColors(members), [members]);

    const colorFor = useCallback(
        (t: DesignTaskListItem) => {
            if (colorMode === 'status') return DESIGN_STATUS_COLOR[t.status];
            if (!t.assignee_name) return UNASSIGNED_COLOR;
            return assigneeColors.get(t.assignee_name) ?? UNASSIGNED_COLOR;
        },
        [colorMode, assigneeColors],
    );

    const openTask = useCallback(
        (taskId: number) => router.push(`/p/${params.slug}/design-tasks/${taskId}`),
        [router, params.slug],
    );

    const monthTaskCount = useMemo(
        () => (data?.tasks ?? []).filter((t) => t.due_date?.slice(0, 7) === month).length,
        [data, month],
    );

    /** Легенда активного режима: дизайнеры с палитрой / статусы с цветами доски. */
    const legend = useMemo(() => {
        if (colorMode === 'status') {
            return (['NEW', 'ASSIGNED', 'IN_PROGRESS', 'REVIEW', 'REVISION', 'ACCEPTED'] as const)
                .map((s) => ({ key: s, label: { NEW: 'Новые', ASSIGNED: 'Назначена', IN_PROGRESS: 'В работе', REVIEW: 'На проверке', REVISION: 'Правки', ACCEPTED: 'Принято' }[s], color: DESIGN_STATUS_COLOR[s] }));
        }
        const names = new Set((data?.tasks ?? []).map((t) => t.assignee_name).filter((n): n is string => !!n));
        return [...names].sort().map((n) => ({ key: n, label: n, color: assigneeColors.get(n) ?? UNASSIGNED_COLOR }));
    }, [colorMode, data, assigneeColors]);

    return (
        <PageGuard page="design-tasks">
            <PageHeader
                title="📅 Дизайн карточек — календарь"
                subtitle="Задачи месяца по сроку сдачи (due date)"
                actions={<DesignTabs slug={params.slug} active="calendar" />}
            />

            <div className="glass-card" style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', padding: 12 }}>
                <button className="btn btn-secondary btn-sm" onClick={() => setMonth((m) => shiftMonth(m, -1))} aria-label="Предыдущий месяц">
                    ←
                </button>
                <span style={{ fontSize: 16, fontWeight: 700, minWidth: 140, textAlign: 'center' }}>
                    {formatMonthTitle(month)}
                </span>
                <button className="btn btn-secondary btn-sm" onClick={() => setMonth((m) => shiftMonth(m, 1))} aria-label="Следующий месяц">
                    →
                </button>
                {month !== currentMonth() && (
                    <button className="btn btn-secondary btn-sm" onClick={() => setMonth(currentMonth())}>
                        Сегодня
                    </button>
                )}
                <div style={{ display: 'flex', gap: 4, marginLeft: 'auto' }}>
                    <button
                        className={`btn btn-sm ${colorMode === 'designer' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setColorMode('designer')}
                    >
                        🎨 По дизайнеру
                    </button>
                    <button
                        className={`btn btn-sm ${colorMode === 'status' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setColorMode('status')}
                    >
                        📊 По статусу
                    </button>
                </div>
            </div>

            {loading && (
                <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка…</div>
            )}
            {error && !loading && (
                <div className="glass-card" style={{ color: 'var(--color-danger)' }}>
                    {error} <button className="btn btn-sm btn-secondary" onClick={() => void load(month)}>Повторить</button>
                </div>
            )}
            {!loading && !error && data && (
                <>
                    {monthTaskCount === 0 && (
                        <div className="glass-card" style={{ marginBottom: 12, textAlign: 'center', color: 'var(--color-text-muted)', padding: 16 }}>
                            В этом месяце задач со сроком нет. Срок задаётся в заявке — поле «Срок сдачи».
                        </div>
                    )}
                    <MonthGrid
                        weeks={weeks}
                        tasksByDay={tasksByDay}
                        colorFor={colorFor}
                        onDayClick={setSelectedDay}
                        onTaskClick={openTask}
                    />
                    {legend.length > 0 && (
                        <div style={{ display: 'flex', gap: '6px 16px', flexWrap: 'wrap', marginTop: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>
                            {legend.map(({ key, label, color }) => (
                                <span key={key} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                                    <span style={{ width: 10, height: 10, borderRadius: '50%', background: color, display: 'inline-block' }} />
                                    {label}
                                </span>
                            ))}
                        </div>
                    )}
                </>
            )}

            {selectedDay && (
                <DayPanel
                    iso={selectedDay}
                    tasks={tasksByDay.get(selectedDay) ?? []}
                    colorFor={colorFor}
                    onTaskClick={openTask}
                    onClose={() => setSelectedDay(null)}
                />
            )}
        </PageGuard>
    );
}

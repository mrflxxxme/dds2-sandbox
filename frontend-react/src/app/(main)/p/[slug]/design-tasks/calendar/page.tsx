'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import PageGuard from '@/components/PageGuard';
import PageHeader from '@/components/PageHeader';
import PeriodPicker from '@/components/PeriodPicker';
import InfoTip from '@/components/InfoTip';
import {
    DESIGN_STATUS_COLOR,
    UNASSIGNED_COLOR,
    buildAssigneeColors,
    buildMonthGrid,
    MAX_CALENDAR_MONTHS,
    countMonthsInRange,
    defaultCalendarRange,
    monthsInRange,
    shiftCalendarRange,
    type CalendarColorMode,
    type CalendarRange,
    type DesignMemberLike,
} from '@/lib/designCalendar';
import { DESIGN_UI_HINT } from '@/lib/designHints';
import type { DesignCalendarOut, DesignTaskListItem } from '@/types/api';
import DesignTabs from '../components/DesignTabs';
import MonthGrid from '../components/MonthGrid';
import DayPanel from '../components/DayPanel';

/** Календарь по due_date (Р7 + Р22): произвольный диапазон, блок на каждый месяц,
 *  раскраска по дизайнеру/статусу. Дефолт — текущий и следующий месяц. */
export default function DesignCalendarPage() {
    const params = useParams<{ slug: string }>();
    const router = useRouter();

    const [range, setRange] = useState<CalendarRange>(defaultCalendarRange);
    const [data, setData] = useState<DesignCalendarOut | null>(null);
    const [members, setMembers] = useState<DesignMemberLike[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [colorMode, setColorMode] = useState<CalendarColorMode>('designer');
    const [selectedDay, setSelectedDay] = useState<string | null>(null);
    const mountedRef = useRef(true);
    /** Поколение загрузки: abort предыдущей + сверка запрошенного окна (learnings). */
    const abortRef = useRef<AbortController | null>(null);
    const rangeRef = useRef(range);

    const load = useCallback(async (r: CalendarRange) => {
        // Быстрое переключение стрелками: предыдущий запрос РВЁТСЯ (signal уходит
        // в fetch), а не просто помечается протухшим — иначе пять кликов по стрелке
        // оставляли бы пять выборок на 2000 задач, доводимых сервером до конца.
        abortRef.current?.abort();
        const controller = new AbortController();
        abortRef.current = controller;
        rangeRef.current = r;
        const stale = () =>
            controller.signal.aborted
            || r.from !== rangeRef.current.from
            || r.to !== rangeRef.current.to
            || !mountedRef.current;
        setLoading(true);
        setError(null);
        try {
            const res = await api.getDesignCalendarRange(r.from, r.to, controller.signal);
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
        void load(range);
        return () => {
            mountedRef.current = false;
            abortRef.current?.abort();
        };
    }, [load, range]);

    useEffect(() => {
        // Палитра по дизайнеру: user_id членов проекта (best-effort, фолбэк — серый).
        api.getMembers(params.slug)
            .then((rows) => { if (mountedRef.current) setMembers(rows); })
            .catch(() => { /* цвета останутся нейтральными */ });
    }, [params.slug]);

    /** Блок на каждый месяц, пересекающийся с диапазоном (Р22), но не больше шести. */
    const months = useMemo(() => monthsInRange(range), [range]);
    const monthsClipped = useMemo(() => countMonthsInRange(range) > MAX_CALENDAR_MONTHS, [range]);
    const gridsByMonth = useMemo(
        () => months.map((m) => ({ month: m, weeks: buildMonthGrid(m) })),
        [months],
    );

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

    /** Задачи внутри ЗАПРОШЕННОГО диапазона: ответ шире на ±6 дней (окно сетки). */
    const rangeTaskCount = useMemo(
        () => (data?.tasks ?? []).filter((t) => !!t.due_date && t.due_date >= range.from && t.due_date <= range.to).length,
        [data, range],
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
                subtitle="Задачи по сроку сдачи (due date) за выбранный период"
                actions={<DesignTabs slug={params.slug} active="calendar" />}
            />

            <div className="glass-card" style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', padding: 12 }}>
                <button className="btn btn-secondary btn-sm" onClick={() => setRange((r) => shiftCalendarRange(r, -1))} aria-label="Период назад">
                    ←
                </button>
                <PeriodPicker
                    from={range.from}
                    to={range.to}
                    // Пустой период календарь не принимает — поэтому кнопки «Сбросить»
                    // здесь нет: иначе она обнуляла бы состояние пикера, родитель
                    // игнорировал бы ('',''), и пикер залипал бы рассинхронизированным.
                    clearable={false}
                    // Длинных пресетов не предлагаем: больше шести блоков календарь
                    // всё равно не рисует (MAX_CALENDAR_MONTHS).
                    presetKeys={['today', 'yesterday', '30d', '3m']}
                    onApply={(from, to) => { if (from && to) setRange({ from, to }); }}
                    minWidth={230}
                />
                <InfoTip text={DESIGN_UI_HINT.calendarRange} icon />
                <button className="btn btn-secondary btn-sm" onClick={() => setRange((r) => shiftCalendarRange(r, 1))} aria-label="Период вперёд">
                    →
                </button>
                <button className="btn btn-secondary btn-sm" onClick={() => setRange(defaultCalendarRange())}>
                    Сегодня
                </button>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginLeft: 'auto' }}>
                    <InfoTip text={DESIGN_UI_HINT.colorMode} icon>
                        <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Раскраска</span>
                    </InfoTip>
                    <button
                        className={`btn btn-sm ${colorMode === 'designer' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setColorMode('designer')}
                    >
                        👤 По дизайнеру
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
                    {error} <button className="btn btn-sm btn-secondary" onClick={() => void load(range)}>Повторить</button>
                </div>
            )}
            {!loading && !error && data && (
                <>
                    {rangeTaskCount === 0 && (
                        <div className="glass-card" style={{ marginBottom: 12, textAlign: 'center', color: 'var(--color-text-muted)', padding: 16 }}>
                            В этом периоде задач со сроком нет. Срок задаётся в заявке — поле «Срок сдачи».
                        </div>
                    )}
                    {monthsClipped && (
                        <div className="glass-card" style={{ marginBottom: 12, color: 'var(--color-warning)', padding: 12, fontSize: 13 }}>
                            {DESIGN_UI_HINT.calendarTooManyMonths}
                        </div>
                    )}
                    {data.truncated && (
                        <div className="glass-card" style={{ marginBottom: 12, color: 'var(--color-warning)', padding: 12, fontSize: 13 }}>
                            {DESIGN_UI_HINT.calendarTruncated}
                        </div>
                    )}
                    {/* Блоки в ряд на широком экране, колонкой на узком — область скроллится. */}
                    <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
                        {gridsByMonth.map(({ month: m, weeks }) => (
                            <MonthGrid
                                key={m}
                                month={m}
                                weeks={weeks}
                                tasksByDay={tasksByDay}
                                colorFor={colorFor}
                                onDayClick={setSelectedDay}
                                onTaskClick={openTask}
                            />
                        ))}
                    </div>
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

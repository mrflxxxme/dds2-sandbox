'use client';

import { useMemo, useState } from 'react';
import { formatDate, formatNumber } from '@/lib/utils';
import {
    TASK_MARKER_COLOR,
    TASK_MARKER_LABEL,
    WEEKDAY_LABELS,
    buildMonthGrid,
    buildTaskRange,
    currentMonth,
    formatMonthTitle,
    markersByDay,
    shiftMonth,
    taskRangeEdge,
    type TaskDatesLike,
    type TaskMarkerKind,
    type TaskRangeEdge,
} from '@/lib/designCalendar';
import ModalShell from './ModalShell';

interface TaskCalendarModalProps {
    /**
     * Уже загруженная задача. Деталка (`DesignTaskDetail`) даёт всю хронологию,
     * карточка доски (`DesignTaskListItem`) — только срок: новых запросов к API
     * модалка не делает и контракт не трогает.
     */
    task: TaskDatesLike & { number: string; title: string };
    onClose: () => void;
    /** Переход на деталку — предлагается, когда хронологии в данных нет. */
    onOpenTask?: () => void;
}

/** Торцы полосы — радиус «кнопки» (12) из сетки скруглений design.md. */
const CELL_RADIUS = 12;

/** Скругление полосы: круглые торцы, прямая середина (референс — системный календарь). */
function bandCorners(edge: TaskRangeEdge) {
    const left = edge === 'start' || edge === 'single';
    const right = edge === 'end' || edge === 'single';
    return {
        left: left ? 3 : 0,
        right: right ? 3 : 0,
        borderTopLeftRadius: left ? CELL_RADIUS : 0,
        borderBottomLeftRadius: left ? CELL_RADIUS : 0,
        borderTopRightRadius: right ? CELL_RADIUS : 0,
        borderBottomRightRadius: right ? CELL_RADIUS : 0,
    };
}

/** Персональный мини-календарь задачи: месяц + подсвеченные сроки этой задачи. */
export default function TaskCalendarModal({ task, onClose, onOpenTask }: TaskCalendarModalProps) {
    const range = useMemo(() => buildTaskRange(task), [task]);
    const [month, setMonth] = useState(range.month);

    const weeks = useMemo(() => buildMonthGrid(month), [month]);
    const marks = useMemo(() => markersByDay(range), [range]);

    // Полоса и акцент срока краснеют на просрочке — тинт через color-mix (design.md).
    const accent = range.isOverdue ? 'var(--color-danger)' : 'var(--color-accent)';
    const band = `color-mix(in srgb, ${accent} 14%, var(--color-bg))`;

    /** Легенда: только те смыслы, которые реально есть в данных этой задачи. */
    const legend: { key: string; label: string; color: string; bar?: boolean }[] = [];
    // Полосы в легенде нет, когда начало неизвестно: там она схлопнута в один день
    // срока и её смысл целиком покрыт строкой «Срок сдачи» (карточка доски).
    if (range.start && range.startSource) {
        legend.push({
            key: 'range',
            label: range.startSource === 'started' ? 'Работа: с момента взятия до срока' : 'Работа: с постановки до срока',
            color: band,
            bar: true,
        });
    }
    for (const kind of ['created', 'started', 'due', 'accepted'] as TaskMarkerKind[]) {
        if (!range.markers.some((m) => m.kind === kind)) continue;
        legend.push({
            key: kind,
            label: kind === 'due' && range.isOverdue ? 'Срок сдачи (просрочен)' : TASK_MARKER_LABEL[kind],
            color: kind === 'due' ? accent : TASK_MARKER_COLOR[kind],
        });
    }

    return (
        <ModalShell title={`📅 Календарь · ${task.number}`} onClose={onClose} width={420}>
            <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>{task.title}</div>

            {/* ── Навигация по месяцам ── */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                <button className="btn btn-secondary btn-sm" onClick={() => setMonth((m) => shiftMonth(m, -1))} aria-label="Предыдущий месяц">
                    ‹
                </button>
                <span style={{ fontSize: 15, fontWeight: 700, flex: 1, textAlign: 'center' }}>{formatMonthTitle(month)}</span>
                <button className="btn btn-secondary btn-sm" onClick={() => setMonth((m) => shiftMonth(m, 1))} aria-label="Следующий месяц">
                    ›
                </button>
                {month !== currentMonth() && (
                    <button className="btn btn-secondary btn-sm" onClick={() => setMonth(currentMonth())}>
                        Сегодня
                    </button>
                )}
                {month !== range.month && (
                    <button className="btn btn-secondary btn-sm" onClick={() => setMonth(range.month)}>
                        К сроку
                    </button>
                )}
            </div>

            {/* ── Сетка месяца (Пн→Вс, стиль MonthGrid) ── */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)' }}>
                {WEEKDAY_LABELS.map((d) => (
                    <div
                        key={d}
                        style={{ textAlign: 'center', fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', padding: '4px 0' }}
                    >
                        {d}
                    </div>
                ))}
                {weeks.flat().map((day) => {
                    const edge = taskRangeEdge(range, day.iso);
                    const corners = bandCorners(edge);
                    const isDue = range.due != null && day.iso === range.due;
                    const dots = (marks.get(day.iso) ?? []).filter((k) => k !== 'due');
                    const labels = (marks.get(day.iso) ?? []).map((k) => TASK_MARKER_LABEL[k]);
                    return (
                        <div
                            key={day.iso}
                            title={labels.length ? `${formatDate(day.iso)} · ${labels.join(' · ')}` : formatDate(day.iso)}
                            style={{
                                position: 'relative',
                                height: 44,
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                opacity: day.inMonth ? 1 : 0.4,
                            }}
                        >
                            {edge && (
                                <span
                                    aria-hidden
                                    style={{
                                        position: 'absolute',
                                        top: 4,
                                        bottom: 4,
                                        left: corners.left,
                                        right: corners.right,
                                        background: band,
                                        borderTopLeftRadius: corners.borderTopLeftRadius,
                                        borderBottomLeftRadius: corners.borderBottomLeftRadius,
                                        borderTopRightRadius: corners.borderTopRightRadius,
                                        borderBottomRightRadius: corners.borderBottomRightRadius,
                                    }}
                                />
                            )}
                            <span style={{ position: 'relative', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
                                <span
                                    style={{
                                        width: 26,
                                        height: 26,
                                        borderRadius: '50%',
                                        display: 'flex',
                                        alignItems: 'center',
                                        justifyContent: 'center',
                                        fontSize: 12,
                                        fontWeight: isDue || day.isToday ? 700 : 500,
                                        background: isDue ? accent : 'transparent',
                                        color: isDue ? 'var(--color-bg)' : day.isToday ? 'var(--color-accent)' : 'var(--color-text)',
                                        border: `1px solid ${day.isToday && !isDue ? 'var(--color-accent)' : 'transparent'}`,
                                    }}
                                >
                                    {formatNumber(day.dayOfMonth, 0)}
                                </span>
                                <span style={{ display: 'flex', gap: 2, height: 4 }}>
                                    {dots.map((k) => (
                                        <span
                                            key={k}
                                            style={{ width: 4, height: 4, borderRadius: '50%', background: TASK_MARKER_COLOR[k] }}
                                        />
                                    ))}
                                </span>
                            </span>
                        </div>
                    );
                })}
            </div>

            {/* ── Легенда ── */}
            {legend.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', marginTop: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>
                    {legend.map(({ key, label, color, bar }) => (
                        <span key={key} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                            <span
                                style={{
                                    width: bar ? 18 : 8,
                                    height: 8,
                                    borderRadius: bar ? 8 : '50%',
                                    background: color,
                                    display: 'inline-block',
                                }}
                            />
                            {label}
                        </span>
                    ))}
                </div>
            )}

            {/* ── Даты текстом (события вне видимого месяца тоже видны) ── */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px', marginTop: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>
                {range.markers.map((m) => (
                    <span key={m.kind}>
                        {TASK_MARKER_LABEL[m.kind]}:{' '}
                        <b style={{ color: m.kind === 'due' && range.isOverdue ? 'var(--color-danger)' : 'var(--color-text)' }}>
                            {formatDate(m.iso)}
                        </b>
                    </span>
                ))}
            </div>

            {range.due == null && (
                <div
                    style={{
                        marginTop: 12,
                        padding: 10,
                        borderRadius: 8,
                        border: '1px solid var(--color-border)',
                        fontSize: 12,
                        color: 'var(--color-text-muted)',
                    }}
                >
                    Срок не задан — полосу работы построить не из чего. Задайте «Срок сдачи» в редактировании заявки.
                </div>
            )}

            {range.isOverdue && (
                <div style={{ marginTop: 12, fontSize: 12, color: 'var(--color-danger)', fontWeight: 600 }}>
                    ⚠ Срок просрочен
                </div>
            )}

            {/* Карточка доски не несёт created_at/started_at/accepted_at — честно говорим, где полная хронология. */}
            {task.created_at == null && (
                <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                        Полная хронология (поставлена · взята в работу · принята) — на странице задачи.
                    </span>
                    {onOpenTask && (
                        <button className="btn btn-sm btn-secondary" onClick={onOpenTask}>
                            Открыть задачу →
                        </button>
                    )}
                </div>
            )}
        </ModalShell>
    );
}

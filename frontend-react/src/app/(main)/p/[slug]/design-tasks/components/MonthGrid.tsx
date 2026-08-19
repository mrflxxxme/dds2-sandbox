'use client';

import { formatNumber } from '@/lib/utils';
import { WEEKDAY_LABELS, formatMonthTitle, type CalendarDay } from '@/lib/designCalendar';
import type { DesignTaskListItem } from '@/types/api';

const MAX_CHIPS = 3; // спек F5: в ячейке ≤3 чипа + «+N»

interface MonthGridProps {
    /** Месяц блока `YYYY-MM` — заголовок над сеткой (в диапазоне блоков несколько). */
    month: string;
    weeks: CalendarDay[][];
    /** due_date (YYYY-MM-DD) → задачи дня (отсортированы бэком). */
    tasksByDay: Map<string, DesignTaskListItem[]>;
    /** Цвет чипа по активному режиму раскраски (по дизайнеру / по статусу). */
    colorFor: (t: DesignTaskListItem) => string;
    onDayClick: (iso: string) => void;
    onTaskClick: (taskId: number) => void;
}

/** Блок одного месяца: CSS grid 7 колонок, неделя с Пн (Р7, без сторонних календарей).
 *  В диапазоне таких блоков несколько — по одному на каждый пересекаемый месяц. */
export default function MonthGrid({ month, weeks, tasksByDay, colorFor, onDayClick, onTaskClick }: MonthGridProps) {
    return (
        <div className="glass-card" style={{ padding: 12, minWidth: 300, flex: '1 1 320px' }}>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>{formatMonthTitle(month)}</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 4 }}>
                {WEEKDAY_LABELS.map((d) => (
                    <div
                        key={d}
                        style={{ textAlign: 'center', fontSize: 12, fontWeight: 500, color: 'var(--color-text-dim)', padding: '4px 0', textTransform: 'lowercase' }}
                    >
                        {d}
                    </div>
                ))}
                {weeks.flat().map((day) => {
                    const tasks = tasksByDay.get(day.iso) ?? [];
                    const extra = tasks.length - MAX_CHIPS;
                    return (
                        <div
                            key={day.iso}
                            onClick={() => onDayClick(day.iso)}
                            style={{
                                minHeight: 96,
                                border: `1px solid ${day.isToday ? 'var(--color-accent)' : 'var(--color-border)'}`,
                                borderRadius: 12,
                                padding: 6,
                                cursor: 'pointer',
                                // Дни соседних месяцев приглушены цветом, а не opacity:
                                // opacity гасила бы и цветные чипы задач вместе с ними.
                                color: day.inMonth ? undefined : 'var(--color-text-dim)',
                                opacity: day.inMonth ? 1 : 0.55,
                                background: day.isToday ? 'var(--color-bg-hover)' : 'transparent',
                                display: 'flex',
                                flexDirection: 'column',
                                gap: 4,
                                overflow: 'hidden',
                            }}
                        >
                            <span
                                style={{
                                    fontSize: 12,
                                    fontWeight: day.isToday ? 700 : 500,
                                    color: day.isToday ? 'var(--color-accent)' : 'var(--color-text-muted)',
                                }}
                            >
                                {formatNumber(day.dayOfMonth, 0)}
                            </span>
                            {tasks.slice(0, MAX_CHIPS).map((t) => (
                                <div
                                    key={t.id}
                                    title={`${t.number} · ${t.title}${t.assignee_name ? ` · ${t.assignee_name}` : ''}`}
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        onTaskClick(t.id);
                                    }}
                                    style={{
                                        borderLeft: `3px solid ${colorFor(t)}`,
                                        background: 'var(--color-bg-card)',
                                        borderRadius: 8,
                                        padding: '2px 6px',
                                        fontSize: 11,
                                        lineHeight: 1.35,
                                        whiteSpace: 'nowrap',
                                        overflow: 'hidden',
                                        textOverflow: 'ellipsis',
                                        fontWeight: t.is_urgent ? 700 : 400,
                                    }}
                                >
                                    {t.is_urgent ? '⚡ ' : ''}{t.number} · {t.title}
                                </div>
                            ))}
                            {extra > 0 && (
                                <span style={{ fontSize: 11, color: 'var(--color-accent)', fontWeight: 600 }}>
                                    +{formatNumber(extra, 0)} ещё
                                </span>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

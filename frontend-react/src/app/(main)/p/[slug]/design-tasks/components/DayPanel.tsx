'use client';

import { formatDate } from '@/lib/utils';
import { DESIGN_STATUS_BADGE, DESIGN_STATUS_LABEL } from '@/lib/design';
import type { DesignTaskListItem } from '@/types/api';

interface DayPanelProps {
    /** ISO-дата дня (YYYY-MM-DD). */
    iso: string;
    tasks: DesignTaskListItem[];
    /** Цвет метки по активному режиму раскраски — как у чипов сетки. */
    colorFor: (t: DesignTaskListItem) => string;
    onTaskClick: (taskId: number) => void;
    onClose: () => void;
}

/** Выезжающая панель дня: список задач по due_date, клик — деталка (спек F5). */
export default function DayPanel({ iso, tasks, colorFor, onTaskClick, onClose }: DayPanelProps) {
    return (
        <>
            <div
                onClick={onClose}
                style={{ position: 'fixed', inset: 0, background: 'rgba(0, 0, 0, 0.25)', zIndex: 90 }}
            />
            <div
                className="glass-card animate-in"
                role="dialog"
                aria-label={`Задачи на ${formatDate(iso)}`}
                style={{
                    position: 'fixed',
                    top: 0,
                    right: 0,
                    height: '100%',
                    width: 'min(380px, 92vw)',
                    zIndex: 91,
                    borderRadius: 0,
                    overflowY: 'auto',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 12,
                }}
            >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <h3 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>📅 {formatDate(iso)}</h3>
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={onClose}>
                        ✕
                    </button>
                </div>

                {tasks.length === 0 && (
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 13, textAlign: 'center', padding: 24 }}>
                        На этот день задач со сроком нет.
                    </div>
                )}

                {tasks.map((t) => (
                    <div
                        key={t.id}
                        onClick={() => onTaskClick(t.id)}
                        style={{
                            border: '1px solid var(--color-border)',
                            borderLeft: `3px solid ${colorFor(t)}`,
                            borderRadius: 8,
                            padding: 10,
                            cursor: 'pointer',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: 6,
                        }}
                    >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)' }}>{t.number}</span>
                            {t.is_urgent && <span className="badge badge-danger" style={{ fontSize: 10 }}>Срочно</span>}
                            <span className={`badge ${DESIGN_STATUS_BADGE[t.status]}`} style={{ fontSize: 10, marginLeft: 'auto' }}>
                                {DESIGN_STATUS_LABEL[t.status]}
                            </span>
                        </div>
                        <div style={{ fontSize: 13, lineHeight: 1.35 }}>{t.title}</div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                            {t.assignee_name ?? 'Не назначено'}
                            {t.is_overdue && <span style={{ color: 'var(--color-danger)', fontWeight: 600 }}> · просрочена</span>}
                        </div>
                    </div>
                ))}
            </div>
        </>
    );
}

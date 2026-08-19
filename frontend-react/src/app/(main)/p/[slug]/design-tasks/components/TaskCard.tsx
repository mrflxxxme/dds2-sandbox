'use client';

import WbThumb from '@/components/WbThumb';
import Tooltip from '@/components/Tooltip';
import { formatDate } from '@/lib/utils';
import { DESIGN_MARK_HINT } from '@/lib/designHints';
import type { DesignTaskListItem } from '@/types/api';

interface TaskCardProps {
    t: DesignTaskListItem;
    onClick: () => void;
    draggable?: boolean;
    onDragStart?: (e: React.DragEvent) => void;
    onDragOver?: (e: React.DragEvent) => void;
    onDrop?: (e: React.DragEvent) => void;
    /** Клик по значку срока — персональный календарь задачи (не мешает dnd/открытию). */
    onOpenCalendar?: () => void;
}

/** Карточка задачи на доске: номер · превью · заголовок · исполнитель · срок · бейджи. */
export default function TaskCard({ t, onClick, draggable, onDragStart, onDragOver, onDrop, onOpenCalendar }: TaskCardProps) {
    return (
        <div
            draggable={draggable}
            onDragStart={onDragStart}
            onDragOver={onDragOver}
            onDrop={onDrop}
            onClick={onClick}
            style={{
                background: 'var(--color-bg-card)',
                border: '1px solid var(--color-border)',
                borderRadius: 12,
                padding: 10,
                cursor: 'pointer',
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
            }}
        >
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                {t.unviewed && (
                    <Tooltip text={DESIGN_MARK_HINT.unviewed}>
                        <span
                            aria-label="Не просмотрено ведущим"
                            style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--color-danger)', flexShrink: 0 }}
                        />
                    </Tooltip>
                )}
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)' }}>{t.number}</span>
                {t.is_urgent && (
                    <Tooltip text={DESIGN_MARK_HINT.urgent}>
                        <span className="badge badge-danger" style={{ fontSize: 10 }}>Срочно</span>
                    </Tooltip>
                )}
                {t.is_outsourced && <span className="badge badge-secondary" style={{ fontSize: 10 }}>Аутсорс</span>}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <WbThumb nmId={t.nm_id} size={28} height={36} />
                <span style={{ fontSize: 13, lineHeight: 1.3, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                    {t.title}
                </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--color-text-muted)' }}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {t.assignee_name ?? '—'}
                </span>
                {t.due_date && (
                    <span
                        title={t.is_overdue ? DESIGN_MARK_HINT.overdue : 'Срок сдачи'}
                        style={{ marginLeft: 'auto', whiteSpace: 'nowrap', display: 'inline-flex', alignItems: 'center', gap: 4, color: t.is_overdue ? 'var(--color-danger)' : 'var(--color-text-muted)', fontWeight: t.is_overdue ? 600 : 400 }}
                    >
                        {formatDate(t.due_date)}
                        {onOpenCalendar && (
                            // Отдельная маленькая мишень: drag стартует с карточки,
                            // поэтому иконка draggable={false} + stopPropagation,
                            // иначе клик утечёт в onClick карточки (переход на деталку).
                            <span
                                role="button"
                                tabIndex={0}
                                draggable={false}
                                aria-label="Календарь задачи"
                                title="Календарь задачи"
                                onClick={(e) => {
                                    e.stopPropagation();
                                    onOpenCalendar();
                                }}
                                onKeyDown={(e) => {
                                    if (e.key !== 'Enter' && e.key !== ' ') return;
                                    e.preventDefault();
                                    e.stopPropagation();
                                    onOpenCalendar();
                                }}
                                onDragStart={(e) => { e.preventDefault(); e.stopPropagation(); }}
                                style={{ cursor: 'pointer', fontSize: 12, lineHeight: 1, padding: 2, borderRadius: 8 }}
                            >
                                📅
                            </span>
                        )}
                    </span>
                )}
            </div>
        </div>
    );
}

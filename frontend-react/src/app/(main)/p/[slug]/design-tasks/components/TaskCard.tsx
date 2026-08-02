'use client';

import WbThumb from '@/components/WbThumb';
import { formatDate } from '@/lib/utils';
import type { DesignTaskListItem } from '@/types/api';

interface TaskCardProps {
    t: DesignTaskListItem;
    onClick: () => void;
    draggable?: boolean;
    onDragStart?: (e: React.DragEvent) => void;
    onDragOver?: (e: React.DragEvent) => void;
    onDrop?: (e: React.DragEvent) => void;
}

/** Карточка задачи на доске: номер · превью · заголовок · исполнитель · срок · бейджи. */
export default function TaskCard({ t, onClick, draggable, onDragStart, onDragOver, onDrop }: TaskCardProps) {
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
                    <span
                        title="Не просмотрено ведущим"
                        style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--color-danger)', flexShrink: 0 }}
                    />
                )}
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)' }}>{t.number}</span>
                {t.is_urgent && <span className="badge badge-danger" style={{ fontSize: 10 }}>Срочно</span>}
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
                        title={t.is_overdue ? 'Просрочена' : 'Срок'}
                        style={{ marginLeft: 'auto', whiteSpace: 'nowrap', color: t.is_overdue ? 'var(--color-danger)' : 'var(--color-text-muted)', fontWeight: t.is_overdue ? 600 : 400 }}
                    >
                        {formatDate(t.due_date)}
                    </span>
                )}
            </div>
        </div>
    );
}

'use client';

import { formatDate } from '@/lib/utils';
import WbThumb from '@/components/WbThumb';
import type { DesignTaskListItem } from '@/types/api';

/**
 * Плоский список задач вне доски (отложенные / отменённые).
 * Пришёл на смену overlay-панели: то же содержимое, но обычным блоком страницы —
 * к нему применимы те же фильтры, что и к доске (Р21).
 */
export default function FlatTaskList({ tasks, emptyText, onOpen }: {
    tasks: DesignTaskListItem[];
    emptyText: string;
    onOpen: (taskId: number) => void;
}) {
    if (tasks.length === 0) {
        return (
            <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: 48 }}>
                {emptyText}
            </div>
        );
    }
    return (
        <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {tasks.map((t) => (
                <div
                    key={t.id}
                    onClick={() => onOpen(t.id)}
                    style={{
                        display: 'flex', alignItems: 'center', gap: 10, padding: 10,
                        border: '1px solid var(--color-border)', borderRadius: 12, cursor: 'pointer',
                    }}
                >
                    <WbThumb nmId={t.nm_id} size={28} height={36} />
                    <span style={{ minWidth: 0 }}>
                        <span style={{ display: 'block', fontSize: 13 }}>
                            <span style={{ color: 'var(--color-text-muted)', fontWeight: 600, marginRight: 6 }}>{t.number}</span>
                            {t.title}
                        </span>
                        <span style={{ display: 'block', fontSize: 12, color: 'var(--color-text-muted)' }}>
                            {t.assignee_name ?? 'без исполнителя'}
                            {t.due_date ? ` · до ${formatDate(t.due_date)}` : ''}
                        </span>
                    </span>
                </div>
            ))}
        </div>
    );
}

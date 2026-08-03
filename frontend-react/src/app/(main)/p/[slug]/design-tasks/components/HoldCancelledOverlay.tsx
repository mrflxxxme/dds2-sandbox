'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate } from '@/lib/utils';
import WbThumb from '@/components/WbThumb';
import { DESIGN_STATUS_LABEL } from '@/lib/design';
import type { DesignTaskListItem, DesignTaskStatus } from '@/types/api';

interface HoldCancelledOverlayProps {
    status: Extract<DesignTaskStatus, 'ON_HOLD' | 'CANCELLED'>;
    onOpen: (taskId: number) => void;
    onClose: () => void;
}

/** Оверлей «Отложенные / Отменённые» — списком вне доски (PRD §4). */
export default function HoldCancelledOverlay({ status, onOpen, onClose }: HoldCancelledOverlayProps) {
    const [tasks, setTasks] = useState<DesignTaskListItem[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const mountedRef = useRef(true);

    const load = useCallback(async () => {
        setError(null);
        try {
            const res = await api.listDesignTasks({ status: [status], limit: 200 });
            if (mountedRef.current) setTasks(res);
        } catch (e) {
            if (mountedRef.current) setError(e instanceof Error ? e.message : 'Не удалось загрузить');
        }
    }, [status]);

    useEffect(() => {
        mountedRef.current = true;
        setTasks(null);
        void load();
        return () => { mountedRef.current = false; };
    }, [load]);

    return (
        <div
            style={{ position: 'fixed', inset: 0, zIndex: 900, background: 'rgba(0, 0, 0, 0.4)' }}
            onClick={onClose}
        >
            <div
                className="glass-card animate-in"
                style={{
                    position: 'absolute', top: 0, right: 0, bottom: 0, width: 380, maxWidth: '90vw',
                    borderRadius: 0, overflowY: 'auto',
                }}
                onClick={(e) => e.stopPropagation()}
            >
                <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
                    <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>
                        {status === 'ON_HOLD' ? '⏸ Отложенные' : '✖ Отменённые'}
                    </h3>
                    <button className="btn btn-sm btn-secondary" style={{ marginLeft: 'auto' }} onClick={onClose}>✕</button>
                </div>

                {tasks === null && !error && (
                    <div style={{ color: 'var(--color-text-muted)', textAlign: 'center', padding: 24 }}>Загрузка…</div>
                )}
                {error && (
                    <div style={{ color: 'var(--color-danger)' }}>
                        {error} <button className="btn btn-sm btn-secondary" onClick={() => void load()}>Повторить</button>
                    </div>
                )}
                {tasks !== null && tasks.length === 0 && (
                    <div style={{ color: 'var(--color-text-muted)', textAlign: 'center', padding: 24 }}>
                        Нет задач в статусе «{DESIGN_STATUS_LABEL[status]}».
                    </div>
                )}
                {tasks !== null && tasks.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
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
                )}
            </div>
        </div>
    );
}

'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDateTime, pluralRu } from '@/lib/utils';
import { Toast } from '@/components';
import type { DraftEvent } from '@/types/api';

interface Props {
    draftId: number;
    onReverted?: () => void;
}

/** Иконка+лейбл по типу события; неизвестный тип — как есть. */
const EVENT_LABELS: Record<string, string> = {
    PREBOOK_TOPUP: '📦 Дозабор из предброни',
    MATRIX_WRITE: '✏️ Запись раскладки',
    MATRIX_EDIT: '✏️ Правка раскладки',
    AUTO_SYNC: '⟳ Авто-синк с расчётом',
    ACCEPTANCE_SYNC: '🔄 Синхронизация с приёмкой WB',
    CAPACITY_TRIM: '⚖ Срез по ёмкости ФФ',
    COMMIT_REQUEST: '🚚 Создание заявки',
};
const eventLabel = (t: string): string => EVENT_LABELS[t] ?? `🕘 ${t}`;

/** Цветной акцент слева карточки события по типу. */
const EVENT_ACCENT: Record<string, string> = {
    PREBOOK_TOPUP: 'var(--color-accent)',
    MATRIX_WRITE: 'var(--color-warning)',
    COMMIT_REQUEST: 'var(--color-success)',
};

/**
 * Вкладка «🕘 История»: журнал изменений черновика (дозабор из предброни, запись
 * ручной раскладки, создание заявок) + откат отдельного события. Новейшие первыми.
 */
export default function DraftHistoryView({ draftId, onReverted }: Props) {
    const [events, setEvents] = useState<DraftEvent[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [busyId, setBusyId] = useState<number | null>(null);
    const [reloadTick, setReloadTick] = useState(0);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    const showToast = useCallback((message: string, type: 'success' | 'error') => setToast({ message, type }), []);
    /** Перезагрузить историю — бампает tick, эффект ниже перезапрашивает (AbortController). */
    const reload = useCallback(() => setReloadTick(t => t + 1), []);

    // ─── Загрузка истории (AbortController — StrictMode монтирует эффект дважды) ──
    useEffect(() => {
        const controller = new AbortController();
        (async () => {
            setLoading(true);
            setError(null);
            try {
                const res = await api.getDraftHistory(draftId);
                if (controller.signal.aborted) return;
                setEvents(res.events ?? []);
            } catch (e) {
                if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка загрузки истории');
            } finally {
                if (!controller.signal.aborted) setLoading(false);
            }
        })();
        return () => controller.abort();
    }, [draftId, reloadTick]);

    const handleRevert = useCallback(async (ev: DraftEvent) => {
        if (busyId !== null || !ev.can_revert) return;
        setBusyId(ev.id);
        try {
            const res = await api.revertDraftEvent(draftId, ev.id);
            const del = res.deleted_request_ids?.length ?? 0;
            const parts: string[] = [];
            if (res.restored_draft) parts.push('черновик восстановлен');
            if (del > 0) parts.push(`удалено ${formatNumber(del, 0)} ${pluralRu(del, ['заявка', 'заявки', 'заявок'])}`);
            showToast(`Откат применён${parts.length ? ` — ${parts.join(', ')}` : ''}`, 'success');
            reload();
            onReverted?.();
        } catch (e) {
            showToast(e instanceof Error ? e.message : 'Не удалось откатить событие', 'error');
        } finally {
            setBusyId(null);
        }
    }, [busyId, draftId, reload, onReverted, showToast]);

    // ─── States ──────────────────────────────────────────────────────────────
    // loading/error-плейсхолдеры — только при пустом списке, чтобы refresh после
    // отката не бланкал уже показанную историю.
    if (loading && events.length === 0) {
        return <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-muted)' }}>Загрузка истории…</div>;
    }
    if (error && events.length === 0) {
        return (
            <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-danger)' }}>
                <div style={{ marginBottom: 12 }}>{error}</div>
                <button className="btn btn-secondary btn-sm" onClick={reload}>↻ Повторить</button>
            </div>
        );
    }

    return (
        <div className="glass-card" style={{ padding: 20 }}>
            {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
                <div>
                    <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--color-text)' }}>🕘 История изменений черновика</div>
                    <div style={{ fontSize: 12, color: 'var(--color-muted)', marginTop: 2 }}>
                        Дозабор из предброни, запись ручной раскладки и создание заявок. Откат возвращает черновик к снимку до события.
                    </div>
                </div>
                <button className="btn btn-secondary btn-sm" onClick={reload} disabled={loading || busyId !== null}>↻ Обновить</button>
            </div>

            {events.length === 0 ? (
                <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-muted)', fontSize: 14 }}>
                    Событий пока нет — история появится после дозабора из предброни, записи ручной раскладки или создания заявок.
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {events.map(ev => {
                        const reverted = !!ev.reverted_at;
                        const requestCount = ev.created_request_ids?.length ?? 0;
                        const isCommit = ev.event_type === 'COMMIT_REQUEST';
                        const reverting = busyId === ev.id;
                        return (
                            <div
                                key={ev.id}
                                style={{
                                    display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12,
                                    padding: '12px 14px', borderRadius: 12,
                                    background: 'var(--color-bg-card)',
                                    border: '1px solid var(--color-border)',
                                    borderLeft: `3px solid ${reverted ? 'var(--color-border)' : (EVENT_ACCENT[ev.event_type] ?? 'var(--color-accent)')}`,
                                    opacity: reverted ? 0.6 : 1,
                                }}
                            >
                                <div style={{ minWidth: 0 }}>
                                    <div style={{ fontWeight: 600, color: 'var(--color-text)', fontSize: 14 }}>
                                        {eventLabel(ev.event_type)}
                                        {isCommit && requestCount > 0 && (
                                            <span className="badge badge-secondary" style={{ marginLeft: 8, fontSize: 11, padding: '1px 8px' }}>
                                                {formatNumber(requestCount, 0)} {pluralRu(requestCount, ['заявка', 'заявки', 'заявок'])}
                                            </span>
                                        )}
                                    </div>
                                    {ev.summary && (
                                        <div style={{ fontSize: 13, color: 'var(--color-text-dim)', marginTop: 3 }}>{ev.summary}</div>
                                    )}
                                    <div style={{ fontSize: 12, color: 'var(--color-muted)', marginTop: 4 }}>
                                        {formatDateTime(ev.created_at)}
                                        {ev.created_by ? ` · ${ev.created_by}` : ''}
                                    </div>
                                    {reverted && (
                                        <div style={{ fontSize: 12, color: 'var(--color-muted)', marginTop: 2 }}>
                                            ↩︎ Откачено {formatDateTime(ev.reverted_at)}{ev.reverted_by ? ` · ${ev.reverted_by}` : ''}
                                        </div>
                                    )}
                                </div>

                                <div style={{ flexShrink: 0 }}>
                                    {reverted ? (
                                        <span className="badge badge-secondary" style={{ fontSize: 11, padding: '3px 10px' }}>Откачено</span>
                                    ) : (
                                        <button
                                            className="btn btn-secondary btn-sm"
                                            onClick={() => handleRevert(ev)}
                                            disabled={!ev.can_revert || busyId !== null}
                                            title={ev.can_revert ? 'Откатить это событие' : (ev.revert_blocked_reason ?? 'Откат недоступен')}
                                        >
                                            {reverting ? 'Откат…' : '↩︎ Откатить'}
                                        </button>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

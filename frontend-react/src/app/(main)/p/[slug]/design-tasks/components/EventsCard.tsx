'use client';

import { formatDateTime } from '@/lib/utils';
import { DESIGN_STATUS_LABEL } from '@/lib/design';
import type { DesignTaskDetail } from '@/types/api';

/** Журнал переходов статуса (append-only, инвариант §6.3). */
export default function EventsCard({ task }: { task: DesignTaskDetail }) {
    const events = [...task.events].sort((a, b) => b.changed_at.localeCompare(a.changed_at));

    return (
        <div className="glass-card">
            <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 12px' }}>Журнал</h3>
            {events.length === 0 && (
                <div style={{ fontSize: 13, color: 'var(--color-muted)' }}>Переходов ещё не было.</div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {events.map((e) => (
                    <div key={e.id} style={{ fontSize: 13, borderBottom: '1px solid var(--color-border)', paddingBottom: 6 }}>
                        <span style={{ color: 'var(--color-muted)' }}>{formatDateTime(e.changed_at)}</span>
                        {' · '}
                        <span>
                            {e.old_status ? `${DESIGN_STATUS_LABEL[e.old_status]} → ` : ''}
                            <b>{DESIGN_STATUS_LABEL[e.new_status]}</b>
                        </span>
                        {e.changed_by && <span style={{ color: 'var(--color-muted)' }}> · {e.changed_by}</span>}
                        {e.comment && (
                            <div style={{ color: 'var(--color-muted)', whiteSpace: 'pre-wrap', marginTop: 2 }}>{e.comment}</div>
                        )}
                    </div>
                ))}
            </div>
        </div>
    );
}

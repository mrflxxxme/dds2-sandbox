'use client';

import { useState } from 'react';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/utils';
import type { DesignTaskDetail } from '@/types/api';

interface CommentsCardProps {
    task: DesignTaskDetail;
    onChanged: () => void;
    onError: (msg: string) => void;
}

/** Тред комментариев; ввод — только can_comment (viewer read-only). */
export default function CommentsCard({ task, onChanged, onError }: CommentsCardProps) {
    const [body, setBody] = useState('');
    const [sending, setSending] = useState(false);

    const send = async () => {
        const text = body.trim();
        if (!text) return;
        setSending(true);
        try {
            await api.addDesignComment(task.id, { body: text });
            setBody('');
            onChanged();
        } catch (e) {
            onError(e instanceof Error ? e.message : 'Не удалось отправить комментарий');
        } finally {
            setSending(false);
        }
    };

    return (
        <div className="glass-card">
            <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 12px' }}>Комментарии</h3>
            {task.comments.length === 0 && (
                <div style={{ fontSize: 13, color: 'var(--color-muted)', marginBottom: 8 }}>Комментариев нет.</div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: task.permissions.can_comment ? 12 : 0 }}>
                {task.comments.map((c) => (
                    <div key={c.id} style={{ border: '1px solid var(--color-border)', borderRadius: 8, padding: '8px 10px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-muted)', marginBottom: 4 }}>
                            {c.author_name ?? `#${c.author_user_id}`} · {formatDateTime(c.created_at)}
                        </div>
                        <div style={{ fontSize: 13, whiteSpace: 'pre-wrap' }}>{c.body}</div>
                    </div>
                ))}
            </div>
            {task.permissions.can_comment && (
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
                    <textarea
                        className="form-input"
                        style={{ flex: 1, minHeight: 60, resize: 'vertical' }}
                        placeholder="Написать комментарий…"
                        value={body}
                        maxLength={2000}
                        onChange={(e) => setBody(e.target.value)}
                    />
                    <button className="btn btn-primary btn-sm" disabled={sending || !body.trim()} onClick={() => void send()}>
                        {sending ? '…' : 'Отправить'}
                    </button>
                </div>
            )}
        </div>
    );
}

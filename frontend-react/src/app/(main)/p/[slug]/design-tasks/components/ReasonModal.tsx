'use client';

import { useState } from 'react';
import ModalShell from './ModalShell';

interface ReasonModalProps {
    title: string;
    label: string;
    /** true — пустую причину не пропускаем (модалка «Вернуть», dnd в «Правки»). */
    required?: boolean;
    submitLabel?: string;
    placeholder?: string;
    onSubmit: (reason: string) => void | Promise<void>;
    onClose: () => void;
}

/** Модалка с одним textarea-полем причины/комментария. */
export default function ReasonModal({
    title, label, required = false, submitLabel = 'Отправить', placeholder, onSubmit, onClose,
}: ReasonModalProps) {
    const [reason, setReason] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState('');

    const handleSubmit = async () => {
        const trimmed = reason.trim();
        if (required && !trimmed) {
            setError('Причина обязательна');
            return;
        }
        setError('');
        setSubmitting(true);
        try {
            await onSubmit(trimmed);
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <ModalShell title={title} onClose={onClose}>
            <label style={{ display: 'block', fontSize: 13, color: 'var(--color-muted)', marginBottom: 6 }}>
                {label}{required ? ' *' : ''}
            </label>
            <textarea
                className="form-input"
                style={{ width: '100%', minHeight: 90, resize: 'vertical' }}
                value={reason}
                maxLength={2000}
                placeholder={placeholder}
                autoFocus
                onChange={(e) => setReason(e.target.value)}
            />
            {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 6 }}>{error}</div>}
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                <button className="btn btn-secondary" onClick={onClose} disabled={submitting}>Отмена</button>
                <button
                    className="btn btn-primary"
                    onClick={() => void handleSubmit()}
                    disabled={submitting || (required && !reason.trim())}
                >
                    {submitting ? 'Отправка…' : submitLabel}
                </button>
            </div>
        </ModalShell>
    );
}

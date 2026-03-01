'use client';

import { useState, useEffect } from 'react';

interface ModalField {
    key: string;
    label: string;
    type?: 'text' | 'number' | 'date' | 'select' | 'textarea';
    options?: string[];
    required?: boolean;
    placeholder?: string;
}

interface FormModalProps {
    open: boolean;
    onClose: () => void;
    onSubmit: (data: Record<string, any>) => void | Promise<void>;
    title: string;
    fields: ModalField[];
    defaults?: Record<string, any>;
    submitLabel?: string;
    columns?: number;
}

/**
 * FormModal — universal form modal / inline form.
 *
 * Usage:
 * ```tsx
 * <FormModal
 *   open={showForm}
 *   onClose={() => setShowForm(false)}
 *   onSubmit={handleSave}
 *   title="Создать заказ"
 *   fields={[
 *     { key: 'order_no', label: 'Номер заказа', required: true },
 *     { key: 'transport', label: 'Транспорт', type: 'select', options: ['AUTO', 'RAIL', 'SEA'] },
 *     { key: 'ship_date', label: 'Дата отправки', type: 'date' },
 *   ]}
 * />
 * ```
 */
export default function FormModal({
    open,
    onClose,
    onSubmit,
    title,
    fields,
    defaults = {},
    submitLabel = '💾 Сохранить',
    columns = 2,
}: FormModalProps) {
    const [form, setForm] = useState<Record<string, any>>({});
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        if (open) {
            const initial: Record<string, any> = {};
            fields.forEach(f => {
                initial[f.key] = defaults[f.key] ?? (f.type === 'number' ? 0 : '');
            });
            setForm(initial);
            setError('');
        }
    }, [open]);

    const handleSubmit = async () => {
        setSubmitting(true);
        setError('');
        try {
            await onSubmit(form);
            onClose();
        } catch (e: any) {
            setError(e?.message || 'Ошибка');
        }
        setSubmitting(false);
    };

    if (!open) return null;

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card" style={{ width: Math.min(columns * 220 + 80, 900), maxWidth: '90vw' }}
                onClick={e => e.stopPropagation()}>
                <div className="modal-title">{title}</div>
                {error && <div className="auth-error" style={{ marginBottom: 12 }}>{error}</div>}
                <div style={{
                    display: 'grid',
                    gridTemplateColumns: `repeat(${columns}, 1fr)`,
                    gap: 12,
                    marginBottom: 20,
                }}>
                    {fields.map(f => (
                        <div key={f.key} className="form-group">
                            <label className="form-label">
                                {f.label}{f.required && <span style={{ color: 'var(--color-danger)' }}>*</span>}
                            </label>
                            {f.type === 'select' ? (
                                <select
                                    className="form-input"
                                    value={form[f.key] ?? ''}
                                    onChange={e => setForm({ ...form, [f.key]: e.target.value })}
                                >
                                    {f.options?.map(o => <option key={o} value={o}>{o}</option>)}
                                </select>
                            ) : f.type === 'textarea' ? (
                                <textarea
                                    className="form-input"
                                    value={form[f.key] ?? ''}
                                    onChange={e => setForm({ ...form, [f.key]: e.target.value })}
                                    rows={3}
                                    placeholder={f.placeholder}
                                />
                            ) : (
                                <input
                                    className="form-input"
                                    type={f.type || 'text'}
                                    value={form[f.key] ?? ''}
                                    onChange={e => setForm({
                                        ...form,
                                        [f.key]: f.type === 'number' ? parseFloat(e.target.value) || 0 : e.target.value,
                                    })}
                                    placeholder={f.placeholder}
                                />
                            )}
                        </div>
                    ))}
                </div>
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button className="btn btn-secondary btn-sm" onClick={onClose} disabled={submitting}>
                        Отмена
                    </button>
                    <button className="btn btn-primary btn-sm" onClick={handleSubmit} disabled={submitting}>
                        {submitting ? 'Сохранение...' : submitLabel}
                    </button>
                </div>
            </div>
        </div>
    );
}

'use client';
import React, { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { PaymentCategory } from '@/types/api';

interface Props {
    /** Управлять (добавлять/править/удалять) может только admin/owner. */
    canManage: boolean;
    onClose: () => void;
    /** Вызывается после любой мутации — родитель освежает списки/карты лейблов. */
    onChanged: () => void;
}

/**
 * Управление справочником «Назначение оплаты»: добавить / переименовать / порядок / удалить.
 * Системные категории (7 дефолтов) можно переименовать, но не удалить. Удаление категории,
 * на которой висят заявки, бэкенд блокирует (показываем сообщение).
 */
export default function PaymentCategoriesModal({ canManage, onClose, onChanged }: Props) {
    const [items, setItems] = useState<PaymentCategory[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);

    const [newLabel, setNewLabel] = useState('');
    const [editingId, setEditingId] = useState<number | null>(null);
    const [editLabel, setEditLabel] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setItems(await api.listPaymentCategories());
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, []);

    useEffect(() => { load(); }, [load]);

    const run = async (fn: () => Promise<unknown>) => {
        setBusy(true);
        setError('');
        try {
            await fn();
            await load();
            onChanged();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setBusy(false);
    };

    const handleAdd = () => {
        const label = newLabel.trim();
        if (!label) return;
        run(async () => { await api.createPaymentCategory(label); setNewLabel(''); });
    };

    const startEdit = (c: PaymentCategory) => { setEditingId(c.id); setEditLabel(c.label); };
    const saveEdit = (c: PaymentCategory) => {
        const label = editLabel.trim();
        setEditingId(null);
        if (!label || label === c.label) return;
        run(() => api.updatePaymentCategory(c.id, { label }));
    };

    const handleDelete = (c: PaymentCategory) => {
        if (!confirm(`Удалить категорию «${c.label}»?`)) return;
        run(() => api.deletePaymentCategory(c.id));
    };

    // Перестановка: меняем sort_order местами с соседом (в текущем порядке списка).
    const move = (idx: number, dir: -1 | 1) => {
        const a = items[idx], b = items[idx + dir];
        if (!a || !b) return;
        run(async () => {
            await api.updatePaymentCategory(a.id, { sort_order: b.sort_order });
            await api.updatePaymentCategory(b.id, { sort_order: a.sort_order });
        });
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div
                className="modal-card"
                onClick={e => e.stopPropagation()}
                style={{ maxWidth: 540, width: '95vw', maxHeight: '90vh', overflow: 'auto', background: '#fff' }}
            >
                <h2 className="modal-title">Категории оплат — «Назначение»</h2>
                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: -8, marginBottom: 16 }}>
                    Общий список для всех брендов. Системные можно переименовать, удалить — нельзя.
                </p>

                {error && (
                    <div style={{ padding: '10px 14px', marginBottom: 12, borderRadius: 8, background: 'rgba(239,68,68,0.1)', color: 'var(--color-danger)', fontSize: 13 }}>
                        {error}
                    </div>
                )}

                {loading ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
                ) : items.length === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>Категорий пока нет</div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 16 }}>
                        {items.map((c, idx) => (
                            <div
                                key={c.id}
                                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', background: 'var(--color-bg)', borderRadius: 8 }}
                            >
                                {canManage && (
                                    <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1 }}>
                                        <button title="Выше" disabled={busy || idx === 0} onClick={() => move(idx, -1)}
                                            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-muted)', fontSize: 11, padding: 0 }}>▲</button>
                                        <button title="Ниже" disabled={busy || idx === items.length - 1} onClick={() => move(idx, 1)}
                                            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-muted)', fontSize: 11, padding: 0 }}>▼</button>
                                    </div>
                                )}
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    {editingId === c.id ? (
                                        <input
                                            className="form-input"
                                            autoFocus
                                            value={editLabel}
                                            onChange={e => setEditLabel(e.target.value)}
                                            onBlur={() => saveEdit(c)}
                                            onKeyDown={e => { if (e.key === 'Enter') saveEdit(c); if (e.key === 'Escape') setEditingId(null); }}
                                            style={{ padding: '4px 8px', fontSize: 14 }}
                                        />
                                    ) : (
                                        <span
                                            onClick={() => canManage && startEdit(c)}
                                            style={{ fontSize: 14, cursor: canManage ? 'pointer' : 'default' }}
                                            title={canManage ? 'Нажмите, чтобы переименовать' : undefined}
                                        >
                                            {c.label}
                                        </span>
                                    )}
                                </div>
                                {c.is_system && (
                                    <span className="badge badge-secondary" style={{ fontSize: 10 }}>системная</span>
                                )}
                                {canManage && !c.is_system && (
                                    <button
                                        title="Удалить"
                                        disabled={busy}
                                        onClick={() => handleDelete(c)}
                                        style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-danger)', fontSize: 14 }}
                                    >
                                        ✕
                                    </button>
                                )}
                            </div>
                        ))}
                    </div>
                )}

                {canManage && (
                    <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                        <input
                            className="form-input"
                            placeholder="Новая категория (напр. Маркетинг)"
                            value={newLabel}
                            onChange={e => setNewLabel(e.target.value)}
                            onKeyDown={e => { if (e.key === 'Enter') handleAdd(); }}
                            disabled={busy}
                            style={{ flex: 1 }}
                        />
                        <button className="btn btn-primary" onClick={handleAdd} disabled={busy || !newLabel.trim()}>
                            Добавить
                        </button>
                    </div>
                )}

                <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                    <button className="btn btn-secondary" onClick={onClose}>Закрыть</button>
                </div>
            </div>
        </div>
    );
}

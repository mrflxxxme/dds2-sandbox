'use client';
import React, { useState } from 'react';
import type { AdsAutopaySetting } from '@/types/api';
import { IcX } from './icons';

const MIN_BUDGET = 1000;

/** Модалка настройки автопополнения БЕЗ привязки к кампании — собирает настройку в стейт.
 *  На форме создания кампании применяется к новой кампании после её создания
 *  (у обычной кампании автопополнение настраивает AutopayModal по campaign_id). */
export default function AutopaySettingsModal({ initial, onClose, onSave }: {
    initial: AdsAutopaySetting;
    onClose: () => void;
    onSave: (s: AdsAutopaySetting) => void;
}) {
    const [form, setForm] = useState<AdsAutopaySetting>({ ...initial, enabled: true });

    const inputStyle: React.CSSProperties = { fontSize: 14, padding: '9px 12px', borderRadius: 10, border: '1px solid #d1d5db', width: 200 };
    const block: React.CSSProperties = { background: '#f9fafb', borderRadius: 14, padding: 18, marginBottom: 14 };

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(17,24,39,.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }} onClick={onClose}>
            <div className="glass-card static" onClick={e => e.stopPropagation()} style={{ width: 560, maxWidth: '100%', maxHeight: '90vh', overflow: 'auto', padding: 24, background: '#fff' }}>
                <div style={{ display: 'flex', alignItems: 'center', marginBottom: 18 }}>
                    <h2 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>Автопополнение бюджета кампании</h2>
                    <button onClick={onClose} style={{ marginLeft: 'auto', border: 'none', background: 'none', cursor: 'pointer', color: '#6b7280' }}><IcX size={18} /></button>
                </div>

                <div style={block}>
                    <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Условия пополнения</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 12, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 13, color: '#6b7280', minWidth: 150 }}>Пополнять на, ₽</span>
                        <input type="number" min={MIN_BUDGET} step={100} value={form.amount}
                            onChange={e => setForm(f => ({ ...f, amount: Math.max(0, Number(e.target.value) || 0) }))} style={inputStyle} />
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 12, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 13, color: '#6b7280', minWidth: 150 }}>В час (МСК)</span>
                        <input type="number" min={0} max={23} value={form.hour}
                            onChange={e => setForm(f => ({ ...f, hour: Math.min(23, Math.max(0, Number(e.target.value) || 0)) }))} style={{ ...inputStyle, width: 90 }} />
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 13, color: '#6b7280', minWidth: 150 }}>Порог открута, %</span>
                        <input type="number" min={0} max={100} value={form.threshold_pct}
                            onChange={e => setForm(f => ({ ...f, threshold_pct: Math.min(100, Math.max(0, Number(e.target.value) || 0)) }))} style={{ ...inputStyle, width: 90 }} />
                        <span style={{ fontSize: 11, color: '#9ca3af' }}>пополнять, только если за сутки открутилось ≥ порога</span>
                    </div>
                    <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 10 }}>Минимальная сумма пополнения — {MIN_BUDGET} ₽</div>
                </div>

                <div style={{ fontSize: 12, color: '#9ca3af', marginBottom: 16 }}>
                    Автопополнение применится к кампании сразу после её создания.
                </div>

                <div style={{ display: 'flex', gap: 8 }}>
                    <button onClick={() => onSave(form)} disabled={form.amount < MIN_BUDGET} className="btn btn-primary" style={{ fontSize: 14 }}>Сохранить</button>
                    <button onClick={onClose} className="btn btn-secondary" style={{ fontSize: 14 }}>Отмена</button>
                </div>
            </div>
        </div>
    );
}

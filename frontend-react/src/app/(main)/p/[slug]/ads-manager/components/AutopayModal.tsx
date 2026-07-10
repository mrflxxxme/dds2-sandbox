import React, { useState } from 'react';
import type { AdsManagerCampaign, AdsAutopaySetting } from '@/types/api';
import { IcClock } from './icons';
import { DEFAULT_AUTOPAY } from './adsShared';

/** Модалка настройки автопополнения одной кампании. */
export default function AutopayModal({ campaign, initial, onClose, onSave }: {
    campaign: AdsManagerCampaign;
    initial: AdsAutopaySetting | undefined;
    onClose: () => void;
    onSave: (s: AdsAutopaySetting) => Promise<void>;
}) {
    const [form, setForm] = useState<AdsAutopaySetting>(initial ?? DEFAULT_AUTOPAY);
    const [saving, setSaving] = useState(false);

    const handleSave = async () => {
        setSaving(true);
        try { await onSave(form); onClose(); }
        catch { setSaving(false); }
    };

    const inputStyle: React.CSSProperties = { background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '8px 10px', fontSize: 14, color: 'var(--color-text)', width: '100%' };

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
            <div className="glass-card" style={{ width: 440, padding: 24, background: '#fff' }} onClick={e => e.stopPropagation()}>
                <h3 style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 16, fontWeight: 600, margin: '0 0 4px' }}><IcClock size={18} />Автопополнение бюджета</h3>
                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 16 }}>{campaign.name || `#${campaign.campaign_id}`} · #{campaign.campaign_id}</div>

                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, marginBottom: 14, cursor: 'pointer' }}>
                    <input type="checkbox" checked={form.enabled} onChange={e => setForm(f => ({ ...f, enabled: e.target.checked }))} />
                    Пополнять автоматически
                </label>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
                    <label style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                        Дневной бюджет X, ₽
                        <input type="number" min={0} step={50} value={form.amount}
                            onChange={e => setForm(f => ({ ...f, amount: Math.max(0, Number(e.target.value) || 0) }))} style={inputStyle} />
                    </label>
                    <label style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                        Час пополнения (МСК)
                        <select value={form.hour} onChange={e => setForm(f => ({ ...f, hour: Number(e.target.value) }))} style={inputStyle}>
                            {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
                        </select>
                    </label>
                </div>
                <label style={{ fontSize: 12, color: 'var(--color-text-dim)', display: 'block', marginBottom: 16 }}>
                    Порог открута за сутки, %
                    <input type="number" min={0} max={100} value={form.threshold_pct}
                        onChange={e => setForm(f => ({ ...f, threshold_pct: Math.min(100, Math.max(0, Number(e.target.value) || 0)) }))} style={inputStyle} />
                    <span style={{ fontSize: 11 }}>Если за сутки открутилось меньше {form.threshold_pct}% от {form.amount || 'X'} ₽ — пополнения не будет. Иначе бюджет доливается до {form.amount || 'X'} ₽.</span>
                </label>

                <div style={{ fontSize: 11, color: 'var(--color-warning, #f59e0b)', marginBottom: 16 }}>
                    ⚠️ Автопополнение активно: в заданный час бюджет доливается до X реальными деньгами
                    (минимум 1000₽ за пополнение — ограничение WB). Все операции — в журнале (значок истории в таблице).
                </div>

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={saving}>Отмена</button>
                    <button className="btn btn-primary" onClick={handleSave} disabled={saving}>{saving ? 'Сохранение…' : 'Сохранить'}</button>
                </div>
            </div>
        </div>
    );
}

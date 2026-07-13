import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/utils';
import type { AdsManagerCampaign, AdsAutopaySetting, AdsAutopayMode, AdsAutopayLogEntry } from '@/types/api';
import { IcClock } from './icons';
import Switch from './Switch';
import { DEFAULT_AUTOPAY, fmt, AUTOPAY_STATUS_BADGE } from './adsShared';

// Два режима автопополнения (см. AdsAutopayMode)
const MODES: { key: AdsAutopayMode; label: string }[] = [
    { key: 'low_balance', label: 'Как на ВБ' },
    { key: 'to_target', label: 'До целевого' },
];

// Стили в духе секции «Управление рекламой» — крупнее и контрастнее
const CARD: React.CSSProperties = { border: '1px solid #e5e7eb', borderRadius: 12, background: '#f9fafb', padding: '18px 20px' };
const CARD_TITLE: React.CSSProperties = { fontSize: 13, fontWeight: 700, color: '#4b5563', textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 16 };
const FIELD_LABEL: React.CSSProperties = { fontSize: 14, color: '#374151', display: 'block', fontWeight: 500 };
const INPUT: React.CSSProperties = { marginTop: 6, background: '#fff', border: '1px solid #d1d5db', borderRadius: 8, padding: '10px 12px', fontSize: 16, color: '#111827', width: '100%', boxSizing: 'border-box' };
const HINT: React.CSSProperties = { fontSize: 13, color: '#6b7280', marginTop: 12, lineHeight: 1.45 };

/** Модалка настройки автопополнения одной кампании. Два режима: «как на ВБ» (по остатку) и «до целевого» (по часу). */
export default function AutopayModal({ campaign, initial, onClose, onSave }: {
    campaign: AdsManagerCampaign;
    initial: AdsAutopaySetting | undefined;
    onClose: () => void;
    onSave: (s: AdsAutopaySetting) => Promise<void>;
}) {
    const [form, setForm] = useState<AdsAutopaySetting>(initial ?? DEFAULT_AUTOPAY);
    const [saving, setSaving] = useState(false);
    // «Не чаще N раз в день»: чекбокс хранит daily_cap=0 (снят) / N (установлен); N помним отдельно
    const [capN, setCapN] = useState<number>(Math.max(1, form.daily_cap || 1));

    // История автопополнений этой кампании — прямо в окне (как в кабинете ВБ)
    const [log, setLog] = useState<AdsAutopayLogEntry[] | null>(null);
    useEffect(() => {
        let alive = true;
        api.getAutopayLog(campaign.campaign_id)
            .then(rows => { if (alive) setLog(rows); })
            .catch(() => { if (alive) setLog([]); });
        return () => { alive = false; };
    }, [campaign.campaign_id]);

    const handleSave = async () => {
        setSaving(true);
        try { await onSave(form); onClose(); }
        catch { setSaving(false); }
    };

    const capOn = (form.daily_cap || 0) > 0;

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }} onClick={onClose}>
            <div className="glass-card" style={{ width: 700, maxWidth: '96vw', maxHeight: '92vh', display: 'flex', flexDirection: 'column', padding: 0, background: '#fff', overflow: 'hidden' }} onClick={e => e.stopPropagation()}>
                {/* Шапка */}
                <div style={{ padding: '22px 28px 16px', borderBottom: '1px solid #eef0f2' }}>
                    <h3 style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 21, fontWeight: 700, margin: '0 0 4px', color: '#111827' }}><IcClock size={20} />Автопополнение бюджета</h3>
                    <div style={{ fontSize: 14, color: '#6b7280' }}>{campaign.name || `#${campaign.campaign_id}`} · #{campaign.campaign_id}</div>
                </div>

                {/* Тело (скроллится) */}
                <div style={{ padding: 24, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 18 }}>
                    {/* Вкл/выкл + режим */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                        <Switch on={form.enabled} ariaLabel="Пополнять автоматически" onClick={() => setForm(f => ({ ...f, enabled: !f.enabled }))} />
                        <span style={{ fontSize: 16, fontWeight: 500, color: form.enabled ? '#111827' : '#6b7280' }}>Пополнять автоматически</span>
                        <span style={{ display: 'inline-flex', borderRadius: 10, overflow: 'hidden', border: '1px solid #d1d5db', marginLeft: 'auto' }}>
                            {MODES.map(m => (
                                <button key={m.key} onClick={() => setForm(f => ({ ...f, mode: m.key }))}
                                    style={{ padding: '8px 20px', fontSize: 15, fontWeight: 500, border: 'none', cursor: 'pointer', background: form.mode === m.key ? '#3b82f6' : '#fff', color: form.mode === m.key ? '#fff' : '#374151' }}>
                                    {m.label}
                                </button>
                            ))}
                        </span>
                    </div>

                    {/* Условия пополнения — фикс. высота, чтобы окно не прыгало при смене режима */}
                    <div style={CARD}>
                        <div style={CARD_TITLE}>Условия пополнения</div>
                        <div style={{ minHeight: 168 }}>
                            {form.mode === 'low_balance' ? (
                                <>
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                                        <label style={FIELD_LABEL}>
                                            Если бюджет меньше, ₽
                                            <input type="number" min={0} step={50} value={form.low_balance_threshold}
                                                onChange={e => setForm(f => ({ ...f, low_balance_threshold: Math.max(0, Number(e.target.value) || 0) }))} style={INPUT} />
                                        </label>
                                        <label style={FIELD_LABEL}>
                                            Пополнять на, ₽
                                            <input type="number" min={1000} step={50} value={form.topup_amount}
                                                onChange={e => setForm(f => ({ ...f, topup_amount: Math.max(0, Number(e.target.value) || 0) }))} style={INPUT} />
                                        </label>
                                    </div>
                                    <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 15, color: '#374151', marginTop: 16, cursor: 'pointer' }}>
                                        <input type="checkbox" checked={capOn} style={{ width: 17, height: 17 }}
                                            onChange={e => setForm(f => ({ ...f, daily_cap: e.target.checked ? capN : 0 }))} />
                                        не чаще
                                        <input type="number" min={1} step={1} value={capN} disabled={!capOn}
                                            onChange={e => { const n = Math.max(1, Number(e.target.value) || 1); setCapN(n); setForm(f => ({ ...f, daily_cap: n })); }}
                                            style={{ ...INPUT, marginTop: 0, width: 64, padding: '6px 10px', opacity: capOn ? 1 : 0.5 }} />
                                        раз в день
                                    </label>
                                    <div style={HINT}>Минимальный бюджет — 1000 ₽ (ограничение WB), округляется вверх до 50 ₽.</div>
                                </>
                            ) : (
                                <>
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                                        <label style={FIELD_LABEL}>
                                            Дневной бюджет X, ₽
                                            <input type="number" min={0} step={50} value={form.amount}
                                                onChange={e => setForm(f => ({ ...f, amount: Math.max(0, Number(e.target.value) || 0) }))} style={INPUT} />
                                        </label>
                                        <label style={FIELD_LABEL}>
                                            Час пополнения (МСК)
                                            <select value={form.hour} onChange={e => setForm(f => ({ ...f, hour: Number(e.target.value) }))} style={INPUT}>
                                                {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
                                            </select>
                                        </label>
                                    </div>
                                    <label style={{ ...FIELD_LABEL, display: 'block', marginTop: 16 }}>
                                        Порог открута за сутки, %
                                        <input type="number" min={0} max={100} value={form.threshold_pct}
                                            onChange={e => setForm(f => ({ ...f, threshold_pct: Math.min(100, Math.max(0, Number(e.target.value) || 0)) }))} style={INPUT} />
                                    </label>
                                    <div style={HINT}>
                                        Если за сутки открутилось меньше {form.threshold_pct}% от {form.amount || 'X'} ₽ — пополнения не будет. Иначе бюджет доливается до {form.amount || 'X'} ₽.
                                    </div>
                                </>
                            )}
                        </div>
                    </div>

                    {/* История автопополнений — прямо в окне */}
                    <div style={CARD}>
                        <div style={CARD_TITLE}>История автопополнений</div>
                        {log == null ? (
                            <div style={{ fontSize: 14, color: '#6b7280', padding: '4px 0' }}>Загрузка…</div>
                        ) : log.length === 0 ? (
                            <div style={{ fontSize: 14, color: '#6b7280', padding: '4px 0' }}>Пополнений ещё не было.</div>
                        ) : (
                            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                                <thead>
                                    <tr style={{ color: '#6b7280', textAlign: 'left' }}>
                                        <th style={{ fontWeight: 500, padding: '0 0 8px' }}>Дата</th>
                                        <th style={{ fontWeight: 500, padding: '0 0 8px' }}>Источник</th>
                                        <th style={{ fontWeight: 500, padding: '0 0 8px', textAlign: 'right' }}>Сумма</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {log.slice(0, 6).map((e, i) => {
                                        const badge = AUTOPAY_STATUS_BADGE[e.status];
                                        const ok = e.status === 'ok';
                                        return (
                                            <tr key={i} style={{ borderTop: '1px solid #eef0f2' }}>
                                                <td style={{ padding: '8px 0', color: '#374151', whiteSpace: 'nowrap' }}>{formatDateTime(e.ts)}</td>
                                                <td style={{ padding: '8px 0', color: '#6b7280' }}>{e.source}</td>
                                                <td style={{ padding: '8px 0', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                    <span title={badge.label + (e.reason ? ` · ${e.reason}` : '')} style={{ fontWeight: 600, color: ok ? '#10b981' : '#ef4444' }}>
                                                        {ok ? `+ ${fmt(e.amount)} ₽` : badge.label}
                                                    </span>
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        )}
                    </div>
                </div>

                {/* Футер */}
                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', padding: '16px 28px', borderTop: '1px solid #eef0f2' }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={saving}>Отмена</button>
                    <button className="btn btn-primary" onClick={handleSave} disabled={saving}>{saving ? 'Сохранение…' : 'Сохранить'}</button>
                </div>
            </div>
        </div>
    );
}

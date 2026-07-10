import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { AdsManagerCampaign, AdsAutopayLogEntry } from '@/types/api';
import { IcHistory } from './icons';
import { AUTOPAY_STATUS_BADGE, fmt, thLeft, thStyle, tdLeft, tdStyle } from './adsShared';

/** Модалка истории автопополнений: по кампании или по всем сразу. */
export default function AutopayLogModal({ campaign, onClose }: {
    campaign: AdsManagerCampaign;
    onClose: () => void;
}) {
    const [entries, setEntries] = useState<AdsAutopayLogEntry[] | null>(null);
    const [error, setError] = useState('');
    const [allCampaigns, setAllCampaigns] = useState(false);

    useEffect(() => {
        const controller = new AbortController();
        setEntries(null);
        setError('');
        api.getAutopayLog(allCampaigns ? undefined : campaign.campaign_id)
            .then(data => { if (!controller.signal.aborted) setEntries(data); })
            .catch(() => { if (!controller.signal.aborted) setError('Не удалось загрузить журнал'); });
        return () => controller.abort();
    }, [campaign.campaign_id, allCampaigns]);

    const fmtTs = (ts: string) => {
        try {
            return new Date(ts).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' });
        } catch { return ts; }
    };

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
            <div className="glass-card" style={{ width: 640, maxHeight: '80vh', overflow: 'auto', padding: 24, background: '#fff' }} onClick={e => e.stopPropagation()}>
                <h3 style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 16, fontWeight: 600, margin: '0 0 4px' }}><IcHistory size={18} />История пополнений</h3>
                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 12 }}>
                    {allCampaigns ? 'Все кампании' : <>{campaign.name || `#${campaign.campaign_id}`} · #{campaign.campaign_id}</>}
                </div>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, marginBottom: 12, cursor: 'pointer' }}>
                    <input type="checkbox" checked={allCampaigns} onChange={e => setAllCampaigns(e.target.checked)} />
                    Показать все кампании
                </label>

                {error && <div style={{ color: '#ef4444', fontSize: 13, padding: '12px 0' }}>{error}</div>}
                {!error && entries === null && <div style={{ color: '#9ca3af', fontSize: 13, padding: '12px 0' }}>Загрузка…</div>}
                {!error && entries !== null && entries.length === 0 && (
                    <div style={{ color: '#9ca3af', fontSize: 13, padding: '12px 0' }}>Пополнений ещё не было.</div>
                )}
                {!error && entries !== null && entries.length > 0 && (
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <thead><tr>
                            <th style={thLeft}>Кампания</th>
                            <th style={thLeft}>Дата (МСК)</th>
                            <th style={thStyle}>Сумма, ₽</th>
                            <th style={thLeft}>Источник</th>
                            <th style={thLeft}>Статус</th>
                            <th style={thStyle} title="Бюджет кампании после пополнения">Бюджет после</th>
                        </tr></thead>
                        <tbody>
                            {entries.map((e, i) => {
                                const badge = AUTOPAY_STATUS_BADGE[e.status] ?? AUTOPAY_STATUS_BADGE.unknown;
                                return (
                                    <tr key={`${e.campaign_id}-${e.ts}-${i}`} style={{ color: '#111827' }}>
                                        <td style={tdLeft}>#{e.campaign_id}</td>
                                        <td style={tdLeft}>{fmtTs(e.ts)}</td>
                                        <td style={{ ...tdStyle, fontWeight: 600 }}>{e.status === 'ok' ? fmt(e.amount) : fmt(e.requested)}</td>
                                        <td style={tdLeft}>{e.source || '—'}</td>
                                        <td style={tdLeft}>
                                            <span className={`badge ${badge.cls}`} title={e.reason || undefined}>{badge.label}</span>
                                        </td>
                                        <td style={tdStyle}>{e.budget_after != null ? fmt(e.budget_after) : '—'}</td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                )}

                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary" onClick={onClose}>Закрыть</button>
                </div>
            </div>
        </div>
    );
}

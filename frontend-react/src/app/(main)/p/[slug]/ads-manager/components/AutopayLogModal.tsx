import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { AdsManagerCampaign, BudgetLedgerEntry } from '@/types/api';
import { IcHistory } from './icons';
import { fmt, thLeft, thStyle, tdLeft, tdStyle } from './adsShared';

type LedgerTab = 'topup' | 'charge';

// Подпись типа записи (в пределах вкладки знак уже один — подпись уточняет источник движения)
const KIND_LABEL: Record<BudgetLedgerEntry['kind'], string> = {
    campaign_topup: 'Пополнение',
    account_topup: 'Пополнение счёта',
    charge: 'Списание',
};

/** Модалка «История бюджета»: две вкладки — «Пополнения» (+) и «Списания» (−) из данных, парсимых
 *  из WB (рост бюджета кампании, пополнения счёта кабинета, списания). По кампании или по всем сразу. */
export default function AutopayLogModal({ campaign, onClose }: {
    campaign: AdsManagerCampaign;
    onClose: () => void;
}) {
    const [tab, setTab] = useState<LedgerTab>('topup');
    const [entries, setEntries] = useState<BudgetLedgerEntry[] | null>(null);
    const [error, setError] = useState('');
    const [allCampaigns, setAllCampaigns] = useState(false);

    useEffect(() => {
        const controller = new AbortController();
        setEntries(null);
        setError('');
        api.getBudgetLedger(allCampaigns ? undefined : campaign.campaign_id, tab)
            .then(data => { if (!controller.signal.aborted) setEntries(data); })
            .catch(() => { if (!controller.signal.aborted) setError('Не удалось загрузить историю бюджета'); });
        return () => controller.abort();
    }, [campaign.campaign_id, allCampaigns, tab]);

    const fmtTs = (ts: string) => {
        try {
            return new Date(ts).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' });
        } catch { return ts; }
    };

    const TABS: { key: LedgerTab; label: string }[] = [
        { key: 'topup', label: 'Пополнения' },
        { key: 'charge', label: 'Списания' },
    ];
    const isTopup = tab === 'topup';

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
            <div className="glass-card" style={{ width: 720, maxHeight: '80vh', overflow: 'auto', padding: 24, background: '#fff' }} onClick={e => e.stopPropagation()}>
                <h3 style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 16, fontWeight: 600, margin: '0 0 4px' }}><IcHistory size={18} />История бюджета</h3>
                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 12 }}>
                    {allCampaigns ? 'Все кампании' : <>{campaign.name || `#${campaign.campaign_id}`} · #{campaign.campaign_id}</>}
                    {' · данные из WB'}
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
                    {/* Вкладки: пополнения / списания — на каждой только свой знак */}
                    <span style={{ display: 'inline-flex', border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden' }}>
                        {TABS.map(t => (
                            <button key={t.key} onClick={() => setTab(t.key)}
                                style={{ padding: '6px 16px', fontSize: 13, fontWeight: 500, border: 'none', cursor: 'pointer', background: tab === t.key ? '#3b82f6' : '#fff', color: tab === t.key ? '#fff' : '#374151' }}>
                                {t.label}
                            </button>
                        ))}
                    </span>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
                        <input type="checkbox" checked={allCampaigns} onChange={e => setAllCampaigns(e.target.checked)} />
                        Показать все кампании
                    </label>
                </div>

                {error && <div style={{ color: '#ef4444', fontSize: 13, padding: '12px 0' }}>{error}</div>}
                {!error && entries === null && <div style={{ color: '#9ca3af', fontSize: 13, padding: '12px 0' }}>Загрузка…</div>}
                {!error && entries !== null && entries.length === 0 && (
                    <div style={{ color: '#9ca3af', fontSize: 13, padding: '12px 0' }}>{isTopup ? 'Пополнений пока нет.' : 'Списаний пока нет.'}</div>
                )}
                {!error && entries !== null && entries.length > 0 && (
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <thead><tr>
                            <th style={thLeft}>Дата (МСК)</th>
                            <th style={thLeft}>Тип</th>
                            <th style={thStyle}>Сумма, ₽</th>
                            <th style={thLeft}>Кампания</th>
                            <th style={thLeft}>Источник</th>
                            <th style={thLeft}>Примечание</th>
                        </tr></thead>
                        <tbody>
                            {entries.map((e, i) => (
                                <tr key={`${e.ts}-${e.campaign_id}-${i}`} style={{ color: '#111827' }}>
                                    <td style={tdLeft}>{fmtTs(e.ts)}</td>
                                    <td style={tdLeft}>{KIND_LABEL[e.kind] ?? '—'}</td>
                                    <td style={{ ...tdStyle, fontWeight: 600, color: isTopup ? '#059669' : '#ef4444' }}>
                                        {isTopup ? '+' : '−'}{fmt(Math.abs(e.amount))}
                                    </td>
                                    <td style={tdLeft}>{e.campaign_id != null ? `#${e.campaign_id}` : '—'}</td>
                                    <td style={tdLeft}>{e.source || '—'}</td>
                                    <td style={{ ...tdLeft, color: '#6b7280' }}>{e.note || '—'}</td>
                                </tr>
                            ))}
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

'use client';
import React from 'react';
import type { CampaignHourlySpend } from '@/types/api';
import { fmt, thStyle, tdStyle, tdLeft } from './adsShared';

/** Почасовой расход кампании: столбчатый график по 24 часам + таблица активных часов.
 *  Данные восстановлены из снимков остатка бюджета (~10 мин) — только расход, не показы/клики. */
export default function CampaignHourlyChart({ resp }: { resp: CampaignHourlySpend }) {
    const hours = resp.hours;
    const max = Math.max(1, ...hours.map(h => h.spend));
    const hasData = resp.total > 0;
    const hh = (h: number) => `${String(h).padStart(2, '0')}`;
    return (
        <div style={{ padding: 16 }}>
            <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 12 }}>
                Расход по часам за <b>{resp.date}</b> · всего <b>{fmt(resp.total)} ₽</b>. Восстановлено из снимков остатка бюджета (точность ~10 мин); показы/клики/заказы WB по часам не отдаёт.
            </div>

            {/* Столбчатый график — 24 часа */}
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 190, borderBottom: '1px solid #e5e7eb' }}>
                {hours.map(h => (
                    <div key={h.hour} title={`${hh(h.hour)}:00 — ${fmt(h.spend)} ₽`}
                        style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', alignItems: 'center', height: '100%' }}>
                        <div style={{ width: '100%', height: `${(h.spend / max) * 100}%`, minHeight: h.spend > 0 ? 2 : 0, background: '#3b82f6', borderRadius: '3px 3px 0 0' }} />
                    </div>
                ))}
            </div>
            <div style={{ display: 'flex', gap: 3, marginBottom: 18 }}>
                {hours.map(h => (
                    <div key={h.hour} style={{ flex: 1, textAlign: 'center', fontSize: 9, color: '#9ca3af' }}>{h.hour % 3 === 0 ? h.hour : ''}</div>
                ))}
            </div>

            {/* Таблица по всем 24 часам */}
            {!hasData ? (
                <div style={{ padding: 20, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>
                    За этот день нет данных о расходе по часам — кампания не крутилась либо не была активна (снимки бюджета есть только у активных кампаний).
                </div>
            ) : (
                <table className="data-table" style={{ borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                    <thead><tr>
                        <th style={{ ...thStyle, textAlign: 'left' }}>Час (МСК)</th>
                        <th style={thStyle}>Затраты ₽</th>
                    </tr></thead>
                    <tbody>
                        {hours.map(h => (
                            <tr key={h.hour}>
                                <td style={tdLeft}>{hh(h.hour)}:00–{hh((h.hour + 1) % 24)}:00</td>
                                <td style={tdStyle}>{fmt(h.spend)}</td>
                            </tr>
                        ))}
                        <tr style={{ background: '#eff6ff' }}>
                            <td style={{ ...tdLeft, fontWeight: 700 }}>Всего</td>
                            <td style={{ ...tdStyle, fontWeight: 700 }}>{fmt(resp.total)}</td>
                        </tr>
                    </tbody>
                </table>
            )}
        </div>
    );
}

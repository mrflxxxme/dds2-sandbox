'use client';
import React from 'react';
import type { CampaignZoneMetricsResponse, CampaignZoneMetricRow } from '@/types/api';
import { fmt, fmtPct, thStyle, tdLeft, tdStyle } from './adsShared';
import { useFitViewport } from './useFitViewport';

// РК-метрики зоны показов (без воронки — WB её по зонам не делит).
// atbs/orders — корзины/заказы, атрибутированные рекламе.
const COLS: { k: keyof CampaignZoneMetricRow; label: string; pct?: boolean }[] = [
    { k: 'views', label: 'Показы' },
    { k: 'clicks', label: 'Клики' },
    { k: 'ctr', label: 'CTR', pct: true },
    { k: 'cpc', label: 'CPC ₽' },
    { k: 'cpm', label: 'CPM ₽' },
    { k: 'spend', label: 'Затраты ₽' },
    { k: 'atbs', label: 'Корзины' },
    { k: 'orders', label: 'Заказы' },
    { k: 'cpo', label: 'CPO ₽' },
];
const fmtCell = (r: CampaignZoneMetricRow, k: keyof CampaignZoneMetricRow, pct?: boolean) => {
    const v = r[k];
    if (v == null) return '—';
    return pct ? fmtPct(v as number) : fmt(v as number);
};
const fmtMetricDate = (d: string) => (/^\d{4}-\d{2}-\d{2}$/.test(d) ? `${d.slice(8, 10)}.${d.slice(5, 7)}.${d.slice(2, 4)}` : d);

// Высота строки-шапки — от неё липнет строка-итог
const HEAD_H = 26;

/** Компактная таблица посуточных РК-метрик кампании по зоне показов, со строкой-итогом. */
export default function CampaignZoneMetricsTable({ resp }: { resp: CampaignZoneMetricsResponse }) {
    const { ref: fitRef, maxHeight: fitHeight } = useFitViewport();
    // Тёмно-серая шапка, липнет к верху скролл-контейнера; дата — липкая слева
    const dTh: React.CSSProperties = { ...thStyle, background: '#374151', color: '#e5e7eb', borderBottom: '1px solid #4b5563', position: 'sticky', top: 0, zIndex: 3 };
    const dThLeft: React.CSSProperties = { ...dTh, textAlign: 'left' };
    // Итог живёт в <thead> (sticky на ячейке внутри tbody браузер не удерживает)
    const totalCell: React.CSSProperties = { ...tdStyle, fontWeight: 700, background: '#eff6ff', position: 'sticky', top: HEAD_H, zIndex: 3 };
    return (
        // Прокручиваются дни, а не страница: шапка остаётся на виду
        <div ref={fitRef} style={{ overflow: 'auto', maxHeight: fitHeight }}>
            <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                <thead>
                    <tr>
                        <th style={{ ...dThLeft, left: 0, zIndex: 5 }}>Дата</th>
                        {COLS.map(c => <th key={c.k} style={dTh}>{c.label}</th>)}
                    </tr>
                    <tr style={{ background: '#eff6ff' }}>
                        <th style={{ ...totalCell, ...tdLeft, fontWeight: 700, background: '#eff6ff', left: 0, zIndex: 4 }}>{resp.totals.date}</th>
                        {COLS.map(c => <th key={c.k} style={totalCell}>{fmtCell(resp.totals, c.k, c.pct)}</th>)}
                    </tr>
                </thead>
                <tbody>
                    {resp.rows.map(r => (
                        <tr key={r.date} style={{ color: '#111827' }}>
                            <td style={{ ...tdLeft, fontWeight: 500, position: 'sticky', left: 0, zIndex: 1, background: '#fff' }}>{fmtMetricDate(r.date)}</td>
                            {COLS.map(c => <td key={c.k} style={tdStyle}>{fmtCell(r, c.k, c.pct)}</td>)}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

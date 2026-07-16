'use client';
import React from 'react';
import type { CampaignMetricsResponse, CampaignMetricRow } from '@/types/api';
import { fmt, fmtPct, thLeft, thStyle, tdLeft, tdStyle } from './adsShared';
import { useFitViewport } from './useFitViewport';

// Колонки посуточных метрик: РК-статистика + воронка продаж
const RK_COLS: { k: keyof CampaignMetricRow; label: string; pct?: boolean }[] = [
    { k: 'views', label: 'Показы' },
    { k: 'clicks', label: 'Клики' },
    { k: 'ctr', label: 'CTR', pct: true },
    { k: 'cpc', label: 'CPC ₽' },
    { k: 'spend', label: 'Затраты ₽' },
];
const FUNNEL_COLS: { k: keyof CampaignMetricRow; label: string; pct?: boolean }[] = [
    { k: 'open_card', label: 'Переходы' },
    { k: 'add_to_cart', label: 'Корзины' },
    { k: 'cr1', label: 'CR1', pct: true },
    { k: 'orders', label: 'Заказы' },
    { k: 'cr2', label: 'CR2', pct: true },
    { k: 'orders_sum', label: 'Сумма ₽' },
    { k: 'cpo', label: 'CPO ₽' },
    { k: 'avg_price', label: 'Цена ₽' },
    { k: 'customer_price', label: 'Цена Клиенту ₽' },
    { k: 'drr', label: 'ДРР', pct: true },
];
const fmtCell = (r: CampaignMetricRow, k: keyof CampaignMetricRow, pct?: boolean) => {
    const v = r[k];
    if (v == null) return '—';
    return pct ? fmtPct(v as number) : fmt(v as number);
};
const fmtMetricDate = (d: string) => (/^\d{4}-\d{2}-\d{2}$/.test(d) ? `${d.slice(8, 10)}.${d.slice(5, 7)}.${d.slice(2, 4)}` : d);

// Высота верхней строки шапки («Статистика по РК» / «Воронка продаж») — от неё липнет вторая
const GROUP_H = 26;
// Итог липнет сразу под двумя строками шапки
const COLS_H = 24;
const TOTAL_TOP = GROUP_H + COLS_H;

/** Компактная таблица посуточных метрик кампании (РК + воронка), со строкой-итогом. */
export default function CampaignMetricsTable({ resp }: { resp: CampaignMetricsResponse }) {
    const cols = [...RK_COLS, ...FUNNEL_COLS];
    const { ref: fitRef, maxHeight: fitHeight } = useFitViewport();
    // Тёмно-серая шапка
    // Шапка липнет к верху скролл-контейнера: группы сверху, названия колонок — под ними
    const dTh: React.CSSProperties = { ...thStyle, background: '#374151', color: '#e5e7eb', borderBottom: '1px solid #4b5563', position: 'sticky', top: GROUP_H, zIndex: 3 };
    const dThLeft: React.CSSProperties = { ...dTh, textAlign: 'left' };
    const groupHead: React.CSSProperties = { ...dTh, fontWeight: 700, textAlign: 'center', top: 0, zIndex: 4 };
    const row = (r: CampaignMetricRow, key: string) => (
        <tr key={key} style={{ color: '#111827' }}>
            <td style={{ ...tdLeft, fontWeight: 500, position: 'sticky', left: 0, zIndex: 1, background: '#fff' }}>{fmtMetricDate(r.date)}</td>
            {cols.map((c, i) => <td key={c.k} style={i === RK_COLS.length ? { ...tdStyle, borderLeft: '1px solid #eef0f2' } : tdStyle}>{fmtCell(r, c.k, c.pct)}</td>)}
        </tr>
    );

    // Итог живёт в <thead>, а не в <tbody>: sticky на ячейке внутри tbody браузер
    // не удерживает (ячейка ограничена своей строкой), а строка thead липнет штатно.
    const totalCell: React.CSSProperties = { ...tdStyle, fontWeight: 700, background: '#eff6ff', position: 'sticky', top: TOTAL_TOP, zIndex: 3 };
    const totalsRow = (r: CampaignMetricRow) => (
        <tr style={{ background: '#eff6ff' }}>
            <th style={{ ...totalCell, ...tdLeft, fontWeight: 700, background: '#eff6ff', left: 0, zIndex: 4 }}>{r.date}</th>
            {cols.map((c, i) => <th key={c.k} style={i === RK_COLS.length ? { ...totalCell, borderLeft: '1px solid #eef0f2' } : totalCell}>{fmtCell(r, c.k, c.pct)}</th>)}
        </tr>
    );
    return (
        // Прокручиваются дни, а не страница: шапка остаётся на виду
        <div ref={fitRef} style={{ overflow: 'auto', maxHeight: fitHeight }}>
            <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                <thead>
                    <tr>
                        <th style={{ ...dThLeft, top: 0, left: 0, zIndex: 5 }} rowSpan={2}>Дата</th>
                        <th style={groupHead} colSpan={RK_COLS.length}>Статистика по РК</th>
                        <th style={{ ...groupHead, borderLeft: '1px solid #4b5563' }} colSpan={FUNNEL_COLS.length}>Воронка продаж</th>
                    </tr>
                    <tr>
                        {RK_COLS.map(c => <th key={c.k} style={dTh}>{c.label}</th>)}
                        {FUNNEL_COLS.map((c, i) => <th key={c.k} style={i === 0 ? { ...dTh, borderLeft: '1px solid #4b5563' } : dTh}>{c.label}</th>)}
                    </tr>
                    {totalsRow(resp.totals)}
                </thead>
                <tbody>
                    {resp.rows.map(r => row(r, r.date))}
                </tbody>
            </table>
        </div>
    );
}

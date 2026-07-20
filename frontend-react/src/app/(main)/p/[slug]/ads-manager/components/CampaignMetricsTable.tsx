'use client';
import React from 'react';
import type { CampaignMetricsResponse, CampaignMetricRow, CampaignMetricEvent } from '@/types/api';
import { fmt, fmtPct, thLeft, thStyle, tdLeft, tdStyle, readTargetDrr } from './adsShared';
import { drrColor } from './ClusterTable';
import { useFitViewport } from './useFitViewport';
import Tooltip from './Tooltip';

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
    { k: 'spp', label: 'СПП', pct: true },
    { k: 'customer_price', label: 'Цена Клиенту ₽' },
    { k: 'drr', label: 'ДРР', pct: true },
];
const fmtCell = (r: CampaignMetricRow, k: keyof CampaignMetricRow, pct?: boolean) => {
    const v = r[k];
    if (v == null) return '—';
    return pct ? fmtPct(v as number) : fmt(v as number);
};
const fmtMetricDate = (d: string) => (/^\d{4}-\d{2}-\d{2}$/.test(d) ? `${d.slice(8, 10)}.${d.slice(5, 7)}.${d.slice(2, 4)}` : d);

// Всё, что случилось за день, — цветные метки в ячейке даты. Строк-разделителей во всю
// ширину больше нет: они рвали таблицу на куски и на длинном окне превращали её в зебру,
// а колонку дат теперь можно просматривать сверху вниз и видеть весь ход событий подряд.
// Цвет отвечает на вопрос «чьё это было решение»:
//   синий      — наша цена (уступили маржу сами),
//   фиолетовый — скидка ВБ, СПП (маркетплейс, мы не влияем и завтра он её уберёт),
//   красный    — бюджет кончился и день так и закончился на нуле,
//   янтарный   — кончился, но долили: кампания стояла кусок дня,
//   серый      — простой целиком: показов почти нет, день не в счёт для сравнения.
const MARK_STYLE: Record<CampaignMetricEvent['kind'], { bg: string; fg: string }> = {
    price: { bg: '#dbeafe', fg: '#1d4ed8' },
    spp: { bg: '#ede9fe', fg: '#6d28d9' },
    budget: { bg: '#fee2e2', fg: '#b91c1c' },
    gap: { bg: '#fef3c7', fg: '#92400e' },
    idle: { bg: '#e5e7eb', fg: '#4b5563' },
};
// Само число внутри метки красится по ЗНАКУ, а не по роду события: фон уже сказал, чьё это
// решение, а цвет цифры — в какую сторону оно сдвинуло. Красный/зелёный здесь читаются как
// «вниз/вверх», а не как «плохо/хорошо»: рост цены не обязательно хорош, но виден сразу.
const DIR_COLOR: Record<number, string | undefined> = { [-1]: '#dc2626', 1: '#16a34a' };
const markStyle = (m: { bg: string; fg: string }): React.CSSProperties => ({
    background: m.bg, color: m.fg, fontSize: 9.5, fontWeight: 700, lineHeight: 1.5,
    padding: '0 5px', borderRadius: 4, marginLeft: 4, whiteSpace: 'nowrap', fontStyle: 'normal',
});

// Высота верхней строки шапки («Статистика по РК» / «Воронка продаж») — от неё липнет вторая
const GROUP_H = 26;
// Итог липнет сразу под двумя строками шапки
const COLS_H = 24;
const TOTAL_TOP = GROUP_H + COLS_H;

/** Компактная таблица посуточных метрик кампании (РК + воронка), со строкой-итогом. */
export default function CampaignMetricsTable({ resp }: { resp: CampaignMetricsResponse }) {
    const cols = [...RK_COLS, ...FUNNEL_COLS];
    const { ref: fitRef, maxHeight: fitHeight } = useFitViewport();
    // Порог светофора ДРР — тот же, что в кластеризаторе (localStorage → кампания → 8).
    // Читаем после монтирования: localStorage недоступен на сервере.
    const [targetDrr, setTargetDrr] = React.useState<number>(resp.target_drr && resp.target_drr > 0 ? resp.target_drr : 8);
    React.useEffect(() => { setTargetDrr(readTargetDrr(resp.target_drr)); }, [resp.target_drr]);
    // За день может случиться несколько событий (сменили цену, ВБ подвинул СПП, кончился
    // бюджет) — показываем все, бэк уже отдаёт их в осмысленном порядке.
    const dayMarks = React.useMemo(() => {
        const m = new Map<string, CampaignMetricEvent[]>();
        for (const e of resp.events ?? []) {
            const list = m.get(e.date);
            if (list) list.push(e); else m.set(e.date, [e]);
        }
        return m;
    }, [resp.events]);

    // Тёмно-серая шапка
    // Шапка липнет к верху скролл-контейнера: группы сверху, названия колонок — под ними
    const dTh: React.CSSProperties = { ...thStyle, background: '#374151', color: '#e5e7eb', borderBottom: '1px solid #4b5563', position: 'sticky', top: GROUP_H, zIndex: 3 };
    const dThLeft: React.CSSProperties = { ...dTh, textAlign: 'left' };
    const groupHead: React.CSSProperties = { ...dTh, fontWeight: 700, textAlign: 'center', top: 0, zIndex: 4 };

    // Светофор только в ДРР: единственная колонка с объективной целью. Не красим:
    //  • неполный день — его цифры нельзя читать наравне с закрытыми;
    //  • день без продаж — там ДРР=0 арифметически (делить не на что), а НЕ «идеальный».
    //    Зелёный на дне, где реклама открутилась впустую, — ровно обратный сигнал.
    const cell = (r: CampaignMetricRow, c: typeof cols[number], i: number) => {
        const base = i === RK_COLS.length ? { ...tdStyle, borderLeft: '1px solid #eef0f2' } : tdStyle;
        const undefinedDrr = !r.orders_sum || r.orders_sum <= 0;
        if (c.k !== 'drr' || r.is_partial || undefinedDrr) {
            const txt = c.k === 'drr' && undefinedDrr ? '—' : fmtCell(r, c.k, c.pct);
            return <td key={c.k} style={base}>{txt}</td>;
        }
        return <td key={c.k} style={{ ...base, color: drrColor(r.drr, targetDrr), fontWeight: 600 }}>{fmtCell(r, c.k, c.pct)}</td>;
    };

    const row = (r: CampaignMetricRow) => {
        const marks = dayMarks.get(r.date) ?? [];
        return (
            <tr key={r.date} style={r.is_partial ? { color: '#9ca3af', fontStyle: 'italic' } : { color: '#111827' }}>
                <td style={{ ...tdLeft, fontWeight: 500, position: 'sticky', left: 0, zIndex: 1, background: '#fff' }}>
                    {fmtMetricDate(r.date)}
                    {r.is_partial && (
                        <Tooltip text="День ещё идёт — цифры неполные, сравнивать с закрытыми днями нельзя">
                            <span style={markStyle({ bg: '#dbeafe', fg: '#1d4ed8' })}>идёт</span>
                        </Tooltip>
                    )}
                    {marks.map(m => (
                        <Tooltip key={m.kind} text={m.text}>
                            <span style={markStyle(MARK_STYLE[m.kind])}>
                                {m.short} <span style={{ color: DIR_COLOR[m.dir] }}>{m.value}</span>
                            </span>
                        </Tooltip>
                    ))}
                </td>
                {cols.map((c, i) => cell(r, c, i))}
            </tr>
        );
    };

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
                    {resp.rows.map(row)}
                </tbody>
            </table>
        </div>
    );
}

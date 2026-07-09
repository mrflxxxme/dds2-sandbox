'use client';
import React, { useEffect, useMemo, useState } from 'react';
import { formatNumber, exportToExcel } from '@/lib/utils';
import type { SearchCluster, ClusterDailyPoint } from '@/types/api';

// ─── Форматтеры (Decimal-поля бэка приходят строкой → Number() перед formatNumber) ───
const num = (v: unknown) => Number(v) || 0;
export const clNum = (v: unknown) => formatNumber(num(v), 0);
export const clMoney = (v: unknown) => `${formatNumber(num(v), 0)} ₽`;
export const clPct = (v: unknown, d = 1) => `${formatNumber(num(v), d)}%`;
export const clMoneyN = (v: number | null | undefined) => (v == null ? '—' : `${formatNumber(Number(v), 0)} ₽`);
export const clPctN = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${formatNumber(Number(v), d)}%`);

// ДРР-цвет: ≤ target зелёный, ≤ 1.5×target янтарь, иначе красный
export function drrColor(drr: number | null, targetDrr: number): string {
    if (drr == null) return '#9ca3af';
    const t = targetDrr > 0 ? targetDrr : 8;
    if (drr <= t) return '#10b981';
    if (drr <= t * 1.5) return '#f59e0b';
    return '#ef4444';
}

const thStyle: React.CSSProperties = { background: '#f9fafb', color: '#4b5563', fontSize: 11, textAlign: 'right', borderBottom: '2px solid #e5e7eb', padding: '8px 10px', whiteSpace: 'nowrap' };
const thLeft: React.CSSProperties = { ...thStyle, textAlign: 'left' };
const tdStyle: React.CSSProperties = { textAlign: 'right', borderBottom: '1px solid #f3f4f6', padding: '7px 10px', fontSize: 13 };
const tdLeft: React.CSSProperties = { ...tdStyle, textAlign: 'left' };

// Стиль компактных инпутов фильтр-строки
const fInput: React.CSSProperties = { width: 56, boxSizing: 'border-box', padding: '2px 4px', fontSize: 11, border: '1px solid #e5e7eb', borderRadius: 6, color: '#374151', background: '#fff' };
const fTextInput: React.CSSProperties = { ...fInput, width: 96 };
const filterThStyle: React.CSSProperties = { background: '#f9fafb', borderBottom: '2px solid #e5e7eb', padding: '2px 6px 6px' };

type SortField = 'norm_query' | 'bid' | 'views' | 'clicks' | 'ctr' | 'orders' | 'cr' | 'cpo' | 'drr' | 'avg_pos' | 'spend';
type SortDir = 'asc' | 'desc';
type RelFilter = 'all' | 'target' | 'trash';

// Числовые колонки, по которым доступен диапазонный фильтр «от / до»
type RangeField = 'bid' | 'views' | 'clicks' | 'ctr' | 'orders' | 'cr' | 'cpo' | 'drr' | 'avg_pos' | 'spend';
const RANGE_FIELDS: RangeField[] = ['bid', 'views', 'clicks', 'ctr', 'orders', 'cr', 'cpo', 'drr', 'avg_pos', 'spend'];
type Range = { min: string; max: string };
const emptyRanges = (): Record<RangeField, Range> =>
    RANGE_FIELDS.reduce((acc, f) => { acc[f] = { min: '', max: '' }; return acc; }, {} as Record<RangeField, Range>);

export interface MinusControls {
    pending: Set<string>;         // norm_query, по которым идёт запрос
    onToggle: (c: SearchCluster) => void;
    error?: string | null;        // текст последней ошибки
}

export interface BidControls {
    pending: Set<string>;         // norm_query, по которым идёт запись ставки
    onSetBid: (c: SearchCluster, bid: number) => void;
    error?: string | null;        // текст последней ошибки
}

/** Редактируемая ячейка CPM-ставки кластера. */
function BidCell({ cluster, bids }: { cluster: SearchCluster; bids?: BidControls }) {
    const current = cluster.bid == null ? null : Number(cluster.bid);
    const [val, setVal] = useState<string>(current == null ? '' : String(current));
    useEffect(() => { setVal(current == null ? '' : String(current)); }, [current]);

    // Категорийная таблица: bids не передан — только чтение
    if (!bids) return <span>{current == null ? '—' : clMoney(current)}</span>;

    if (cluster.locked) {
        return (
            <span title="<100 показов — WB не даёт ставку" style={{ color: '#9ca3af', fontSize: 12, fontStyle: 'italic' }}>
                сбор данных
            </span>
        );
    }

    const pending = bids.pending.has(cluster.norm_query);
    const commit = () => {
        if (pending) return;
        const s = val.trim();
        if (s === '') { setVal(current == null ? '' : String(current)); return; }
        const n = Number(s);
        if (!Number.isFinite(n) || n <= 0) { setVal(current == null ? '' : String(current)); return; }
        if (n === current) return;   // без изменения — не дёргаем WB
        bids.onSetBid(cluster, n);
    };

    return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end' }}>
            <input
                type="number" min={0} value={val} disabled={pending}
                onChange={e => setVal(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
                onBlur={commit}
                title="CPM-ставка кластера, ₽ — Enter или потеря фокуса применит в кабинете WB"
                style={{ width: 64, boxSizing: 'border-box', padding: '3px 6px', fontSize: 12, textAlign: 'right', border: '1px solid #e5e7eb', borderRadius: 6, color: '#111827', background: pending ? '#f3f4f6' : '#fff', opacity: pending ? 0.6 : 1 }} />
            {pending ? <span style={{ fontSize: 11, color: '#9ca3af' }}>…</span> : <span style={{ fontSize: 11, color: '#9ca3af' }}>₽</span>}
        </span>
    );
}

/**
 * Сортируемая таблица кластеров: сегмент релевантности + пер-колоночные фильтры.
 * Общая для модалки кампании, страницы артикула и категории.
 *  - `minus` — колонка «В минус / Вернуть» (разрез кампании/артикула).
 *  - `bids`  — редактируемая колонка «Ставка» (те же разрезы); без неё ставка read-only.
 */
export default function ClusterTable({ clusters, targetDrr, exportName, minus, bids }: {
    clusters: SearchCluster[];
    targetDrr: number;
    exportName: string;
    minus?: MinusControls;
    bids?: BidControls;
}) {
    const [rel, setRel] = useState<RelFilter>('all');
    const [contains, setContains] = useState('');
    const [notContains, setNotContains] = useState('');
    const [ranges, setRanges] = useState<Record<RangeField, Range>>(emptyRanges);
    const [sort, setSort] = useState<{ field: SortField; dir: SortDir }>({ field: 'spend', dir: 'desc' });

    const setRange = (f: RangeField, key: 'min' | 'max', v: string) =>
        setRanges(prev => ({ ...prev, [f]: { ...prev[f], [key]: v } }));

    // Диапазонная проверка: если задана хотя бы одна граница — null-значения отсеиваются
    const inRange = (raw: number | null | undefined, r: Range): boolean => {
        const hasMin = r.min.trim() !== '';
        const hasMax = r.max.trim() !== '';
        if (!hasMin && !hasMax) return true;
        if (raw == null) return false;
        const n = Number(raw);
        if (hasMin && n < Number(r.min)) return false;
        if (hasMax && n > Number(r.max)) return false;
        return true;
    };

    const filtered = useMemo(() => {
        const cont = contains.trim().toLowerCase();
        const ncont = notContains.trim().toLowerCase();
        const base = clusters.filter(c => {
            if (rel === 'target' && !c.relevant) return false;
            if (rel === 'trash' && c.relevant) return false;
            const q = c.norm_query.toLowerCase();
            if (cont && !q.includes(cont)) return false;
            if (ncont && q.includes(ncont)) return false;
            for (const f of RANGE_FIELDS) {
                const raw = c[f] as number | null | undefined;
                if (!inRange(raw, ranges[f])) return false;
            }
            return true;
        });
        const dir = sort.dir === 'asc' ? 1 : -1;
        return [...base].sort((a, b) => {
            if (sort.field === 'norm_query') return dir * a.norm_query.localeCompare(b.norm_query, 'ru');
            const av = a[sort.field] == null ? -Infinity : Number(a[sort.field]);
            const bv = b[sort.field] == null ? -Infinity : Number(b[sort.field]);
            return dir * (av - bv);
        });
    }, [clusters, rel, contains, notContains, ranges, sort]);

    const toggleSort = (field: SortField) =>
        setSort(prev => ({ field, dir: prev.field === field && prev.dir === 'desc' ? 'asc' : 'desc' }));
    const arrow = (field: SortField) => sort.field === field ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : '';
    const sortableTh = (field: SortField, label: string, left = false, title?: string) => (
        <th style={{ ...(left ? thLeft : thStyle), cursor: 'pointer' }} onClick={() => toggleSort(field)} title={title}>{label}{arrow(field)}</th>
    );

    // Ячейка фильтр-строки для числовой колонки: «от / до»
    const rangeFilterTh = (f: RangeField) => (
        <th style={filterThStyle}>
            <span style={{ display: 'inline-flex', gap: 3, justifyContent: 'flex-end', width: '100%' }}>
                <input type="number" placeholder="от" value={ranges[f].min} onChange={e => setRange(f, 'min', e.target.value)} style={fInput} />
                <input type="number" placeholder="до" value={ranges[f].max} onChange={e => setRange(f, 'max', e.target.value)} style={fInput} />
            </span>
        </th>
    );

    const doExport = () => exportToExcel(filtered.map(c => ({
        'Кластер': c.norm_query, 'Релевантность': c.relevant ? 'целевой' : 'мусор', 'Причина': c.reason,
        'Ставка ₽': c.bid == null ? '' : Number(c.bid),
        'Показы': num(c.views), 'Клики': num(c.clicks), 'CTR %': num(c.ctr), 'CPC ₽': num(c.cpc), 'CPM ₽': num(c.cpm),
        'Заказы': num(c.orders), 'CR %': num(c.cr), 'CPO ₽': c.cpo == null ? '' : Number(c.cpo),
        'ДРР %': c.drr == null ? '' : Number(c.drr), 'Ср. позиция': num(c.avg_pos), 'Расход ₽': num(c.spend),
        'В минусе': c.is_minused ? 'да' : '',
    })), exportName);

    // Мусор — красноватый фон; в минусе — приглушённо-серый (приоритет минуса)
    const rowBg = (c: SearchCluster) => c.is_minused ? '#f3f4f6' : (c.relevant === false ? '#fef2f2' : undefined);

    const REL_TABS: { key: RelFilter; label: string }[] = [
        { key: 'all', label: 'Все' },
        { key: 'target', label: '🎯 Целевые' },
        { key: 'trash', label: '🗑 Мусор' },
    ];

    return (
        <div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
                <div style={{ display: 'inline-flex', border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden' }}>
                    {REL_TABS.map((t, i) => {
                        const active = rel === t.key;
                        return (
                            <button key={t.key} onClick={() => setRel(t.key)}
                                style={{ padding: '5px 14px', fontSize: 12, fontWeight: 500, cursor: 'pointer', border: 'none',
                                    borderLeft: i > 0 ? '1px solid #e5e7eb' : 'none',
                                    background: active ? '#3b82f6' : '#fff', color: active ? '#fff' : '#374151' }}>
                                {t.label}
                            </button>
                        );
                    })}
                </div>
                <span style={{ fontSize: 12, color: 'var(--color-text-dim)', marginLeft: 'auto' }}>Кластеров: {filtered.length}</span>
                <button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }} onClick={doExport} disabled={filtered.length === 0} title="Выгрузить в Excel">📥 Excel</button>
            </div>

            {minus?.error && <div style={{ color: '#ef4444', fontSize: 12, marginBottom: 8 }}>⚠️ {minus.error}</div>}
            {bids?.error && <div style={{ color: '#ef4444', fontSize: 12, marginBottom: 8 }}>⚠️ {bids.error}</div>}

            {filtered.length === 0 ? (
                <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>Нет кластеров по выбранному фильтру</div>
            ) : (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                        <thead>
                            <tr>
                                <th style={{ ...thStyle, textAlign: 'center', width: 28 }} title="🎯 целевой / 🗑 мусор"> </th>
                                {sortableTh('norm_query', 'Кластер', true)}
                                {sortableTh('bid', 'Ставка', false, 'CPM-ставка кластера, ₽')}
                                {sortableTh('views', 'Показы')}
                                {sortableTh('clicks', 'Клики')}
                                {sortableTh('ctr', 'CTR')}
                                {sortableTh('orders', 'Заказы')}
                                {sortableTh('cr', 'CR', false, 'Конверсия клик→заказ')}
                                {sortableTh('cpo', 'CPO', false, 'Стоимость заказа')}
                                {sortableTh('drr', 'ДРР', false, 'Доля рекламных расходов = расход / (заказы × AOV)')}
                                {sortableTh('avg_pos', 'Поз.', false, 'Средняя позиция')}
                                {sortableTh('spend', 'Расход ₽')}
                                {minus && <th style={{ ...thStyle, textAlign: 'center' }}>Действие</th>}
                            </tr>
                            <tr>
                                <th style={filterThStyle}> </th>
                                <th style={{ ...filterThStyle, textAlign: 'left' }}>
                                    <span style={{ display: 'inline-flex', gap: 3 }}>
                                        <input placeholder="содержит" value={contains} onChange={e => setContains(e.target.value)} style={fTextInput} />
                                        <input placeholder="не содержит" value={notContains} onChange={e => setNotContains(e.target.value)} style={fTextInput} />
                                    </span>
                                </th>
                                {rangeFilterTh('bid')}
                                {rangeFilterTh('views')}
                                {rangeFilterTh('clicks')}
                                {rangeFilterTh('ctr')}
                                {rangeFilterTh('orders')}
                                {rangeFilterTh('cr')}
                                {rangeFilterTh('cpo')}
                                {rangeFilterTh('drr')}
                                {rangeFilterTh('avg_pos')}
                                {rangeFilterTh('spend')}
                                {minus && <th style={filterThStyle}> </th>}
                            </tr>
                        </thead>
                        <tbody>
                            {filtered.map(c => {
                                const pending = minus?.pending.has(c.norm_query);
                                return (
                                    <tr key={c.norm_query} style={{ color: '#111827', background: rowBg(c) }}>
                                        <td style={{ ...tdStyle, textAlign: 'center' }} title={c.relevant ? 'целевой' : 'мусор'}>{c.relevant ? '🎯' : '🗑'}</td>
                                        <td style={{ ...tdLeft, maxWidth: 260 }}>
                                            <span style={{ fontWeight: 600, textDecoration: c.is_minused ? 'line-through' : undefined }}>{c.norm_query}</span>
                                            {c.is_minused && <span style={{ fontSize: 11, color: '#9ca3af', marginLeft: 6 }}>в минусе</span>}
                                        </td>
                                        <td style={tdStyle}><BidCell cluster={c} bids={bids} /></td>
                                        <td style={tdStyle}>{clNum(c.views)}</td>
                                        <td style={tdStyle}>{clNum(c.clicks)}</td>
                                        <td style={tdStyle}>{clPct(c.ctr, 2)}</td>
                                        <td style={{ ...tdStyle, fontWeight: 600 }}>{clNum(c.orders)}</td>
                                        <td style={tdStyle}>{clPct(c.cr, 2)}</td>
                                        <td style={tdStyle}>{clMoneyN(c.cpo)}</td>
                                        <td style={{ ...tdStyle, fontWeight: 600, color: drrColor(c.drr, targetDrr) }}>{clPctN(c.drr)}</td>
                                        <td style={tdStyle}>{c.avg_pos > 0 ? formatNumber(num(c.avg_pos), 1) : '—'}</td>
                                        <td style={{ ...tdStyle, fontWeight: 600 }}>{clMoney(c.spend)}</td>
                                        {minus && (
                                            <td style={{ ...tdStyle, textAlign: 'center' }}>
                                                <button
                                                    onClick={() => minus.onToggle(c)}
                                                    disabled={pending || c.locked}
                                                    className={`btn btn-sm ${c.is_minused ? 'btn-secondary' : 'btn-danger'}`}
                                                    style={{ fontSize: 12, whiteSpace: 'nowrap', opacity: (pending || c.locked) ? 0.6 : 1 }}
                                                    title={c.locked ? 'Стадия сбора данных, <100 показов — нельзя в минус'
                                                        : c.is_minused ? 'Убрать запрос из минус-фраз кампании' : 'Добавить запрос в минус-фразы кампании в кабинете WB'}>
                                                    {pending ? '…' : c.is_minused ? '↩ Вернуть' : '⛔ В минус'}
                                                </button>
                                            </td>
                                        )}
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

/** Feature 1 — распределение бюджета по дням: горизонтальная полоса, ширина = spend_pct. */
export function DailyBudgetBar({ daily }: { daily: ClusterDailyPoint[] }) {
    if (!daily || daily.length === 0) return null;
    const palette = ['#60a5fa', '#818cf8', '#a78bfa', '#f472b6', '#fb923c', '#34d399'];
    const total = daily.reduce((s, d) => s + num(d.spend), 0);
    const dm = (iso: string) => { const p = iso.slice(5); return p; }; // MM-DD
    return (
        <div>
            <div style={{ display: 'flex', width: '100%', height: 26, borderRadius: 8, overflow: 'hidden', border: '1px solid #e5e7eb' }}>
                {daily.map((d, i) => {
                    const pct = num(d.spend_pct);
                    return (
                        <div key={d.date}
                            title={`${dm(d.date)} · ${clMoney(d.spend)} · ${clPct(d.spend_pct, 1)} бюджета`}
                            style={{ width: `${pct}%`, minWidth: pct > 0 ? 2 : 0, background: palette[i % palette.length], cursor: 'default' }} />
                    );
                })}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', marginTop: 8 }}>
                {daily.map((d, i) => (
                    <span key={d.date} style={{ fontSize: 11, color: '#6b7280', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                        <span style={{ width: 8, height: 8, borderRadius: 2, background: palette[i % palette.length] }} />
                        {dm(d.date)}: {clPct(d.spend_pct, 0)}
                    </span>
                ))}
                <span style={{ fontSize: 11, color: '#9ca3af', marginLeft: 'auto' }}>Всего расход: {clMoney(total)}</span>
            </div>
        </div>
    );
}

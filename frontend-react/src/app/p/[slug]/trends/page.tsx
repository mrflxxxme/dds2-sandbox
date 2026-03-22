'use client';
import { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import AnomaliesTab from './components/AnomaliesTab';

const fmt = (n: number) => n?.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) ?? '0';
const fmtK = (n: number) => {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace('.0', '') + 'M';
    if (n >= 1_000) return (n / 1_000).toFixed(0) + 'K';
    return fmt(n);
};
const fmtPct = (n: number) => (n || 0).toFixed(2) + '%';

/** Trend arrow + colored percentage */
function TrendBadge({ value }: { value: number }) {
    if (value === 0 || value == null) return <span style={{ color: '#666', fontSize: 10 }}>—</span>;
    const isUp = value > 5;
    const isDown = value < -5;
    const color = isUp ? '#10b981' : isDown ? '#ef4444' : '#888';
    const arrow = isUp ? '▲' : isDown ? '▼' : '→';
    return (
        <span style={{ fontSize: 10, color, fontWeight: 600, whiteSpace: 'nowrap' }}>
            {arrow} {value > 0 ? '+' : ''}{value.toFixed(1)}%
        </span>
    );
}

/** Turnover days with special color coding */
function TurnoverCell({ days, trendPct, stocksWb }: { days: number; trendPct: number; stocksWb: number }) {
    let color = '#10b981'; // 11-39 green (normal)
    let bg = 'transparent';
    if (days === 0 && stocksWb === 0) {
        color = '#6b7280';
    } else if (days < 10 || days === 0) {
        color = '#ef4444'; // <10 red (running out)
        bg = 'rgba(239,68,68,0.08)';
    } else if (days >= 70) {
        color = '#ef4444'; // 70+ red (too much)
        bg = 'rgba(239,68,68,0.08)';
    } else if (days >= 40) {
        color = '#f59e0b'; // 40-69 yellow
        bg = 'rgba(245,158,11,0.08)';
    }

    return (
        <td style={{ textAlign: 'center', background: bg, borderBottom: '1px solid #f3f4f6' }}>
            <div style={{ fontWeight: 700, fontSize: 14, color }}>{days > 0 ? `${Math.round(days)} дн` : '—'}</div>
            <div style={{ fontSize: 10, color: '#6b7280' }}>{fmt(stocksWb)} шт</div>
            <TrendBadge value={trendPct} />
        </td>
    );
}

/** Metric cell: main value + trend underneath */
function MetricCell({ value, trend, format = 'number', color }: {
    value: number; trend: number; format?: 'number' | 'money' | 'pct' | 'compact';
    color?: string;
}) {
    let display: string;
    if (format === 'money') display = '₽' + fmtK(value);
    else if (format === 'pct') display = fmtPct(value);
    else if (format === 'compact') display = fmtK(value);
    else display = fmt(value);

    return (
        <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>
            <div style={{ fontWeight: 600, color: color || '#111827' }}>{display}</div>
            <TrendBadge value={trend} />
        </td>
    );
}

/* ─── Main page ──────────────────────────────────────────────── */

export default function TrendsPage() {
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [trendDays, setTrendDays] = useState(7);
    const [brand, setBrand] = useState('');
    const [search, setSearch] = useState('');
    const [filters, setFilters] = useState<{ brands: string[] }>({ brands: [] });
    const [sortField, setSortField] = useState('orders_sum_rub');
    const [sortAsc, setSortAsc] = useState(false);
    const [totalProducts, setTotalProducts] = useState(0);
    const [activeTab, setActiveTab] = useState<'table' | 'anomalies'>('table');
    const [anomalyCount, setAnomalyCount] = useState(0);

    const loadFilters = useCallback(async () => {
        try {
            const f = await api.getFunnelFilters();
            setFilters({ brands: f.brands || [] });
        } catch { }
    }, []);

    const loadData = useCallback(async () => {
        setLoading(true);
        try {
            const res = await api.getProductTrends({
                trend_days: trendDays,
                brand: brand || undefined,
                search: search || undefined,
            });
            setData(res.products || []);
            setTotalProducts(res.total_products || 0);
        } catch (err: any) {
            console.error('Trends error:', err);
        } finally {
            setLoading(false);
        }
    }, [trendDays, brand, search]);

    // Load anomaly count for badge
    const loadAnomalyCount = useCallback(async () => {
        try {
            const res = await api.getAnomalies({
                period_days: trendDays,
                brand: brand || undefined,
            });
            setAnomalyCount(res.anomalies.filter(a => a.severity === 'critical').length);
        } catch { }
    }, [trendDays, brand]);

    useEffect(() => { loadFilters(); }, [loadFilters]);
    useEffect(() => { loadData(); }, [loadData]);
    useEffect(() => { loadAnomalyCount(); }, [loadAnomalyCount]);

    // Sorting
    const handleSort = (field: string) => {
        if (sortField === field) {
            setSortAsc(!sortAsc);
        } else {
            setSortField(field);
            setSortAsc(false);
        }
    };

    const sortedData = [...data].sort((a, b) => {
        const av = a[sortField] ?? 0;
        const bv = b[sortField] ?? 0;
        return sortAsc ? av - bv : bv - av;
    });

    const SortTh = ({ field, children, bg, style }: { field: string; children: React.ReactNode; bg?: string; style?: React.CSSProperties }) => (
        <th
            onClick={() => handleSort(field)}
            style={{
                position: 'sticky', top: 0, background: bg || '#f9fafb', zIndex: 10,
                cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap', fontSize: 11,
                borderBottom: '1px solid #e5e7eb', color: '#4b5563',
                ...style,
            }}
        >
            {children} {sortField === field ? (sortAsc ? '↑' : '↓') : ''}
        </th>
    );

    // Column defs matching the screenshot
    const columns = [
        { field: 'orders_count', label: 'ЗАКАЗЫ', format: 'number' as const, trend: 'trend_orders' },
        { field: 'orders_sum_rub', label: 'ВЫРУЧКА', format: 'money' as const, trend: 'trend_revenue', subLabel: `${trendDays}д` },
        { field: 'open_card', label: 'ПЕРЕХОДЫ', format: 'compact' as const, trend: 'trend_open' },
        { field: 'add_to_cart_pct', label: 'КОНВ. ЗАКАЗ', format: 'pct' as const, trend: 'trend_add_to_cart_pct' },
        { field: 'cart_to_order_pct', label: 'КОНВ. КОРЗИНА', format: 'pct' as const, trend: 'trend_cart_to_order' },
        { field: 'margin', label: 'МАРЖА', format: 'pct' as const, trend: 'trend_margin' },
        { field: 'profit', label: 'ЧИСТАЯ ПР.', format: 'money' as const, trend: 'trend_profit' },
        { field: 'avg_price', label: 'СР. ЦЕНА', format: 'money' as const, trend: 'trend_avg_price' },
        { field: 'drr', label: 'ДРР', format: 'pct' as const, trend: 'trend_drr' },
        { field: 'adv_sum', label: 'РЕКЛАМА', format: 'money' as const, trend: 'trend_adv' },
        { field: 'ctr', label: 'CTR', format: 'pct' as const, trend: 'trend_ctr' },
    ];

    // Filter to only show items matching search client-side (already handled server-side, but also for instant filter)
    const displayData = search ? sortedData.filter(p =>
        (p.vendor_code || '').toLowerCase().includes(search.toLowerCase()) ||
        String(p.nm_id).includes(search)
    ) : sortedData;

    return (
        <div>
            <h1 style={{ fontSize: 22, fontWeight: 600, marginBottom: 16 }}>📈 Метрики и тренды</h1>

            {/* Tab switcher */}
            <div style={{ display: 'flex', gap: 4, marginBottom: 20 }}>
                <button
                    className={`btn btn-sm ${activeTab === 'table' ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setActiveTab('table')}
                >
                    Таблица трендов
                </button>
                <button
                    className={`btn btn-sm ${activeTab === 'anomalies' ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setActiveTab('anomalies')}
                    style={{ position: 'relative' }}
                >
                    Аномалии
                    {anomalyCount > 0 && (
                        <span style={{
                            position: 'absolute',
                            top: -6,
                            right: -6,
                            background: '#E02424',
                            color: '#fff',
                            fontSize: 10,
                            fontWeight: 700,
                            minWidth: 18,
                            height: 18,
                            borderRadius: 9,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            padding: '0 5px',
                        }}>
                            {anomalyCount}
                        </span>
                    )}
                </button>
            </div>

            {/* Anomalies tab */}
            {activeTab === 'anomalies' && (
                <AnomaliesTab
                    trendDays={trendDays}
                    brand={brand}
                    onTrendDaysChange={setTrendDays}
                    onBrandChange={setBrand}
                    brands={filters.brands}
                />
            )}

            {/* Table tab — existing content */}
            {activeTab === 'table' && (
                <>
                    {/* Controls */}
                    <div className="glass-card" style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '10px 16px', marginBottom: 16, flexWrap: 'wrap' }}>
                        {/* Trend period buttons */}
                        <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Тренд:</span>
                        {[7, 14, 30].map(d => (
                            <button key={d}
                                onClick={() => setTrendDays(d)}
                                style={{
                                    padding: '5px 14px', fontSize: 13, borderRadius: 6, cursor: 'pointer',
                                    border: trendDays === d ? '1px solid var(--color-accent)' : '1px solid var(--color-border)',
                                    background: trendDays === d ? 'rgba(139,92,246,0.15)' : 'var(--color-bg-card)',
                                    color: trendDays === d ? 'var(--color-accent)' : 'var(--color-text)',
                                    fontWeight: trendDays === d ? 600 : 400,
                                    transition: 'all 0.15s',
                                }}
                            >
                                {d} дней
                            </button>
                        ))}

                        <span style={{ width: 1, height: 24, background: 'var(--color-border)', margin: '0 4px' }} />

                        {/* Brand filter */}
                        <select value={brand} onChange={e => setBrand(e.target.value)}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '5px 8px', color: 'var(--color-text)', fontSize: 13 }}>
                            <option value="">Все бренды</option>
                            {filters.brands.map(b => <option key={b} value={b}>{b}</option>)}
                        </select>

                        {/* Search */}
                        <input placeholder="🔍 Поиск по артикулу..." value={search} onChange={e => setSearch(e.target.value)}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '5px 8px', color: 'var(--color-text)', fontSize: 13, width: 180 }} />

                        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--color-text-dim)' }}>
                            Показано: {displayData.length} из {totalProducts} товаров
                        </span>
                    </div>

                    {/* Info bar */}
                    <div style={{ marginBottom: 12, fontSize: 11, color: '#888', display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center' }}>
                        <span>📊 Считается по линейной регрессии за {trendDays} дней</span>
                        <span style={{ display: 'flex', gap: 8 }}>
                            <span>Порог: <span style={{ color: '#10b981' }}>&gt;+5%</span> — растёт,</span>
                            <span><span style={{ color: '#ef4444' }}>&lt;-5%</span> — падает,</span>
                            <span>иначе стабильно</span>
                        </span>
                    </div>

                    {/* Table */}
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                        {loading ? (
                            <div style={{ padding: 60, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                                <div style={{ fontSize: 24, marginBottom: 8 }}>⏳</div>
                                Загрузка данных...
                            </div>
                        ) : (
                            <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 260px)' }}>
                                <table className="data-table" style={{ minWidth: 1400, borderCollapse: 'separate', borderSpacing: 0 }}>
                                    <thead>
                                        <tr>
                                            <SortTh field="vendor_code" bg="#f9fafb" style={{ left: 0, zIndex: 22, borderRight: '1px solid #e5e7eb' }}>АРТИКУЛ</SortTh>
                                            <SortTh field="turnover_days" bg="#f9fafb">ОБОРАЧИВ.</SortTh>
                                            {columns.map(c => (
                                                <SortTh key={c.field} field={c.field} bg="#f9fafb">{c.label}</SortTh>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {displayData.length === 0 && (
                                            <tr><td colSpan={20} style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-dim)' }}>
                                                Нет данных за выбранный период
                                            </td></tr>
                                        )}
                                        {displayData.map((p, i) => {
                                            const rowBg = i % 2 === 0 ? '#ffffff' : '#f9fafb';
                                            return (
                                                <tr key={p.nm_id} style={{ background: rowBg, color: '#111827' }}>
                                                    {/* Article */}
                                                    <td style={{ position: 'sticky', left: 0, background: rowBg, zIndex: 11, borderRight: '1px solid #e5e7eb', borderBottom: '1px solid #f3f4f6' }}>
                                                        <div style={{ fontWeight: 600, fontSize: 12, maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: '#111827' }}>
                                                            {p.vendor_code}
                                                        </div>
                                                        <div style={{ fontSize: 10, color: '#6b7280' }}>
                                                            {p.brand} · {p.subject}
                                                        </div>
                                                    </td>

                                                {/* Turnover */}
                                                <TurnoverCell days={p.turnover_days} trendPct={p.trend_turnover} stocksWb={p.stocks_wb} />

                                                    {/* All metric columns */}
                                                    {columns.map(c => {
                                                        let cellColor: string | undefined;
                                                        if (c.field === 'drr') {
                                                            cellColor = p.drr > 30 ? '#ef4444' : p.drr > 15 ? '#f59e0b' : p.drr > 0 ? '#10b981' : '#6b7280';
                                                        } else if (c.field === 'margin') {
                                                            cellColor = p.margin > 20 ? '#10b981' : p.margin > 0 ? '#a3e635' : '#ef4444';
                                                        } else if (c.field === 'profit') {
                                                            cellColor = p.profit > 0 ? '#10b981' : '#ef4444';
                                                        }
                                                        return (
                                                            <MetricCell
                                                                key={c.field}
                                                                value={p[c.field] ?? 0}
                                                                trend={p[c.trend] ?? 0}
                                                                format={c.format}
                                                                color={cellColor}
                                                            />
                                                        );
                                                    })}
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>

                    <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-dim)' }}>
                        Всего товаров: {totalProducts} · Период: {trendDays} дней · Сортировка: {sortField} {sortAsc ? '↑' : '↓'}
                    </div>
                </>
            )}
        </div>
    );
}

'use client';
import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import { WarehouseNeedView } from './WarehouseNeedView';
import { WarehouseExclusionSettings } from './WarehouseExclusionSettings';

/* ─── Types ──────────────────────────────────────────────── */
interface Article {
    nm_id: number;
    vendor_code: string;
    subject: string;
    brand: string;
    orders_30d: number;
    trend_pct: number;
    avg_daily: number;
    stocks_wb: number;
    days_left: number;
    traffic_light: string;
    forecast: number[];
    stocks_rf?: number;
    in_assembly?: number;
    in_transit?: number;
    wb_buyout_pct?: number;
    revenue_bdr?: number;
}

interface StockData {
    articles: Article[];
    dates: string[];
    total_articles: number;
    orders_30d: number;
    avg_daily: number;
    data_date: string;
    most_critical: { article: string; days_left: number } | null;
    traffic_light: { red: number; orange: number; yellow: number; green: number };
    subjects: string[];
    brands: string[];
}

/* ─── Helpers ────────────────────────────────────────────── */
const shortDate = (d: string) => {
    const dt = new Date(d);
    const days = ['ВС', 'ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ'];
    return `${days[dt.getDay()]}, ${String(dt.getDate()).padStart(2, '0')}.${String(dt.getMonth() + 1).padStart(2, '0')}`;
};

const daysLeftBadge = (dl: number) => {
    let bg: string, color: string;
    if (dl < 7) { bg = '#ff4444'; color = '#fff'; }
    else if (dl <= 14) { bg = '#ff9800'; color = '#fff'; }
    else if (dl <= 29) { bg = '#ffd600'; color = '#333'; }
    else { bg = '#4caf50'; color = '#fff'; }
    return (
        <span style={{
            display: 'inline-block', padding: '2px 8px', borderRadius: 8,
            fontWeight: 700, fontSize: 11, minWidth: 30, textAlign: 'center',
            background: bg, color,
        }}>{dl}</span>
    );
};

const forecastCellStyle = (projected: number, avgDaily: number): React.CSSProperties => {
    if (projected <= 0) return { color: '#ff4444', background: 'rgba(255,68,68,0.12)', fontWeight: 700 };
    const daysOfStock = avgDaily > 0 ? projected / avgDaily : 999;
    if (daysOfStock < 7) return { color: '#ff4444', background: 'rgba(255,68,68,0.06)' };
    if (daysOfStock < 14) return { color: '#ff9800' };
    return {};
};

type SortKey = 'orders_30d' | 'avg_daily' | 'stocks_wb' | 'days_left' | 'vendor_code' | 'revenue_bdr' | 'stocks_rf' | 'in_assembly' | 'in_transit';

/* ─── Main Component ─────────────────────────────────────── */
export function StockAnalytics() {
    const [subView, setSubView] = useState<'articles' | 'need' | 'settings'>('articles');
    const [data, setData] = useState<StockData | null>(null);
    const [loading, setLoading] = useState(false);
    const [trendDays, setTrendDays] = useState(7);
    const [subjectFilter, setSubjectFilter] = useState('');
    const [brandFilter, setBrandFilter] = useState('');
    const [articleFilter, setArticleFilter] = useState('');
    const [trafficFilter, setTrafficFilter] = useState<string | null>(null);
    const [page, setPage] = useState(0);
    const [perPage, setPerPage] = useState(25);
    const [sortKey, setSortKey] = useState<SortKey>('days_left');
    const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
    const [stockMode, setStockMode] = useState<'wb' | 'wb_rf' | 'wb_rf_transit'>('wb');

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const res = await api.getStockAnalytics(
                trendDays,
                subjectFilter || undefined,
                brandFilter || undefined,
                articleFilter || undefined,
                stockMode,
            );
            setData(res);
            setPage(0);
        } catch { /* empty */ }
        setLoading(false);
    }, [trendDays, subjectFilter, brandFilter, articleFilter, stockMode]);

    useEffect(() => { load(); }, [trendDays, subjectFilter, brandFilter, stockMode]);

    // Debounced article search
    const [searchTimeout, setSearchTimeout] = useState<ReturnType<typeof setTimeout> | null>(null);
    const handleArticleSearch = (val: string) => {
        setArticleFilter(val);
        if (searchTimeout) clearTimeout(searchTimeout);
        setSearchTimeout(setTimeout(() => load(), 500));
    };

    // Sort + filter
    const filteredArticles = useMemo(() => {
        if (!data?.articles) return [];
        let list = trafficFilter === 'no_wb'
            ? data.articles.filter(a => a.stocks_wb === 0 && (a.stocks_rf ?? 0) > 0 && (a.in_transit ?? 0) === 0)
            : trafficFilter
                ? data.articles.filter(a => a.traffic_light === trafficFilter)
                : data.articles;

        list = [...list].sort((a, b) => {
            const av = (a[sortKey] ?? 0);
            const bv = (b[sortKey] ?? 0);
            if (typeof av === 'string' && typeof bv === 'string') {
                return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
            }
            return sortDir === 'asc' ? (av as number) - (bv as number) : (bv as number) - (av as number);
        });
        return list;
    }, [data, trafficFilter, sortKey, sortDir]);

    // Count articles on RF but not on WB
    const noWbCount = useMemo(() => {
        if (!data?.articles) return 0;
        return data.articles.filter(a => a.stocks_wb === 0 && (a.stocks_rf ?? 0) > 0 && (a.in_transit ?? 0) === 0).length;
    }, [data]);

    const totalPages = Math.ceil(filteredArticles.length / perPage);
    const pageArticles = filteredArticles.slice(page * perPage, (page + 1) * perPage);

    // Totals
    const totals = useMemo(() => ({
        orders_30d: filteredArticles.reduce((s, a) => s + a.orders_30d, 0),
        avg_daily: filteredArticles.reduce((s, a) => s + a.avg_daily, 0),
        stocks_wb: filteredArticles.reduce((s, a) => s + a.stocks_wb, 0),
        forecast: (data?.dates || []).map((_, idx) =>
            filteredArticles.reduce((s, a) => s + ((a.forecast || [])[idx] ?? 0), 0)),
    }), [filteredArticles, data?.dates]);

    const handleSort = (key: SortKey) => {
        if (sortKey === key) {
            setSortDir(d => d === 'asc' ? 'desc' : 'asc');
        } else {
            setSortKey(key);
            setSortDir(key === 'vendor_code' ? 'asc' : 'desc');
        }
        setPage(0);
    };

    const sortIcon = (key: SortKey) => {
        if (sortKey !== key) return '';
        return sortDir === 'asc' ? ' ↑' : ' ↓';
    };

    const handleExport = () => {
        const rows = filteredArticles.map(a => {
            const row: Record<string, unknown> = {
                'Артикул': a.vendor_code,
                'Предмет': a.subject,
                'Бренд': a.brand,
                'Заказы 30д': a.orders_30d,
                [`Реализ. БДР ${trendDays}д, ₽`]: a.revenue_bdr ?? 0,
                'Тренд %': a.trend_pct,
                [`Ср ${trendDays}д`]: a.avg_daily,
                'Остатки WB': a.stocks_wb,
            };
            if (stockMode !== 'wb') row['Остатки РФ'] = a.stocks_rf ?? 0;
            if (stockMode === 'wb_rf_transit') {
                row['На сборке'] = a.in_assembly ?? 0;
                row['В пути'] = a.in_transit ?? 0;
            }
            row['Хватит дн'] = a.days_left;
            (data?.dates || []).forEach((d, i) => {
                row[d] = (a.forecast || [])[i] ?? 0;
            });
            return row;
        });
        exportToExcel(rows, `stock_forecast_${stockMode}_${trendDays}d`);
    };

    /* ─── Sub-views ──────────────────────────────────────── */
    if (subView === 'need') return <WarehouseNeedView />;
    if (subView === 'settings') return <WarehouseExclusionSettings />;

    if (loading && !data) {
        return (
            <div className="glass-card" style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-muted)' }}>
                <div className="spinner" style={{ margin: '0 auto 12px' }} />
                Загрузка аналитики остатков...
            </div>
        );
    }

    if (!data || !data.articles) {
        return (
            <div className="glass-card">
                <div className="empty-state">
                    <div className="empty-state-icon">📦</div>
                    <div className="empty-state-text">Нет данных. Синхронизируйте воронку продаж.</div>
                </div>
            </div>
        );
    }

    const tl = data.traffic_light || { red: 0, orange: 0, yellow: 0, green: 0 };
    const trafficColors: Record<string, { bg: string; text: string; label: string }> = {
        red: { bg: '#ff4444', text: '#fff', label: `< 7 дн (критично) ${tl.red}` },
        orange: { bg: '#ff9800', text: '#fff', label: `8–14 дн (опасно) ${tl.orange}` },
        yellow: { bg: '#ffd600', text: '#333', label: `15–29 дн (норма) ${tl.yellow}` },
        green: { bg: '#4caf50', text: '#fff', label: `≥ 30 дн (избыток) ${tl.green}` },
    };

    const thStyle = (key: SortKey, align: 'left' | 'right' | 'center' = 'right'): React.CSSProperties => ({
        textAlign: align,
        cursor: 'pointer',
        userSelect: 'none',
        whiteSpace: 'nowrap',
    });

    const stickyTh: React.CSSProperties = {
        position: 'sticky', left: 0, background: 'var(--color-bg-secondary)',
        zIndex: 2, minWidth: 170, cursor: 'pointer', userSelect: 'none',
        boxShadow: '2px 0 4px rgba(0,0,0,0.05)',
    };

    const stickyTd: React.CSSProperties = {
        position: 'sticky', left: 0, background: '#f5f5f7', zIndex: 1, fontWeight: 600,
        boxShadow: '2px 0 4px rgba(0,0,0,0.05)',
    };

    return (
        <div>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
                <div>
                    <span style={{ fontSize: 13, opacity: 0.6 }}>{data.total_articles} артикулов · данные на {data.data_date}</span>
                </div>
                <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                    <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                        <span style={{ fontSize: 13, opacity: 0.6, marginRight: 4 }}>Остатки</span>
                        {([
                            { key: 'wb' as const, label: 'WB' },
                            { key: 'wb_rf' as const, label: 'WB + РФ' },
                            { key: 'wb_rf_transit' as const, label: 'WB + РФ + Поставки' },
                        ]).map(m => (
                            <button key={m.key} className={`btn btn-sm ${stockMode === m.key ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setStockMode(m.key)}>{m.label}</button>
                        ))}
                    </div>
                    <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                        <span style={{ fontSize: 13, opacity: 0.6, marginRight: 4 }}>Прогноз</span>
                        {[7, 14, 30].map(d => (
                            <button key={d} className={`btn btn-sm ${trendDays === d ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setTrendDays(d)}>{d}д</button>
                        ))}
                    </div>
                </div>
            </div>

            {/* Sub-view toggle */}
            <div style={{ display: 'flex', gap: 4, marginBottom: 16 }}>
                {[
                    { key: 'articles' as const, label: '📋 По артикулам' },
                    { key: 'need' as const, label: '📦 Потребность' },
                    { key: 'settings' as const, label: '⚙️ Настройки' },
                ].map(v => (
                    <button key={v.key}
                        className={`btn btn-sm ${subView === v.key ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setSubView(v.key)}>{v.label}</button>
                ))}
            </div>

            {/* Filters */}
            <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
                <select className="input" style={{ maxWidth: 180, fontSize: 13 }} value={subjectFilter} onChange={e => setSubjectFilter(e.target.value)}>
                    <option value="">Предмет: Все</option>
                    {(data.subjects || []).map((s: string) => <option key={s} value={s}>{s}</option>)}
                </select>
                <select className="input" style={{ maxWidth: 180, fontSize: 13 }} value={brandFilter} onChange={e => setBrandFilter(e.target.value)}>
                    <option value="">Бренд: Все</option>
                    {(data.brands || []).map((b: string) => <option key={b} value={b}>{b}</option>)}
                </select>
                <input className="input" style={{ maxWidth: 200, fontSize: 13 }} placeholder="🔍 Поиск артикула" value={articleFilter}
                    onChange={e => handleArticleSearch(e.target.value)} />
                <button className="btn btn-sm btn-secondary" onClick={handleExport} style={{ marginLeft: 'auto' }}>📥 Excel</button>
            </div>

            {/* KPI Cards */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12, marginBottom: 16 }}>
                <div className="glass-card" style={{ padding: '14px 16px', borderLeft: '4px solid var(--color-primary)' }}>
                    <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 4 }}>ЗАКАЗЫ ЗА 30 ДНЕЙ</div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>{formatNumber(data.orders_30d)}</div>
                    <div style={{ fontSize: 12, opacity: 0.5, marginTop: 2 }}>~ {data.total_articles} артикулов</div>
                </div>
                <div className="glass-card" style={{ padding: '14px 16px', borderLeft: '4px solid #4caf50' }}>
                    <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 4 }}>СРЕДНЕЕ ЗА {trendDays} ДНЕЙ</div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>{formatNumber(data.avg_daily)}</div>
                    <div style={{ fontSize: 12, opacity: 0.5, marginTop: 2 }}>заказов/день (сумма)</div>
                </div>
                <div className="glass-card" style={{ padding: '14px 16px', borderLeft: '4px solid #ff4444' }}>
                    <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 4 }}>КРИТИЧЕСКИХ ОСТАТКОВ</div>
                    <div style={{ fontSize: 22, fontWeight: 700, color: tl.red > 0 ? '#ff4444' : undefined }}>{tl.red}</div>
                    <div style={{ fontSize: 12, opacity: 0.5, marginTop: 2 }}>≤ 7 дней до обнуления</div>
                </div>
                <div className="glass-card" style={{ padding: '14px 16px', borderLeft: '4px solid #ff9800' }}>
                    <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 4 }}>САМЫЙ КРИТИЧНЫЙ</div>
                    <div style={{ fontSize: 16, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {data.most_critical ? data.most_critical.article : '—'}
                    </div>
                    <div style={{ fontSize: 12, opacity: 0.5, marginTop: 2 }}>
                        {data.most_critical ? `${data.most_critical.days_left} дн до обнуления` : 'нет критичных'}
                    </div>
                </div>
            </div>

            {/* Traffic light filter */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, opacity: 0.6 }}>Светофор</span>
                <button className={`btn btn-sm ${!trafficFilter ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => { setTrafficFilter(null); setPage(0); }}>Все ({data.total_articles})</button>
                {Object.entries(trafficColors).map(([key, c]) => (
                    <button key={key}
                        style={{
                            padding: '4px 12px', borderRadius: 12, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: 'none',
                            background: trafficFilter === key ? c.bg : 'var(--color-bg-tertiary)',
                            color: trafficFilter === key ? c.text : 'var(--text-primary)',
                            opacity: trafficFilter === key ? 1 : 0.7,
                        }}
                        onClick={() => { setTrafficFilter(trafficFilter === key ? null : key); setPage(0); }}>
                        {c.label}
                    </button>
                ))}
                {stockMode !== 'wb' && noWbCount > 0 && (
                    <button
                        style={{
                            padding: '4px 12px', borderRadius: 12, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: 'none',
                            background: trafficFilter === 'no_wb' ? '#ff9f0a' : 'var(--color-bg-tertiary)',
                            color: trafficFilter === 'no_wb' ? '#fff' : 'var(--text-primary)',
                            opacity: trafficFilter === 'no_wb' ? 1 : 0.7,
                        }}
                        onClick={() => { setTrafficFilter(trafficFilter === 'no_wb' ? null : 'no_wb'); setPage(0); }}>
                        ⚠️ Нет на WB ({noWbCount})
                    </button>
                )}
            </div>

            {/* Table */}
            <div className="glass-card" style={{ overflow: 'auto', padding: 0 }}>
                {/* TODO: migrate to TanStackDataTable — has sticky columns, dynamic forecast columns, custom pagination, TOTAL row */}
                <table className="data-table" style={{ fontSize: 12, width: '100%' }}>
                    <thead>
                        <tr>
                            <th style={stickyTh} onClick={() => handleSort('vendor_code')}>
                                АРТИКУЛ{sortIcon('vendor_code')}
                            </th>
                            <th style={thStyle('orders_30d')} onClick={() => handleSort('orders_30d')}>
                                ЗАКАЗЫ 30Д{sortIcon('orders_30d')}
                            </th>
                            <th style={thStyle('revenue_bdr')} onClick={() => handleSort('revenue_bdr')}
                                title={`Реализация (БДР) за ${trendDays}д — продажи минус возвраты по retail_price_withdisc_rub.`}>
                                РЕАЛИЗ. БДР {trendDays}Д{sortIcon('revenue_bdr')}
                            </th>
                            <th style={{ textAlign: 'right' }}>ТРЕНД</th>
                            <th style={thStyle('avg_daily')} onClick={() => handleSort('avg_daily')}>
                                СР {trendDays}Д{sortIcon('avg_daily')}
                            </th>
                            <th style={thStyle('stocks_wb')} onClick={() => handleSort('stocks_wb')}
                                title="Полезный остаток WB = на складе + от клиента + к клиенту × (1 − выкуп%). Выкуп% берётся средний за выбранный период.">
                                WB{sortIcon('stocks_wb')}
                            </th>
                            {stockMode !== 'wb' && (
                                <th style={thStyle('stocks_rf')} onClick={() => handleSort('stocks_rf')}>
                                    РФ{sortIcon('stocks_rf')}
                                </th>
                            )}
                            {stockMode === 'wb_rf_transit' && (
                                <th style={thStyle('in_assembly')} onClick={() => handleSort('in_assembly')}
                                    title="В заявках на сборку (PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED). Вычтено из РФ.">
                                    НА СБОРКЕ{sortIcon('in_assembly')}
                                </th>
                            )}
                            {stockMode === 'wb_rf_transit' && (
                                <th style={thStyle('in_transit')} onClick={() => handleSort('in_transit')}>
                                    В ПУТИ{sortIcon('in_transit')}
                                </th>
                            )}
                            <th style={thStyle('days_left', 'center')} onClick={() => handleSort('days_left')}>
                                ХВАТИТ ДН{sortIcon('days_left')}
                            </th>
                            {(data.dates || []).map((d: string) => (
                                <th key={d} style={{ textAlign: 'right', fontSize: 10, whiteSpace: 'nowrap' }}>
                                    {shortDate(d)}
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {/* ИТОГО */}
                        <tr style={{ fontWeight: 700, background: 'var(--color-bg-tertiary)' }}>
                            <td style={{ ...stickyTd, background: 'var(--color-bg-tertiary)' }}>ИТОГО</td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(totals.orders_30d, 0)}</td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(filteredArticles.reduce((s, a) => s + (a.revenue_bdr ?? 0), 0), 0)}{' ₽'}</td>
                            <td></td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(totals.avg_daily)}</td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(totals.stocks_wb, 0)}</td>
                            {stockMode !== 'wb' && (
                                <td style={{ textAlign: 'right' }}>{formatNumber(filteredArticles.reduce((s, a) => s + (a.stocks_rf ?? 0), 0), 0)}</td>
                            )}
                            {stockMode === 'wb_rf_transit' && (
                                <td style={{ textAlign: 'right' }}>{formatNumber(filteredArticles.reduce((s, a) => s + (a.in_assembly ?? 0), 0), 0)}</td>
                            )}
                            {stockMode === 'wb_rf_transit' && (
                                <td style={{ textAlign: 'right' }}>{formatNumber(filteredArticles.reduce((s, a) => s + (a.in_transit ?? 0), 0), 0)}</td>
                            )}
                            <td></td>
                            {totals.forecast.map((v, i) => (
                                <td key={i} style={{ textAlign: 'right' }}>{formatNumber(v, 0)}</td>
                            ))}
                        </tr>

                        {/* Data rows */}
                        {pageArticles.map(a => {
                            const isNoWb = stockMode !== 'wb' && a.stocks_wb === 0 && (a.stocks_rf ?? 0) > 0 && (a.in_transit ?? 0) === 0;
                            const rowBg = isNoWb ? 'rgba(255, 159, 10, 0.08)' : undefined;
                            return (
                            <tr key={a.nm_id} style={rowBg ? { background: rowBg } : undefined}>
                                <td style={{ ...stickyTd, ...(isNoWb ? { background: 'rgba(255, 239, 204, 1)' } : {}) }}>
                                    {isNoWb && <span title="Есть на РФ, нет на WB">⚠️ </span>}
                                    {a.vendor_code || `#${a.nm_id}`}
                                </td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(a.orders_30d, 0)}</td>
                                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                    {(a.revenue_bdr ?? 0) > 0 ? formatNumber(a.revenue_bdr ?? 0, 0) + ' ₽' : '—'}
                                </td>
                                <td style={{
                                    textAlign: 'right',
                                    color: a.trend_pct > 0 ? '#4caf50' : a.trend_pct < 0 ? '#ff4444' : undefined,
                                }}>
                                    {a.trend_pct > 0 ? '↑' : a.trend_pct < 0 ? '↓' : ''}{a.trend_pct}%
                                </td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(a.avg_daily)}</td>
                                <td style={{ textAlign: 'right' }}
                                    title={a.wb_buyout_pct !== undefined ? `Выкуп ${a.wb_buyout_pct}% (за ${trendDays}д)` : undefined}>
                                    {formatNumber(a.stocks_wb, 0)}
                                </td>
                                {stockMode !== 'wb' && (
                                    <td style={{ textAlign: 'right', color: (a.stocks_rf ?? 0) > 0 ? '#4caf50' : undefined }}>
                                        {formatNumber(a.stocks_rf ?? 0, 0)}
                                    </td>
                                )}
                                {stockMode === 'wb_rf_transit' && (
                                    <td style={{ textAlign: 'right', color: (a.in_assembly ?? 0) > 0 ? '#ff9f0a' : undefined }} title="В заявках на сборку (ещё на РФ-складе)">
                                        {formatNumber(a.in_assembly ?? 0, 0)}
                                    </td>
                                )}
                                {stockMode === 'wb_rf_transit' && (
                                    <td style={{ textAlign: 'right', color: (a.in_transit ?? 0) > 0 ? '#2196f3' : undefined }}>
                                        {formatNumber(a.in_transit ?? 0, 0)}
                                    </td>
                                )}
                                <td style={{ textAlign: 'center' }}>{daysLeftBadge(a.days_left)}</td>
                                {(data.dates || []).map((d: string, idx: number) => {
                                    const projected = (a.forecast || [])[idx] ?? 0;
                                    return (
                                        <td key={d} style={{ textAlign: 'right', ...forecastCellStyle(projected, a.avg_daily) }}>
                                            {formatNumber(projected, 0)}
                                        </td>
                                    );
                                })}
                            </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>

            {/* Pagination */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12, fontSize: 13 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4, opacity: 0.7 }}>
                    <span>{filteredArticles.length} артикулов ·</span>
                    {[25, 50, 100].map(n => (
                        <button key={n}
                            className={`btn btn-sm ${perPage === n ? 'btn-primary' : 'btn-secondary'}`}
                            style={{ padding: '2px 8px', fontSize: 11 }}
                            onClick={() => { setPerPage(n); setPage(0); }}>{n}</button>
                    ))}
                    <span>строк</span>
                </div>
                {totalPages > 1 && (
                    <div style={{ display: 'flex', gap: 4 }}>
                        <button className="btn btn-sm btn-secondary" disabled={page === 0} onClick={() => setPage(p => p - 1)}>←</button>
                        <span style={{ padding: '4px 8px', fontSize: 12, opacity: 0.7 }}>
                            {page + 1} / {totalPages}
                        </span>
                        <button className="btn btn-sm btn-secondary" disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>→</button>
                    </div>
                )}
            </div>
        </div>
    );
}

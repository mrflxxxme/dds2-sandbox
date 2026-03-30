'use client';
import { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import { WarehouseStocksView } from './WarehouseStocksView';
import { WarehouseNeedView } from './WarehouseNeedView';
import { WarehouseExclusionSettings } from './WarehouseExclusionSettings';

export function StockAnalytics() {
    const [subView, setSubView] = useState<'articles' | 'warehouses' | 'need' | 'settings'>('articles');
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [trendDays, setTrendDays] = useState(7);
    const [subjectFilter, setSubjectFilter] = useState('');
    const [brandFilter, setBrandFilter] = useState('');
    const [articleFilter, setArticleFilter] = useState('');
    const [trafficFilter, setTrafficFilter] = useState<string | null>(null);
    const [page, setPage] = useState(0);
    const [perPage, setPerPage] = useState(25);

    const load = async () => {
        setLoading(true);
        try {
            const res = await api.getStockAnalytics(
                trendDays,
                subjectFilter || undefined,
                brandFilter || undefined,
                articleFilter || undefined,
            );
            setData(res);
            setPage(0);
        } catch { }
        setLoading(false);
    };

    useEffect(() => { load(); }, [trendDays, subjectFilter, brandFilter]);

    // Debounced article search
    const [searchTimeout, setSearchTimeout] = useState<any>(null);
    const handleArticleSearch = (val: string) => {
        setArticleFilter(val);
        if (searchTimeout) clearTimeout(searchTimeout);
        setSearchTimeout(setTimeout(() => load(), 500));
    };

    if (subView === 'warehouses') return <WarehouseStocksView />;
    if (subView === 'need') return <WarehouseNeedView />;
    if (subView === 'settings') return <WarehouseExclusionSettings />;

    if (loading && !data) return <div className="glass-card" style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-muted)' }}>Загрузка аналитики остатков...</div>;

    if (!data || !data.articles) return <div className="glass-card"><div className="empty-state"><div className="empty-state-text">Нет данных. Синхронизируйте воронку продаж.</div></div></div>;

    const filteredArticles = trafficFilter
        ? data.articles.filter((a: any) => a.traffic_light === trafficFilter)
        : data.articles;

    const totalPages = Math.ceil(filteredArticles.length / perPage);
    const pageArticles = filteredArticles.slice(page * perPage, (page + 1) * perPage);

    const tl = data.traffic_light || { red: 0, orange: 0, yellow: 0, green: 0 };

    const trafficColors: Record<string, { bg: string; text: string; label: string }> = {
        red: { bg: '#ff4444', text: '#fff', label: `< 7 дн (критично) ${tl.red}` },
        orange: { bg: '#ff9800', text: '#fff', label: `8–14 дн (опасно) ${tl.orange}` },
        yellow: { bg: '#ffd600', text: '#333', label: `15–29 дн (норма) ${tl.yellow}` },
        green: { bg: '#4caf50', text: '#fff', label: `≥ 30 дн (избыток) ${tl.green}` },
    };

    const shortDate = (d: string) => {
        const dt = new Date(d);
        const days = ['ВС', 'ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ'];
        return `${days[dt.getDay()]}, ${String(dt.getDate()).padStart(2, '0')}.${String(dt.getMonth() + 1).padStart(2, '0')}`;
    };

    const daysLeftBg = (dl: number) => {
        if (dl < 7) return '#ff4444';
        if (dl <= 14) return '#ff9800';
        if (dl <= 29) return '#ffd600';
        return '#4caf50';
    };

    const daysLeftColor = (dl: number) => dl <= 14 ? '#fff' : (dl <= 29 ? '#333' : '#fff');

    const forecastCellStyle = (projected: number, avgDaily: number) => {
        if (projected <= 0) return { color: '#ff4444', background: 'rgba(255,68,68,0.12)', fontWeight: 700 };
        const daysOfStock = avgDaily > 0 ? projected / avgDaily : 999;
        if (daysOfStock < 7) return { color: '#ff4444', background: 'rgba(255,68,68,0.06)' };
        if (daysOfStock < 14) return { color: '#ff9800' };
        return {};
    };

    const handleExport = () => {
        const rows = filteredArticles.map((a: any) => {
            const row: Record<string, any> = {
                'Артикул': a.vendor_code,
                'Предмет': a.subject,
                'Бренд': a.brand,
                'Заказы 30д': a.orders_30d,
                'Тренд %': a.trend_pct,
                [`Ср ${trendDays}д`]: a.avg_daily,
                'Остатки': a.stocks_wb,
                'Хватит дн': a.days_left,
            };
            (data.dates || []).forEach((d: string, i: number) => {
                row[d] = (a.forecast || [])[i] ?? 0;
            });
            return row;
        });
        exportToExcel(rows, `stock_forecast_${trendDays}d`);
    };

    return (
        <div>
            {/* Header + Sub-view toggle */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div>
                    <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>📦 Аналитика остатков</h2>
                    <span style={{ fontSize: 13, opacity: 0.6 }}>{data.total_articles} артикулов · данные на {data.data_date}</span>
                </div>
                <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                    <span style={{ fontSize: 13, opacity: 0.6, marginRight: 8 }}>Горизонт прогноза</span>
                    {[7, 14, 30].map(d => (
                        <button key={d} className={`btn btn-sm ${trendDays === d ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setTrendDays(d)}>{d}д</button>
                    ))}
                </div>
            </div>

            {/* Sub-view toggle */}
            <div style={{ display: 'flex', gap: 4, marginBottom: 16 }}>
                {[
                    { key: 'articles' as const, label: '📋 По артикулам' },
                    { key: 'warehouses' as const, label: '🏭 По складам' },
                    { key: 'need' as const, label: '📦 Потребность' },
                    { key: 'settings' as const, label: '⚙️ Настройки' },
                ].map(v => (
                    <button key={v.key}
                        className={`btn btn-sm ${subView === v.key ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setSubView(v.key)}>{v.label}</button>
                ))}
            </div>

            {/* Filters */}
            <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
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
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
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

            {/* Traffic light filter pills */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, opacity: 0.6 }}>Светофор запасов</span>
                <button className={`btn btn-sm ${!trafficFilter ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setTrafficFilter(null)}>Все ({data.total_articles})</button>
                {Object.entries(trafficColors).map(([key, c]) => (
                    <button key={key}
                        style={{
                            padding: '4px 12px', borderRadius: 12, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: 'none',
                            background: trafficFilter === key ? c.bg : 'var(--color-bg-tertiary)',
                            color: trafficFilter === key ? c.text : 'var(--text-primary)',
                            opacity: trafficFilter === key ? 1 : 0.7,
                        }}
                        onClick={() => setTrafficFilter(trafficFilter === key ? null : key)}>
                        {c.label}
                    </button>
                ))}
            </div>

            {/* Table */}
            {/* TODO: migrate to TanStackDataTable — complex table with sticky columns, custom pagination, hover effects, ИТОГО row */}
            <div className="glass-card" style={{ overflow: 'auto', padding: 0 }}>
                <table className="data-table" style={{ fontSize: 12, width: '100%' }}>
                    <thead>
                        <tr>
                            <th style={{ position: 'sticky', left: 0, background: 'var(--color-bg-secondary)', zIndex: 2, minWidth: 170 }}>АРТИКУЛ</th>
                            <th style={{ textAlign: 'right', minWidth: 80 }}>ЗАКАЗЫ 30Д</th>
                            <th style={{ textAlign: 'right', minWidth: 65 }}>ТРЕНД</th>
                            <th style={{ textAlign: 'right', minWidth: 60 }}>СР {trendDays}Д</th>
                            <th style={{ textAlign: 'right', minWidth: 65 }}>ОСТАТКИ</th>
                            <th style={{ textAlign: 'center', minWidth: 75 }}>ХВАТИТ ДН</th>
                            {(data.dates || []).map((d: string) => (
                                <th key={d} style={{ textAlign: 'right', minWidth: 60, fontSize: 10, whiteSpace: 'nowrap' }}>{shortDate(d)}</th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {/* ИТОГО row */}
                        <tr style={{ fontWeight: 700, background: 'var(--color-bg-tertiary)' }}>
                            <td style={{ position: 'sticky', left: 0, background: 'var(--color-bg-tertiary)', zIndex: 1 }}>ИТОГО</td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(filteredArticles.reduce((s: number, a: any) => s + a.orders_30d, 0))}</td>
                            <td></td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(filteredArticles.reduce((s: number, a: any) => s + a.avg_daily, 0))}</td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(filteredArticles.reduce((s: number, a: any) => s + a.stocks_wb, 0))}</td>
                            <td></td>
                            {(data.dates || []).map((d: string, idx: number) => {
                                const dayTotal = filteredArticles.reduce((s: number, a: any) => {
                                    return s + ((a.forecast || [])[idx] ?? 0);
                                }, 0);
                                return <td key={d} style={{ textAlign: 'right' }}>{formatNumber(dayTotal, 0)}</td>;
                            })}
                        </tr>
                        {pageArticles.map((a: any) => (
                            <tr key={a.nm_id} style={{ transition: 'background 0.15s' }}
                                onMouseEnter={e => {
                                    e.currentTarget.style.background = 'var(--color-bg-tertiary)';
                                    const stickyTd = e.currentTarget.querySelector('td') as HTMLElement;
                                    if (stickyTd) stickyTd.style.background = 'var(--color-bg-tertiary)';
                                }}
                                onMouseLeave={e => {
                                    e.currentTarget.style.background = '';
                                    const stickyTd = e.currentTarget.querySelector('td') as HTMLElement;
                                    if (stickyTd) stickyTd.style.background = '#f5f5f7';
                                }}>
                                <td style={{ position: 'sticky', left: 0, background: '#f5f5f7', zIndex: 1, fontWeight: 600, boxShadow: '2px 0 4px rgba(0,0,0,0.05)' }}>
                                    {a.vendor_code || `#${a.nm_id}`}
                                </td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(a.orders_30d)}</td>
                                <td style={{ textAlign: 'right', color: a.trend_pct > 0 ? '#4caf50' : a.trend_pct < 0 ? '#ff4444' : undefined }}>
                                    {a.trend_pct > 0 ? '↑' : a.trend_pct < 0 ? '↓' : ''}{a.trend_pct}%
                                </td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(a.avg_daily)}</td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(a.stocks_wb)}</td>
                                <td style={{ textAlign: 'center' }}>
                                    <span style={{
                                        display: 'inline-block', padding: '2px 8px', borderRadius: 8,
                                        fontWeight: 700, fontSize: 11, minWidth: 30,
                                        background: daysLeftBg(a.days_left), color: daysLeftColor(a.days_left),
                                    }}>{a.days_left}</span>
                                </td>
                                {(data.dates || []).map((d: string, idx: number) => {
                                    const projected = (a.forecast || [])[idx] ?? 0;
                                    const style = forecastCellStyle(projected, a.avg_daily);
                                    return (
                                        <td key={d} style={{
                                            textAlign: 'right',
                                            ...style,
                                        }}>
                                            {formatNumber(projected, 0)}
                                        </td>
                                    );
                                })}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {/* Pagination */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12, fontSize: 13 }}>
                <div style={{ opacity: 0.6 }}>
                    {filteredArticles.length} артикулов
                    {[25, 50, 100].map(n => (
                        <button key={n} className={`btn btn-sm ${perPage === n ? 'btn-primary' : 'btn-secondary'}`}
                            style={{ marginLeft: 4, padding: '2px 8px', fontSize: 11 }}
                            onClick={() => { setPerPage(n); setPage(0); }}>{n}</button>
                    ))}
                    <span style={{ marginLeft: 4 }}>строк</span>
                </div>
                <div style={{ display: 'flex', gap: 4 }}>
                    <button className="btn btn-sm btn-secondary" disabled={page === 0} onClick={() => setPage(p => p - 1)}>←</button>
                    {Array.from({ length: Math.min(totalPages, 5) }, (_, i) => {
                        const p = page < 3 ? i : page - 2 + i;
                        if (p >= totalPages) return null;
                        return <button key={p} className={`btn btn-sm ${page === p ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setPage(p)}>{p + 1}</button>;
                    })}
                    <button className="btn btn-sm btn-secondary" disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>→</button>
                </div>
            </div>
        </div>
    );
}

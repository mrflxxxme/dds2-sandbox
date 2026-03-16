'use client';
import { useEffect, useState, useCallback, useRef } from 'react';
import { api } from '@/lib/api';
import { MultiLineChart } from './components/MultiLineChart';
import { DayAnalysisTab } from './components/DayAnalysisTab';
import { StockAnalytics } from './components/StockAnalytics';

const fmt = (n: number) => n?.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) ?? '0';
const fmtPct = (n: number) => (n || 0).toFixed(2) + '%';

/* ─── Main page ──────────────────────────────────────────────── */

export default function FunnelPage() {
    const [tab, setTab] = useState<'funnel' | 'day-analysis' | 'stock'>('funnel');
    const [data, setData] = useState<any[]>([]);
    const [detailed, setDetailed] = useState(false);
    const [summary, setSummary] = useState<any>(null);
    const [filters, setFilters] = useState<any>({ brands: [], subjects: [] });
    const [loading, setLoading] = useState(false);
    const headerRow1Ref = useRef<HTMLTableRowElement>(null);
    const [row1H, setRow1H] = useState(32);
    const [taxRate, setTaxRate] = useState(6);
    const [initDone, setInitDone] = useState(false);
    const [lastSyncAt, setLastSyncAt] = useState<string | null>(null);
    const [missingDays, setMissingDays] = useState<number | null>(null);

    // Filters
    const [dateFrom, setDateFrom] = useState('');
    const [dateTo, setDateTo] = useState('');
    const [brand, setBrand] = useState('');
    const [subject, setSubject] = useState('');
    const [search, setSearch] = useState('');

    // Which charts to display (multiple selection)
    const [chartFields, setChartFields] = useState<{ field: string; label: string; color: string }[]>([
        { field: 'orders_sum_rub', label: 'Сумма заказов ₽', color: '#8b5cf6' }
    ]);

    // Measure first header row height dynamically
    useEffect(() => {
        const el = headerRow1Ref.current;
        if (!el) return;
        const measure = () => setRow1H(el.offsetHeight);
        measure();
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        return () => ro.disconnect();
    }, [data]);

    const loadFilters = useCallback(async () => {
        try {
            const f = await api.getFunnelFilters();
            setFilters(f);
            return f;
        } catch { return null; }
    }, []);

    const loadData = useCallback(async (df?: string, dt?: string) => {
        const from = df || dateFrom;
        const to = dt || dateTo;
        if (!from || !to) return [];
        setLoading(true);
        try {
            const [res, sum, tax] = await Promise.all([
                api.getFunnelData({ date_from: from, date_to: to, brand, vendor_code: search, subject }),
                api.getFunnelSummary(from, to, brand, subject),
                api.getFunnelTax(),
            ]);
            setData(res.data || []);
            setDetailed(res.detailed || false);
            setSummary(sum);
            setTaxRate(tax.tax_rate || 6);
            return res.data || [];
        } catch (e: any) {
            console.error(e);
            return [];
        } finally {
            setLoading(false);
        }
    }, [dateFrom, dateTo, brand, subject, search]);

    const loadSyncStatus = useCallback(async () => {
        try {
            const s = await api.getSyncStatus();
            if (s.last_syncs?.length > 0) {
                const last = s.last_syncs[0];
                setLastSyncAt(last.finished_at || last.started_at || null);
            }
            if (s.missing_days != null) {
                setMissingDays(s.missing_days);
            }
        } catch { }
    }, []);

    // Init: load filters → set dates from DB range → load data
    useEffect(() => {
        (async () => {
            const f = await loadFilters();
            await loadSyncStatus();
            if (f?.min_date && f?.max_date) {
                setDateFrom(f.min_date);
                setDateTo(f.max_date);
                await loadData(f.min_date, f.max_date);
            }
            setInitDone(true);
        })();
    }, []);

    useEffect(() => { if (initDone && dateFrom && dateTo) loadData(); }, [dateFrom, dateTo, brand, subject, search]);

    const handleSaveTax = async () => {
        try {
            await api.setFunnelTax(taxRate);
            loadData();
        } catch (e: any) { alert(e.message); }
    };

    // Summary card definitions
    const summaryCards = [
        { label: 'Переходы', field: 'open_card', color: '#f59e0b' },
        { label: 'Корзины', field: 'add_to_cart', color: '#3b82f6' },
        { label: 'Заказы', field: 'orders_count', color: '#10b981' },
        { label: 'Сумма заказов ₽', field: 'orders_sum_rub', color: '#8b5cf6' },
        { label: 'Расходы рекл. ₽', field: 'adv_sum', color: '#ef4444' },
        { label: 'ДРР %', field: 'drr', color: '#f97316', suffix: '%' },
        { label: 'Просмотры', field: 'adv_views', color: '#6366f1' },
        { label: 'Клики', field: 'adv_clicks', color: '#ec4899' },
    ];

    const formatSyncDate = (iso: string | null) => {
        if (!iso) return '—';
        try {
            const d = new Date(iso);
            return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        } catch { return iso; }
    };

    return (
        <div>
            <h1 style={{ fontSize: 22, fontWeight: 600, marginBottom: 16 }}>📊 Воронка продаж</h1>

            {/* Tabs */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                <button className={`tab-btn ${tab === 'funnel' ? 'active' : ''}`} onClick={() => setTab('funnel')}>Воронка</button>
                <button className={`tab-btn ${tab === 'day-analysis' ? 'active' : ''}`} onClick={() => setTab('day-analysis')}>🔍 Анализ дня</button>
                <button className={`tab-btn ${tab === 'stock' ? 'active' : ''}`} onClick={() => setTab('stock')}>📦 Остатки</button>
            </div>

            {tab === 'funnel' && (
                <>
                    {/* Sync status + Tax */}
                    <div className="glass-card" style={{ marginBottom: 16, padding: '12px 16px' }}>
                        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                            <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>
                                🔄 Последняя синхронизация: <strong style={{ color: 'var(--color-text)' }}>{formatSyncDate(lastSyncAt)}</strong>
                            </span>
                            <span style={{ fontSize: 12, color: 'rgba(16,185,129,0.8)' }}>● авто</span>
                            {missingDays != null && missingDays > 0 && (
                                <span style={{ fontSize: 12, color: '#f59e0b', background: 'rgba(245,158,11,0.1)', padding: '2px 8px', borderRadius: 4 }}>
                                    ⏳ Осталось обновить: {missingDays} {missingDays === 1 ? 'день' : missingDays < 5 ? 'дня' : 'дней'}
                                </span>
                            )}
                            {missingDays === 0 && (
                                <span style={{ fontSize: 12, color: '#10b981' }}>✅ Все дни синхронизированы</span>
                            )}
                            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
                                <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Налог %:</span>
                                <input type="number" value={taxRate} step="0.1"
                                    onChange={e => setTaxRate(parseFloat(e.target.value) || 0)}
                                    style={{ width: 60, background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', textAlign: 'center' }} />
                                <button className="btn-secondary" onClick={handleSaveTax} style={{ padding: '4px 10px', fontSize: 12 }}>Сохранить</button>
                            </div>
                        </div>
                    </div>

                    {/* Summary header — clickable cards to switch chart */}
                    {summary && (
                        <div style={{ display: 'grid', gridTemplateColumns: `repeat(${summaryCards.length}, 1fr)`, gap: 6, marginBottom: 12 }}>
                            {summaryCards.map((s: any) => {
                                const isActive = chartFields.some(c => c.field === s.field);
                                const toggleChart = () => {
                                    setChartFields(prev => {
                                        const exists = prev.find(c => c.field === s.field);
                                        if (exists) {
                                            const next = prev.filter(c => c.field !== s.field);
                                            return next.length > 0 ? next : prev; // keep at least one
                                        }
                                        return [...prev, { field: s.field, label: s.label, color: s.color }];
                                    });
                                };
                                return (
                                    <div key={s.label} className="glass-card"
                                        onClick={toggleChart}
                                        style={{
                                            padding: '10px 14px', textAlign: 'center',
                                            cursor: 'pointer', transition: 'transform 0.15s, box-shadow 0.15s',
                                            border: isActive ? `1px solid ${s.color}60` : '1px solid transparent',
                                            boxShadow: isActive ? `0 2px 12px ${s.color}20` : 'none',
                                        }}
                                        onMouseEnter={e => { (e.currentTarget as HTMLElement).style.transform = 'translateY(-2px)'; }}
                                        onMouseLeave={e => { (e.currentTarget as HTMLElement).style.transform = 'none'; }}>
                                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>
                                            {s.label} {isActive ? '📈' : ''}
                                        </div>
                                        <div style={{ fontSize: 18, fontWeight: 700, color: s.color }}>
                                            {s.suffix ? fmtPct(summary[s.field]).replace('%', '') + s.suffix : fmt(summary[s.field])}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}

                    {/* Filters */}
                    <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                        <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
                            min={filters.min_date || undefined} max={filters.max_date || undefined}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13 }} />
                        <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                        <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
                            min={filters.min_date || undefined} max={filters.max_date || undefined}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13 }} />
                        <select value={brand} onChange={e => setBrand(e.target.value)}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13 }}>
                            <option value="">Все бренды</option>
                            {filters.brands?.map((b: string) => <option key={b} value={b}>{b}</option>)}
                        </select>
                        <select value={subject} onChange={e => setSubject(e.target.value)}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13 }}>
                            <option value="">Все категории</option>
                            {filters.subjects?.map((s: string) => <option key={s} value={s}>{s}</option>)}
                        </select>
                        <input placeholder="🔍 Артикул..." value={search} onChange={e => setSearch(e.target.value)}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13, width: 160 }} />
                        {detailed && (
                            <span style={{ fontSize: 12, color: '#f59e0b', marginLeft: 8 }}>📋 Детализация по артикулам</span>
                        )}
                    </div>

                    {/* Inline chart — above table */}
                    {data.length > 0 && chartFields.length > 0 && (
                        <MultiLineChart data={data} lines={chartFields} />
                    )}

                    {/* Table with sticky header — both rows pinned */}
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                        <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 280px)' }}>
                            {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> : (
                                <table className="data-table" style={{ minWidth: detailed ? 1800 : 1200, borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#ffffff' }}>
                                <thead>
                                    <tr ref={headerRow1Ref}>
                                        <th rowSpan={2} style={{ position: 'sticky', left: 0, top: 0, background: '#ffffff', color: '#374151', backdropFilter: 'none', zIndex: 22, verticalAlign: 'bottom', borderBottom: '2px solid #e5e7eb', minWidth: 100, borderRight: '1px solid #e5e7eb', padding: '8px 12px', boxShadow: !detailed ? 'inset -6px 0 6px -6px rgba(0,0,0,0.08)' : 'none' }}>ДАТА</th>
                                        {detailed && <th rowSpan={2} style={{ position: 'sticky', left: 100, top: 0, background: '#ffffff', color: '#374151', backdropFilter: 'none', zIndex: 22, verticalAlign: 'bottom', minWidth: 130, borderRight: '1px solid #e5e7eb', padding: '8px 12px', boxShadow: 'inset -6px 0 6px -6px rgba(0,0,0,0.08)' }}>Артикул</th>}
                                        {detailed && <th rowSpan={2} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', zIndex: 20, verticalAlign: 'bottom', borderBottom: '2px solid #e5e7eb' }}>nmId</th>}
                                        {detailed && <th rowSpan={2} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', zIndex: 20, verticalAlign: 'bottom', borderBottom: '2px solid #e5e7eb' }}>Предмет</th>}
                                        {detailed && <th rowSpan={2} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', zIndex: 20, verticalAlign: 'bottom', borderBottom: '2px solid #e5e7eb' }}>Бренд</th>}
                                        <th colSpan={5} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb' }}>ВОРОНКА</th>
                                        <th colSpan={7} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>ВНУТРЕННЯЯ РЕКЛАМА</th>
                                        <th colSpan={4} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>ФИНАНСЫ</th>
                                        <th colSpan={2} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>КОНВЕРСИЯ</th>
                                    </tr>
                                    <tr>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Переходы</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Корзины</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Заказы</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Сумма ₽</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Выручка ₽</th>
                                        
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>Расходы ₽</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Просмотры</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Клики</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>CTR</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>CPC</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>CPM</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>ДРР</th>
                                        
                                        {detailed && <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>Себест. ₽</th>}
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', borderLeft: !detailed ? '1px solid #e5e7eb' : 'none' }}>Налог ₽</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Прибыль ₽</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Маржа</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Ср. цена</th>
                                        
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>В корзину</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>В заказ</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.length === 0 && (
                                        <tr><td colSpan={30} style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-dim)' }}>
                                            Данные загружаются автоматически. Ожидайте синхронизации.
                                        </td></tr>
                                    )}
                                    {data.map((r, i) => {
                                        const rowBg = i % 2 === 0 ? '#ffffff' : '#f9fafb';
                                        return (
                                            <tr key={i} style={{ background: rowBg, color: '#111827' }}>
                                                <td style={{ position: 'sticky', left: 0, background: rowBg, color: '#111827', zIndex: 2, whiteSpace: 'nowrap', fontSize: 13, fontWeight: 500, minWidth: 100, padding: '8px 12px', borderRight: '1px solid #e5e7eb', borderBottom: '1px solid #f3f4f6', boxShadow: !detailed ? 'inset -6px 0 6px -6px rgba(0,0,0,0.05)' : 'none' }}>{r.date}</td>
                                                {detailed && <td style={{ position: 'sticky', left: 100, background: rowBg, color: '#111827', zIndex: 2, fontSize: 12, minWidth: 130, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 160, padding: '8px 12px', borderRight: '1px solid #e5e7eb', borderBottom: '1px solid #f3f4f6', boxShadow: 'inset -6px 0 6px -6px rgba(0,0,0,0.05)' }}>{r.vendor_code}</td>}
                                                {detailed && <td style={{ fontSize: 12, borderBottom: '1px solid #f3f4f6' }}><a href={`https://www.wildberries.ru/catalog/${r.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>{r.nm_id}</a></td>}
                                                {detailed && <td style={{ fontSize: 12, borderBottom: '1px solid #f3f4f6' }}>{r.subject}</td>}
                                                {detailed && <td style={{ fontSize: 12, borderBottom: '1px solid #f3f4f6' }}>{r.brand}</td>}
                                                {/* Воронка */}
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', background: r.open_card > 300000 ? '#fffbeb' : undefined }}>{fmt(r.open_card)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', background: r.add_to_cart > 15000 ? '#eff6ff' : undefined }}>{fmt(r.add_to_cart)}</td>
                                                <td style={{ textAlign: 'right', fontWeight: 600, borderBottom: '1px solid #f3f4f6', background: r.orders_count > 2500 ? '#f0fdf4' : undefined }}>{fmt(r.orders_count)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', background: r.orders_sum_rub > 5000000 ? '#faf5ff' : undefined }}>{fmt(r.orders_sum_rub)}</td>
                                                <td style={{ textAlign: 'right', fontWeight: 500, borderBottom: '1px solid #f3f4f6', color: r.revenue > 0 ? '#111827' : '#ef4444' }}>{fmt(r.revenue)}</td>
                                                {/* Реклама */}
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', borderLeft: '1px solid #f3f4f6', color: r.adv_sum > 400000 ? '#ef4444' : r.adv_sum > 100000 ? '#f59e0b' : r.adv_sum > 0 ? '#f97316' : '#9ca3af', background: r.adv_sum > 400000 ? '#fef2f2' : undefined }}>{fmt(r.adv_sum)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.adv_views)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.adv_clicks)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.ctr > 5 ? '#10b981' : r.ctr > 2 ? '#374151' : '#f59e0b' }}>{fmtPct(r.ctr)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.cpc)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.cpm)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.drr > 30 ? '#ef4444' : r.drr > 15 ? '#f59e0b' : r.drr > 0 ? '#10b981' : '#9ca3af', fontWeight: r.drr > 30 ? 600 : 400, background: r.drr > 30 ? '#fef2f2' : undefined }}>{fmtPct(r.drr)}</td>
                                                {/* Финансы */}
                                                {detailed && <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', borderLeft: '1px solid #f3f4f6' }}>{r.cost_price ? fmt(r.cost_total) : <span style={{ color: '#f59e0b', fontSize: 11 }}>—</span>}</td>}
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: '#6b7280', borderLeft: !detailed ? '1px solid #f3f4f6' : 'none' }}>{fmt(r.tax)}</td>
                                                <td style={{ textAlign: 'right', fontWeight: 700, borderBottom: '1px solid #f3f4f6', color: r.profit > 0 ? '#10b981' : '#ef4444', background: r.profit > 0 ? '#f0fdf4' : r.profit < 0 ? '#fef2f2' : undefined }}>{fmt(r.profit)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.margin > 20 ? '#10b981' : r.margin > 0 ? '#65a30d' : '#ef4444', fontWeight: r.margin > 20 ? 600 : 400 }}>{fmtPct(r.margin)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.avg_price)}</td>
                                                {/* Конверсия */}
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', borderLeft: '1px solid #f3f4f6', color: r.add_to_cart_pct > 8 ? '#10b981' : r.add_to_cart_pct > 4 ? '#374151' : '#f59e0b' }}>{fmtPct(r.add_to_cart_pct)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.cart_to_order_pct > 15 ? '#10b981' : r.cart_to_order_pct > 8 ? '#374151' : '#f59e0b' }}>{fmtPct(r.cart_to_order_pct)}</td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                                </table>
                            )}
                        </div>
                        <div style={{ padding: '16px 20px', borderTop: '1px solid #e5e7eb', fontSize: 12, color: 'var(--color-text-dim)', background: '#f9fafb' }}>
                            Всего строк: {data.length} {!detailed && '(агрегация по дням)'}
                        </div>
                    </div>
                </>
            )
            }

            {/* ─── Day Analysis tab ─── */}
            {tab === 'day-analysis' && (
                <DayAnalysisTab brand={brand} subject={subject} filters={filters} />
            )}

            {tab === 'stock' && <StockAnalytics />}
        </div >
    );
}

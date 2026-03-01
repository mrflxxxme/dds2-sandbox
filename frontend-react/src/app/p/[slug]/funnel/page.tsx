'use client';
import { useEffect, useState, useCallback, useRef } from 'react';
import { api } from '@/lib/api';

const fmt = (n: number) => n?.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) ?? '0';
const fmtPct = (n: number) => (n || 0).toFixed(2) + '%';

/* ─── Chart modal (simple canvas line chart) ─────────────────── */

function ChartModal({ title, data, field, color, onClose }: {
    title: string; data: any[]; field: string; color: string; onClose: () => void;
}) {
    const canvasRef = useRef<HTMLCanvasElement>(null);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas || !data.length) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        const dpr = window.devicePixelRatio || 1;
        const W = canvas.clientWidth;
        const H = canvas.clientHeight;
        canvas.width = W * dpr;
        canvas.height = H * dpr;
        ctx.scale(dpr, dpr);

        // Aggregate by date (data may have multiple rows per date if detailed)
        const byDate: Record<string, number> = {};
        data.forEach(r => {
            const d = r.date;
            byDate[d] = (byDate[d] || 0) + (r[field] || 0);
        });
        const dates = Object.keys(byDate).sort();
        const values = dates.map(d => byDate[d]);
        if (!values.length) return;

        const maxVal = Math.max(...values, 1);
        const minVal = Math.min(...values, 0);
        const range = maxVal - minVal || 1;

        const padTop = 30, padBottom = 50, padLeft = 70, padRight = 20;
        const chartW = W - padLeft - padRight;
        const chartH = H - padTop - padBottom;

        // Background
        ctx.fillStyle = '#1a1a2e';
        ctx.fillRect(0, 0, W, H);

        // Grid lines
        ctx.strokeStyle = 'rgba(255,255,255,0.06)';
        ctx.lineWidth = 1;
        for (let i = 0; i <= 4; i++) {
            const y = padTop + (chartH * i) / 4;
            ctx.beginPath();
            ctx.moveTo(padLeft, y);
            ctx.lineTo(W - padRight, y);
            ctx.stroke();
            // Y labels
            const val = maxVal - (range * i) / 4;
            ctx.fillStyle = 'rgba(255,255,255,0.5)';
            ctx.font = '11px sans-serif';
            ctx.textAlign = 'right';
            ctx.fillText(fmt(Math.round(val)), padLeft - 8, y + 4);
        }

        // Line + fill
        const xStep = dates.length > 1 ? chartW / (dates.length - 1) : chartW;
        ctx.beginPath();
        values.forEach((v, i) => {
            const x = padLeft + i * xStep;
            const y = padTop + chartH - ((v - minVal) / range) * chartH;
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.5;
        ctx.stroke();

        // Fill under curve
        const gradient = ctx.createLinearGradient(0, padTop, 0, H - padBottom);
        gradient.addColorStop(0, color + '40');
        gradient.addColorStop(1, color + '05');
        ctx.lineTo(padLeft + (values.length - 1) * xStep, padTop + chartH);
        ctx.lineTo(padLeft, padTop + chartH);
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();

        // Dots
        values.forEach((v, i) => {
            const x = padLeft + i * xStep;
            const y = padTop + chartH - ((v - minVal) / range) * chartH;
            ctx.beginPath();
            ctx.arc(x, y, 3.5, 0, Math.PI * 2);
            ctx.fillStyle = color;
            ctx.fill();
            ctx.strokeStyle = '#1a1a2e';
            ctx.lineWidth = 1.5;
            ctx.stroke();
        });

        // X labels (every Nth)
        const labelEvery = Math.max(1, Math.floor(dates.length / 10));
        ctx.fillStyle = 'rgba(255,255,255,0.5)';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        dates.forEach((d, i) => {
            if (i % labelEvery === 0 || i === dates.length - 1) {
                const x = padLeft + i * xStep;
                ctx.fillText(d.slice(5), x, H - padBottom + 18);  // MM-DD
            }
        });
    }, [data, field, color]);

    return (
        <div onClick={onClose}
            style={{
                position: 'fixed', inset: 0, zIndex: 1000,
                background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
            <div onClick={e => e.stopPropagation()}
                style={{
                    background: '#1a1a2e', borderRadius: 16,
                    border: '1px solid rgba(255,255,255,0.1)',
                    padding: 24, width: 'min(90vw, 800px)', maxHeight: '80vh',
                    boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
                }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>{title}</h3>
                    <button onClick={onClose}
                        style={{
                            background: 'rgba(255,255,255,0.1)', border: 'none', color: '#fff',
                            borderRadius: 8, padding: '6px 12px', cursor: 'pointer', fontSize: 14,
                        }}>✕</button>
                </div>
                <canvas ref={canvasRef}
                    style={{ width: '100%', height: 300, borderRadius: 8 }} />
            </div>
        </div>
    );
}

/* ─── Main page ──────────────────────────────────────────────── */

export default function FunnelPage() {
    const [tab, setTab] = useState<'funnel' | 'costs'>('funnel');
    const [data, setData] = useState<any[]>([]);
    const [detailed, setDetailed] = useState(false);
    const [summary, setSummary] = useState<any>(null);
    const [filters, setFilters] = useState<any>({ brands: [], subjects: [] });
    const [loading, setLoading] = useState(false);
    const [taxRate, setTaxRate] = useState(6);
    const [initDone, setInitDone] = useState(false);
    const [lastSyncAt, setLastSyncAt] = useState<string | null>(null);

    // Filters — dates will be set from DB range
    const [dateFrom, setDateFrom] = useState('');
    const [dateTo, setDateTo] = useState('');
    const [brand, setBrand] = useState('');
    const [subject, setSubject] = useState('');
    const [search, setSearch] = useState('');

    // Chart modal
    const [chartField, setChartField] = useState<{ field: string; label: string; color: string } | null>(null);

    // Costs tab
    const [costs, setCosts] = useState<any>({ overrides: [], missing: [] });
    const [editCost, setEditCost] = useState<{ nm_id: number; cost_price: string } | null>(null);

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
                api.getFunnelSummary(from, to),
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
        } catch { }
    }, []);

    const loadCosts = useCallback(async () => {
        try {
            const c = await api.getFunnelCosts();
            setCosts(c);
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
    useEffect(() => { if (tab === 'costs') loadCosts(); }, [tab]);

    const handleSaveCost = async () => {
        if (!editCost) return;
        try {
            await api.setFunnelCost(editCost.nm_id, parseFloat(editCost.cost_price));
            setEditCost(null);
            loadCosts();
            loadData();
        } catch (e: any) { alert(e.message); }
    };

    const handleSaveTax = async () => {
        try {
            await api.setFunnelTax(taxRate);
            loadData();
        } catch (e: any) { alert(e.message); }
    };

    // Summary card definitions (clickable for chart)
    const summaryCards = [
        { label: 'Переходы', field: 'open_card', color: '#f59e0b' },
        { label: 'Корзины', field: 'add_to_cart', color: '#3b82f6' },
        { label: 'Заказы', field: 'orders_count', color: '#10b981' },
        { label: 'Сумма заказов ₽', field: 'orders_sum_rub', color: '#8b5cf6' },
        { label: 'Расходы рекл. ₽', field: 'adv_sum', color: '#ef4444' },
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
                <button className={`tab-btn ${tab === 'costs' ? 'active' : ''}`} onClick={() => setTab('costs')}>Себестоимость</button>
            </div>

            {tab === 'funnel' && (
                <>
                    {/* Sync status + Tax (replaced manual sync) */}
                    <div className="glass-card" style={{ marginBottom: 16, padding: '12px 16px' }}>
                        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                            <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>
                                🔄 Последняя синхронизация: <strong style={{ color: 'var(--color-text)' }}>{formatSyncDate(lastSyncAt)}</strong>
                            </span>
                            <span style={{ fontSize: 12, color: 'rgba(16,185,129,0.8)' }}>● авто</span>
                            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
                                <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Налог %:</span>
                                <input type="number" value={taxRate} step="0.1"
                                    onChange={e => setTaxRate(parseFloat(e.target.value) || 0)}
                                    style={{ width: 60, background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', textAlign: 'center' }} />
                                <button className="btn-secondary" onClick={handleSaveTax} style={{ padding: '4px 10px', fontSize: 12 }}>Сохранить</button>
                            </div>
                        </div>
                    </div>

                    {/* Summary header — clickable cards */}
                    {summary && (
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 8, marginBottom: 16 }}>
                            {summaryCards.map(s => (
                                <div key={s.label} className="glass-card"
                                    onClick={() => setChartField({ field: s.field, label: s.label, color: s.color })}
                                    style={{
                                        padding: '10px 14px', textAlign: 'center',
                                        cursor: 'pointer', transition: 'transform 0.15s, box-shadow 0.15s',
                                    }}
                                    onMouseEnter={e => { (e.currentTarget as HTMLElement).style.transform = 'translateY(-2px)'; (e.currentTarget as HTMLElement).style.boxShadow = `0 4px 20px ${s.color}30`; }}
                                    onMouseLeave={e => { (e.currentTarget as HTMLElement).style.transform = 'none'; (e.currentTarget as HTMLElement).style.boxShadow = 'none'; }}>
                                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>{s.label} 📈</div>
                                    <div style={{ fontSize: 18, fontWeight: 700, color: s.color }}>{fmt(summary[s.field])}</div>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Chart modal */}
                    {chartField && data.length > 0 && (
                        <ChartModal
                            title={`${chartField.label} — динамика по дням`}
                            data={data}
                            field={chartField.field}
                            color={chartField.color}
                            onClose={() => setChartField(null)}
                        />
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

                    {/* Table with sticky header + sticky date column */}
                    <div className="glass-card" style={{ overflow: 'auto', maxHeight: 'calc(100vh - 380px)' }}>
                        {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> : (
                            <table className="data-table" style={{ minWidth: detailed ? 1800 : 1200, borderCollapse: 'separate', borderSpacing: 0 }}>
                                <thead>
                                    <tr>
                                        <th style={{ position: 'sticky', left: 0, top: 0, background: 'var(--color-bg-card)', zIndex: 10 }}>Дата</th>
                                        {detailed && <th style={{ position: 'sticky', top: 0, background: 'var(--color-bg-card)', zIndex: 5 }}>Артикул</th>}
                                        {detailed && <th style={{ position: 'sticky', top: 0, background: 'var(--color-bg-card)', zIndex: 5 }}>nmId</th>}
                                        {detailed && <th style={{ position: 'sticky', top: 0, background: 'var(--color-bg-card)', zIndex: 5 }}>Предмет</th>}
                                        {detailed && <th style={{ position: 'sticky', top: 0, background: 'var(--color-bg-card)', zIndex: 5 }}>Бренд</th>}
                                        <th colSpan={5} style={{ position: 'sticky', top: 0, background: 'rgba(245,158,11,0.15)', textAlign: 'center', zIndex: 5 }}>Воронка</th>
                                        <th colSpan={7} style={{ position: 'sticky', top: 0, background: 'rgba(99,102,241,0.15)', textAlign: 'center', zIndex: 5 }}>Внутренняя реклама</th>
                                        <th colSpan={4} style={{ position: 'sticky', top: 0, background: 'rgba(16,185,129,0.15)', textAlign: 'center', zIndex: 5 }}>Финансы</th>
                                        <th colSpan={2} style={{ position: 'sticky', top: 0, background: 'rgba(236,72,153,0.15)', textAlign: 'center', zIndex: 5 }}>Конверсия</th>
                                    </tr>
                                    <tr>
                                        <th style={{ position: 'sticky', left: 0, top: 32, background: 'var(--color-bg-card)', zIndex: 10, fontSize: 11 }}></th>
                                        {detailed && <th style={{ position: 'sticky', top: 32, background: 'var(--color-bg-card)', zIndex: 5, fontSize: 0, padding: 0, height: 0 }}></th>}
                                        {detailed && <th style={{ position: 'sticky', top: 32, background: 'var(--color-bg-card)', zIndex: 5, fontSize: 0, padding: 0, height: 0 }}></th>}
                                        {detailed && <th style={{ position: 'sticky', top: 32, background: 'var(--color-bg-card)', zIndex: 5, fontSize: 0, padding: 0, height: 0 }}></th>}
                                        {detailed && <th style={{ position: 'sticky', top: 32, background: 'var(--color-bg-card)', zIndex: 5, fontSize: 0, padding: 0, height: 0 }}></th>}
                                        {/* Воронка */}
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(245,158,11,0.08)', zIndex: 5 }}>Переходы</th>
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(245,158,11,0.08)', zIndex: 5 }}>Корзины</th>
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(245,158,11,0.08)', zIndex: 5 }}>Заказы</th>
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(245,158,11,0.08)', zIndex: 5 }}>Сумма ₽</th>
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(245,158,11,0.08)', zIndex: 5 }}>Выручка ₽</th>
                                        {/* Реклама */}
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(99,102,241,0.08)', zIndex: 5 }}>Расходы ₽</th>
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(99,102,241,0.08)', zIndex: 5 }}>Просмотры</th>
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(99,102,241,0.08)', zIndex: 5 }}>Клики</th>
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(99,102,241,0.08)', zIndex: 5 }}>CTR</th>
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(99,102,241,0.08)', zIndex: 5 }}>CPC</th>
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(99,102,241,0.08)', zIndex: 5 }}>CPM</th>
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(99,102,241,0.08)', zIndex: 5 }}>ДРР</th>
                                        {/* Финансы */}
                                        {detailed && <th style={{ position: 'sticky', top: 32, background: 'rgba(16,185,129,0.08)', zIndex: 5 }}>Себест. ₽</th>}
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(16,185,129,0.08)', zIndex: 5 }}>Налог ₽</th>
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(16,185,129,0.08)', zIndex: 5 }}>Прибыль ₽</th>
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(16,185,129,0.08)', zIndex: 5 }}>Маржа</th>
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(16,185,129,0.08)', zIndex: 5 }}>Ср. цена</th>
                                        {/* Конверсия */}
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(236,72,153,0.08)', zIndex: 5 }}>В корзину</th>
                                        <th style={{ position: 'sticky', top: 32, background: 'rgba(236,72,153,0.08)', zIndex: 5 }}>В заказ</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.length === 0 && (
                                        <tr><td colSpan={30} style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-dim)' }}>
                                            Данные загружаются автоматически. Ожидайте синхронизации.
                                        </td></tr>
                                    )}
                                    {data.map((r, i) => (
                                        <tr key={i}>
                                            <td style={{ position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 1, whiteSpace: 'nowrap', fontSize: 12 }}>{r.date}</td>
                                            {detailed && <td style={{ fontSize: 12 }}>{r.vendor_code}</td>}
                                            {detailed && <td style={{ fontSize: 12 }}><a href={`https://www.wildberries.ru/catalog/${r.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>{r.nm_id}</a></td>}
                                            {detailed && <td style={{ fontSize: 12 }}>{r.subject}</td>}
                                            {detailed && <td style={{ fontSize: 12 }}>{r.brand}</td>}
                                            {/* Воронка */}
                                            <td style={{ textAlign: 'right' }}>{fmt(r.open_card)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmt(r.add_to_cart)}</td>
                                            <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(r.orders_count)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmt(r.orders_sum_rub)}</td>
                                            <td style={{ textAlign: 'right', fontWeight: 500 }}>{fmt(r.revenue)}</td>
                                            {/* Реклама */}
                                            <td style={{ textAlign: 'right', color: r.adv_sum > 0 ? '#ef4444' : '' }}>{fmt(r.adv_sum)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmt(r.adv_views)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmt(r.adv_clicks)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmtPct(r.ctr)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmt(r.cpc)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmt(r.cpm)}</td>
                                            <td style={{ textAlign: 'right', color: r.drr > 30 ? '#ef4444' : r.drr > 15 ? '#f59e0b' : '#10b981' }}>{fmtPct(r.drr)}</td>
                                            {/* Финансы */}
                                            {detailed && <td style={{ textAlign: 'right' }}>{r.cost_price ? fmt(r.cost_total) : <span style={{ color: '#f59e0b', fontSize: 11 }}>—</span>}</td>}
                                            <td style={{ textAlign: 'right' }}>{fmt(r.tax)}</td>
                                            <td style={{ textAlign: 'right', fontWeight: 700, color: r.profit > 0 ? '#10b981' : '#ef4444' }}>{fmt(r.profit)}</td>
                                            <td style={{ textAlign: 'right', color: r.margin > 0 ? '#10b981' : '#ef4444' }}>{fmtPct(r.margin)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmt(r.avg_price)}</td>
                                            {/* Конверсия */}
                                            <td style={{ textAlign: 'right' }}>{fmtPct(r.add_to_cart_pct)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmtPct(r.cart_to_order_pct)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </div>
                    <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-dim)' }}>
                        Всего строк: {data.length} {!detailed && '(агрегация по дням)'}
                    </div>
                </>
            )}

            {tab === 'costs' && (
                <div>
                    <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12 }}>Ручная себестоимость</h2>
                    <p style={{ fontSize: 13, color: 'var(--color-text-dim)', marginBottom: 16 }}>
                        Товары без себестоимости из заказов. Укажите себестоимость за штуку для расчёта прибыли.
                    </p>
                    {costs.missing?.length > 0 && (
                        <div className="glass-card" style={{ marginBottom: 16 }}>
                            <h3 style={{ fontSize: 14, fontWeight: 500, marginBottom: 8, padding: '12px 16px 0' }}>
                                ⚠️ Без себестоимости ({costs.missing.length})
                            </h3>
                            <table className="data-table">
                                <thead><tr><th>nmId</th><th>Артикул</th><th>Предмет</th><th>Бренд</th><th>Себестоимость ₽</th><th></th></tr></thead>
                                <tbody>
                                    {costs.missing.map((m: any) => (
                                        <tr key={m.nm_id}>
                                            <td><a href={`https://www.wildberries.ru/catalog/${m.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>{m.nm_id}</a></td>
                                            <td>{m.vendor_code}</td><td>{m.subject}</td><td>{m.brand}</td>
                                            <td>{editCost?.nm_id === m.nm_id ? (
                                                <input type="number" value={editCost.cost_price} autoFocus
                                                    onChange={e => setEditCost({ ...editCost, cost_price: e.target.value })}
                                                    onKeyDown={e => e.key === 'Enter' && handleSaveCost()}
                                                    style={{ width: 100, background: 'var(--color-bg)', border: '1px solid var(--color-accent)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)' }} />
                                            ) : '—'}</td>
                                            <td>{editCost?.nm_id === m.nm_id ? (
                                                <div style={{ display: 'flex', gap: 4 }}>
                                                    <button className="btn-primary" onClick={handleSaveCost} style={{ padding: '2px 8px', fontSize: 12 }}>✓</button>
                                                    <button className="btn-secondary" onClick={() => setEditCost(null)} style={{ padding: '2px 8px', fontSize: 12 }}>✕</button>
                                                </div>
                                            ) : (
                                                <button className="btn-secondary" onClick={() => setEditCost({ nm_id: m.nm_id, cost_price: '' })} style={{ padding: '2px 8px', fontSize: 12 }}>✏️</button>
                                            )}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                    {costs.overrides?.length > 0 && (
                        <div className="glass-card">
                            <h3 style={{ fontSize: 14, fontWeight: 500, marginBottom: 8, padding: '12px 16px 0' }}>
                                ✅ Установленные ({costs.overrides.length})
                            </h3>
                            <table className="data-table">
                                <thead><tr><th>nmId</th><th>Себестоимость ₽</th><th></th></tr></thead>
                                <tbody>
                                    {costs.overrides.map((o: any) => (
                                        <tr key={o.nm_id}>
                                            <td><a href={`https://www.wildberries.ru/catalog/${o.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>{o.nm_id}</a></td>
                                            <td>{editCost?.nm_id === o.nm_id ? (
                                                <input type="number" value={editCost.cost_price} autoFocus
                                                    onChange={e => setEditCost({ ...editCost, cost_price: e.target.value })}
                                                    onKeyDown={e => e.key === 'Enter' && handleSaveCost()}
                                                    style={{ width: 100, background: 'var(--color-bg)', border: '1px solid var(--color-accent)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)' }} />
                                            ) : fmt(o.cost_price)}</td>
                                            <td>{editCost?.nm_id === o.nm_id ? (
                                                <div style={{ display: 'flex', gap: 4 }}>
                                                    <button className="btn-primary" onClick={handleSaveCost} style={{ padding: '2px 8px', fontSize: 12 }}>✓</button>
                                                    <button className="btn-secondary" onClick={() => setEditCost(null)} style={{ padding: '2px 8px', fontSize: 12 }}>✕</button>
                                                </div>
                                            ) : (
                                                <button className="btn-secondary" onClick={() => setEditCost({ nm_id: o.nm_id, cost_price: String(o.cost_price) })} style={{ padding: '2px 8px', fontSize: 12 }}>✏️</button>
                                            )}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                    {costs.missing?.length === 0 && costs.overrides?.length === 0 && (
                        <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                            Нет данных. Дождитесь автоматической синхронизации воронки.
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

'use client';
import { useEffect, useState, useCallback, useRef } from 'react';
import { api } from '@/lib/api';

const fmt = (n: number) => n?.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) ?? '0';
const fmtPct = (n: number) => (n || 0).toFixed(2) + '%';

/* ─── Inline chart (canvas line chart, rendered above table) ── */

function InlineChart({ title, data, field, color }: {
    title: string; data: any[]; field: string; color: string;
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

        // Aggregate by date
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

        const padTop = 20, padBottom = 35, padLeft = 70, padRight = 20;
        const chartW = W - padLeft - padRight;
        const chartH = H - padTop - padBottom;

        // Background transparent
        ctx.clearRect(0, 0, W, H);

        // Grid lines
        ctx.strokeStyle = 'rgba(255,255,255,0.06)';
        ctx.lineWidth = 1;
        for (let i = 0; i <= 4; i++) {
            const y = padTop + (chartH * i) / 4;
            ctx.beginPath();
            ctx.moveTo(padLeft, y);
            ctx.lineTo(W - padRight, y);
            ctx.stroke();
            const val = maxVal - (range * i) / 4;
            ctx.fillStyle = 'rgba(255,255,255,0.45)';
            ctx.font = '10px sans-serif';
            ctx.textAlign = 'right';
            ctx.fillText(fmt(Math.round(val)), padLeft - 8, y + 4);
        }

        // Line
        const xStep = dates.length > 1 ? chartW / (dates.length - 1) : chartW;
        ctx.beginPath();
        values.forEach((v, i) => {
            const x = padLeft + i * xStep;
            const y = padTop + chartH - ((v - minVal) / range) * chartH;
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.stroke();

        // Fill under curve
        const gradient = ctx.createLinearGradient(0, padTop, 0, H - padBottom);
        gradient.addColorStop(0, color + '30');
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
            ctx.arc(x, y, 3, 0, Math.PI * 2);
            ctx.fillStyle = color;
            ctx.fill();
        });

        // X labels
        const labelEvery = Math.max(1, Math.floor(dates.length / 12));
        ctx.fillStyle = 'rgba(255,255,255,0.45)';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        dates.forEach((d, i) => {
            if (i % labelEvery === 0 || i === dates.length - 1) {
                const x = padLeft + i * xStep;
                ctx.fillText(d.slice(5), x, H - padBottom + 14);
            }
        });
    }, [data, field, color]);

    return (
        <div className="glass-card" style={{ marginBottom: 12, padding: '12px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8, color: 'var(--color-text-dim)' }}>
                {title}
            </div>
            <canvas ref={canvasRef}
                style={{ width: '100%', height: 180, borderRadius: 8 }} />
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

    // Filters
    const [dateFrom, setDateFrom] = useState('');
    const [dateTo, setDateTo] = useState('');
    const [brand, setBrand] = useState('');
    const [subject, setSubject] = useState('');
    const [search, setSearch] = useState('');

    // Which chart to display (field name)
    const [chartField, setChartField] = useState<{ field: string; label: string; color: string }>(
        { field: 'orders_sum_rub', label: 'Сумма заказов ₽', color: '#8b5cf6' }
    );

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

    // Summary card definitions
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
                    {/* Sync status + Tax */}
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

                    {/* Summary header — clickable cards to switch chart */}
                    {summary && (
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 8, marginBottom: 12 }}>
                            {summaryCards.map(s => {
                                const isActive = chartField.field === s.field;
                                return (
                                    <div key={s.label} className="glass-card"
                                        onClick={() => setChartField({ field: s.field, label: s.label, color: s.color })}
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
                                        <div style={{ fontSize: 18, fontWeight: 700, color: s.color }}>{fmt(summary[s.field])}</div>
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
                    {data.length > 0 && (
                        <InlineChart
                            title={`${chartField.label} — динамика по дням`}
                            data={data}
                            field={chartField.field}
                            color={chartField.color}
                        />
                    )}

                    {/* Table with sticky header — both rows pinned */}
                    <div className="glass-card" style={{ overflow: 'auto', maxHeight: 'calc(100vh - 280px)' }}>
                        {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> : (
                            <table className="data-table" style={{ minWidth: detailed ? 1800 : 1200, borderCollapse: 'separate', borderSpacing: 0 }}>
                                <thead>
                                    <tr>
                                        <th rowSpan={2} style={{ position: 'sticky', left: 0, top: 0, background: 'var(--color-bg-card)', zIndex: 12, verticalAlign: 'bottom', borderBottom: '2px solid rgba(255,255,255,0.08)' }}>ДАТА</th>
                                        {detailed && <th rowSpan={2} style={{ position: 'sticky', top: 0, background: 'var(--color-bg-card)', zIndex: 8, verticalAlign: 'bottom' }}>Артикул</th>}
                                        {detailed && <th rowSpan={2} style={{ position: 'sticky', top: 0, background: 'var(--color-bg-card)', zIndex: 8, verticalAlign: 'bottom' }}>nmId</th>}
                                        {detailed && <th rowSpan={2} style={{ position: 'sticky', top: 0, background: 'var(--color-bg-card)', zIndex: 8, verticalAlign: 'bottom' }}>Предмет</th>}
                                        {detailed && <th rowSpan={2} style={{ position: 'sticky', top: 0, background: 'var(--color-bg-card)', zIndex: 8, verticalAlign: 'bottom' }}>Бренд</th>}
                                        <th colSpan={5} style={{ position: 'sticky', top: 0, background: 'rgba(245,158,11,0.15)', textAlign: 'center', zIndex: 8 }}>ВОРОНКА</th>
                                        <th colSpan={7} style={{ position: 'sticky', top: 0, background: 'rgba(99,102,241,0.15)', textAlign: 'center', zIndex: 8 }}>ВНУТРЕННЯЯ РЕКЛАМА</th>
                                        <th colSpan={4} style={{ position: 'sticky', top: 0, background: 'rgba(16,185,129,0.15)', textAlign: 'center', zIndex: 8 }}>ФИНАНСЫ</th>
                                        <th colSpan={2} style={{ position: 'sticky', top: 0, background: 'rgba(236,72,153,0.15)', textAlign: 'center', zIndex: 8 }}>КОНВЕРСИЯ</th>
                                    </tr>
                                    <tr>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(245,158,11,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>Переходы</th>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(245,158,11,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>Корзины</th>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(245,158,11,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>Заказы</th>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(245,158,11,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>Сумма ₽</th>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(245,158,11,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>Выручка ₽</th>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(99,102,241,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>Расходы ₽</th>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(99,102,241,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>Просмотры</th>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(99,102,241,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>Клики</th>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(99,102,241,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>CTR</th>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(99,102,241,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>CPC</th>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(99,102,241,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>CPM</th>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(99,102,241,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>ДРР</th>
                                        {detailed && <th style={{ position: 'sticky', top: 28, background: 'rgba(16,185,129,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>Себест. ₽</th>}
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(16,185,129,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>Налог ₽</th>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(16,185,129,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>Прибыль ₽</th>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(16,185,129,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>Маржа</th>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(16,185,129,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>Ср. цена</th>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(236,72,153,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>В корзину</th>
                                        <th style={{ position: 'sticky', top: 28, background: 'rgba(236,72,153,0.08)', zIndex: 7, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>В заказ</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.length === 0 && (
                                        <tr><td colSpan={30} style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-dim)' }}>
                                            Данные загружаются автоматически. Ожидайте синхронизации.
                                        </td></tr>
                                    )}
                                    {data.map((r, i) => {
                                        const rowBg = i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)';
                                        return (
                                            <tr key={i} style={{ background: rowBg }}>
                                                <td style={{ position: 'sticky', left: 0, background: i % 2 === 0 ? 'var(--color-bg-card)' : 'color-mix(in srgb, var(--color-bg-card) 95%, white)', zIndex: 1, whiteSpace: 'nowrap', fontSize: 12 }}>{r.date}</td>
                                                {detailed && <td style={{ fontSize: 12 }}>{r.vendor_code}</td>}
                                                {detailed && <td style={{ fontSize: 12 }}><a href={`https://www.wildberries.ru/catalog/${r.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>{r.nm_id}</a></td>}
                                                {detailed && <td style={{ fontSize: 12 }}>{r.subject}</td>}
                                                {detailed && <td style={{ fontSize: 12 }}>{r.brand}</td>}
                                                {/* Воронка */}
                                                <td style={{ textAlign: 'right', background: r.open_card > 300000 ? 'rgba(245,158,11,0.08)' : undefined }}>{fmt(r.open_card)}</td>
                                                <td style={{ textAlign: 'right', background: r.add_to_cart > 15000 ? 'rgba(59,130,246,0.08)' : undefined }}>{fmt(r.add_to_cart)}</td>
                                                <td style={{ textAlign: 'right', fontWeight: 600, background: r.orders_count > 2500 ? 'rgba(16,185,129,0.1)' : undefined }}>{fmt(r.orders_count)}</td>
                                                <td style={{ textAlign: 'right', background: r.orders_sum_rub > 5000000 ? 'rgba(139,92,246,0.08)' : undefined }}>{fmt(r.orders_sum_rub)}</td>
                                                <td style={{ textAlign: 'right', fontWeight: 500, color: r.revenue > 0 ? '#d4d4d8' : '#ef4444' }}>{fmt(r.revenue)}</td>
                                                {/* Реклама */}
                                                <td style={{ textAlign: 'right', color: r.adv_sum > 400000 ? '#ef4444' : r.adv_sum > 100000 ? '#f59e0b' : r.adv_sum > 0 ? '#fb923c' : 'var(--color-text-dim)', background: r.adv_sum > 400000 ? 'rgba(239,68,68,0.06)' : undefined }}>{fmt(r.adv_sum)}</td>
                                                <td style={{ textAlign: 'right' }}>{fmt(r.adv_views)}</td>
                                                <td style={{ textAlign: 'right' }}>{fmt(r.adv_clicks)}</td>
                                                <td style={{ textAlign: 'right', color: r.ctr > 5 ? '#10b981' : r.ctr > 2 ? '#d4d4d8' : '#f59e0b' }}>{fmtPct(r.ctr)}</td>
                                                <td style={{ textAlign: 'right' }}>{fmt(r.cpc)}</td>
                                                <td style={{ textAlign: 'right' }}>{fmt(r.cpm)}</td>
                                                <td style={{ textAlign: 'right', color: r.drr > 30 ? '#ef4444' : r.drr > 15 ? '#f59e0b' : r.drr > 0 ? '#10b981' : 'var(--color-text-dim)', fontWeight: r.drr > 30 ? 600 : 400, background: r.drr > 30 ? 'rgba(239,68,68,0.06)' : undefined }}>{fmtPct(r.drr)}</td>
                                                {/* Финансы */}
                                                {detailed && <td style={{ textAlign: 'right' }}>{r.cost_price ? fmt(r.cost_total) : <span style={{ color: '#f59e0b', fontSize: 11 }}>—</span>}</td>}
                                                <td style={{ textAlign: 'right', color: 'var(--color-text-dim)' }}>{fmt(r.tax)}</td>
                                                <td style={{ textAlign: 'right', fontWeight: 700, color: r.profit > 0 ? '#10b981' : '#ef4444', background: r.profit > 0 ? 'rgba(16,185,129,0.05)' : r.profit < 0 ? 'rgba(239,68,68,0.05)' : undefined }}>{fmt(r.profit)}</td>
                                                <td style={{ textAlign: 'right', color: r.margin > 20 ? '#10b981' : r.margin > 0 ? '#a3e635' : '#ef4444', fontWeight: r.margin > 20 ? 600 : 400 }}>{fmtPct(r.margin)}</td>
                                                <td style={{ textAlign: 'right' }}>{fmt(r.avg_price)}</td>
                                                {/* Конверсия */}
                                                <td style={{ textAlign: 'right', color: r.add_to_cart_pct > 8 ? '#10b981' : r.add_to_cart_pct > 4 ? '#d4d4d8' : '#f59e0b' }}>{fmtPct(r.add_to_cart_pct)}</td>
                                                <td style={{ textAlign: 'right', color: r.cart_to_order_pct > 15 ? '#10b981' : r.cart_to_order_pct > 8 ? '#d4d4d8' : '#f59e0b' }}>{fmtPct(r.cart_to_order_pct)}</td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        )}
                    </div>
                    <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-dim)' }}>
                        Всего строк: {data.length} {!detailed && '(агрегация по дням)'}
                    </div>
                </>
            )
            }

            {
                tab === 'costs' && (
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
                )
            }
        </div >
    );
}

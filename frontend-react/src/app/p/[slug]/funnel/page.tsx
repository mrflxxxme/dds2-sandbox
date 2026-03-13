'use client';
import { useEffect, useState, useCallback, useRef } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';

const fmt = (n: number) => n?.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) ?? '0';
const fmtPct = (n: number) => (n || 0).toFixed(2) + '%';

/* ─── Multi-line overlay chart (all selected metrics on one canvas) ── */

function MultiLineChart({ data, lines }: {
    data: any[];
    lines: { field: string; label: string; color: string }[];
}) {
    const canvasRef = useRef<HTMLCanvasElement>(null);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas || !data.length || !lines.length) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        const dpr = window.devicePixelRatio || 1;
        const W = canvas.clientWidth;
        const H = canvas.clientHeight;
        canvas.width = W * dpr;
        canvas.height = H * dpr;
        ctx.scale(dpr, dpr);

        // Get all unique dates
        const dateSet = new Set<string>();
        data.forEach(r => dateSet.add(r.date));
        const dates = Array.from(dateSet).sort();
        if (!dates.length) return;

        const padTop = 20, padBottom = 35, padLeft = 60, padRight = 20;
        const chartW = W - padLeft - padRight;
        const chartH = H - padTop - padBottom;
        const xStep = dates.length > 1 ? chartW / (dates.length - 1) : chartW;

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
        }

        // Draw each line with its own normalization
        lines.forEach((line) => {
            const byDate: Record<string, number> = {};
            data.forEach(r => {
                byDate[r.date] = (byDate[r.date] || 0) + (r[line.field] || 0);
            });
            const values = dates.map(d => byDate[d] || 0);
            const maxVal = Math.max(...values, 1);
            const minVal = Math.min(...values, 0);
            const range = maxVal - minVal || 1;

            // Line path
            ctx.beginPath();
            values.forEach((v, i) => {
                const x = padLeft + i * xStep;
                const y = padTop + chartH - ((v - minVal) / range) * chartH;
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            });
            ctx.strokeStyle = line.color;
            ctx.lineWidth = 2;
            ctx.stroke();

            // Subtle fill under
            const gradient = ctx.createLinearGradient(0, padTop, 0, H - padBottom);
            gradient.addColorStop(0, line.color + '18');
            gradient.addColorStop(1, line.color + '02');
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
                ctx.arc(x, y, 2.5, 0, Math.PI * 2);
                ctx.fillStyle = line.color;
                ctx.fill();
            });
        });

        // X labels
        const labelEvery = Math.max(1, Math.floor(dates.length / 14));
        ctx.fillStyle = 'rgba(255,255,255,0.45)';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        dates.forEach((d, i) => {
            if (i % labelEvery === 0 || i === dates.length - 1) {
                const x = padLeft + i * xStep;
                ctx.fillText(d.slice(5), x, H - padBottom + 14);
            }
        });

        // Legend in top-right corner
        ctx.textAlign = 'left';
        ctx.font = '11px sans-serif';
        let legendX = W - padRight - 10;
        ctx.textAlign = 'right';
        lines.slice().reverse().forEach((line, i) => {
            const y = padTop + 4 + i * 16;
            ctx.fillStyle = line.color;
            ctx.fillRect(legendX - ctx.measureText(line.label).width - 16, y - 4, 10, 10);
            ctx.fillStyle = 'rgba(255,255,255,0.7)';
            ctx.fillText(line.label, legendX, y + 5);
        });
    }, [data, lines]);

    return (
        <div className="glass-card" style={{ marginBottom: 12, padding: '12px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8, color: 'var(--color-text-dim)' }}>
                Динамика по дням
            </div>
            <canvas ref={canvasRef}
                style={{ width: '100%', height: 200, borderRadius: 8 }} />
        </div>
    );
}

/* ─── Day-analysis trend chart ─────────────────────────────────── */
function DayTrendChart({ data, fields, targetDate }: {
    data: any[];
    fields: { key: string; label: string; color: string }[];
    targetDate: string;
}) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas || data.length === 0) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        const dpr = window.devicePixelRatio || 1;
        const W = canvas.clientWidth, H = canvas.clientHeight;
        canvas.width = W * dpr; canvas.height = H * dpr;
        ctx.scale(dpr, dpr); ctx.clearRect(0, 0, W, H);
        const pad = { top: 20, right: 20, bottom: 28, left: 10 };
        const cw = W - pad.left - pad.right, ch = H - pad.top - pad.bottom;
        ctx.fillStyle = '#666'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
        data.forEach((d, i) => {
            const x = pad.left + (i / (data.length - 1 || 1)) * cw;
            if (i % Math.max(1, Math.floor(data.length / 8)) === 0 || i === data.length - 1)
                ctx.fillText(d.date.slice(5), x, H - 6);
        });
        const targetIdx = data.findIndex((d: any) => d.date === targetDate);
        if (targetIdx >= 0) {
            const x = pad.left + (targetIdx / (data.length - 1 || 1)) * cw;
            ctx.fillStyle = 'rgba(139,92,246,0.12)'; ctx.fillRect(x - 12, pad.top, 24, ch);
        }
        fields.forEach(f => {
            const vals = data.map((d: any) => Number(d[f.key] || 0));
            const max = Math.max(...vals, 1), min = Math.min(...vals, 0), range = max - min || 1;
            ctx.beginPath(); ctx.strokeStyle = f.color; ctx.lineWidth = 2;
            vals.forEach((v, i) => {
                const x = pad.left + (i / (data.length - 1 || 1)) * cw;
                const y = pad.top + ch - ((v - min) / range) * ch;
                i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
            });
            ctx.stroke();
            if (targetIdx >= 0) {
                const x = pad.left + (targetIdx / (data.length - 1 || 1)) * cw;
                const y = pad.top + ch - ((vals[targetIdx] - min) / range) * ch;
                ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fillStyle = f.color; ctx.fill();
            }
        });
    }, [data, fields, targetDate]);
    return (
        <div style={{ position: 'relative' }}>
            <canvas ref={canvasRef} style={{ width: '100%', height: 200, display: 'block' }} />
            <div style={{ display: 'flex', gap: 16, padding: '6px 0', justifyContent: 'center', flexWrap: 'wrap' }}>
                {fields.map(f => (
                    <span key={f.key} style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}>
                        <span style={{ width: 10, height: 10, borderRadius: 2, background: f.color, display: 'inline-block' }} />
                        {f.label}
                    </span>
                ))}
            </div>
        </div>
    );
}

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



    // Day analysis
    const [dayReport, setDayReport] = useState<any>(null);
    const [dayLoading, setDayLoading] = useState(false);
    const [dayDate, setDayDate] = useState(() => { const d = new Date(); d.setDate(d.getDate() - 1); return d.toISOString().slice(0, 10); });
    const [dayTrendDays, setDayTrendDays] = useState(14);
    const dayTrendFields = [
        { key: 'orders_sum', label: 'Выручка', color: '#8b5cf6' },
        { key: 'adv_sum', label: 'Реклама', color: '#f59e0b' },
        { key: 'orders_count', label: 'Заказы', color: '#10b981' },
        { key: 'open_card', label: 'Переходы', color: '#3b82f6' },
        { key: 'drr', label: 'ДРР %', color: '#ef4444' },
    ];
    const [dayActiveFields, setDayActiveFields] = useState<string[]>(['orders_sum', 'adv_sum']);
    const dayFmt = (v: any) => { if (v == null) return '—'; const n = Number(v); return isNaN(n) ? String(v) : n.toLocaleString('ru-RU', { maximumFractionDigits: 2 }); };

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


    const loadDayReport = useCallback(async () => {
        setDayLoading(true);
        try {
            const data = await api.getDayAnalysis({ target_date: dayDate, brand: brand || undefined, subject: subject || undefined, trend_days: dayTrendDays });
            setDayReport(data);
        } catch (err: any) { console.error('Day analysis error:', err); }
        finally { setDayLoading(false); }
    }, [dayDate, brand, subject, dayTrendDays]);

    useEffect(() => { if (tab === 'day-analysis') loadDayReport(); }, [tab, loadDayReport]);



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
                <>
                    {/* Filters */}
                    <div className="glass-card" style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '10px 16px', marginBottom: 16, flexWrap: 'wrap' }}>
                        <label style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Дата:</label>
                        <input type="date" value={dayDate} onChange={e => setDayDate(e.target.value)}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13 }} />
                        <label style={{ fontSize: 13, color: 'var(--color-text-dim)', marginLeft: 8 }}>Бренд:</label>
                        <select value={brand} onChange={e => setBrand(e.target.value)}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13, maxWidth: 160 }}>
                            <option value="">Все</option>
                            {(filters.brands || []).map((b: string) => <option key={b} value={b}>{b}</option>)}
                        </select>
                        <label style={{ fontSize: 13, color: 'var(--color-text-dim)', marginLeft: 8 }}>Категория:</label>
                        <select value={subject} onChange={e => setSubject(e.target.value)}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13, maxWidth: 180 }}>
                            <option value="">Все</option>
                            {(filters.subjects || []).map((s: string) => <option key={s} value={s}>{s}</option>)}
                        </select>
                        <label style={{ fontSize: 13, color: 'var(--color-text-dim)', marginLeft: 8 }}>Тренд:</label>
                        <select value={dayTrendDays} onChange={e => setDayTrendDays(Number(e.target.value))}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13 }}>
                            <option value={7}>7 дней</option>
                            <option value={14}>14 дней</option>
                            <option value={30}>30 дней</option>
                        </select>
                    </div>

                    {dayLoading && <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка...</div>}

                    {dayReport && !dayLoading && (() => {
                        const cmp = dayReport.comparison || {};
                        const summaryCards = [
                            { label: 'Выручка', key: 'orders_sum', icon: '💰', color: '#8b5cf6' },
                            { label: 'Реклама', key: 'adv_sum', icon: '📢', color: '#f59e0b' },
                            { label: 'ДРР', key: 'drr', icon: '📊', suffix: '%', color: '#ef4444' },
                            { label: 'Заказы', key: 'orders_count', icon: '📦', color: '#10b981' },
                            { label: 'Прибыль', key: 'profit', icon: '🏆', color: '#06b6d4' },
                            { label: 'Переходы', key: 'open_card', icon: '👁', color: '#3b82f6' },
                            { label: 'Корзины', key: 'add_to_cart', icon: '🛒', color: '#ec4899' },
                        ];
                        const selectedFields = dayTrendFields.filter(f => dayActiveFields.includes(f.key));

                        // Split anomalies into positive and negative
                        const positiveAnomalies = (dayReport.anomalies || []).filter((a: any) => a.flags.some((f: string) => f.includes('📈')));
                        const negativeAnomalies = (dayReport.anomalies || []).filter((a: any) => a.flags.some((f: string) => f.includes('📉') || f.includes('⚠️') || f.includes('🚫')));

                        const AnomalyRow = ({ a, positive }: { a: any; positive: boolean }) => (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 8px', fontSize: 11, borderBottom: '1px solid rgba(255,255,255,0.04)', whiteSpace: 'nowrap' }}>
                                <span style={{ fontSize: 12, flexShrink: 0 }}>{positive ? '📈' : '📉'}</span>
                                <span style={{ fontWeight: 600, maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', flexShrink: 0 }} title={a.vendor_code || String(a.nm_id)}>{a.vendor_code || a.nm_id}</span>
                                <span style={{ color: '#888', fontSize: 10, maxWidth: 80, overflow: 'hidden', textOverflow: 'ellipsis', flexShrink: 0 }} title={a.subject}>{a.subject}</span>
                                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', color: '#aaa', fontSize: 10 }} title={a.flags.join(' · ')}>{a.flags.join(' · ')}</span>
                                <span style={{ color: '#8b5cf6', fontWeight: 600, flexShrink: 0, minWidth: 60, textAlign: 'right' }}>₽{dayFmt(a.orders_sum)}</span>
                                <span style={{ color: '#f59e0b', flexShrink: 0, minWidth: 50, textAlign: 'right' }}>₽{dayFmt(a.adv_sum)}</span>
                            </div>
                        );

                        return (
                            <>
                                {/* Summary cards */}
                                <div style={{ display: 'grid', gridTemplateColumns: `repeat(${summaryCards.length}, 1fr)`, gap: 10, marginBottom: 16 }}>
                                    {summaryCards.map(c => {
                                        const comp = cmp[c.key];
                                        const pct = comp?.change_pct ?? 0;
                                        const isUp = pct > 0, isDown = pct < 0;
                                        return (
                                            <div key={c.key} className="glass-card" style={{ padding: '12px 14px' }}>
                                                <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>{c.icon} {c.label}</div>
                                                <div style={{ fontSize: 20, fontWeight: 700, color: c.color }}>
                                                    {dayFmt(dayReport.summary[c.key])}{c.suffix || ''}
                                                </div>
                                                {comp && (
                                                    <div style={{ fontSize: 11, marginTop: 4, color: isUp ? '#10b981' : isDown ? '#ef4444' : '#666' }}>
                                                        {isUp ? '↑' : isDown ? '↓' : '→'} {Math.abs(pct)}% vs вчера
                                                        <span style={{ color: '#666', marginLeft: 6 }}>({dayFmt(comp.previous)})</span>
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>

                                {/* Trend chart */}
                                <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                                        <h3 style={{ margin: 0, fontSize: 14 }}>📈 Тренд за {dayTrendDays} дней</h3>
                                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                                            {dayTrendFields.map(f => (
                                                <button key={f.key} onClick={() => setDayActiveFields((prev: string[]) => prev.includes(f.key) ? prev.filter((k: string) => k !== f.key) : [...prev, f.key])}
                                                    style={{
                                                        padding: '3px 8px', fontSize: 11, borderRadius: 4, cursor: 'pointer', border: '1px solid',
                                                        borderColor: dayActiveFields.includes(f.key) ? f.color : 'rgba(255,255,255,0.1)',
                                                        background: dayActiveFields.includes(f.key) ? f.color + '22' : 'transparent',
                                                        color: dayActiveFields.includes(f.key) ? f.color : '#888'
                                                    }}>{f.label}</button>
                                            ))}
                                        </div>
                                    </div>
                                    {dayReport.trend?.length > 0 && selectedFields.length > 0 && (
                                        <DayTrendChart data={dayReport.trend} fields={selectedFields} targetDate={dayDate} />
                                    )}
                                </div>

                                {/* Anomalies — split into positive & negative */}
                                {(positiveAnomalies.length > 0 || negativeAnomalies.length > 0) && (
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
                                        {/* Positive */}
                                        <div className="glass-card" style={{ padding: '12px 14px', borderLeft: '3px solid #10b981', display: 'flex', flexDirection: 'column' as const, minWidth: 0, overflow: 'hidden' }}>
                                            <h4 style={{ margin: '0 0 8px', fontSize: 13, color: '#10b981', flexShrink: 0 }}>📈 Рост ({positiveAnomalies.length})</h4>
                                            <div style={{ maxHeight: 340, overflowY: 'auto' as const, flex: 1 }}>
                                                {positiveAnomalies.length === 0 ? <div style={{ color: '#666', fontSize: 12 }}>Нет аномалий роста</div> :
                                                    positiveAnomalies.map((a: any, i: number) => <AnomalyRow key={i} a={a} positive />)
                                                }
                                            </div>
                                        </div>
                                        {/* Negative */}
                                        <div className="glass-card" style={{ padding: '12px 14px', borderLeft: '3px solid #ef4444', display: 'flex', flexDirection: 'column' as const, minWidth: 0, overflow: 'hidden' }}>
                                            <h4 style={{ margin: '0 0 8px', fontSize: 13, color: '#ef4444', flexShrink: 0 }}>📉 Снижение / Проблемы ({negativeAnomalies.length})</h4>
                                            <div style={{ maxHeight: 340, overflowY: 'auto' as const, flex: 1 }}>
                                                {negativeAnomalies.length === 0 ? <div style={{ color: '#666', fontSize: 12 }}>Нет проблемных товаров</div> :
                                                    negativeAnomalies.map((a: any, i: number) => <AnomalyRow key={i} a={a} positive={false} />)
                                                }
                                            </div>
                                        </div>
                                    </div>
                                )}

                                {/* Top products table */}
                                <div className="glass-card" style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                                    <h3 style={{ margin: '16px 20px 12px', fontSize: 14 }}>🏆 Топ товаров за {dayDate}</h3>
                                    <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 380px)' }}>
                                        <table className="data-table" style={{ minWidth: 900, borderCollapse: 'separate', borderSpacing: 0 }}>
                                            <thead>
                                                <tr>
                                                    <th style={{ position: 'sticky', top: 0, background: '#f9fafb', zIndex: 10, borderBottom: '1px solid #e5e7eb' }}>#</th>
                                                    <th style={{ position: 'sticky', top: 0, background: '#f9fafb', zIndex: 10, borderBottom: '1px solid #e5e7eb' }}>Артикул</th>
                                                    <th style={{ position: 'sticky', top: 0, background: '#f9fafb', zIndex: 10, borderBottom: '1px solid #e5e7eb' }}>nmId</th>
                                                    <th style={{ position: 'sticky', top: 0, background: '#f9fafb', zIndex: 10, borderBottom: '1px solid #e5e7eb' }}>Категория</th>
                                                    <th style={{ position: 'sticky', top: 0, background: '#f9fafb', zIndex: 10, borderBottom: '1px solid #e5e7eb' }}>Бренд</th>
                                                    <th style={{ position: 'sticky', top: 0, background: '#f9fafb', zIndex: 10, borderBottom: '1px solid #e5e7eb' }}>Переходы</th>
                                                    <th style={{ position: 'sticky', top: 0, background: '#f9fafb', zIndex: 10, borderBottom: '1px solid #e5e7eb' }}>Корзины</th>
                                                    <th style={{ position: 'sticky', top: 0, background: '#f9fafb', zIndex: 10, borderBottom: '1px solid #e5e7eb' }}>Заказы</th>
                                                    <th style={{ position: 'sticky', top: 0, background: '#f9fafb', zIndex: 10, borderBottom: '1px solid #e5e7eb' }}>Выручка ₽</th>
                                                    <th style={{ position: 'sticky', top: 0, background: '#f9fafb', zIndex: 10, borderBottom: '1px solid #e5e7eb' }}>Реклама ₽</th>
                                                    <th style={{ position: 'sticky', top: 0, background: '#f9fafb', zIndex: 10, borderBottom: '1px solid #e5e7eb' }}>ДРР %</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {(dayReport.top_products || []).map((p: any, i: number) => {
                                                    const rowBg = i % 2 === 0 ? '#ffffff' : '#f9fafb';
                                                    return (
                                                    <tr key={i} style={{ background: rowBg, color: '#111827' }}>
                                                        <td style={{ textAlign: 'center', color: '#6b7280', fontSize: 11, borderBottom: '1px solid #f3f4f6' }}>{i + 1}</td>
                                                        <td style={{ fontSize: 12, borderBottom: '1px solid #f3f4f6' }}>{p.vendor_code}</td>
                                                        <td style={{ fontSize: 12, borderBottom: '1px solid #f3f4f6' }}>
                                                            <a href={`https://www.wildberries.ru/catalog/${p.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>{p.nm_id}</a>
                                                        </td>
                                                        <td style={{ fontSize: 12, borderBottom: '1px solid #f3f4f6' }}>{p.subject}</td>
                                                        <td style={{ fontSize: 12, borderBottom: '1px solid #f3f4f6' }}>{p.brand}</td>
                                                        <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{dayFmt(p.open_card)}</td>
                                                        <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{dayFmt(p.add_to_cart)}</td>
                                                        <td style={{ textAlign: 'right', fontWeight: 600, borderBottom: '1px solid #f3f4f6' }}>{dayFmt(p.orders_count)}</td>
                                                        <td style={{ textAlign: 'right', color: '#8b5cf6', fontWeight: 600, borderBottom: '1px solid #f3f4f6' }}>{dayFmt(p.orders_sum)}</td>
                                                        <td style={{ textAlign: 'right', color: '#f59e0b', borderBottom: '1px solid #f3f4f6' }}>{dayFmt(p.adv_sum)}</td>
                                                        <td style={{ textAlign: 'right', color: p.drr > 20 ? '#ef4444' : p.drr > 10 ? '#f59e0b' : '#10b981', fontWeight: 600, borderBottom: '1px solid #f3f4f6' }}>
                                                            {p.drr.toFixed(1)}%
                                                        </td>
                                                    </tr>
                                                )})}
                                            </tbody>
                                        </table>
                                    </div>
                                </div>
                            </>
                        );
                    })()}
                </>
            )}

            {tab === 'stock' && <StockAnalytics />}
        </div >
    );
}
// ═══════════════════════════════════════════════════════════════════════════════
// StockAnalytics — Аналитика остатков
// ═══════════════════════════════════════════════════════════════════════════════

function StockAnalytics() {
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

    // Color for forecast cell based on projected stock level
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
                                onMouseEnter={e => (e.currentTarget.style.background = 'var(--color-bg-tertiary)')}
                                onMouseLeave={e => (e.currentTarget.style.background = '')}>
                                <td style={{ position: 'sticky', left: 0, background: 'inherit', zIndex: 1, fontWeight: 600 }}>
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

/* ─────────── Warehouse Stocks View ─────────── */
function WarehouseStocksView() {
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [syncing, setSyncing] = useState(false);

    const load = async () => {
        setLoading(true);
        try { setData(await api.getWarehouseStocks()); } catch { }
        setLoading(false);
    };

    const sync = async () => {
        setSyncing(true);
        try {
            const res = await api.syncWarehouseStocks();
            alert(`Синхронизировано ${res.synced} записей`);
            await load();
        } catch (e: any) {
            alert(e?.message || 'Ошибка синхронизации');
        }
        setSyncing(false);
    };

    useEffect(() => { load(); }, []);

    if (loading && !data) return <div className="glass-card" style={{ textAlign: 'center', padding: 40 }}>Загрузка складов...</div>;

    return (
        <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div>
                    <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>🏭 Остатки по складам</h2>
                    <span style={{ fontSize: 13, opacity: 0.6 }}>
                        {data ? `${data.total_warehouses} складов · ${formatNumber(data.total_qty)} шт` : 'Нет данных'}
                    </span>
                </div>
                <button className="btn btn-sm btn-primary" onClick={sync} disabled={syncing}>
                    {syncing ? '⏳ Синхронизация...' : '🔄 Синхронизировать с WB'}
                </button>
            </div>

            {data && data.warehouses && data.warehouses.length > 0 ? (
                <div className="glass-card" style={{ overflowX: 'auto' }}>
                    <table className="data-table" style={{ width: '100%', fontSize: 13 }}>
                        <thead>
                            <tr>
                                <th style={{ textAlign: 'left' }}>Склад</th>
                                <th style={{ textAlign: 'right' }}>Остаток (шт)</th>
                                <th style={{ textAlign: 'right' }}>Артикулов</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr style={{ fontWeight: 700, background: 'rgba(0,0,0,0.03)' }}>
                                <td>ИТОГО</td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(data.total_qty)}</td>
                                <td style={{ textAlign: 'right' }}>{data.total_warehouses}</td>
                            </tr>
                            {data.warehouses.map((wh: any) => (
                                <tr key={wh.name}>
                                    <td>{wh.name}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(wh.total_qty)}</td>
                                    <td style={{ textAlign: 'right' }}>{wh.articles_count}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : (
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-text">Нет данных по складам. Нажмите «Синхронизировать с WB».</div>
                    </div>
                </div>
            )}
        </div>
    );
}

/* ─────────── Warehouse Need View ─────────── */
function WarehouseNeedView() {
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [supplyDays, setSupplyDays] = useState(14);
    const [analysisDays, setAnalysisDays] = useState(14);
    const [mode, setMode] = useState<'actual' | 'hypothetical'>('actual');
    const [brandFilter, setBrandFilter] = useState('');
    const [subjectFilter, setSubjectFilter] = useState('');
    const [sortCol, setSortCol] = useState<string>('total_need');
    const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

    const load = async () => {
        setLoading(true);
        try { setData(await api.getStockNeed(supplyDays, analysisDays, mode)); } catch { }
        setLoading(false);
    };

    useEffect(() => { load(); }, [supplyDays, analysisDays, mode]);

    // Compute per-article total need & per-warehouse need
    const getArticleNeed = (a: any, whName?: string) => {
        if (!data?.warehouses) return 0;
        if (whName) return data.warehouses.find((w: any) => w.name === whName)?.articles?.[a.nm_id]?.need || 0;
        let total = 0;
        data.warehouses.forEach((wh: any) => { total += wh.articles?.[a.nm_id]?.need || 0; });
        return total;
    };

    // Filter articles
    const filteredArticles = (data?.articles || []).filter((a: any) => {
        if (brandFilter && a.brand !== brandFilter) return false;
        if (subjectFilter && a.subject !== subjectFilter) return false;
        return true;
    });

    // Sort articles
    const sortedArticles = [...filteredArticles].sort((a: any, b: any) => {
        let va: any, vb: any;
        if (sortCol === 'vendor_code') { va = a.vendor_code; vb = b.vendor_code; }
        else if (sortCol === 'total_need') { va = getArticleNeed(a); vb = getArticleNeed(b); }
        else { va = getArticleNeed(a, sortCol); vb = getArticleNeed(b, sortCol); }
        if (typeof va === 'string') return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        return sortDir === 'asc' ? va - vb : vb - va;
    });

    // Warehouse totals (sum of need for filtered articles)
    const getWhTotal = (whName: string) => {
        let sum = 0;
        filteredArticles.forEach((a: any) => { sum += getArticleNeed(a, whName); });
        return sum;
    };

    const grandTotal = (() => {
        let sum = 0;
        filteredArticles.forEach((a: any) => { sum += getArticleNeed(a); });
        return sum;
    })();

    // Sort handler
    const handleSort = (col: string) => {
        if (sortCol === col) setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
        else { setSortCol(col); setSortDir('desc'); }
    };

    const sortIcon = (col: string) => sortCol === col ? (sortDir === 'asc' ? ' ↑' : ' ↓') : '';

    // Excel export
    const handleExport = () => {
        if (!data) return;
        const whs = data.warehouses || [];
        const header = ['Артикул', 'Бренд', 'Категория', 'Потребность', ...whs.map((w: any) => w.name)];
        const rows = sortedArticles.map((a: any) => [
            a.vendor_code, a.brand || '', a.subject || '',
            getArticleNeed(a),
            ...whs.map((wh: any) => getArticleNeed(a, wh.name) || 0),
        ]);
        const totalRow = ['ИТОГО', '', '', grandTotal, ...whs.map((w: any) => getWhTotal(w.name))];
        rows.push(totalRow);
        exportToExcel([header, ...rows], `Потребность_запас${supplyDays}д_анализ${analysisDays}д`);
    };

    if (loading && !data) return <div className="glass-card" style={{ textAlign: 'center', padding: 40 }}>Расчёт потребности...</div>;

    const modeLabel = mode === 'actual' ? 'Фактический' : 'Гипотетический';

    const thStyle: any = { textAlign: 'right', minWidth: 85, cursor: 'pointer', userSelect: 'none', fontSize: 11, whiteSpace: 'nowrap', padding: '10px 8px', borderBottom: '2px solid var(--color-border)' };
    const thStickyStyle: any = { ...thStyle, textAlign: 'left', position: 'sticky', left: 0, background: 'var(--color-bg)', zIndex: 2, minWidth: 180 };
    const tdStyle: any = { padding: '8px', textAlign: 'right', fontSize: 12, borderBottom: '1px solid var(--color-border)' };
    const tdStickyStyle: any = { ...tdStyle, textAlign: 'left', fontWeight: 600, position: 'sticky', left: 0, background: 'var(--color-bg)', zIndex: 1 };

    return (
        <div>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
                <div>
                    <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>📦 Потребность по складам</h2>
                    <span style={{ fontSize: 13, opacity: 0.6 }}>
                        {data ? `${data.total_warehouses} складов · ${filteredArticles.length} артикулов · запас ${supplyDays} дн · анализ ${analysisDays} дн · ${modeLabel}` : 'Нет данных'}
                    </span>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    {/* Brand filter */}
                    {data?.brands?.length > 0 && (
                        <select value={brandFilter} onChange={e => setBrandFilter(e.target.value)}
                            style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--color-border)', background: 'var(--color-bg)', fontSize: 12 }}>
                            <option value="">Все бренды</option>
                            {data.brands.map((b: string) => <option key={b} value={b}>{b}</option>)}
                        </select>
                    )}
                    {/* Subject filter */}
                    {data?.subjects?.length > 0 && (
                        <select value={subjectFilter} onChange={e => setSubjectFilter(e.target.value)}
                            style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--color-border)', background: 'var(--color-bg)', fontSize: 12 }}>
                            <option value="">Все категории</option>
                            {data.subjects.map((s: string) => <option key={s} value={s}>{s}</option>)}
                        </select>
                    )}
                    {/* Mode toggle */}
                    <div style={{ display: 'flex', gap: 2, background: 'var(--color-border)', borderRadius: 8, padding: 2 }}>
                        <button className={`btn btn-sm ${mode === 'actual' ? 'btn-primary' : 'btn-secondary'}`}
                            style={{ borderRadius: 6, fontSize: 11 }}
                            onClick={() => setMode('actual')}>📊 Факт</button>
                        <button className={`btn btn-sm ${mode === 'hypothetical' ? 'btn-primary' : 'btn-secondary'}`}
                            style={{ borderRadius: 6, fontSize: 11 }}
                            onClick={() => setMode('hypothetical')}>🗺️ Гипотез.</button>
                    </div>
                    {/* Upload order cities (hypothetical only) */}
                    {mode === 'hypothetical' && (
                        <label className="btn btn-sm btn-secondary" style={{ cursor: 'pointer', fontSize: 11 }}
                            title="Загрузить Excel «Лента заказов» из ЛК WB для точного определения городов">
                            📤 Загрузить ленту
                            <input type="file" accept=".xlsx" style={{ display: 'none' }} onChange={async (e) => {
                                const f = e.target.files?.[0];
                                if (!f) return;
                                try {
                                    const result = await api.uploadOrderCities(f);
                                    alert(`✅ Загружено ${result.total_mappings} городов`);
                                    await load();
                                } catch (err: any) {
                                    alert(`❌ Ошибка: ${err.message}`);
                                }
                                e.target.value = '';
                            }} />
                        </label>
                    )}
                    {/* Supply days selector */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <span style={{ fontSize: 11, opacity: 0.6, whiteSpace: 'nowrap' }}>Запас:</span>
                        {[7, 14, 30, 60].map(d => (
                            <button key={d} className={`btn btn-sm ${supplyDays === d ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setSupplyDays(d)}>{d}д</button>
                        ))}
                    </div>
                    {/* Analysis days selector */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <span style={{ fontSize: 11, opacity: 0.6, whiteSpace: 'nowrap' }}>Анализ:</span>
                        {[7, 14, 30].map(d => (
                            <button key={d} className={`btn btn-sm ${analysisDays === d ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setAnalysisDays(d)}>{d}д</button>
                        ))}
                    </div>
                    {/* Excel export */}
                    <button className="btn btn-sm btn-secondary" onClick={handleExport} title="Экспорт в Excel">📥 Excel</button>
                </div>
            </div>

            {/* Table */}
            {data && sortedArticles.length > 0 ? (
                <div className="glass-card" style={{ overflowX: 'auto', padding: 0 }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                        <thead>
                            <tr style={{ background: 'var(--color-bg)' }}>
                                <th style={thStickyStyle} onClick={() => handleSort('vendor_code')}>
                                    Артикул{sortIcon('vendor_code')}
                                </th>
                                <th style={thStyle} onClick={() => handleSort('total_need')}>
                                    Потребность{sortIcon('total_need')}
                                </th>
                                {(data.warehouses || []).map((wh: any) => (
                                    <th key={wh.name} style={thStyle} onClick={() => handleSort(wh.name)}>
                                        {wh.name.length > 16 ? wh.name.slice(0, 16) + '…' : wh.name}
                                        {sortIcon(wh.name)}
                                    </th>
                                ))}
                            </tr>
                            {/* Totals row */}
                            <tr style={{ background: 'rgba(var(--color-primary-rgb, 59,130,246), 0.06)', fontWeight: 700 }}>
                                <td style={{ ...tdStickyStyle, fontWeight: 700, background: 'rgba(var(--color-primary-rgb, 59,130,246), 0.06)', borderBottom: '2px solid var(--color-border)' }}>ИТОГО</td>
                                <td style={{ ...tdStyle, fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>{grandTotal > 0 ? formatNumber(grandTotal, 0) : '—'}</td>
                                {(data.warehouses || []).map((wh: any) => {
                                    const t = getWhTotal(wh.name);
                                    return (
                                        <td key={wh.name} style={{ ...tdStyle, fontWeight: 700, borderBottom: '2px solid var(--color-border)', color: t > 0 ? '#ef4444' : 'var(--color-text-muted)' }}>
                                            {t > 0 ? formatNumber(t, 0) : '—'}
                                        </td>
                                    );
                                })}
                            </tr>
                        </thead>
                        <tbody>
                            {sortedArticles.map((a: any) => {
                                const totalNeed = getArticleNeed(a);
                                return (
                                    <tr key={a.nm_id} style={{ transition: 'background 0.15s' }}
                                        onMouseEnter={e => (e.currentTarget.style.background = 'rgba(var(--color-primary-rgb, 59,130,246), 0.03)')}
                                        onMouseLeave={e => (e.currentTarget.style.background = '')}>
                                        <td style={tdStickyStyle}>
                                            <div>{a.vendor_code}</div>
                                            {(a.brand || a.subject) && (
                                                <div style={{ fontSize: 10, opacity: 0.5, fontWeight: 400 }}>
                                                    {[a.brand, a.subject].filter(Boolean).join(' · ')}
                                                </div>
                                            )}
                                        </td>
                                        <td style={{ ...tdStyle, fontWeight: 700, color: totalNeed > 0 ? '#ef4444' : 'var(--color-text-muted)' }}>
                                            {totalNeed > 0 ? formatNumber(totalNeed, 0) : '—'}
                                        </td>
                                        {(data.warehouses || []).map((wh: any) => {
                                            const need = getArticleNeed(a, wh.name);
                                            return (
                                                <td key={wh.name} style={{
                                                    ...tdStyle,
                                                    background: need > 0 ? 'rgba(239,68,68,0.08)' : undefined,
                                                    color: need > 0 ? '#ef4444' : 'var(--color-text-muted)',
                                                    fontWeight: need > 0 ? 600 : 400,
                                                }}>
                                                    {need > 0 ? formatNumber(need, 0) : '—'}
                                                </td>
                                            );
                                        })}
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            ) : (
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-text">{data ? 'Нет артикулов по выбранным фильтрам' : 'Нет данных. Сначала синхронизируйте склады (вкладка «По складам»).'}</div>
                    </div>
                </div>
            )}
        </div>
    );
}

// ─── Warehouse Exclusion Settings ────────────────────────────────────────────

function WarehouseExclusionSettings() {
    const [warehouses, setWarehouses] = useState<Array<{ name: string; lat: number; lng: number }>>([]);
    const [excluded, setExcluded] = useState<string[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState('');
    const [hasChanges, setHasChanges] = useState(false);

    useEffect(() => {
        (async () => {
            try {
                const [wh, ex] = await Promise.all([
                    api.getWarehouses(),
                    api.getExcludedWarehouses(),
                ]);
                setWarehouses(wh);
                setExcluded(ex);
            } catch {
                setMsg('Ошибка загрузки складов');
            } finally {
                setLoading(false);
            }
        })();
    }, []);

    const toggle = (name: string) => {
        setExcluded(prev => {
            const next = prev.includes(name) ? prev.filter(n => n !== name) : [...prev, name];
            return next;
        });
        setHasChanges(true);
    };

    const save = async () => {
        setSaving(true);
        setMsg('');
        try {
            await api.setExcludedWarehouses(excluded);
            setMsg('✅ Сохранено');
            setHasChanges(false);
        } catch {
            setMsg('❌ Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    if (loading) return <div className="glass-card" style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-muted)' }}>Загрузка складов...</div>;

    const active = warehouses.filter(w => !excluded.includes(w.name));
    const excludedList = warehouses.filter(w => excluded.includes(w.name));

    return (
        <div>
            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <h3 style={{ margin: '0 0 8px' }}>🏭 Исключение складов</h3>
                <p style={{ color: 'var(--color-text-muted)', margin: '0 0 16px', fontSize: 14 }}>
                    Исключённые склады не участвуют в расчёте потребностей. Заказы из их регионов перераспределяются на ближайшие оставшиеся склады.
                </p>

                <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center' }}>
                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        Активных: <strong>{active.length}</strong> / {warehouses.length}
                    </span>
                    {excluded.length > 0 && (
                        <span style={{ fontSize: 13, color: 'var(--color-danger)', fontWeight: 500 }}>
                            ⛔ Исключено: {excluded.length}
                        </span>
                    )}
                </div>

                <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
                    gap: 8,
                }}>
                    {warehouses.map(w => {
                        const isExcluded = excluded.includes(w.name);
                        return (
                            <label
                                key={w.name}
                                style={{
                                    display: 'flex', alignItems: 'center', gap: 8,
                                    padding: '8px 12px',
                                    borderRadius: 8,
                                    border: `1px solid ${isExcluded ? 'var(--color-danger)' : 'var(--color-border)'}`,
                                    background: isExcluded ? 'rgba(239,68,68,0.08)' : 'var(--color-bg-card)',
                                    cursor: 'pointer',
                                    transition: 'all 0.15s',
                                    opacity: isExcluded ? 0.7 : 1,
                                }}
                            >
                                <input
                                    type="checkbox"
                                    checked={!isExcluded}
                                    onChange={() => toggle(w.name)}
                                    style={{ accentColor: 'var(--color-primary)' }}
                                />
                                <span style={{
                                    fontSize: 14,
                                    textDecoration: isExcluded ? 'line-through' : 'none',
                                    color: isExcluded ? 'var(--color-danger)' : 'var(--color-text)',
                                }}>
                                    {w.name}
                                </span>
                            </label>
                        );
                    })}
                </div>
            </div>

            <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                <button
                    className="btn btn-primary"
                    onClick={save}
                    disabled={saving || !hasChanges}
                >
                    {saving ? 'Сохранение...' : '💾 Сохранить'}
                </button>
                {msg && <span style={{ fontSize: 14 }}>{msg}</span>}
            </div>

            {excludedList.length > 0 && (
                <div className="glass-card" style={{ padding: 16, marginTop: 16 }}>
                    <p style={{ fontSize: 13, color: 'var(--color-text-muted)', margin: 0 }}>
                        ⚠️ Исключены: <strong>{excludedList.map(w => w.name).join(', ')}</strong>.
                        Заказы из этих регионов будут распределены на ближайшие активные склады.
                    </p>
                </div>
            )}
        </div>
    );
}

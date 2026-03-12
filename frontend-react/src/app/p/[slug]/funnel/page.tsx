'use client';
import { useEffect, useState, useCallback, useRef } from 'react';
import { api } from '@/lib/api';

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
    const [tab, setTab] = useState<'funnel' | 'day-analysis'>('funnel');
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
                    <div className="glass-card" style={{ overflow: 'auto', maxHeight: 'calc(100vh - 280px)' }}>
                        {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> : (
                            <table className="data-table" style={{ minWidth: detailed ? 1800 : 1200, borderCollapse: 'separate', borderSpacing: 0 }}>
                                <thead>
                                    <tr ref={headerRow1Ref}>
                                        <th rowSpan={2} style={{ position: 'sticky', left: 0, top: 0, background: '#1a1a2e', zIndex: 22, verticalAlign: 'bottom', borderBottom: '2px solid rgba(255,255,255,0.08)', minWidth: 90, borderRight: '1px solid rgba(255,255,255,0.08)' }}>ДАТА</th>
                                        {detailed && <th rowSpan={2} style={{ position: 'sticky', left: 90, top: 0, background: '#1a1a2e', zIndex: 22, verticalAlign: 'bottom', minWidth: 130, borderRight: '1px solid rgba(255,255,255,0.08)' }}>Артикул</th>}
                                        {detailed && <th rowSpan={2} style={{ position: 'sticky', top: 0, background: '#1a1a2e', zIndex: 20, verticalAlign: 'bottom' }}>nmId</th>}
                                        {detailed && <th rowSpan={2} style={{ position: 'sticky', top: 0, background: '#1a1a2e', zIndex: 20, verticalAlign: 'bottom' }}>Предмет</th>}
                                        {detailed && <th rowSpan={2} style={{ position: 'sticky', top: 0, background: '#1a1a2e', zIndex: 20, verticalAlign: 'bottom' }}>Бренд</th>}
                                        <th colSpan={5} style={{ position: 'sticky', top: 0, background: '#2a2517', textAlign: 'center', zIndex: 20 }}>ВОРОНКА</th>
                                        <th colSpan={7} style={{ position: 'sticky', top: 0, background: '#1f1e36', textAlign: 'center', zIndex: 20 }}>ВНУТРЕННЯЯ РЕКЛАМА</th>
                                        <th colSpan={4} style={{ position: 'sticky', top: 0, background: '#1a2a28', textAlign: 'center', zIndex: 20 }}>ФИНАНСЫ</th>
                                        <th colSpan={2} style={{ position: 'sticky', top: 0, background: '#2a1a28', textAlign: 'center', zIndex: 20 }}>КОНВЕРСИЯ</th>
                                    </tr>
                                    <tr>
                                        <th style={{ position: 'sticky', top: row1H, background: '#231f16', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>Переходы</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#231f16', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>Корзины</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#231f16', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>Заказы</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#231f16', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>Сумма ₽</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#231f16', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>Выручка ₽</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#1d1c30', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>Расходы ₽</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#1d1c30', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>Просмотры</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#1d1c30', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>Клики</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#1d1c30', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>CTR</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#1d1c30', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>CPC</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#1d1c30', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>CPM</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#1d1c30', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>ДРР</th>
                                        {detailed && <th style={{ position: 'sticky', top: row1H, background: '#1a2422', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>Себест. ₽</th>}
                                        <th style={{ position: 'sticky', top: row1H, background: '#1a2422', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>Налог ₽</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#1a2422', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>Прибыль ₽</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#1a2422', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>Маржа</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#1a2422', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>Ср. цена</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#241a24', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>В корзину</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#241a24', zIndex: 19, fontSize: 11, borderBottom: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>В заказ</th>
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
                                                <td style={{ position: 'sticky', left: 0, background: i % 2 === 0 ? '#1a1a2e' : '#1d1d31', zIndex: 2, whiteSpace: 'nowrap', fontSize: 12, minWidth: 90, borderRight: '1px solid rgba(255,255,255,0.06)' }}>{r.date}</td>
                                                {detailed && <td style={{ position: 'sticky', left: 90, background: i % 2 === 0 ? '#1a1a2e' : '#1d1d31', zIndex: 2, fontSize: 12, minWidth: 130, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 160, borderRight: '1px solid rgba(255,255,255,0.06)' }}>{r.vendor_code}</td>}
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
                                <div className="glass-card" style={{ overflow: 'auto', maxHeight: 'calc(100vh - 380px)' }}>
                                    <h3 style={{ margin: '12px 16px', fontSize: 14 }}>🏆 Топ товаров за {dayDate}</h3>
                                    <table className="data-table" style={{ minWidth: 900, borderCollapse: 'separate', borderSpacing: 0 }}>
                                        <thead>
                                            <tr>
                                                <th style={{ position: 'sticky', top: 0, background: '#1a1a2e', zIndex: 10 }}>#</th>
                                                <th style={{ position: 'sticky', top: 0, background: '#1a1a2e', zIndex: 10 }}>Артикул</th>
                                                <th style={{ position: 'sticky', top: 0, background: '#1a1a2e', zIndex: 10 }}>nmId</th>
                                                <th style={{ position: 'sticky', top: 0, background: '#1a1a2e', zIndex: 10 }}>Категория</th>
                                                <th style={{ position: 'sticky', top: 0, background: '#1a1a2e', zIndex: 10 }}>Бренд</th>
                                                <th style={{ position: 'sticky', top: 0, background: '#231f16', zIndex: 10 }}>Переходы</th>
                                                <th style={{ position: 'sticky', top: 0, background: '#231f16', zIndex: 10 }}>Корзины</th>
                                                <th style={{ position: 'sticky', top: 0, background: '#231f16', zIndex: 10 }}>Заказы</th>
                                                <th style={{ position: 'sticky', top: 0, background: '#231f16', zIndex: 10 }}>Выручка ₽</th>
                                                <th style={{ position: 'sticky', top: 0, background: '#1d1c30', zIndex: 10 }}>Реклама ₽</th>
                                                <th style={{ position: 'sticky', top: 0, background: '#1d1c30', zIndex: 10 }}>ДРР %</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {(dayReport.top_products || []).map((p: any, i: number) => (
                                                <tr key={i} style={{ background: i % 2 ? 'rgba(255,255,255,0.02)' : 'transparent' }}>
                                                    <td style={{ textAlign: 'center', color: '#888', fontSize: 11 }}>{i + 1}</td>
                                                    <td style={{ fontSize: 12 }}>{p.vendor_code}</td>
                                                    <td style={{ fontSize: 12 }}>
                                                        <a href={`https://www.wildberries.ru/catalog/${p.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>{p.nm_id}</a>
                                                    </td>
                                                    <td style={{ fontSize: 12 }}>{p.subject}</td>
                                                    <td style={{ fontSize: 12 }}>{p.brand}</td>
                                                    <td style={{ textAlign: 'right' }}>{dayFmt(p.open_card)}</td>
                                                    <td style={{ textAlign: 'right' }}>{dayFmt(p.add_to_cart)}</td>
                                                    <td style={{ textAlign: 'right', fontWeight: 600 }}>{dayFmt(p.orders_count)}</td>
                                                    <td style={{ textAlign: 'right', color: '#8b5cf6', fontWeight: 600 }}>{dayFmt(p.orders_sum)}</td>
                                                    <td style={{ textAlign: 'right', color: '#f59e0b' }}>{dayFmt(p.adv_sum)}</td>
                                                    <td style={{ textAlign: 'right', color: p.drr > 20 ? '#ef4444' : p.drr > 10 ? '#f59e0b' : '#10b981', fontWeight: 600 }}>
                                                        {p.drr.toFixed(1)}%
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </>
                        );
                    })()}
                </>
            )}
        </div >
    );
}

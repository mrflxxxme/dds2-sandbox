'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '@/lib/api';

/* ─── Format number ───────────────────────────────────────────── */
const fmt = (v: any) => {
    if (v == null) return '—';
    const n = Number(v);
    if (isNaN(n)) return String(v);
    return n.toLocaleString('ru-RU', { maximumFractionDigits: 2 });
};

/* ─── Trend canvas ────────────────────────────────────────────── */
function TrendChart({ data, fields, targetDate }: {
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
        const W = canvas.clientWidth;
        const H = canvas.clientHeight;
        canvas.width = W * dpr;
        canvas.height = H * dpr;
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, W, H);

        const pad = { top: 20, right: 20, bottom: 28, left: 10 };
        const cw = W - pad.left - pad.right;
        const ch = H - pad.top - pad.bottom;

        // Date labels
        ctx.fillStyle = '#666';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        data.forEach((d, i) => {
            const x = pad.left + (i / (data.length - 1 || 1)) * cw;
            const label = d.date.slice(5); // MM-DD
            if (i % Math.max(1, Math.floor(data.length / 8)) === 0 || i === data.length - 1) {
                ctx.fillText(label, x, H - 6);
            }
        });

        // Highlight target date column
        const targetIdx = data.findIndex((d: any) => d.date === targetDate);
        if (targetIdx >= 0) {
            const x = pad.left + (targetIdx / (data.length - 1 || 1)) * cw;
            ctx.fillStyle = 'rgba(139,92,246,0.12)';
            ctx.fillRect(x - 12, pad.top, 24, ch);
        }

        // Draw each field as a line
        fields.forEach((f) => {
            const values = data.map((d: any) => Number(d[f.key] || 0));
            const max = Math.max(...values, 1);
            const min = Math.min(...values, 0);
            const range = max - min || 1;

            ctx.beginPath();
            ctx.strokeStyle = f.color;
            ctx.lineWidth = 2;
            values.forEach((v, i) => {
                const x = pad.left + (i / (data.length - 1 || 1)) * cw;
                const y = pad.top + ch - ((v - min) / range) * ch;
                if (i === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            });
            ctx.stroke();

            // Dot on target
            if (targetIdx >= 0) {
                const x = pad.left + (targetIdx / (data.length - 1 || 1)) * cw;
                const y = pad.top + ch - ((values[targetIdx] - min) / range) * ch;
                ctx.beginPath();
                ctx.arc(x, y, 4, 0, Math.PI * 2);
                ctx.fillStyle = f.color;
                ctx.fill();
            }
        });
    }, [data, fields, targetDate]);

    return (
        <div style={{ position: 'relative' }}>
            <canvas ref={canvasRef} style={{ width: '100%', height: 200, display: 'block' }} />
            {/* Legend */}
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

/* ─── Main page ─────────────────────────────────────────────── */
export default function DayAnalysisPage() {
    const [targetDate, setTargetDate] = useState(() => {
        const d = new Date();
        d.setDate(d.getDate() - 1);
        return d.toISOString().slice(0, 10);
    });
    const [brand, setBrand] = useState('');
    const [subject, setSubject] = useState('');
    const [filters, setFilters] = useState<any>({ brands: [], subjects: [] });
    const [report, setReport] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [trendDays, setTrendDays] = useState(14);

    // Trend chart field selection
    const allTrendFields = [
        { key: 'orders_sum', label: 'Выручка', color: '#8b5cf6' },
        { key: 'adv_sum', label: 'Реклама', color: '#f59e0b' },
        { key: 'orders_count', label: 'Заказы', color: '#10b981' },
        { key: 'open_card', label: 'Переходы', color: '#3b82f6' },
        { key: 'drr', label: 'ДРР %', color: '#ef4444' },
    ];
    const [activeFields, setActiveFields] = useState<string[]>(['orders_sum', 'adv_sum']);

    const toggleField = (key: string) => {
        setActiveFields(prev =>
            prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]
        );
    };

    const loadFilters = useCallback(async () => {
        try {
            const f = await api.getFunnelFilters();
            setFilters(f);
        } catch { }
    }, []);

    const loadReport = useCallback(async () => {
        if (!targetDate) return;
        setLoading(true);
        try {
            const data = await api.getDayAnalysis({
                target_date: targetDate,
                brand: brand || undefined,
                subject: subject || undefined,
                trend_days: trendDays,
            });
            setReport(data);
        } catch (err: any) {
            console.error('Day analysis error:', err);
        } finally {
            setLoading(false);
        }
    }, [targetDate, brand, subject, trendDays]);

    useEffect(() => { loadFilters(); }, [loadFilters]);
    useEffect(() => { loadReport(); }, [loadReport]);

    const cmp = report?.comparison || {};

    const summaryCards = [
        { label: 'Выручка', key: 'orders_sum', icon: '💰', color: '#8b5cf6' },
        { label: 'Реклама', key: 'adv_sum', icon: '📢', color: '#f59e0b' },
        { label: 'ДРР', key: 'drr', icon: '📊', suffix: '%', color: '#ef4444' },
        { label: 'Заказы', key: 'orders_count', icon: '📦', color: '#10b981' },
        { label: 'Прибыль', key: 'profit', icon: '🏆', color: '#06b6d4' },
        { label: 'Переходы', key: 'open_card', icon: '👁', color: '#3b82f6' },
        { label: 'Корзины', key: 'add_to_cart', icon: '🛒', color: '#ec4899' },
    ];

    const selectedTrendFields = allTrendFields.filter(f => activeFields.includes(f.key));

    return (
        <div className="page-container">
            <div className="page-header" style={{ marginBottom: 16 }}>
                <h1>🔍 Анализ дня</h1>
            </div>

            {/* Filters */}
            <div className="glass-card" style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '10px 16px', marginBottom: 16, flexWrap: 'wrap' }}>
                <label style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Дата:</label>
                <input type="date" value={targetDate} onChange={e => setTargetDate(e.target.value)}
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
                <select value={trendDays} onChange={e => setTrendDays(Number(e.target.value))}
                    style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13 }}>
                    <option value={7}>7 дней</option>
                    <option value={14}>14 дней</option>
                    <option value={30}>30 дней</option>
                </select>
            </div>

            {loading && <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка...</div>}

            {report && !loading && (
                <>
                    {/* Summary cards with comparison */}
                    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${summaryCards.length}, 1fr)`, gap: 10, marginBottom: 16 }}>
                        {summaryCards.map(c => {
                            const comp = cmp[c.key];
                            const pct = comp?.change_pct ?? 0;
                            const isUp = pct > 0;
                            const isDown = pct < 0;
                            return (
                                <div key={c.key} className="glass-card" style={{ padding: '12px 14px' }}>
                                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>{c.icon} {c.label}</div>
                                    <div style={{ fontSize: 20, fontWeight: 700, color: c.color }}>
                                        {fmt(report.summary[c.key])}{c.suffix || ''}
                                    </div>
                                    {comp && (
                                        <div style={{ fontSize: 11, marginTop: 4, color: isUp ? '#10b981' : isDown ? '#ef4444' : '#666' }}>
                                            {isUp ? '↑' : isDown ? '↓' : '→'} {Math.abs(pct)}% vs вчера
                                            <span style={{ color: '#666', marginLeft: 6 }}>({fmt(comp.previous)})</span>
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>

                    {/* Trend chart */}
                    <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                            <h3 style={{ margin: 0, fontSize: 14 }}>📈 Тренд за {trendDays} дней</h3>
                            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                                {allTrendFields.map(f => (
                                    <button key={f.key} onClick={() => toggleField(f.key)}
                                        style={{
                                            padding: '3px 8px', fontSize: 11, borderRadius: 4, cursor: 'pointer', border: '1px solid',
                                            borderColor: activeFields.includes(f.key) ? f.color : 'rgba(255,255,255,0.1)',
                                            background: activeFields.includes(f.key) ? f.color + '22' : 'transparent',
                                            color: activeFields.includes(f.key) ? f.color : '#888',
                                        }}>{f.label}</button>
                                ))}
                            </div>
                        </div>
                        {report.trend?.length > 0 && selectedTrendFields.length > 0 && (
                            <TrendChart data={report.trend} fields={selectedTrendFields} targetDate={targetDate} />
                        )}
                    </div>

                    {/* Anomalies */}
                    {report.anomalies?.length > 0 && (
                        <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                            <h3 style={{ margin: '0 0 10px', fontSize: 14 }}>⚠️ Аномалии ({report.anomalies.length})</h3>
                            <div style={{ display: 'grid', gap: 8 }}>
                                {report.anomalies.map((a: any, i: number) => (
                                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 12px', background: 'rgba(239,68,68,0.06)', borderRadius: 6, border: '1px solid rgba(239,68,68,0.15)' }}>
                                        <div style={{ minWidth: 100 }}>
                                            <div style={{ fontSize: 12, fontWeight: 600 }}>{a.vendor_code || a.nm_id}</div>
                                            <div style={{ fontSize: 10, color: '#888' }}>{a.subject}</div>
                                        </div>
                                        <div style={{ flex: 1 }}>
                                            {a.flags.map((f: string, j: number) => (
                                                <span key={j} style={{ fontSize: 11, marginRight: 12 }}>{f}</span>
                                            ))}
                                        </div>
                                        <div style={{ textAlign: 'right', fontSize: 12 }}>
                                            <div>Выр: {fmt(a.orders_sum)}</div>
                                            <div style={{ color: '#f59e0b' }}>Рекл: {fmt(a.adv_sum)}</div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Top products table */}
                    <div className="glass-card" style={{ overflow: 'auto', maxHeight: 'calc(100vh - 380px)' }}>
                        <h3 style={{ margin: '12px 16px', fontSize: 14 }}>🏆 Топ товаров за {targetDate}</h3>
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
                                {(report.top_products || []).map((p: any, i: number) => {
                                    const rowBg = i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)';
                                    return (
                                        <tr key={i} style={{ background: rowBg }}>
                                            <td style={{ textAlign: 'center', color: '#888', fontSize: 11 }}>{i + 1}</td>
                                            <td style={{ fontSize: 12 }}>{p.vendor_code}</td>
                                            <td style={{ fontSize: 12 }}>
                                                <a href={`https://www.wildberries.ru/catalog/${p.nm_id}/detail.aspx`} target="_blank" rel="noreferrer"
                                                    style={{ color: 'var(--color-accent)' }}>{p.nm_id}</a>
                                            </td>
                                            <td style={{ fontSize: 12 }}>{p.subject}</td>
                                            <td style={{ fontSize: 12 }}>{p.brand}</td>
                                            <td style={{ textAlign: 'right' }}>{fmt(p.open_card)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmt(p.add_to_cart)}</td>
                                            <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(p.orders_count)}</td>
                                            <td style={{ textAlign: 'right', color: '#8b5cf6', fontWeight: 600 }}>{fmt(p.orders_sum)}</td>
                                            <td style={{ textAlign: 'right', color: '#f59e0b' }}>{fmt(p.adv_sum)}</td>
                                            <td style={{ textAlign: 'right', color: p.drr > 20 ? '#ef4444' : p.drr > 10 ? '#f59e0b' : '#10b981', fontWeight: 600 }}>
                                                {p.drr.toFixed(1)}%
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </>
            )}
        </div>
    );
}

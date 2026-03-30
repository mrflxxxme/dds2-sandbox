'use client';
import { useState, useCallback, useEffect } from 'react';
import { api } from '@/lib/api';
import { DayTrendChart } from './DayTrendChart';

const dayFmt = (v: any) => { if (v == null) return '—'; const n = Number(v); return isNaN(n) ? String(v) : n.toLocaleString('ru-RU', { maximumFractionDigits: 2 }); };

const dayTrendFields = [
    { key: 'orders_sum', label: 'Выручка', color: '#8b5cf6' },
    { key: 'adv_sum', label: 'Реклама', color: '#f59e0b' },
    { key: 'orders_count', label: 'Заказы', color: '#10b981' },
    { key: 'open_card', label: 'Переходы', color: '#3b82f6' },
    { key: 'drr', label: 'ДРР %', color: '#ef4444' },
];

export function DayAnalysisTab({ brand, subject, filters }: {
    brand: string;
    subject: string;
    filters: any;
}) {
    const [dayReport, setDayReport] = useState<any>(null);
    const [dayLoading, setDayLoading] = useState(false);
    const [dayDate, setDayDate] = useState(() => { const d = new Date(); d.setDate(d.getDate() - 1); return d.toISOString().slice(0, 10); });
    const [dayTrendDays, setDayTrendDays] = useState(14);
    const [dayActiveFields, setDayActiveFields] = useState<string[]>(['orders_sum', 'adv_sum']);

    const loadDayReport = useCallback(async () => {
        setDayLoading(true);
        try {
            const data = await api.getDayAnalysis({ target_date: dayDate, brand: brand || undefined, subject: subject || undefined, trend_days: dayTrendDays });
            setDayReport(data);
        } catch (err: any) { console.error('Day analysis error:', err); }
        finally { setDayLoading(false); }
    }, [dayDate, brand, subject, dayTrendDays]);

    useEffect(() => { loadDayReport(); }, [loadDayReport]);

    const summaryCards = [
        { label: 'Выручка', key: 'orders_sum', icon: '💰', color: '#8b5cf6' },
        { label: 'Реклама', key: 'adv_sum', icon: '📢', color: '#f59e0b' },
        { label: 'ДРР', key: 'drr', icon: '📊', suffix: '%', color: '#ef4444' },
        { label: 'Заказы', key: 'orders_count', icon: '📦', color: '#10b981' },
        { label: 'Прибыль', key: 'profit', icon: '🏆', color: '#06b6d4' },
        { label: 'Переходы', key: 'open_card', icon: '👁', color: '#3b82f6' },
        { label: 'Корзины', key: 'add_to_cart', icon: '🛒', color: '#ec4899' },
    ];

    return (
        <>
            {/* Filters */}
            <div className="glass-card" style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '10px 16px', marginBottom: 16, flexWrap: 'wrap' }}>
                <label style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Дата:</label>
                <input type="date" value={dayDate} onChange={e => setDayDate(e.target.value)}
                    style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13 }} />
                <label style={{ fontSize: 13, color: 'var(--color-text-dim)', marginLeft: 8 }}>Бренд:</label>
                <select value={brand} disabled
                    style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13, maxWidth: 160 }}>
                    <option value="">Все</option>
                    {(filters.brands || []).map((b: string) => <option key={b} value={b}>{b}</option>)}
                </select>
                <label style={{ fontSize: 13, color: 'var(--color-text-dim)', marginLeft: 8 }}>Категория:</label>
                <select value={subject} disabled
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
                        {/* TODO: migrate to TanStackDataTable — complex table with sticky header rows, alternating row backgrounds, external links */}
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
    );
}

'use client';
import { useState, useCallback, useEffect } from 'react';
import { api } from '@/lib/api';
import { DayTrendChart } from './DayTrendChart';
import { CARD_TOOLBAR, CARD_FOOTER, thFlat, Segmented, StatCard } from './funnelUi';
import { IcCalendar, IcChart, IcFlame } from '../../ads-manager/components/icons';

const dayFmt = (v: any) => { if (v == null) return '—'; const n = Number(v); return isNaN(n) ? String(v) : n.toLocaleString('ru-RU', { maximumFractionDigits: 2 }); };

const dayTrendFields = [
    { key: 'orders_sum', label: 'Выручка', color: '#8b5cf6' },
    { key: 'adv_sum', label: 'Реклама', color: '#f59e0b' },
    { key: 'orders_count', label: 'Заказы', color: '#10b981' },
    { key: 'open_card', label: 'Переходы', color: '#3b82f6' },
    { key: 'drr', label: 'ДРР %', color: '#ef4444' },
];

// `filters` больше не нужен: бренд/категорию задаёт общий фильтр раздела, здесь они
// показаны чипами. Проп оставлен в сигнатуре, чтобы не трогать вызов на странице.
export function DayAnalysisTab({ brand, subject }: {
    brand: string;
    subject: string;
    filters?: any;
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
        { label: 'Выручка', key: 'orders_sum', color: '#8b5cf6' },
        { label: 'Реклама', key: 'adv_sum', color: '#f59e0b' },
        { label: 'ДРР', key: 'drr', suffix: '%', color: '#ef4444' },
        { label: 'Заказы', key: 'orders_count', color: '#10b981' },
        { label: 'Прибыль', key: 'profit', color: '#06b6d4' },
        { label: 'Переходы', key: 'open_card', color: '#3b82f6' },
        { label: 'Корзины', key: 'add_to_cart', color: '#ec4899' },
    ];

    // Бренд/категория задаются общим фильтром раздела (он скрыт на этой вкладке) —
    // показываем их как неизменяемые чипы, а не как мёртвые выпадающие списки.
    const chip = (label: string, value: string) => (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--color-text-dim)', background: '#fff', border: '1px solid var(--color-border)', borderRadius: 8, padding: '5px 10px', whiteSpace: 'nowrap' }}>
            {label}: <strong style={{ color: 'var(--color-text)', fontWeight: 600 }}>{value || 'все'}</strong>
        </span>
    );

    return (
        <>
            {/* Filters */}
            <div className="glass-card static" style={{ ...CARD_TOOLBAR, border: '1px solid var(--color-border)', borderRadius: 12, marginBottom: 12 }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text-dim)' }}>
                    <IcCalendar size={15} />Дата
                </span>
                <input type="date" value={dayDate} onChange={e => setDayDate(e.target.value)}
                    style={{ background: '#fff', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', color: 'var(--color-text)', fontSize: 13 }} />
                {chip('Бренд', brand)}
                {chip('Категория', subject)}
                <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Тренд</span>
                    <Segmented value={String(dayTrendDays)} onChange={v => setDayTrendDays(Number(v))} compact
                        options={[{ key: '7', label: '7 дней' }, { key: '14', label: '14 дней' }, { key: '30', label: '30 дней' }]} />
                </span>
            </div>

            {dayLoading && <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка...</div>}

            {dayReport && !dayLoading && (() => {
                const cmp = dayReport.comparison || {};
                const selectedFields = dayTrendFields.filter(f => dayActiveFields.includes(f.key));

                // Split anomalies into positive and negative
                const positiveAnomalies = (dayReport.anomalies || []).filter((a: any) => a.flags.some((f: string) => f.includes('📈')));
                const negativeAnomalies = (dayReport.anomalies || []).filter((a: any) => a.flags.some((f: string) => f.includes('📉') || f.includes('⚠️') || f.includes('🚫')));

                const AnomalyRow = ({ a, positive }: { a: any; positive: boolean }) => (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 8px', fontSize: 11, borderBottom: '1px solid #f3f4f6', whiteSpace: 'nowrap' }}>
                        <span style={{ fontSize: 12, flexShrink: 0, color: positive ? '#10b981' : '#ef4444' }}>{positive ? '↑' : '↓'}</span>
                        <span style={{ fontWeight: 600, maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', flexShrink: 0 }} title={a.vendor_code || String(a.nm_id)}>{a.vendor_code || a.nm_id}</span>
                        <span style={{ color: '#6b7280', fontSize: 10, maxWidth: 80, overflow: 'hidden', textOverflow: 'ellipsis', flexShrink: 0 }} title={a.subject}>{a.subject}</span>
                        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', color: '#9ca3af', fontSize: 10 }} title={a.flags.join(' · ')}>{a.flags.join(' · ')}</span>
                        <span style={{ color: '#8b5cf6', fontWeight: 600, flexShrink: 0, minWidth: 60, textAlign: 'right' }}>₽{dayFmt(a.orders_sum)}</span>
                        <span style={{ color: '#f59e0b', flexShrink: 0, minWidth: 50, textAlign: 'right' }}>₽{dayFmt(a.adv_sum)}</span>
                    </div>
                );

                return (
                    <>
                        {/* Summary cards */}
                        <div style={{ display: 'grid', gridTemplateColumns: `repeat(${summaryCards.length}, 1fr)`, gap: 6, marginBottom: 12 }}>
                            {summaryCards.map(c => {
                                const comp = cmp[c.key];
                                const pct = comp?.change_pct ?? 0;
                                const isUp = pct > 0, isDown = pct < 0;
                                return (
                                    <StatCard key={c.key} label={c.label} color={c.color}
                                        value={dayFmt(dayReport.summary[c.key]) + (c.suffix || '')}
                                        hint={comp ? (
                                            <div style={{ fontSize: 11, marginTop: 2, color: isUp ? '#10b981' : isDown ? '#ef4444' : 'var(--color-text-dim)', whiteSpace: 'nowrap' }}>
                                                {isUp ? '↑' : isDown ? '↓' : '→'} {Math.abs(pct)}% vs вчера
                                                <span style={{ color: 'var(--color-text-dim)', marginLeft: 5 }}>({dayFmt(comp.previous)})</span>
                                            </div>
                                        ) : undefined} />
                                );
                            })}
                        </div>

                        {/* Trend chart */}
                        <div className="glass-card static" style={{ padding: '12px 16px', marginBottom: 12, borderRadius: 12 }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, gap: 12, flexWrap: 'wrap' }}>
                                <h3 style={{ margin: 0, fontSize: 13, fontWeight: 700, display: 'inline-flex', alignItems: 'center', gap: 7, color: 'var(--color-text)' }}>
                                    <IcChart size={15} />Тренд за {dayTrendDays} дней
                                </h3>
                                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                                    {dayTrendFields.map(f => {
                                        const on = dayActiveFields.includes(f.key);
                                        return (
                                            <button key={f.key} onClick={() => setDayActiveFields((prev: string[]) => prev.includes(f.key) ? prev.filter((k: string) => k !== f.key) : [...prev, f.key])}
                                                style={{
                                                    padding: '4px 10px', fontSize: 12, fontWeight: on ? 600 : 500, borderRadius: 8, cursor: 'pointer',
                                                    border: `1px solid ${on ? f.color : 'var(--color-border)'}`,
                                                    background: on ? f.color + '14' : '#fff',
                                                    color: on ? f.color : '#6b7280',
                                                }}>{f.label}</button>
                                        );
                                    })}
                                </div>
                            </div>
                            {dayReport.trend?.length > 0 && selectedFields.length > 0 && (
                                <DayTrendChart data={dayReport.trend} fields={selectedFields} targetDate={dayDate} />
                            )}
                        </div>

                        {/* Anomalies — split into positive & negative */}
                        {(positiveAnomalies.length > 0 || negativeAnomalies.length > 0) && (
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
                                {/* Positive */}
                                <div className="glass-card static" style={{ padding: '12px 14px', borderRadius: 12, borderLeft: '3px solid #10b981', display: 'flex', flexDirection: 'column' as const, minWidth: 0, overflow: 'hidden' }}>
                                    <h4 style={{ margin: '0 0 8px', fontSize: 13, fontWeight: 700, color: '#10b981', flexShrink: 0 }}>Рост ({positiveAnomalies.length})</h4>
                                    <div style={{ maxHeight: 340, overflowY: 'auto' as const, flex: 1 }}>
                                        {positiveAnomalies.length === 0 ? <div style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>Нет аномалий роста</div> :
                                            positiveAnomalies.map((a: any, i: number) => <AnomalyRow key={i} a={a} positive />)
                                        }
                                    </div>
                                </div>
                                {/* Negative */}
                                <div className="glass-card static" style={{ padding: '12px 14px', borderRadius: 12, borderLeft: '3px solid #ef4444', display: 'flex', flexDirection: 'column' as const, minWidth: 0, overflow: 'hidden' }}>
                                    <h4 style={{ margin: '0 0 8px', fontSize: 13, fontWeight: 700, color: '#ef4444', flexShrink: 0 }}>Снижение / Проблемы ({negativeAnomalies.length})</h4>
                                    <div style={{ maxHeight: 340, overflowY: 'auto' as const, flex: 1 }}>
                                        {negativeAnomalies.length === 0 ? <div style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>Нет проблемных товаров</div> :
                                            negativeAnomalies.map((a: any, i: number) => <AnomalyRow key={i} a={a} positive={false} />)
                                        }
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Top products table */}
                        {/* TODO: migrate to TanStackDataTable — complex table with sticky header rows, alternating row backgrounds, external links */}
                        <div className="glass-card static" style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                            <div style={CARD_TOOLBAR}>
                                <h3 style={{ margin: 0, fontSize: 13, fontWeight: 700, display: 'inline-flex', alignItems: 'center', gap: 7, color: 'var(--color-text)' }}>
                                    <IcFlame size={15} />Топ товаров за {dayDate}
                                </h3>
                            </div>
                            <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 380px)' }}>
                                <table className="data-table" style={{ minWidth: 900, borderCollapse: 'separate', borderSpacing: 0 }}>
                                    <thead>
                                        <tr>
                                            <th style={{ ...thFlat, textAlign: 'center' }}>#</th>
                                            <th style={{ ...thFlat, textAlign: 'left' }}>Артикул</th>
                                            <th style={{ ...thFlat, textAlign: 'left' }}>nmId</th>
                                            <th style={{ ...thFlat, textAlign: 'left' }}>Категория</th>
                                            <th style={{ ...thFlat, textAlign: 'left' }}>Бренд</th>
                                            <th style={thFlat}>Переходы</th>
                                            <th style={thFlat}>Корзины</th>
                                            <th style={thFlat}>Заказы</th>
                                            <th style={thFlat}>Выручка ₽</th>
                                            <th style={thFlat}>Реклама ₽</th>
                                            <th style={thFlat}>ДРР %</th>
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
                            <div style={CARD_FOOTER}>Всего товаров: {(dayReport.top_products || []).length}</div>
                        </div>
                    </>
                );
            })()}
        </>
    );
}

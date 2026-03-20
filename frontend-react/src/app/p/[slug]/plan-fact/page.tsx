'use client';

import { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import type { PlanFactDailyResult, PlanFactBrandRow } from '@/types/api';
import {
    ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip,
    ResponsiveContainer, Cell,
} from 'recharts';

const MONTH_NAMES = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

export default function PlanFactPage() {
    const [tab, setTab] = useState<'daily' | 'brands'>('daily');
    const [year, setYear] = useState(new Date().getFullYear());
    const [month, setMonth] = useState(new Date().getMonth() + 1);
    const [brand, setBrand] = useState('');
    const [brands, setBrands] = useState<string[]>([]);

    const [dailyData, setDailyData] = useState<PlanFactDailyResult | null>(null);
    const [brandData, setBrandData] = useState<PlanFactBrandRow[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const loadBrands = useCallback(async () => {
        try {
            const b = await api.getWbBrands();
            setBrands(b);
            if (b.length && !brand) setBrand(b[0]);
        } catch { /* ignore */ }
    }, []);

    useEffect(() => { loadBrands(); }, [loadBrands]);

    const loadDailyData = useCallback(async () => {
        if (!brand) return;
        setLoading(true);
        setError('');
        try {
            setDailyData(await api.getPlanFactDaily(brand, year, month));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Error');
        } finally {
            setLoading(false);
        }
    }, [brand, year, month]);

    const loadBrandData = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setBrandData(await api.getPlanFactBrands(year, month));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Error');
        } finally {
            setLoading(false);
        }
    }, [year, month]);

    useEffect(() => {
        if (tab === 'daily') loadDailyData();
        else loadBrandData();
    }, [tab, loadDailyData, loadBrandData]);

    const statusIcon = (forecast: number, planAdj: number) => {
        if (planAdj <= 0) return '';
        const ratio = forecast / planAdj;
        if (ratio >= 0.95) return ' \u2705';
        if (ratio >= 0.7) return ' \u26A0\uFE0F';
        return ' \u274C';
    };

    const pctColor = (pct: number | null) => {
        if (pct === null) return 'var(--color-text-muted)';
        if (pct >= 100) return 'var(--color-success)';
        if (pct >= 70) return 'var(--color-warning)';
        return 'var(--color-danger)';
    };

    const handleExport = () => {
        if (tab === 'daily' && dailyData) {
            exportToExcel(dailyData.rows.map(r => ({
                'Дата': r.dt,
                'Факт/день': r.fact_day,
                'План/день': r.plan_day,
                'Факт накоп.': r.fact_cumulative,
                'План накоп.': r.plan_cumulative,
                '%': r.pct ?? '',
            })), `plan_fact_${brand}_${year}_${month}`);
        } else if (tab === 'brands') {
            exportToExcel(brandData.map(r => ({
                'Бренд': r.brand,
                'План': r.plan_month,
                'Долг': r.debt_prev,
                'Скорр. план': r.plan_adjusted,
                'Факт': r.fact_mtd,
                '%': r.pct ?? '',
                'Прогноз': r.forecast,
            })), `plan_fact_brands_${year}_${month}`);
        }
    };

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">План-Факт</h1>
                    <p className="page-subtitle">Отслеживание выполнения плана по выручке</p>
                </div>
                <button className="btn btn-secondary btn-sm" onClick={handleExport}>
                    📥 Экспорт Excel
                </button>
            </div>

            {/* Tabs */}
            <div style={{ display: 'flex', gap: 4, marginBottom: 20, flexWrap: 'wrap' }}>
                <button className={`btn ${tab === 'daily' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                    onClick={() => setTab('daily')}>По дням</button>
                <button className={`btn ${tab === 'brands' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                    onClick={() => setTab('brands')}>По брендам</button>
            </div>

            {/* Filters */}
            <div className="glass-card" style={{ marginBottom: 20, padding: 16 }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'end', flexWrap: 'wrap' }}>
                    {tab === 'daily' && (
                        <div className="form-group">
                            <label className="form-label">Бренд</label>
                            <select className="form-input" style={{ minWidth: 180 }}
                                value={brand} onChange={e => setBrand(e.target.value)}>
                                {brands.map(b => <option key={b} value={b}>{b}</option>)}
                            </select>
                        </div>
                    )}
                    <div className="form-group">
                        <label className="form-label">Период</label>
                        <div style={{ display: 'flex', gap: 4 }}>
                            {(() => {
                                const now = new Date();
                                const curM = now.getMonth() + 1;
                                const curY = now.getFullYear();
                                const prevDate = new Date(curY, curM - 2, 1);
                                const prevM = prevDate.getMonth() + 1;
                                const prevY = prevDate.getFullYear();
                                const isCurrentMonth = month === curM && year === curY;
                                const isPrevMonth = month === prevM && year === prevY;
                                return (
                                    <>
                                        <button
                                            className={`btn ${isCurrentMonth ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                                            onClick={() => { setMonth(curM); setYear(curY); }}
                                        >Тек. месяц</button>
                                        <button
                                            className={`btn ${isPrevMonth ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                                            onClick={() => { setMonth(prevM); setYear(prevY); }}
                                        >Пред.</button>
                                    </>
                                );
                            })()}
                        </div>
                    </div>
                    <div className="form-group">
                        <label className="form-label">Месяц</label>
                        <select className="form-input" style={{ minWidth: 140 }}
                            value={month} onChange={e => setMonth(Number(e.target.value))}>
                            {MONTH_NAMES.map((name, i) => (
                                <option key={i} value={i + 1}>{name}</option>
                            ))}
                        </select>
                    </div>
                    <div className="form-group">
                        <label className="form-label">Год</label>
                        <input type="number" className="form-input" style={{ width: 90 }}
                            value={year} onChange={e => setYear(Number(e.target.value))} />
                    </div>
                </div>
            </div>

            {loading ? (
                <div className="glass-card">
                    <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
                </div>
            ) : error ? (
                <div className="glass-card">
                    <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-danger)' }}>{error}</div>
                </div>
            ) : tab === 'daily' ? (
                dailyData && dailyData.rows.length > 0 ? (
                    <DailyTab data={dailyData} pctColor={pctColor} statusIcon={statusIcon} />
                ) : (
                    <div className="glass-card">
                        <div className="empty-state">
                            <div className="empty-state-icon">🎯</div>
                            <div className="empty-state-text">Нет данных за выбранный период</div>
                            <p style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                                Убедитесь что планы заданы в Настройки → План по брендам
                            </p>
                        </div>
                    </div>
                )
            ) : (
                <BrandsTab data={brandData} pctColor={pctColor} statusIcon={statusIcon} />
            )}
        </div>
    );
}

function DailyTab({ data, pctColor, statusIcon }: {
    data: PlanFactDailyResult;
    pctColor: (p: number | null) => string;
    statusIcon: (f: number, p: number) => string;
}) {
    const chartData = data.rows.map(r => ({
        ...r,
        day: r.dt.split('-')[2],
    }));

    return (
        <>
            {/* Summary cards */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 20 }}>
                <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 4 }}>План (скорр.)</div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>{formatNumber(data.plan_adjusted, 0)}</div>
                    {data.debt_prev > 0 && (
                        <div style={{ fontSize: 12, color: 'var(--color-warning)', marginTop: 4 }}>
                            вкл. долг +{formatNumber(data.debt_prev, 0)}
                        </div>
                    )}
                </div>
                <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 4 }}>Факт</div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>{formatNumber(data.fact_mtd, 0)}</div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: pctColor(data.pct), marginTop: 4 }}>
                        {data.pct !== null ? `${data.pct}%` : '\u2014'}
                    </div>
                </div>
                <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 4 }}>Прогноз</div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>{formatNumber(data.forecast, 0)}</div>
                    <div style={{ fontSize: 14, marginTop: 4 }}>
                        {statusIcon(data.forecast, data.plan_adjusted)}
                    </div>
                </div>
            </div>

            {/* Chart */}
            {chartData.length > 0 && (
                <div className="glass-card" style={{ marginBottom: 20, padding: 20 }}>
                    <ResponsiveContainer width="100%" height={300}>
                        <ComposedChart data={chartData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                            <XAxis dataKey="day" tick={{ fontSize: 12 }} />
                            <YAxis tickFormatter={v => `${(v / 1_000_000).toFixed(1)}M`} tick={{ fontSize: 12 }} />
                            <Tooltip
                                formatter={(value: number) => formatNumber(value, 0)}
                                labelFormatter={l => `День ${l}`}
                                contentStyle={{ borderRadius: 12, border: '1px solid var(--color-border)', boxShadow: '0 4px 12px rgba(0,0,0,0.08)' }}
                            />
                            <Bar dataKey="fact_day" name="Факт" radius={[4, 4, 0, 0]}>
                                {chartData.map((entry, i) => (
                                    <Cell
                                        key={i}
                                        fill={entry.is_future ? 'transparent' : entry.fact_day >= entry.plan_day ? 'var(--color-success)' : 'var(--color-danger)'}
                                    />
                                ))}
                            </Bar>
                            <Line
                                type="monotone"
                                dataKey="plan_day"
                                name="Адаптивный план"
                                stroke="var(--color-warning)"
                                strokeWidth={2}
                                dot={false}
                            />
                        </ComposedChart>
                    </ResponsiveContainer>
                </div>
            )}

            {/* Table */}
            <div className="glass-card">
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Дата</th>
                                <th style={{ textAlign: 'right' }}>Факт/день</th>
                                <th style={{ textAlign: 'right' }}>План/день</th>
                                <th style={{ textAlign: 'right' }}>Факт накоп.</th>
                                <th style={{ textAlign: 'right' }}>План накоп.</th>
                                <th style={{ textAlign: 'right' }}>%</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data.rows.map(r => (
                                <tr key={r.dt} style={r.is_future ? { opacity: 0.45 } : undefined}>
                                    <td style={{ fontSize: 13, whiteSpace: 'nowrap' }}>
                                        {r.dt.slice(5).replace('-', '.')}
                                    </td>
                                    <td style={{ textAlign: 'right', fontFamily: 'monospace', fontWeight: 600 }}>
                                        {r.is_future ? '\u2014' : formatNumber(r.fact_day, 0)}
                                    </td>
                                    <td style={{ textAlign: 'right', fontFamily: 'monospace', fontWeight: 500, color: 'var(--color-text-muted)' }}>
                                        {formatNumber(r.plan_day, 0)}
                                    </td>
                                    <td style={{ textAlign: 'right', fontFamily: 'monospace', fontWeight: 600 }}>
                                        {formatNumber(r.fact_cumulative, 0)}
                                    </td>
                                    <td style={{ textAlign: 'right', fontFamily: 'monospace', fontWeight: 500, color: 'var(--color-text-muted)' }}>
                                        {formatNumber(r.plan_cumulative, 0)}
                                    </td>
                                    <td style={{ textAlign: 'right', fontWeight: 600, color: pctColor(r.pct) }}>
                                        {r.pct !== null ? `${r.pct}%` : '\u2014'}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </>
    );
}

function BrandsTab({ data, pctColor, statusIcon }: {
    data: PlanFactBrandRow[];
    pctColor: (p: number | null) => string;
    statusIcon: (f: number, p: number) => string;
}) {
    if (data.length === 0) {
        return (
            <div className="glass-card">
                <div className="empty-state">
                    <div className="empty-state-icon">📊</div>
                    <div className="empty-state-text">Нет планов на этот месяц</div>
                    <p style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                        Задайте планы в Настройки → План по брендам
                    </p>
                </div>
            </div>
        );
    }

    const totalPlan = data.reduce((s, r) => s + Number(r.plan_adjusted), 0);
    const totalFact = data.reduce((s, r) => s + Number(r.fact_mtd), 0);
    const totalForecast = data.reduce((s, r) => s + Number(r.forecast), 0);
    const totalPct = totalPlan > 0 ? totalFact / totalPlan * 100 : null;

    return (
        <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
                {data.map(r => (
                    <div key={r.brand} className="glass-card" style={{ padding: 20 }}>
                        <h6 style={{ margin: '0 0 16px', fontWeight: 600, fontSize: 16 }}>{r.brand}</h6>

                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, fontSize: 14 }}>
                            <span style={{ color: 'var(--color-text-muted)' }}>План</span>
                            <span style={{ fontFamily: 'monospace', fontWeight: 500 }}>{formatNumber(r.plan_month, 0)}</span>
                        </div>
                        {r.debt_prev > 0 && (
                            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, fontSize: 14, color: 'var(--color-warning)' }}>
                                <span>Долг (перенос)</span>
                                <span style={{ fontFamily: 'monospace', fontWeight: 500 }}>+{formatNumber(r.debt_prev, 0)}</span>
                            </div>
                        )}
                        {r.debt_prev > 0 && (
                            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, fontSize: 14, fontWeight: 600 }}>
                                <span>Скорр. план</span>
                                <span style={{ fontFamily: 'monospace' }}>{formatNumber(r.plan_adjusted, 0)}</span>
                            </div>
                        )}
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 14, fontSize: 15, fontWeight: 600 }}>
                            <span>Факт</span>
                            <span style={{ fontFamily: 'monospace' }}>{formatNumber(r.fact_mtd, 0)}</span>
                        </div>

                        {/* Progress bar */}
                        <div style={{ background: 'var(--color-border)', borderRadius: 8, height: 8, overflow: 'hidden', marginBottom: 14 }}>
                            <div style={{
                                width: `${Math.min(r.pct ?? 0, 100)}%`,
                                height: '100%',
                                background: (r.pct ?? 0) >= 100 ? 'var(--color-success)' : (r.pct ?? 0) >= 70 ? 'var(--color-warning)' : 'var(--color-danger)',
                                borderRadius: 8,
                                transition: 'width 0.3s ease',
                            }} />
                        </div>

                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14 }}>
                            <span style={{ fontWeight: 600, color: pctColor(r.pct) }}>
                                {r.pct !== null ? `${r.pct}%` : '\u2014'}
                            </span>
                            <span style={{ color: 'var(--color-text-muted)' }}>
                                Прогноз: {formatNumber(r.forecast, 0)}
                                {statusIcon(r.forecast, r.plan_adjusted)}
                            </span>
                        </div>
                    </div>
                ))}
            </div>

            {/* Totals */}
            <div className="glass-card" style={{ marginTop: 20, padding: '16px 24px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 16, fontWeight: 600, fontSize: 15 }}>
                    <span>Итого</span>
                    <span>План: <span style={{ fontFamily: 'monospace' }}>{formatNumber(totalPlan, 0)}</span></span>
                    <span>Факт: <span style={{ fontFamily: 'monospace' }}>{formatNumber(totalFact, 0)}</span></span>
                    <span style={{ color: pctColor(totalPct) }}>
                        {totalPct !== null ? `${totalPct.toFixed(1)}%` : ''}
                    </span>
                    <span>Прогноз: <span style={{ fontFamily: 'monospace' }}>{formatNumber(totalForecast, 0)}</span>
                        {statusIcon(totalForecast, totalPlan)}
                    </span>
                </div>
            </div>
        </>
    );
}

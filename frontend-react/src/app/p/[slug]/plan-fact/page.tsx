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
        if (pct === null) return '';
        if (pct >= 100) return 'text-success';
        if (pct >= 70) return 'text-warning';
        return 'text-danger';
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
                <button className="btn btn-outline-secondary btn-sm" onClick={handleExport}>
                    Excel
                </button>
            </div>

            <div className="table-toolbar" style={{ marginBottom: 16 }}>
                <button className={`btn ${tab === 'daily' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                    onClick={() => setTab('daily')}>По дням</button>
                <button className={`btn ${tab === 'brands' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                    onClick={() => setTab('brands')}>По брендам</button>

                <div style={{ flex: 1 }} />

                {tab === 'daily' && (
                    <select className="form-select form-select-sm" style={{ width: 180 }}
                        value={brand} onChange={e => setBrand(e.target.value)}>
                        {brands.map(b => <option key={b} value={b}>{b}</option>)}
                    </select>
                )}
                <select className="form-select form-select-sm" style={{ width: 140 }}
                    value={month} onChange={e => setMonth(Number(e.target.value))}>
                    {MONTH_NAMES.map((name, i) => (
                        <option key={i} value={i + 1}>{name}</option>
                    ))}
                </select>
                <input type="number" className="form-control form-control-sm" style={{ width: 80 }}
                    value={year} onChange={e => setYear(Number(e.target.value))} />
            </div>

            {loading && <div className="text-center py-8 text-secondary">Загрузка...</div>}
            {error && <div className="text-center py-8 text-danger">{error}</div>}

            {!loading && !error && tab === 'daily' && dailyData && (
                <DailyTab data={dailyData} pctColor={pctColor} statusIcon={statusIcon} />
            )}
            {!loading && !error && tab === 'brands' && (
                <BrandsTab data={brandData} pctColor={pctColor} statusIcon={statusIcon} />
            )}

            {!loading && !error && tab === 'daily' && !dailyData?.rows.length && (
                <div className="text-center py-8 text-secondary">
                    Нет данных. Убедитесь что планы заданы в Настройки &rarr; План по брендам
                </div>
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
            {chartData.length > 0 && (
                <div className="card" style={{ marginBottom: 20 }}>
                    <div style={{ padding: 20 }}>
                        <ResponsiveContainer width="100%" height={300}>
                            <ComposedChart data={chartData}>
                                <CartesianGrid strokeDasharray="3 3" />
                                <XAxis dataKey="day" />
                                <YAxis tickFormatter={v => `${(v / 1_000_000).toFixed(1)}M`} />
                                <Tooltip
                                    formatter={(value: number) => formatNumber(value, 0)}
                                    labelFormatter={l => `День ${l}`}
                                />
                                <Bar dataKey="fact_day" name="Факт" radius={[2, 2, 0, 0]}>
                                    {chartData.map((entry, i) => (
                                        <Cell
                                            key={i}
                                            fill={entry.fact_day >= entry.plan_day ? '#22c55e' : '#ef4444'}
                                        />
                                    ))}
                                </Bar>
                                <Line
                                    type="monotone"
                                    dataKey="plan_day"
                                    name="Адаптивный план"
                                    stroke="#f59e0b"
                                    strokeWidth={2}
                                    dot={false}
                                />
                            </ComposedChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            )}

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
                            <tr key={r.dt}>
                                <td>{r.dt.slice(5).replace('-', '.')}</td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(r.fact_day, 0)}</td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(r.plan_day, 0)}</td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(r.fact_cumulative, 0)}</td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(r.plan_cumulative, 0)}</td>
                                <td style={{ textAlign: 'right' }} className={pctColor(r.pct)}>
                                    {r.pct !== null ? `${r.pct}%` : '\u2014'}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            <div className="card" style={{ marginTop: 16 }}>
                <div style={{ padding: '16px 20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 16 }}>
                    <div>
                        <strong>План:</strong> {formatNumber(data.plan_adjusted, 0)}
                        {data.debt_prev > 0 && (
                            <span className="text-warning" style={{ marginLeft: 8 }}>
                                (вкл. долг {formatNumber(data.debt_prev, 0)})
                            </span>
                        )}
                    </div>
                    <div>
                        <strong>Факт:</strong> {formatNumber(data.fact_mtd, 0)}
                        <span className={pctColor(data.pct)} style={{ marginLeft: 8 }}>
                            {data.pct !== null ? `${data.pct}%` : ''}
                        </span>
                    </div>
                    <div>
                        <strong>Прогноз:</strong> {formatNumber(data.forecast, 0)}
                        {statusIcon(data.forecast, data.plan_adjusted)}
                    </div>
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
            <div className="text-center py-8 text-secondary">
                Нет планов на этот месяц. Задайте планы в Настройки &rarr; План по брендам
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
                    <div key={r.brand} className="card">
                        <div style={{ padding: 20 }}>
                            <h6 style={{ margin: '0 0 16px', fontWeight: 600 }}>{r.brand}</h6>

                            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                                <span style={{ color: 'var(--color-text-muted)' }}>План</span>
                                <span>{formatNumber(r.plan_month, 0)}</span>
                            </div>
                            {r.debt_prev > 0 && (
                                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }} className="text-warning">
                                    <span>Долг (перенос)</span>
                                    <span>+{formatNumber(r.debt_prev, 0)}</span>
                                </div>
                            )}
                            {r.debt_prev > 0 && (
                                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6, fontWeight: 600 }}>
                                    <span>Скорр. план</span>
                                    <span>{formatNumber(r.plan_adjusted, 0)}</span>
                                </div>
                            )}
                            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12, fontWeight: 600 }}>
                                <span>Факт</span>
                                <span>{formatNumber(r.fact_mtd, 0)}</span>
                            </div>

                            <div style={{ background: 'var(--color-bg-hover)', borderRadius: 6, height: 24, overflow: 'hidden', marginBottom: 12 }}>
                                <div style={{
                                    width: `${Math.min(r.pct ?? 0, 100)}%`,
                                    height: '100%',
                                    background: (r.pct ?? 0) >= 100 ? '#22c55e' : (r.pct ?? 0) >= 70 ? '#f59e0b' : '#ef4444',
                                    borderRadius: 6,
                                    transition: 'width 0.3s',
                                }} />
                            </div>

                            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                <span className={pctColor(r.pct)} style={{ fontWeight: 600 }}>
                                    {r.pct !== null ? `${r.pct}%` : '\u2014'}
                                </span>
                                <span style={{ color: 'var(--color-text-muted)' }}>
                                    Прогноз: {formatNumber(r.forecast, 0)}
                                    {statusIcon(r.forecast, r.plan_adjusted)}
                                </span>
                            </div>
                        </div>
                    </div>
                ))}
            </div>

            <div className="card" style={{ marginTop: 20 }}>
                <div style={{ padding: '16px 20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 16, fontWeight: 600 }}>
                    <span>Итого</span>
                    <span>План: {formatNumber(totalPlan, 0)}</span>
                    <span>Факт: {formatNumber(totalFact, 0)}</span>
                    <span className={pctColor(totalPct)}>
                        {totalPct !== null ? `${totalPct.toFixed(1)}%` : ''}
                    </span>
                    <span>Прогноз: {formatNumber(totalForecast, 0)}
                        {statusIcon(totalForecast, totalPlan)}
                    </span>
                </div>
            </div>
        </>
    );
}

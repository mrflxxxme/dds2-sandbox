'use client';

import { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import type { PlanFactDailyResult, PlanFactBrandRow } from '@/types/api';
import {
    ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip,
    ResponsiveContainer, Cell,
} from 'recharts';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';


export default function PlanFactPage() {
    const [tab, setTab] = useState<'daily' | 'brands'>('daily');
    const [dateFrom, setDateFrom] = useState(() => {
        const d = new Date();
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`;
    });
    const [dateTo, setDateTo] = useState(() => {
        const d = new Date();
        const dim = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(dim).padStart(2, '0')}`;
    });
    const [brand, setBrand] = useState('');

    // Derive year/month from dates for API
    const year = parseInt(dateFrom.slice(0, 4));
    const month = parseInt(dateFrom.slice(5, 7));
    const yearTo = parseInt(dateTo.slice(0, 4));
    const monthTo = parseInt(dateTo.slice(5, 7));
    const [brands, setBrands] = useState<string[]>([]);

    const [dailyData, setDailyData] = useState<PlanFactDailyResult | null>(null);
    const [brandData, setBrandData] = useState<PlanFactBrandRow[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const loadBrands = useCallback(async () => {
        try {
            const b = await api.getWbBrands();
            const filtered = b.filter(name => name !== 'Неопознанный Товар');
            setBrands(filtered);
            if (filtered.length && !brand) setBrand(filtered[0]);
        } catch { /* ignore */ }
    }, []);

    useEffect(() => { loadBrands(); }, [loadBrands]);

    const loadDailyData = useCallback(async () => {
        if (!brand) return;
        setLoading(true);
        setError('');
        try {
            setDailyData(await api.getPlanFactDaily(brand, year, month, yearTo, monthTo));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Error');
        } finally {
            setLoading(false);
        }
    }, [brand, year, month, yearTo, monthTo]);

    const loadBrandData = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setBrandData(await api.getPlanFactBrands(year, month, yearTo, monthTo));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Error');
        } finally {
            setLoading(false);
        }
    }, [year, month, yearTo, monthTo]);

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
                'Бонус': r.surplus_prev,
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
                <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                    <input type="date" className="form-input"
                        value={dateFrom}
                        max={dateTo}
                        onChange={e => setDateFrom(e.target.value)} />
                    <span style={{ color: 'var(--color-text-muted)', fontSize: 18 }}>&mdash;</span>
                    <input type="date" className="form-input"
                        value={dateTo}
                        min={dateFrom}
                        onChange={e => setDateTo(e.target.value)} />
                    {tab === 'daily' && (
                        <select className="form-input" style={{ minWidth: 180 }}
                            value={brand} onChange={e => setBrand(e.target.value)}>
                            {brands.map(b => <option key={b} value={b}>{b}</option>)}
                        </select>
                    )}
                    {(() => {
                        const now = new Date();
                        const curY = now.getFullYear();
                        const curM = now.getMonth();
                        const curDim = new Date(curY, curM + 1, 0).getDate();
                        const curFrom = `${curY}-${String(curM + 1).padStart(2, '0')}-01`;
                        const curTo = `${curY}-${String(curM + 1).padStart(2, '0')}-${String(curDim).padStart(2, '0')}`;
                        const prevD = new Date(curY, curM - 1, 1);
                        const prevY = prevD.getFullYear();
                        const prevM = prevD.getMonth();
                        const prevDim = new Date(prevY, prevM + 1, 0).getDate();
                        const prevFrom = `${prevY}-${String(prevM + 1).padStart(2, '0')}-01`;
                        const prevTo = `${prevY}-${String(prevM + 1).padStart(2, '0')}-${String(prevDim).padStart(2, '0')}`;
                        return (
                            <div style={{ display: 'flex', gap: 4 }}>
                                <button
                                    className={`btn ${dateFrom === curFrom && dateTo === curTo ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                                    onClick={() => { setDateFrom(curFrom); setDateTo(curTo); }}
                                >Тек. месяц</button>
                                <button
                                    className={`btn ${dateFrom === prevFrom && dateTo === prevTo ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                                    onClick={() => { setDateFrom(prevFrom); setDateTo(prevTo); }}
                                >Пред.</button>
                            </div>
                        );
                    })()}
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
                    {data.surplus_prev > 0 && (
                        <div style={{ fontSize: 12, color: 'var(--color-success)', marginTop: 4 }}>
                            вкл. бонус &minus;{formatNumber(data.surplus_prev, 0)}
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
            <TanStackDataTable
                columns={[
                    { key: 'dt', label: 'Дата', render: (v: any) => <span style={{ fontSize: 13, whiteSpace: 'nowrap' }}>{v.slice(5).replace('-', '.')}</span> },
                    { key: 'fact_day', label: 'Факт/день', align: 'right', render: (_v: any, row: any) => <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{row.is_future ? '\u2014' : (row.fact_day ? formatNumber(row.fact_day, 0) : '\u2014')}</span> },
                    { key: 'plan_day', label: 'План/день', align: 'right', render: (v: any) => <span style={{ fontFamily: 'monospace', fontWeight: 500, color: 'var(--color-text-muted)' }}>{v ? formatNumber(v, 0) : '\u2014'}</span> },
                    { key: 'fact_cumulative', label: 'Факт накоп.', align: 'right', render: (v: any) => <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{v ? formatNumber(v, 0) : '\u2014'}</span> },
                    { key: 'plan_cumulative', label: 'План накоп.', align: 'right', render: (v: any) => <span style={{ fontFamily: 'monospace', fontWeight: 500, color: 'var(--color-text-muted)' }}>{v ? formatNumber(v, 0) : '\u2014'}</span> },
                    { key: 'pct', label: '%', align: 'right', render: (v: any) => <span style={{ fontWeight: 600, color: pctColor(v) }}>{v !== null ? `${v}%` : '\u2014'}</span> },
                ]}
                data={data.rows}
                enableSorting
                enablePagination={false}
                rowClassName={(row: any) => row.is_future ? 'opacity-45' : ''}
            />
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
                        {r.surplus_prev > 0 && (
                            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, fontSize: 14, color: 'var(--color-success)' }}>
                                <span>Бонус (перевып.)</span>
                                <span style={{ fontFamily: 'monospace', fontWeight: 500 }}>&minus;{formatNumber(r.surplus_prev, 0)}</span>
                            </div>
                        )}
                        {(r.debt_prev > 0 || r.surplus_prev > 0) && (
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

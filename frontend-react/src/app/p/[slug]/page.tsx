'use client';
import { useEffect, useState, useCallback, useMemo } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import {
    XAxis, YAxis, CartesianGrid, Tooltip, Legend,
    ResponsiveContainer, PieChart, Pie, Cell,
    AreaChart, Area,
} from 'recharts';

/* ─── Цвета ────────────────────────────────────────────────────── */
const C = {
    income: '#22c55e',
    expense: '#ef4444',
    accent: '#a78bfa',
    warning: '#f59e0b',
    info: '#3b82f6',
    muted: '#64748b',
};

const PIE_COLORS = [
    '#a78bfa', '#f472b6', '#38bdf8', '#22c55e', '#f59e0b',
    '#ef4444', '#6366f1', '#14b8a6', '#e879f9', '#fb923c',
];

/* ─── Period presets ───────────────────────────────────────────── */
type PeriodKey = 'month' | 'prev_month' | '7d' | '30d' | '90d' | 'all' | 'custom';

function getPeriodDates(key: PeriodKey, customFrom?: string, customTo?: string): { from: string; to: string; label: string } {
    const today = new Date();
    const yyyy = today.getFullYear();
    const mm = String(today.getMonth() + 1).padStart(2, '0');
    const dd = String(today.getDate()).padStart(2, '0');
    const todayStr = `${yyyy}-${mm}-${dd}`;

    switch (key) {
        case 'month':
            return { from: `${yyyy}-${mm}-01`, to: todayStr, label: 'Текущий месяц' };
        case 'prev_month': {
            const prev = new Date(yyyy, today.getMonth() - 1, 1);
            const prevEnd = new Date(yyyy, today.getMonth(), 0);
            const pY = prev.getFullYear();
            const pM = String(prev.getMonth() + 1).padStart(2, '0');
            const eD = String(prevEnd.getDate()).padStart(2, '0');
            return { from: `${pY}-${pM}-01`, to: `${pY}-${pM}-${eD}`, label: 'Прошлый месяц' };
        }
        case '7d': {
            const s = new Date(today); s.setDate(s.getDate() - 6);
            return { from: `${s.getFullYear()}-${String(s.getMonth()+1).padStart(2,'0')}-${String(s.getDate()).padStart(2,'0')}`, to: todayStr, label: '7 дней' };
        }
        case '30d': {
            const s = new Date(today); s.setDate(s.getDate() - 29);
            return { from: `${s.getFullYear()}-${String(s.getMonth()+1).padStart(2,'0')}-${String(s.getDate()).padStart(2,'0')}`, to: todayStr, label: '30 дней' };
        }
        case '90d': {
            const s = new Date(today); s.setDate(s.getDate() - 89);
            return { from: `${s.getFullYear()}-${String(s.getMonth()+1).padStart(2,'0')}-${String(s.getDate()).padStart(2,'0')}`, to: todayStr, label: '90 дней' };
        }
        case 'custom':
            return { from: customFrom || todayStr, to: customTo || todayStr, label: 'Произвольный' };
        case 'all':
            return { from: '2020-01-01', to: todayStr, label: 'Всё время' };
    }
}

/* ─── Helpers ──────────────────────────────────────────────────── */
function fmtK(v: number): string {
    if (Math.abs(v) >= 1_000_000) return (v / 1_000_000).toFixed(1) + 'М';
    if (Math.abs(v) >= 1_000) return (v / 1_000).toFixed(0) + 'К';
    return v.toFixed(0);
}
function shortDay(iso: string) {
    const d = new Date(iso + 'T00:00:00');
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}
function truncate(s: string, n: number) {
    return s.length > n ? s.slice(0, n) + '…' : s;
}

/* ─── Custom Tooltip ───────────────────────────────────────────── */
function ChartTooltip({ active, payload, label }: any) {
    if (!active || !payload?.length) return null;
    return (
        <div style={{ background: 'rgba(15,17,26,0.95)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, padding: '10px 14px', fontSize: 13 }}>
            <div style={{ color: '#94a3b8', marginBottom: 4 }}>{label}</div>
            {payload.map((p: any, i: number) => (
                <div key={i} style={{ color: p.color || p.fill, fontWeight: 600 }}>
                    {p.name}: {formatNumber(p.value)}
                </div>
            ))}
        </div>
    );
}

/* ─── Pie label ────────────────────────────────────────────────── */
function renderPieLabel({ cx, cy, midAngle, outerRadius, name, percent, index }: any) {
    const RADIAN = Math.PI / 180;
    const r = outerRadius + 20;
    const x = cx + r * Math.cos(-midAngle * RADIAN);
    const y = cy + r * Math.sin(-midAngle * RADIAN);
    if (percent < 0.03) return null;
    return (
        <text x={x} y={y} fill={PIE_COLORS[index % PIE_COLORS.length]}
            textAnchor={x > cx ? 'start' : 'end'} dominantBaseline="central" fontSize={11} fontWeight={600}>
            {truncate(name, 12)} {(percent * 100).toFixed(0)}%
        </text>
    );
}

/* ─── KPI Card ─────────────────────────────────────────────────── */
function KpiCard({ icon, label, value, sub, color, borderColor }: {
    icon: string; label: string; value: string; sub?: string; color: string; borderColor: string;
}) {
    return (
        <div className="stat-card" style={{ borderLeft: `3px solid ${borderColor}` }}>
            <div className="stat-card-label" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 16 }}>{icon}</span> {label}
            </div>
            <div className="stat-card-value" style={{ color, fontSize: 22 }}>{value}</div>
            {sub && <div className="stat-card-sub" style={{ fontSize: 11, opacity: 0.6 }}>{sub}</div>}
        </div>
    );
}

/* ═══════════════════════════════════════════════════════════════ */
export default function DashboardPage() {
    const [data, setData] = useState<any>(null);
    const [balance, setBalance] = useState<any[]>([]);
    const [funnel, setFunnel] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [period, setPeriod] = useState<PeriodKey>('month');
    const [customFrom, setCustomFrom] = useState('');
    const [customTo, setCustomTo] = useState('');

    /* ─── Filter state ────────────────────────────────────────── */
    const [selectedCp, setSelectedCp] = useState<any>(null);   // { name, key }
    const [selectedExpCat, setSelectedExpCat] = useState<string | null>(null);
    const [filteredDaily, setFilteredDaily] = useState<any[] | null>(null);
    const [txnList, setTxnList] = useState<any[]>([]);
    const [txnTotal, setTxnTotal] = useState(0);
    const [txnFlow, setTxnFlow] = useState<'all' | 'income' | 'expense'>('all');
    const [filterLoading, setFilterLoading] = useState(false);

    const { from: dateFrom, to: dateTo, label: periodLabel } = getPeriodDates(period, customFrom, customTo);

    /* ─── Load main data ──────────────────────────────────────── */
    const loadData = useCallback(async () => {
        try {
            setLoading(true); setError('');
            const { from, to } = getPeriodDates(period, customFrom, customTo);
            const [summary, bal, fun] = await Promise.all([
                api.getDashboardSummary(from, to),
                api.getBalance(),
                api.getFunnelSummary(from, to).catch(() => null),
            ]);
            setData(summary); setBalance(bal); setFunnel(fun);
            resetFilters();
        } catch (e: any) {
            setError(e.message || 'Ошибка загрузки');
        } finally { setLoading(false); }
    }, [period, customFrom, customTo]);

    useEffect(() => { loadData(); }, [loadData]);

    /* ─── Reset all filters ───────────────────────────────────── */
    const resetFilters = useCallback(() => {
        setSelectedCp(null); setSelectedExpCat(null);
        setFilteredDaily(null); setTxnList([]); setTxnTotal(0); setTxnFlow('all');
    }, []);

    /* ─── Load filtered data (chart + transactions) ───────────── */
    const loadFiltered = useCallback(async (cpKey?: string, category?: string, flow: string = 'all') => {
        try {
            setFilterLoading(true);
            const { from, to } = getPeriodDates(period, customFrom, customTo);
            const [daily, txns] = await Promise.all([
                api.getDailyFiltered(from, to, cpKey, category),
                api.getFilteredTransactions(from, to, { cpKey, category, flow, limit: 50 }),
            ]);
            setFilteredDaily(daily);
            setTxnList(txns.items);
            setTxnTotal(txns.total);
        } catch { setFilteredDaily(null); setTxnList([]); }
        finally { setFilterLoading(false); }
    }, [period, customFrom, customTo]);

    /* ─── Handle counterparty click ───────────────────────────── */
    const handleCpClick = useCallback((cp: any) => {
        if (selectedCp?.name === cp.name) { resetFilters(); return; }
        setSelectedCp(cp); setSelectedExpCat(null); setTxnFlow('all');
        loadFiltered(cp.key, undefined, 'all');
    }, [selectedCp, loadFiltered, resetFilters]);

    /* ─── Handle expense category click ───────────────────────── */
    const handleExpClick = useCallback((catName: string) => {
        if (selectedExpCat === catName) { resetFilters(); return; }
        setSelectedExpCat(catName); setSelectedCp(null); setTxnFlow('all');
        loadFiltered(undefined, catName, 'all');
    }, [selectedExpCat, loadFiltered, resetFilters]);

    /* ─── Handle flow toggle (all / income / expense) ─────────── */
    const handleFlowChange = useCallback((flow: 'all' | 'income' | 'expense') => {
        setTxnFlow(flow);
        loadFiltered(selectedCp?.key, selectedExpCat || undefined, flow);
    }, [selectedCp, selectedExpCat, loadFiltered]);

    const allPeriods: { key: PeriodKey; label: string }[] = [
        { key: 'month', label: 'Месяц' }, { key: 'prev_month', label: 'Пред.' },
        { key: '7d', label: '7д' }, { key: '30d', label: '30д' },
        { key: '90d', label: '90д' }, { key: 'all', label: 'Всё' },
    ];

    const incomeCounterparties = data?.income_counterparties || [];
    const expensePie: any[] = data?.expense_by_category || [];

    /* ─── Chart data ──────────────────────────────────────────── */
    const dailyChart = useMemo(() => {
        const raw = filteredDaily || data?.daily_cashflow || [];
        return raw.map((d: any) => ({ ...d, label: shortDay(d.date) }));
    }, [data, filteredDaily]);

    const hasFilter = selectedCp || selectedExpCat;
    const filterLabel = selectedCp ? truncate(selectedCp.name, 20) : selectedExpCat ? truncate(selectedExpCat, 20) : '';

    if (loading) return (
        <div style={{ padding: 40, color: 'var(--color-text-muted)', display: 'flex', alignItems: 'center', gap: 12 }}>
            <div className="spinner" /> Загрузка дашборда...
        </div>
    );
    if (error) return <div style={{ padding: 40, color: 'var(--color-danger)' }}>❌ {error}</div>;
    if (!data) return null;

    const netCashflow = data.month_income - data.month_expense;
    const funnelDRR = funnel && funnel.orders_sum_rub > 0 ? (funnel.adv_sum / funnel.orders_sum_rub * 100) : 0;

    return (
        <div className="animate-in">
            {/* ─── Header ─────────────────────────────────────── */}
            <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
                <div>
                    <h1 className="page-title">Дашборд</h1>
                    <p className="page-subtitle">{periodLabel} ({dateFrom} — {dateTo})</p>
                </div>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
                    {allPeriods.map(p => (
                        <button key={p.key}
                            className={`btn btn-sm ${period === p.key ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setPeriod(p.key)}
                            style={period === p.key ? { background: 'var(--color-accent)', borderColor: 'var(--color-accent)' } : {}}
                        >{p.label}</button>
                    ))}
                    <span style={{ color: 'var(--color-text-muted)', fontSize: 12, margin: '0 4px' }}>|</span>
                    <input type="date" value={period === 'custom' ? customFrom : dateFrom}
                        onChange={e => { setCustomFrom(e.target.value); setPeriod('custom'); }}
                        style={{ background: 'var(--color-bg-elevated)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 12 }}
                    />
                    <span style={{ color: 'var(--color-text-muted)', fontSize: 12 }}>—</span>
                    <input type="date" value={period === 'custom' ? customTo : dateTo}
                        onChange={e => { setCustomTo(e.target.value); setPeriod('custom'); }}
                        style={{ background: 'var(--color-bg-elevated)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 12 }}
                    />
                </div>
            </div>

            {/* ─── KPI Row 1 ──────────────────────────────────── */}
            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
                <KpiCard icon="💰" label="Баланс RUB" value={`${formatNumber(data.balance_rub)} ₽`} color={C.income} borderColor={C.income} />
                <KpiCard icon="💴" label="Баланс CNY" value={`${formatNumber(data.balance_cny)} ¥`} color={C.warning} borderColor={C.warning} />
                <KpiCard icon={netCashflow >= 0 ? '📈' : '📉'} label="Cashflow"
                    value={`${netCashflow >= 0 ? '+' : ''}${formatNumber(netCashflow)} ₽`}
                    sub={`Приход ${fmtK(data.month_income)} / Расход ${fmtK(data.month_expense)}`}
                    color={netCashflow >= 0 ? C.income : C.expense} borderColor={netCashflow >= 0 ? C.income : C.expense} />
                <KpiCard icon="📊" label="ДРР" value={funnel ? `${funnelDRR.toFixed(1)}%` : '—'}
                    sub={funnel ? `${fmtK(funnel.adv_sum)} / ${fmtK(funnel.orders_sum_rub)} ₽` : ''}
                    color={funnelDRR > 15 ? C.expense : C.income} borderColor={C.accent} />
            </div>

            {/* ─── KPI Row 2 ──────────────────────────────────── */}
            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', marginTop: 0 }}>
                <KpiCard icon="📦" label="Заказы" value={`${data.orders_count}`} sub={`¥ ${formatNumber(data.orders_total_cny, 0)}`} color={C.info} borderColor={C.info} />
                <KpiCard icon="💳" label="Долг"
                    value={data.debt_cny > 0 ? `${formatNumber(data.debt_cny, 0)} ¥` : (data.debt_rub > 0 ? `${formatNumber(data.debt_rub, 0)} ₽` : '0')}
                    sub={data.debt_cny > 0 && data.debt_rub > 0 ? `+ ${formatNumber(data.debt_rub, 0)} ₽` : 'неоплаченные'}
                    color={data.debt_rub > 0 || data.debt_cny > 0 ? C.expense : C.income} borderColor={C.expense} />
                <KpiCard icon="📥" label="INBOX" value={`${data.inbox_count}`} sub="нераспределённых"
                    color={data.inbox_count > 0 ? C.warning : C.income} borderColor={data.inbox_count > 0 ? C.warning : C.income} />
                {funnel ? (
                    <KpiCard icon="🛒" label="Заказы WB" value={funnel.orders_count?.toLocaleString('ru-RU') || '—'} sub={`${formatNumber(funnel.orders_sum_rub, 0)} ₽`} color={C.info} borderColor={C.info} />
                ) : (
                    <KpiCard icon="📊" label="Счета" value={`${data.accounts_count}`} sub="активных" color={C.accent} borderColor={C.accent} />
                )}
            </div>

            {/* ─── Charts row ─────────────────────────────────── */}
            <div style={{ display: 'grid', gridTemplateColumns: dailyChart.length > 0 && expensePie.length > 0 ? '1.6fr 1fr' : '1fr', gap: 20, marginTop: 20 }}>
                {/* Area chart */}
                {dailyChart.length > 0 && (
                    <div className="glass-card" style={{ padding: '20px 16px' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                            <h3 style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-text)' }}>
                                📈 Доходы и расходы по дням
                                {hasFilter && <span style={{ fontSize: 12, color: C.accent, marginLeft: 8 }}>— {filterLabel}</span>}
                                {filterLoading && <span style={{ fontSize: 12, color: C.muted, marginLeft: 8 }}>⏳</span>}
                            </h3>
                            {hasFilter && <button className="btn btn-sm btn-secondary" onClick={resetFilters}>Сбросить</button>}
                        </div>
                        <ResponsiveContainer width="100%" height={320}>
                            <AreaChart data={dailyChart} margin={{ left: 10, right: 10 }}>
                                <defs>
                                    <linearGradient id="gradIncome" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor={C.income} stopOpacity={0.3} />
                                        <stop offset="95%" stopColor={C.income} stopOpacity={0.02} />
                                    </linearGradient>
                                    <linearGradient id="gradExpense" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor={C.expense} stopOpacity={0.3} />
                                        <stop offset="95%" stopColor={C.expense} stopOpacity={0.02} />
                                    </linearGradient>
                                </defs>
                                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                                <XAxis dataKey="label" tick={{ fill: '#94a3b8', fontSize: 11 }} />
                                <YAxis tickFormatter={fmtK} tick={{ fill: '#94a3b8', fontSize: 12 }} />
                                <Tooltip content={<ChartTooltip />} />
                                <Legend wrapperStyle={{ fontSize: 12, color: '#94a3b8' }} />
                                <Area type="monotone" dataKey="income" name="Приход" stroke={C.income} fill="url(#gradIncome)" strokeWidth={2} />
                                <Area type="monotone" dataKey="expense" name="Расход" stroke={C.expense} fill="url(#gradExpense)" strokeWidth={2} />
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                )}

                {/* Expense Pie */}
                {expensePie.length > 0 && (
                    <div className="glass-card" style={{ padding: '20px 16px' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                            <h3 style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-text)' }}>🥧 Структура расходов</h3>
                            {selectedExpCat && <button className="btn btn-sm btn-secondary" onClick={resetFilters}>Все</button>}
                        </div>
                        <ResponsiveContainer width="100%" height={320}>
                            <PieChart>
                                <Pie data={expensePie} dataKey="value" nameKey="name"
                                    cx="50%" cy="50%" innerRadius={50} outerRadius={90} paddingAngle={2}
                                    label={renderPieLabel} labelLine={{ stroke: '#475569', strokeWidth: 1 }}
                                    onClick={(entry: any) => handleExpClick(entry.name)}
                                    style={{ cursor: 'pointer' }}>
                                    {expensePie.map((_: any, i: number) => (
                                        <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]}
                                            opacity={!selectedExpCat || selectedExpCat === expensePie[i]?.name ? 1 : 0.3}
                                            stroke={selectedExpCat === expensePie[i]?.name ? '#fff' : 'none'}
                                            strokeWidth={selectedExpCat === expensePie[i]?.name ? 2 : 0} />
                                    ))}
                                </Pie>
                                <Tooltip formatter={(v: number) => formatNumber(v) + ' ₽'}
                                    contentStyle={{ background: 'rgba(15,17,26,0.95)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, color: '#e2e8f0' }} />
                            </PieChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </div>

            {/* ─── Transaction list (when filter active) ──────── */}
            {hasFilter && (
                <div className="glass-card" style={{ marginTop: 20 }}>
                    <div className="table-toolbar">
                        <h3 style={{ fontSize: 16, fontWeight: 600 }}>
                            Операции: {filterLabel}
                            <span style={{ fontSize: 13, fontWeight: 400, color: '#94a3b8', marginLeft: 8 }}>
                                ({txnTotal} шт.)
                            </span>
                        </h3>
                        <div style={{ display: 'flex', gap: 4 }}>
                            {(['all', 'income', 'expense'] as const).map(f => (
                                <button key={f}
                                    className={`btn btn-sm ${txnFlow === f ? 'btn-primary' : 'btn-secondary'}`}
                                    onClick={() => handleFlowChange(f)}
                                    style={txnFlow === f ? { background: f === 'income' ? C.income : f === 'expense' ? C.expense : 'var(--color-accent)', borderColor: 'transparent' } : {}}
                                >
                                    {f === 'all' ? 'Все' : f === 'income' ? '📈 Приходы' : '📉 Расходы'}
                                </button>
                            ))}
                            <button className="btn btn-sm btn-secondary"
                                onClick={() => exportToExcel(txnList, `txn_${filterLabel}`)}>
                                📥 Excel
                            </button>
                        </div>
                    </div>
                    {filterLoading ? (
                        <div style={{ padding: 20, textAlign: 'center', color: '#94a3b8' }}>
                            <div className="spinner" style={{ display: 'inline-block', marginRight: 8 }} /> Загрузка...
                        </div>
                    ) : txnList.length === 0 ? (
                        <div style={{ padding: 20, textAlign: 'center', color: '#94a3b8' }}>Нет операций</div>
                    ) : (
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Дата</th>
                                    <th>Контрагент</th>
                                    <th>Категория</th>
                                    <th style={{ textAlign: 'right' }}>Приход</th>
                                    <th style={{ textAlign: 'right' }}>Расход</th>
                                    <th>Назначение</th>
                                </tr>
                            </thead>
                            <tbody>
                                {txnList.map((t: any, i: number) => (
                                    <tr key={i}>
                                        <td style={{ whiteSpace: 'nowrap', fontSize: 13 }}>{formatDate(t.date)}</td>
                                        <td style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {t.counterparty || '—'}
                                        </td>
                                        <td style={{ fontSize: 12, color: '#94a3b8' }}>{t.category || '—'}</td>
                                        <td style={{ textAlign: 'right', fontWeight: 600, color: t.income > 0 ? C.income : '#475569' }}>
                                            {t.income > 0 ? formatNumber(t.income) : '—'}
                                        </td>
                                        <td style={{ textAlign: 'right', fontWeight: 600, color: t.expense > 0 ? C.expense : '#475569' }}>
                                            {t.expense > 0 ? formatNumber(t.expense) : '—'}
                                        </td>
                                        <td style={{ maxWidth: 250, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 12, color: '#94a3b8' }}>
                                            {t.purpose || '—'}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                    {txnTotal > 50 && (
                        <div style={{ padding: '10px 16px', fontSize: 12, color: '#94a3b8', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                            Показано 50 из {txnTotal}. Для полного списка → Операции.
                        </div>
                    )}
                </div>
            )}

            {/* ─── Income counterparties ───────────────────────── */}
            {incomeCounterparties.length > 0 && (
                <div className="glass-card" style={{ marginTop: 20 }}>
                    <div className="table-toolbar">
                        <h3 style={{ fontSize: 16, fontWeight: 600 }}>Приходы по контрагентам</h3>
                        <div style={{ display: 'flex', gap: 4 }}>
                            {selectedCp && <button className="btn btn-sm btn-secondary" onClick={resetFilters}>Все</button>}
                        </div>
                    </div>
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Контрагент</th>
                                <th style={{ textAlign: 'right' }}>Сумма</th>
                                <th style={{ textAlign: 'right' }}>Операций</th>
                                <th style={{ textAlign: 'right' }}>% от общего</th>
                            </tr>
                        </thead>
                        <tbody>
                            {incomeCounterparties.map((c: any, i: number) => {
                                const pct = data.month_income > 0 ? (c.total / data.month_income * 100) : 0;
                                const isSelected = selectedCp?.name === c.name;
                                return (
                                    <tr key={i} onClick={() => handleCpClick(c)}
                                        style={{ cursor: 'pointer', background: isSelected ? 'rgba(167,139,250,0.1)' : undefined }}>
                                        <td style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {isSelected ? '▼ ' : '▶ '}{c.name}
                                        </td>
                                        <td style={{ textAlign: 'right', fontWeight: 600, color: 'var(--color-success)' }}>{formatNumber(c.total)} ₽</td>
                                        <td style={{ textAlign: 'right' }}>{c.count}</td>
                                        <td style={{ textAlign: 'right' }}><span className="badge badge-info">{pct.toFixed(1)}%</span></td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}

            {/* ─── Expense categories table ────────────────────── */}
            {expensePie.length > 0 && (
                <div className="glass-card" style={{ marginTop: 20 }}>
                    <div className="table-toolbar">
                        <h3 style={{ fontSize: 16, fontWeight: 600 }}>Расходы по категориям</h3>
                        <div style={{ display: 'flex', gap: 4 }}>
                            {selectedExpCat && <button className="btn btn-sm btn-secondary" onClick={resetFilters}>Все</button>}
                        </div>
                    </div>
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Категория</th>
                                <th style={{ textAlign: 'right' }}>Сумма</th>
                                <th style={{ textAlign: 'right' }}>Операций</th>
                                <th style={{ textAlign: 'right' }}>% от расходов</th>
                            </tr>
                        </thead>
                        <tbody>
                            {expensePie.map((c: any, i: number) => {
                                const pct = data.month_expense > 0 ? (c.value / data.month_expense * 100) : 0;
                                const isSelected = selectedExpCat === c.name;
                                return (
                                    <tr key={i} onClick={() => handleExpClick(c.name)}
                                        style={{ cursor: 'pointer', background: isSelected ? 'rgba(239,68,68,0.1)' : undefined }}>
                                        <td style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                            <span style={{
                                                width: 10, height: 10, borderRadius: '50%', display: 'inline-block',
                                                background: PIE_COLORS[i % PIE_COLORS.length],
                                            }} />
                                            {isSelected ? '▼ ' : ''}{c.name}
                                        </td>
                                        <td style={{ textAlign: 'right', fontWeight: 600, color: 'var(--color-danger)' }}>{formatNumber(c.value)} ₽</td>
                                        <td style={{ textAlign: 'right' }}>{c.count || '—'}</td>
                                        <td style={{ textAlign: 'right' }}><span className="badge badge-warning">{pct.toFixed(1)}%</span></td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}

            {/* ─── Balance table ───────────────────────────────── */}
            <div className="glass-card" style={{ marginTop: 20 }}>
                <div className="table-toolbar">
                    <h3 style={{ fontSize: 16, fontWeight: 600 }}>Остатки на счетах</h3>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(balance, 'balance')}>📥 Excel</button>
                </div>
                <table className="data-table">
                    <thead><tr><th>Счёт</th><th>Название</th><th>Валюта</th><th style={{ textAlign: 'right' }}>Остаток</th></tr></thead>
                    <tbody>
                        {balance.map((b, i) => (
                            <tr key={i}>
                                <td style={{ fontFamily: 'monospace', fontSize: 13 }}>{b.account}</td>
                                <td>{b.account_name || '—'}</td>
                                <td><span className="badge badge-info">{b.currency}</span></td>
                                <td style={{ textAlign: 'right', fontWeight: 600, color: b.balance >= 0 ? 'var(--color-success)' : 'var(--color-danger)' }}>
                                    {formatNumber(b.balance)} {b.currency === 'RUB' ? '₽' : '¥'}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

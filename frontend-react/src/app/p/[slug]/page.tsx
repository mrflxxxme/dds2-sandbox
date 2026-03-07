'use client';
import { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import {
    BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
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

/* ─── Helpers ──────────────────────────────────────────────────── */
function fmtK(v: number): string {
    if (Math.abs(v) >= 1_000_000) return (v / 1_000_000).toFixed(1) + 'М';
    if (Math.abs(v) >= 1_000) return (v / 1_000).toFixed(0) + 'К';
    return v.toFixed(0);
}

function shortDay(iso: string) {
    const d = new Date(iso);
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}

/* ─── Custom Tooltip ───────────────────────────────────────────── */
function ChartTooltip({ active, payload, label }: any) {
    if (!active || !payload?.length) return null;
    return (
        <div style={{
            background: 'rgba(15,17,26,0.95)', border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: 8, padding: '10px 14px', fontSize: 13,
        }}>
            <div style={{ color: '#94a3b8', marginBottom: 4 }}>{label}</div>
            {payload.map((p: any, i: number) => (
                <div key={i} style={{ color: p.color || p.fill, fontWeight: 600 }}>
                    {p.name}: {formatNumber(p.value)}
                </div>
            ))}
        </div>
    );
}

/* ─── KPI Card ─────────────────────────────────────────────────── */
function KpiCard({ icon, label, value, sub, color, borderColor, onClick }: {
    icon: string; label: string; value: string; sub?: string;
    color: string; borderColor: string; onClick?: () => void;
}) {
    return (
        <div
            className="stat-card"
            style={{ borderLeft: `3px solid ${borderColor}`, cursor: onClick ? 'pointer' : 'default' }}
            onClick={onClick}
        >
            <div className="stat-card-label" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 16 }}>{icon}</span> {label}
            </div>
            <div className="stat-card-value" style={{ color, fontSize: 22 }}>{value}</div>
            {sub && <div className="stat-card-sub" style={{ fontSize: 11, opacity: 0.6 }}>{sub}</div>}
        </div>
    );
}

/* ═════════════════════════════════════════════════════════════════ */
export default function DashboardPage() {
    const [data, setData] = useState<any>(null);
    const [balance, setBalance] = useState<any[]>([]);
    const [funnel, setFunnel] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const now = new Date();
    const monthName = now.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' });

    const loadData = useCallback(async () => {
        try {
            setLoading(true);
            const [summary, bal, fun] = await Promise.all([
                api.getDashboardSummary(),
                api.getBalance(),
                api.getFunnelSummary().catch(() => null),
            ]);
            setData(summary);
            setBalance(bal);
            setFunnel(fun);
        } catch (e: any) {
            setError(e.message || 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { loadData(); }, [loadData]);

    if (loading) return (
        <div style={{ padding: 40, color: 'var(--color-text-muted)', display: 'flex', alignItems: 'center', gap: 12 }}>
            <div className="spinner" /> Загрузка дашборда...
        </div>
    );

    if (error) return (
        <div style={{ padding: 40, color: 'var(--color-danger)' }}>❌ {error}</div>
    );

    if (!data) return null;

    const netCashflow = data.month_income - data.month_expense;
    const funnelDRR = funnel && funnel.orders_sum_rub > 0
        ? (funnel.adv_sum / funnel.orders_sum_rub * 100) : 0;

    /* Daily cashflow chart data — add formatted label */
    const dailyChart = (data.daily_cashflow || []).map((d: any) => ({
        ...d,
        label: shortDay(d.date),
    }));

    /* Expense pie */
    const expensePie = data.expense_by_category || [];

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Дашборд</h1>
                    <p className="page-subtitle">Управленческая сводка • {monthName}</p>
                </div>
                <button className="btn btn-secondary btn-sm" onClick={loadData}>🔄 Обновить</button>
            </div>

            {/* ─── Row 1: Financial Health ────────────────────── */}
            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
                <KpiCard
                    icon="💰" label="Баланс RUB"
                    value={`${formatNumber(data.balance_rub)} ₽`}
                    color={C.income} borderColor={C.income}
                />
                <KpiCard
                    icon="💴" label="Баланс CNY"
                    value={`${formatNumber(data.balance_cny)} ¥`}
                    color={C.warning} borderColor={C.warning}
                />
                <KpiCard
                    icon={netCashflow >= 0 ? '📈' : '📉'} label="Cashflow (мес.)"
                    value={`${netCashflow >= 0 ? '+' : ''}${formatNumber(netCashflow)} ₽`}
                    sub={`Приход ${fmtK(data.month_income)} / Расход ${fmtK(data.month_expense)}`}
                    color={netCashflow >= 0 ? C.income : C.expense}
                    borderColor={netCashflow >= 0 ? C.income : C.expense}
                />
                <KpiCard
                    icon="📊" label="ДРР (реклама)"
                    value={funnel ? `${funnelDRR.toFixed(1)}%` : '—'}
                    sub={funnel ? `${fmtK(funnel.adv_sum)} ₽ / ${fmtK(funnel.orders_sum_rub)} ₽` : ''}
                    color={funnelDRR > 15 ? C.expense : C.income}
                    borderColor={C.accent}
                />
            </div>

            {/* ─── Row 2: Operational metrics ─────────────────── */}
            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', marginTop: 0 }}>
                <KpiCard
                    icon="📦" label="Заказы"
                    value={`${data.orders_count}`}
                    sub={`¥ ${formatNumber(data.orders_total_cny, 0)}`}
                    color={C.info} borderColor={C.info}
                />
                <KpiCard
                    icon="💳" label="Долг по оплатам"
                    value={data.debt_rub > 0 ? `${formatNumber(data.debt_rub, 0)} ₽` : (data.debt_cny > 0 ? `${formatNumber(data.debt_cny, 0)} ¥` : '0 ₽')}
                    sub={data.debt_rub > 0 && data.debt_cny > 0 ? `+ ¥ ${formatNumber(data.debt_cny, 0)}` : 'неоплаченные платежи'}
                    color={data.debt_rub > 0 || data.debt_cny > 0 ? C.expense : C.income}
                    borderColor={C.expense}
                />
                <KpiCard
                    icon="📥" label="INBOX"
                    value={`${data.inbox_count}`}
                    sub="нераспределённых операций"
                    color={data.inbox_count > 0 ? C.warning : C.income}
                    borderColor={data.inbox_count > 0 ? C.warning : C.income}
                />
                {funnel && (
                    <KpiCard
                        icon="🛒" label="Заказы WB"
                        value={funnel.orders_count?.toLocaleString('ru-RU') || '—'}
                        sub={`${formatNumber(funnel.orders_sum_rub, 0)} ₽`}
                        color={C.info} borderColor={C.info}
                    />
                )}
                {!funnel && (
                    <KpiCard
                        icon="📊" label="Счета"
                        value={`${data.accounts_count}`}
                        sub="активных"
                        color={C.accent} borderColor={C.accent}
                    />
                )}
            </div>

            {/* ─── Charts row ─────────────────────────────────── */}
            <div style={{
                display: 'grid',
                gridTemplateColumns: dailyChart.length > 0 && expensePie.length > 0 ? '1.6fr 1fr' : '1fr',
                gap: 20, marginTop: 20,
            }}>

                {/* Area chart — daily income + expense */}
                {dailyChart.length > 0 && (
                    <div className="glass-card" style={{ padding: '20px 16px' }}>
                        <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 16, color: 'var(--color-text)' }}>
                            📈 Доходы и расходы по дням
                        </h3>
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
                                <Area
                                    type="monotone" dataKey="income" name="Приход"
                                    stroke={C.income} fill="url(#gradIncome)" strokeWidth={2}
                                />
                                <Area
                                    type="monotone" dataKey="expense" name="Расход"
                                    stroke={C.expense} fill="url(#gradExpense)" strokeWidth={2}
                                />
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                )}

                {/* Expense Pie */}
                {expensePie.length > 0 && (
                    <div className="glass-card" style={{ padding: '20px 16px' }}>
                        <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 16, color: 'var(--color-text)' }}>
                            🥧 Структура расходов
                        </h3>
                        <ResponsiveContainer width="100%" height={320}>
                            <PieChart>
                                <Pie
                                    data={expensePie}
                                    dataKey="value"
                                    nameKey="name"
                                    cx="50%" cy="50%"
                                    innerRadius={55} outerRadius={110}
                                    paddingAngle={2}
                                    label={({ name, percent }) =>
                                        `${name} ${(percent * 100).toFixed(0)}%`
                                    }
                                    labelLine={false}
                                    style={{ fontSize: 11, fill: '#cbd5e1' }}
                                >
                                    {expensePie.map((_: any, i: number) => (
                                        <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                                    ))}
                                </Pie>
                                <Tooltip formatter={(v: number) => formatNumber(v) + ' ₽'} />
                            </PieChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </div>

            {/* ─── Balance table ───────────────────────────────── */}
            <div className="glass-card" style={{ marginTop: 20 }}>
                <div className="table-toolbar">
                    <h3 style={{ fontSize: 16, fontWeight: 600 }}>Остатки на счетах</h3>
                    <button className="btn btn-secondary btn-sm"
                        onClick={() => exportToExcel(balance, 'balance')}>
                        📥 Экспорт Excel
                    </button>
                </div>
                <table className="data-table">
                    <thead>
                        <tr>
                            <th>Счёт</th>
                            <th>Название</th>
                            <th>Валюта</th>
                            <th style={{ textAlign: 'right' }}>Остаток</th>
                        </tr>
                    </thead>
                    <tbody>
                        {balance.map((b, i) => (
                            <tr key={i}>
                                <td style={{ fontFamily: 'monospace', fontSize: 13 }}>{b.account}</td>
                                <td>{b.account_name || '—'}</td>
                                <td><span className="badge badge-info">{b.currency}</span></td>
                                <td style={{
                                    textAlign: 'right', fontWeight: 600,
                                    color: b.balance >= 0 ? 'var(--color-success)' : 'var(--color-danger)'
                                }}>
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

'use client';
import { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import {
    XAxis, YAxis, CartesianGrid, Tooltip,
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
type PeriodKey = 'month' | 'prev_month' | '7d' | '30d' | '90d' | 'all';

function getPeriodDates(key: PeriodKey): { from: string; to: string; label: string } {
    const today = new Date();
    const fmt = (d: Date) => d.toISOString().slice(0, 10);

    switch (key) {
        case 'month': {
            const s = new Date(today.getFullYear(), today.getMonth(), 1);
            return { from: fmt(s), to: fmt(today), label: 'Текущий месяц' };
        }
        case 'prev_month': {
            const s = new Date(today.getFullYear(), today.getMonth() - 1, 1);
            const e = new Date(today.getFullYear(), today.getMonth(), 0);
            return { from: fmt(s), to: fmt(e), label: 'Прошлый месяц' };
        }
        case '7d': {
            const s = new Date(today); s.setDate(s.getDate() - 6);
            return { from: fmt(s), to: fmt(today), label: '7 дней' };
        }
        case '30d': {
            const s = new Date(today); s.setDate(s.getDate() - 29);
            return { from: fmt(s), to: fmt(today), label: '30 дней' };
        }
        case '90d': {
            const s = new Date(today); s.setDate(s.getDate() - 89);
            return { from: fmt(s), to: fmt(today), label: '90 дней' };
        }
        case 'all':
            return { from: '2020-01-01', to: fmt(today), label: 'Всё время' };
    }
}

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
function KpiCard({ icon, label, value, sub, color, borderColor }: {
    icon: string; label: string; value: string; sub?: string;
    color: string; borderColor: string;
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

/* ═════════════════════════════════════════════════════════════════ */
export default function DashboardPage() {
    const [data, setData] = useState<any>(null);
    const [balance, setBalance] = useState<any[]>([]);
    const [funnel, setFunnel] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [period, setPeriod] = useState<PeriodKey>('month');

    const { from: dateFrom, to: dateTo, label: periodLabel } = getPeriodDates(period);

    const loadData = useCallback(async () => {
        try {
            setLoading(true);
            setError('');
            const { from, to } = getPeriodDates(period);
            const [summary, bal, fun] = await Promise.all([
                api.getDashboardSummary(from, to),
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
    }, [period]);

    useEffect(() => { loadData(); }, [loadData]);

    const allPeriods: { key: PeriodKey; label: string }[] = [
        { key: 'month', label: 'Месяц' },
        { key: 'prev_month', label: 'Пред. месяц' },
        { key: '7d', label: '7д' },
        { key: '30d', label: '30д' },
        { key: '90d', label: '90д' },
        { key: 'all', label: 'Всё' },
    ];

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

    const dailyChart = (data.daily_cashflow || []).map((d: any) => ({
        ...d,
        label: shortDay(d.date),
    }));

    const expensePie = data.expense_by_category || [];

    return (
        <div className="animate-in">
            <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
                <div>
                    <h1 className="page-title">Дашборд</h1>
                    <p className="page-subtitle">Управленческая сводка • {periodLabel} ({dateFrom} — {dateTo})</p>
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {allPeriods.map(p => (
                        <button
                            key={p.key}
                            className={`btn btn-sm ${period === p.key ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setPeriod(p.key)}
                            style={period === p.key ? { background: 'var(--color-accent)', borderColor: 'var(--color-accent)' } : {}}
                        >
                            {p.label}
                        </button>
                    ))}
                </div>
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
                    icon={netCashflow >= 0 ? '📈' : '📉'} label="Cashflow"
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
                    sub="нераспределённых"
                    color={data.inbox_count > 0 ? C.warning : C.income}
                    borderColor={data.inbox_count > 0 ? C.warning : C.income}
                />
                {funnel ? (
                    <KpiCard
                        icon="🛒" label="Заказы WB"
                        value={funnel.orders_count?.toLocaleString('ru-RU') || '—'}
                        sub={`${formatNumber(funnel.orders_sum_rub, 0)} ₽`}
                        color={C.info} borderColor={C.info}
                    />
                ) : (
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

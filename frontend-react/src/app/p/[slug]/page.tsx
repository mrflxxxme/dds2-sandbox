'use client';
import { useEffect, useState, useMemo } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import {
    BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
    ResponsiveContainer, PieChart, Pie, Cell, Legend,
    LineChart, Line, AreaChart, Area,
} from 'recharts';

/* ─── Цвета для графиков ─────────────────────────────────────────── */
const COLORS = {
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

/* ─── Helpers ─────────────────────────────────────────────────────── */
function getMonthRange() {
    const now = new Date();
    const y = now.getFullYear();
    const m = now.getMonth() + 1;
    return { year: y, month: m };
}

function shortDate(iso: string) {
    const d = new Date(iso);
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}

function fmtK(v: number): string {
    if (Math.abs(v) >= 1_000_000) return (v / 1_000_000).toFixed(1) + 'М';
    if (Math.abs(v) >= 1_000) return (v / 1_000).toFixed(0) + 'К';
    return v.toFixed(0);
}

/* ─── Custom Recharts Tooltip ──────────────────────────────────────── */
function CustomTooltip({ active, payload, label }: any) {
    if (!active || !payload?.length) return null;
    return (
        <div style={{
            background: 'rgba(15,17,26,0.95)', border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: 8, padding: '10px 14px', fontSize: 13,
        }}>
            <div style={{ color: '#94a3b8', marginBottom: 4 }}>{label}</div>
            {payload.map((p: any, i: number) => (
                <div key={i} style={{ color: p.color, fontWeight: 600 }}>
                    {p.name}: {formatNumber(p.value)}
                </div>
            ))}
        </div>
    );
}

/* ═══════════════════════════════════════════════════════════════════ */
export default function DashboardPage() {
    const [balance, setBalance] = useState<any[]>([]);
    const [ddsMonth, setDdsMonth] = useState<any[]>([]);
    const [funnel, setFunnel] = useState<any>(null);
    const [loading, setLoading] = useState(true);

    const { year, month } = getMonthRange();
    const monthName = new Date(year, month - 1).toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' });

    useEffect(() => {
        Promise.allSettled([
            api.getBalance().then(setBalance),
            api.getDDSMonth(year, month, 'RUB').then(setDdsMonth),
            api.getFunnelSummary().then(setFunnel),
        ]).finally(() => setLoading(false));
    }, [year, month]);

    /* ─── Computed data ─────────────────────────────── */
    const totalRub = balance.filter(b => b.currency === 'RUB').reduce((s, b) => s + b.balance, 0);
    const totalCny = balance.filter(b => b.currency === 'CNY').reduce((s, b) => s + b.balance, 0);

    // Cashflow bar data
    const cashflowData = useMemo(() => {
        const map: Record<string, { category: string; income: number; expense: number }> = {};
        for (const row of ddsMonth) {
            const cat = row.cat_lvl1 || 'Без категории';
            if (!map[cat]) map[cat] = { category: cat, income: 0, expense: 0 };
            map[cat].income += row.income || 0;
            map[cat].expense += Math.abs(row.expense || 0);
        }
        return Object.values(map).sort((a, b) => (b.income + b.expense) - (a.income + a.expense));
    }, [ddsMonth]);

    // Expense pie data
    const expensePie = useMemo(() => {
        return cashflowData
            .filter(c => c.expense > 0)
            .map(c => ({ name: c.category, value: c.expense }))
            .sort((a, b) => b.value - a.value);
    }, [cashflowData]);

    const totalIncome = cashflowData.reduce((s, c) => s + c.income, 0);
    const totalExpense = cashflowData.reduce((s, c) => s + c.expense, 0);
    const netCashflow = totalIncome - totalExpense;

    // Funnel stats
    const funnelOrders = funnel?.orders_count || 0;
    const funnelSum = funnel?.orders_sum_rub || 0;
    const funnelAdv = funnel?.adv_sum || 0;
    const funnelDRR = funnelSum > 0 ? (funnelAdv / funnelSum * 100) : 0;

    if (loading) return (
        <div style={{ padding: 40, color: 'var(--color-text-muted)', display: 'flex', alignItems: 'center', gap: 12 }}>
            <div className="spinner" /> Загрузка дашборда...
        </div>
    );

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Дашборд</h1>
                    <p className="page-subtitle">Общая сводка по проекту • {monthName}</p>
                </div>
            </div>

            {/* ─── Top KPI cards ──────────────────────────────── */}
            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
                <div className="stat-card" style={{ borderLeft: '3px solid var(--color-success)' }}>
                    <div className="stat-card-label">Баланс RUB</div>
                    <div className="stat-card-value" style={{ color: 'var(--color-success)' }}>
                        {formatNumber(totalRub)} ₽
                    </div>
                </div>
                <div className="stat-card" style={{ borderLeft: '3px solid var(--color-warning)' }}>
                    <div className="stat-card-label">Баланс CNY</div>
                    <div className="stat-card-value" style={{ color: 'var(--color-warning)' }}>
                        {formatNumber(totalCny)} ¥
                    </div>
                </div>
                <div className="stat-card" style={{ borderLeft: `3px solid ${netCashflow >= 0 ? COLORS.income : COLORS.expense}` }}>
                    <div className="stat-card-label">Cashflow (мес.)</div>
                    <div className="stat-card-value" style={{ color: netCashflow >= 0 ? COLORS.income : COLORS.expense }}>
                        {netCashflow >= 0 ? '+' : ''}{formatNumber(netCashflow)} ₽
                    </div>
                    <div className="stat-card-sub" style={{ fontSize: 11, opacity: 0.6 }}>
                        Приход {formatNumber(totalIncome, 0)} / Расход {formatNumber(totalExpense, 0)}
                    </div>
                </div>
                <div className="stat-card" style={{ borderLeft: '3px solid var(--color-accent)' }}>
                    <div className="stat-card-label">Счета</div>
                    <div className="stat-card-value" style={{ color: 'var(--color-accent)' }}>{balance.length}</div>
                    <div className="stat-card-sub">активных счетов</div>
                </div>
            </div>

            {/* ─── WB Funnel mini-cards ───────────────────────── */}
            {funnel && (
                <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', marginTop: 0 }}>
                    <div className="stat-card" style={{ borderLeft: `3px solid ${COLORS.info}` }}>
                        <div className="stat-card-label">🛒 Заказы WB</div>
                        <div className="stat-card-value" style={{ color: COLORS.info, fontSize: 22 }}>
                            {funnelOrders.toLocaleString('ru-RU')}
                        </div>
                        <div className="stat-card-sub">за период</div>
                    </div>
                    <div className="stat-card" style={{ borderLeft: `3px solid ${COLORS.income}` }}>
                        <div className="stat-card-label">💰 Сумма заказов</div>
                        <div className="stat-card-value" style={{ color: COLORS.income, fontSize: 22 }}>
                            {formatNumber(funnelSum, 0)} ₽
                        </div>
                    </div>
                    <div className="stat-card" style={{ borderLeft: `3px solid ${COLORS.warning}` }}>
                        <div className="stat-card-label">📢 Реклама</div>
                        <div className="stat-card-value" style={{ color: COLORS.warning, fontSize: 22 }}>
                            {formatNumber(funnelAdv, 0)} ₽
                        </div>
                    </div>
                    <div className="stat-card" style={{ borderLeft: `3px solid ${funnelDRR > 15 ? COLORS.expense : COLORS.income}` }}>
                        <div className="stat-card-label">📊 ДРР</div>
                        <div className="stat-card-value" style={{
                            color: funnelDRR > 15 ? COLORS.expense : COLORS.income,
                            fontSize: 22
                        }}>
                            {funnelDRR.toFixed(1)}%
                        </div>
                        <div className="stat-card-sub">реклама / заказы</div>
                    </div>
                </div>
            )}

            {/* ─── Charts row ────────────────────────────────── */}
            <div style={{
                display: 'grid',
                gridTemplateColumns: cashflowData.length > 0 && expensePie.length > 0 ? '1.6fr 1fr' : '1fr',
                gap: 20, marginTop: 20,
            }}>
                {/* Cashflow chart */}
                {cashflowData.length > 0 && (
                    <div className="glass-card" style={{ padding: '20px 16px' }}>
                        <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 16, color: 'var(--color-text)' }}>
                            Cashflow по категориям
                        </h3>
                        <ResponsiveContainer width="100%" height={320}>
                            <BarChart data={cashflowData} layout="vertical" margin={{ left: 80, right: 20 }}>
                                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                                <XAxis type="number" tickFormatter={fmtK} tick={{ fill: '#94a3b8', fontSize: 12 }} />
                                <YAxis
                                    type="category" dataKey="category" width={100}
                                    tick={{ fill: '#94a3b8', fontSize: 12 }}
                                />
                                <Tooltip content={<CustomTooltip />} />
                                <Bar dataKey="income" name="Приход" fill={COLORS.income} radius={[0, 4, 4, 0]} barSize={14} />
                                <Bar dataKey="expense" name="Расход" fill={COLORS.expense} radius={[0, 4, 4, 0]} barSize={14} />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                )}

                {/* Expense Pie  */}
                {expensePie.length > 0 && (
                    <div className="glass-card" style={{ padding: '20px 16px' }}>
                        <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 16, color: 'var(--color-text)' }}>
                            Структура расходов
                        </h3>
                        <ResponsiveContainer width="100%" height={320}>
                            <PieChart>
                                <Pie
                                    data={expensePie}
                                    dataKey="value"
                                    nameKey="name"
                                    cx="50%" cy="50%"
                                    innerRadius={60} outerRadius={110}
                                    paddingAngle={2}
                                    label={({ name, percent }) =>
                                        `${name} ${(percent * 100).toFixed(0)}%`
                                    }
                                    labelLine={false}
                                    style={{ fontSize: 11, fill: '#cbd5e1' }}
                                >
                                    {expensePie.map((_, i) => (
                                        <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                                    ))}
                                </Pie>
                                <Tooltip formatter={(v: number) => formatNumber(v) + ' ₽'} />
                            </PieChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </div>

            {/* ─── Balance table ──────────────────────────────── */}
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

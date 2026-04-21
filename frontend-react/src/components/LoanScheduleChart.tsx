'use client';

import {
    BarChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip,
    Legend, ResponsiveContainer, ComposedChart,
} from 'recharts';
import { formatNumber, formatDate } from '@/lib/utils';
import type { LoanPayment } from '@/types/api';

interface LoanScheduleChartProps {
    payments: LoanPayment[];
    principal: number;
    currency: string;
}

interface MonthData {
    month: string;
    principal_repay: number;
    interest_pay: number;
    disbursement: number;
    penalty: number;
    remaining: number;
}

const PAYMENT_TYPE_LABELS: Record<string, string> = {
    DISBURSEMENT: 'Выдача',
    PRINCIPAL_REPAY: 'Возврат осн.',
    INTEREST_PAY: 'Проценты',
    PENALTY: 'Штраф',
};

function buildMonthlyData(payments: LoanPayment[], principal: number): MonthData[] {
    if (payments.length === 0) return [];

    // Group by month
    const byMonth: Record<string, MonthData> = {};
    for (const p of payments) {
        const month = p.paid_at.slice(0, 7); // "YYYY-MM"
        if (!byMonth[month]) {
            byMonth[month] = {
                month,
                principal_repay: 0,
                interest_pay: 0,
                disbursement: 0,
                penalty: 0,
                remaining: 0,
            };
        }
        switch (p.payment_type) {
            case 'PRINCIPAL_REPAY': byMonth[month].principal_repay += Number(p.amount); break;
            case 'INTEREST_PAY': byMonth[month].interest_pay += Number(p.amount); break;
            case 'DISBURSEMENT': byMonth[month].disbursement += Number(p.amount); break;
            case 'PENALTY': byMonth[month].penalty += Number(p.amount); break;
        }
    }

    // Sort by month and compute running remaining principal
    const sorted = Object.values(byMonth).sort((a, b) => a.month.localeCompare(b.month));
    let remaining = principal;
    for (const row of sorted) {
        remaining = remaining + row.disbursement - row.principal_repay;
        row.remaining = Math.max(0, remaining);
    }

    return sorted;
}

const tooltipFormatter = (value: number, name: string) => {
    return [formatNumber(value), PAYMENT_TYPE_LABELS[name] ?? name];
};

export default function LoanScheduleChart({ payments, principal, currency }: LoanScheduleChartProps) {
    const data = buildMonthlyData(payments, principal);

    if (data.length === 0) {
        return (
            <div
                className="glass-card"
                style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}
            >
                Нет данных о платежах для графика
            </div>
        );
    }

    return (
        <div className="glass-card" style={{ padding: 16 }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 16 }}>
                График платежей по займу ({currency})
            </div>
            <ResponsiveContainer width="100%" height={280}>
                <ComposedChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                    <XAxis
                        dataKey="month"
                        tick={{ fontSize: 11 }}
                        tickFormatter={(v: string) => {
                            const [y, m] = v.split('-');
                            return `${m}/${y.slice(2)}`;
                        }}
                    />
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => formatNumber(v, 0)} />
                    <Tooltip formatter={tooltipFormatter} />
                    <Legend formatter={(v: string) => PAYMENT_TYPE_LABELS[v] ?? v} />
                    <Bar dataKey="disbursement" stackId="a" fill="#3b82f6" name="disbursement" />
                    <Bar dataKey="principal_repay" stackId="a" fill="#34c759" name="principal_repay" />
                    <Bar dataKey="interest_pay" stackId="a" fill="#ff9f0a" name="interest_pay" />
                    <Bar dataKey="penalty" stackId="a" fill="#ff3b30" name="penalty" />
                    <Line
                        type="monotone"
                        dataKey="remaining"
                        stroke="#0071e3"
                        strokeWidth={2}
                        dot={{ r: 3 }}
                        name="remaining"
                    />
                </ComposedChart>
            </ResponsiveContainer>
        </div>
    );
}

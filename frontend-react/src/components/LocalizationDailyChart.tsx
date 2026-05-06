'use client';

import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
    Legend, ResponsiveContainer,
} from 'recharts';
import { formatNumber, formatDate } from '@/lib/utils';
import type { LocalizationDailyPoint } from '@/types/api';

interface LocalizationDailyChartProps {
    data: LocalizationDailyPoint[];
}

interface ChartRow {
    date: string;
    il: number;
    irp: number;
    orders: number;
    articles: number;
}

function toRows(data: LocalizationDailyPoint[]): ChartRow[] {
    return data.map(p => ({
        date: p.date,
        il: Number(p.localization_index),
        irp: Number(p.irp_percent),
        orders: Number(p.total_orders),
        articles: Number(p.articles_count),
    }));
}

const FIELD_LABELS: Record<string, string> = {
    il: 'ИЛ (КТР)',
    irp: 'ИРП, %',
};

function CustomTooltip({ active, payload, label }: {
    active?: boolean;
    payload?: Array<{ name: string; value: number; payload: ChartRow }>;
    label?: string;
}) {
    if (!active || !payload || payload.length === 0) return null;
    const row = payload[0].payload;
    return (
        <div
            className="glass-card"
            style={{
                padding: 12,
                fontSize: 12,
                minWidth: 180,
            }}
        >
            <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 13 }}>
                {formatDate(label ?? '')}
            </div>
            {payload.map(p => (
                <div
                    key={p.name}
                    style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        gap: 12,
                        marginBottom: 4,
                    }}
                >
                    <span style={{ color: 'var(--color-text-muted)' }}>
                        {FIELD_LABELS[p.name] ?? p.name}
                    </span>
                    <span style={{ fontWeight: 600 }}>
                        {p.name === 'irp'
                            ? `${formatNumber(p.value, 2)}%`
                            : formatNumber(p.value, 2)}
                    </span>
                </div>
            ))}
            <div
                style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    gap: 12,
                    marginTop: 8,
                    paddingTop: 8,
                    borderTop: '1px solid var(--color-border)',
                    color: 'var(--color-text-muted)',
                }}
            >
                <span>Заказов</span>
                <span style={{ fontWeight: 500 }}>{formatNumber(row.orders, 0)}</span>
            </div>
            <div
                style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    gap: 12,
                    color: 'var(--color-text-muted)',
                }}
            >
                <span>Артикулов</span>
                <span style={{ fontWeight: 500 }}>{formatNumber(row.articles, 0)}</span>
            </div>
        </div>
    );
}

export default function LocalizationDailyChart({ data }: LocalizationDailyChartProps) {
    const rows = toRows(data);

    if (rows.length === 0) {
        return (
            <div
                className="glass-card"
                style={{
                    padding: 32,
                    textAlign: 'center',
                    color: 'var(--color-text-dim)',
                    fontSize: 13,
                }}
            >
                Нет данных за выбранный период
            </div>
        );
    }

    return (
        <div className="glass-card" style={{ padding: 20 }}>
            <div
                style={{
                    fontSize: 14,
                    fontWeight: 600,
                    marginBottom: 4,
                    color: 'var(--color-text)',
                }}
            >
                Динамика ИЛ и ИРП по дням
            </div>
            <div
                style={{
                    fontSize: 12,
                    color: 'var(--color-text-muted)',
                    marginBottom: 16,
                }}
            >
                {rows.length} {rows.length === 1 ? 'день' : 'дн.'} · реагирует на фильтры по бренду и предмету
            </div>
            <ResponsiveContainer width="100%" height={280}>
                <LineChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                    <XAxis
                        dataKey="date"
                        tick={{ fontSize: 11 }}
                        tickFormatter={(v: string) => {
                            const [, m, d] = v.split('-');
                            return `${d}.${m}`;
                        }}
                    />
                    <YAxis
                        yAxisId="il"
                        tick={{ fontSize: 11 }}
                        tickFormatter={(v: number) => formatNumber(v, 2)}
                        width={48}
                        label={{
                            value: 'ИЛ (КТР)',
                            angle: -90,
                            position: 'insideLeft',
                            style: { fontSize: 11, fill: 'var(--color-text-muted)' },
                        }}
                    />
                    <YAxis
                        yAxisId="irp"
                        orientation="right"
                        tick={{ fontSize: 11 }}
                        tickFormatter={(v: number) => `${formatNumber(v, 1)}%`}
                        width={56}
                        label={{
                            value: 'ИРП, %',
                            angle: 90,
                            position: 'insideRight',
                            style: { fontSize: 11, fill: 'var(--color-text-muted)' },
                        }}
                    />
                    <Tooltip content={<CustomTooltip />} />
                    <Legend
                        formatter={(v: string) => FIELD_LABELS[v] ?? v}
                        wrapperStyle={{ fontSize: 12 }}
                    />
                    <Line
                        yAxisId="il"
                        type="monotone"
                        dataKey="il"
                        stroke="var(--color-accent)"
                        strokeWidth={2}
                        dot={{ r: 3 }}
                        activeDot={{ r: 5 }}
                        name="il"
                    />
                    <Line
                        yAxisId="irp"
                        type="monotone"
                        dataKey="irp"
                        stroke="#a855f7"
                        strokeWidth={2}
                        dot={{ r: 3 }}
                        activeDot={{ r: 5 }}
                        name="irp"
                    />
                </LineChart>
            </ResponsiveContainer>
        </div>
    );
}

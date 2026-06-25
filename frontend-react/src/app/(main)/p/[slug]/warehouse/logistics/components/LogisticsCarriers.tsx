'use client';

import React, { useMemo } from 'react';
import {
    BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip,
    ResponsiveContainer, Cell,
} from 'recharts';
import { formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { LogisticsCarrierStat, LogisticsDestStat } from '@/types/api';

interface Props {
    byCarrier: LogisticsCarrierStat[];
    byDestination: LogisticsDestStat[];
}

/** Task: топ по подрядчикам + какие склады сдаются (объём отгрузок по складам). */
export default function LogisticsCarriers({ byCarrier, byDestination }: Props) {
    const topCarriers = useMemo(() => byCarrier.slice(0, 10).map(c => ({
        name: c.carrier_name.length > 22 ? c.carrier_name.slice(0, 21) + '…' : c.carrier_name,
        full: c.carrier_name,
        total_cost: c.total_cost,
        avg: c.avg_cost_per_pallet,
    })), [byCarrier]);

    const carrierColumns: Column[] = [
        { key: 'carrier_name', label: 'Подрядчик', sortable: true },
        { key: 'carrier_inn', label: 'ИНН', sortable: true, render: (v: string | null) => v || '—' },
        { key: 'shipments_count', label: 'Отправок', align: 'right', sortable: true, format: 'number' },
        { key: 'total_pallets', label: 'Паллет', align: 'right', sortable: true, format: 'number' },
        { key: 'destinations_count', label: 'Складов', align: 'right', sortable: true, format: 'number' },
        { key: 'avg_cost_per_pallet', label: 'Сред ₽/пал', align: 'right', sortable: true, render: (v: number) => formatNumber(v, 0) },
        { key: 'total_cost', label: 'Сумма ₽', align: 'right', sortable: true, render: (v: number) => <strong>{formatNumber(v, 0)}</strong> },
    ];

    const destColumns: Column[] = [
        { key: 'dest_warehouse', label: 'Склад сдачи', sortable: true },
        { key: 'shipments_count', label: 'Отправок', align: 'right', sortable: true, format: 'number' },
        { key: 'total_pallets', label: 'Паллет', align: 'right', sortable: true, format: 'number' },
        { key: 'avg_cost', label: 'Сред ₽/пал', align: 'right', sortable: true, render: (v: number) => formatNumber(v, 0) },
        { key: 'total_cost', label: 'Сумма ₽', align: 'right', sortable: true, render: (v: number) => formatNumber(v, 0) },
    ];

    const maxCost = topCarriers.length ? Math.max(...topCarriers.map(c => c.total_cost)) : 0;

    return (
        <div>
            <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Топ-10 подрядчиков по расходам</div>
                {topCarriers.length === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>Нет привязанных подрядчиков за период</div>
                ) : (
                    <ResponsiveContainer width="100%" height={Math.max(220, topCarriers.length * 34)}>
                        <BarChart data={topCarriers} layout="vertical" margin={{ top: 5, right: 30, bottom: 5, left: 10 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" horizontal={false} />
                            <XAxis type="number" tick={{ fontSize: 11 }} />
                            <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={150} />
                            <RechartsTooltip
                                formatter={(value: number) => [formatNumber(value, 0) + ' ₽', 'Сумма']}
                                labelFormatter={(_l, p) => (p && p[0] ? p[0].payload.full : '')}
                                contentStyle={{ background: 'var(--color-bg-primary)', border: '1px solid var(--color-border)', borderRadius: 8, fontSize: 12 }}
                            />
                            <Bar dataKey="total_cost" radius={[0, 4, 4, 0]}>
                                {topCarriers.map((c, i) => (
                                    <Cell key={i} fill={c.total_cost >= maxCost * 0.66 ? '#007aff' : c.total_cost >= maxCost * 0.33 ? '#5ac8fa' : '#a0d4f5'} />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                )}
            </div>

            {byCarrier.length > 0 && (
                <div style={{ marginBottom: 16 }}>
                    <TanStackDataTable
                        columns={carrierColumns}
                        data={byCarrier}
                        title="Подрядчики"
                        exportName="logistics_carriers"
                        enableSorting
                        enablePagination={byCarrier.length > 50}
                        pageSize={50}
                    />
                </div>
            )}

            {byDestination.length > 0 && (
                <TanStackDataTable
                    columns={destColumns}
                    data={byDestination}
                    title="Склады сдачи — объём отгрузок"
                    exportName="logistics_destinations"
                    enableSorting
                    enablePagination={false}
                />
            )}
        </div>
    );
}

'use client';

import React, { useMemo } from 'react';
import { formatNumber, formatDate } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { LogisticsAnomaly, LogisticsAnomalyType } from '@/types/api';

const TYPE_CFG: Record<LogisticsAnomalyType, { label: string; className: string }> = {
    no_cost: { label: 'Нет стоимости', className: 'badge-secondary' },
    overpriced: { label: 'Завышена', className: 'badge-danger' },
    underpriced: { label: 'Занижена', className: 'badge-warning' },
};

interface Props {
    anomalies: LogisticsAnomaly[];
    slug: string;
}

/** Task: аномальные отгрузки — без стоимости / завышенная / заниженная ₽/паллета. */
export default function LogisticsAnomalies({ anomalies, slug }: Props) {
    const counts = useMemo(() => {
        const c: Record<LogisticsAnomalyType, number> = { no_cost: 0, overpriced: 0, underpriced: 0 };
        for (const a of anomalies) c[a.anomaly_type]++;
        return c;
    }, [anomalies]);

    const columns: Column[] = [
        {
            key: 'anomaly_type', label: 'Тип', sortable: true,
            render: (v: LogisticsAnomalyType) => {
                const cfg = TYPE_CFG[v];
                return <span className={`badge ${cfg.className}`}>{cfg.label}</span>;
            },
        },
        {
            key: 'assembly_number', label: 'Заявка', sortable: true,
            render: (v: string | null, row: LogisticsAnomaly) => v
                ? <a href={`/p/${slug}/warehouse/assembly/${row.assembly_request_id}`} style={{ fontWeight: 600, color: 'var(--color-primary)', textDecoration: 'none' }}>{v}</a>
                : '—',
        },
        { key: 'dest_warehouse', label: 'Склад сдачи', sortable: true },
        { key: 'carrier_name', label: 'Подрядчик', sortable: true, render: (v: string | null) => v || '—' },
        { key: 'pallets_count', label: 'Паллет', align: 'right', sortable: true, render: (v: number | null) => v ?? '—' },
        { key: 'pickup_cost', label: 'Стоимость', align: 'right', sortable: true, render: (v: number | null) => v != null ? formatNumber(v, 0) : '—' },
        {
            key: 'cost_per_pallet', label: '₽/паллета', align: 'right', sortable: true,
            render: (v: number | null, row: LogisticsAnomaly) => v == null ? '—'
                : <strong style={{ color: row.anomaly_type === 'overpriced' ? 'var(--color-danger)' : row.anomaly_type === 'underpriced' ? 'var(--color-warning)' : undefined }}>{formatNumber(v, 0)}</strong>,
        },
        {
            key: 'expected', label: 'Ожидаемо ₽/пал', align: 'right', sortable: false,
            getValue: (row: LogisticsAnomaly) => row.expected_low ?? 0,
            render: (_v: unknown, row: LogisticsAnomaly) => (row.expected_low != null && row.expected_high != null)
                ? <span style={{ color: 'var(--color-text-muted)', fontSize: 12 }}>{formatNumber(row.expected_low, 0)}–{formatNumber(row.expected_high, 0)}</span>
                : '—',
        },
        { key: 'shipped_date', label: 'Отгрузка', sortable: true, render: (v: string | null) => v ? formatDate(v) : '—' },
        { key: 'reason', label: 'Причина', sortable: false, render: (v: string) => <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{v}</span> },
    ];

    const chip = (label: string, n: number, color: string) => (
        <div className="glass-card" style={{ padding: '12px 16px', textAlign: 'center', flex: 1 }}>
            <div style={{ fontSize: 22, fontWeight: 700, color }}>{n}</div>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{label}</div>
        </div>
    );

    return (
        <div>
            <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
                {chip('Без стоимости', counts.no_cost, 'var(--color-text)')}
                {chip('Завышенная цена', counts.overpriced, 'var(--color-danger)')}
                {chip('Заниженная цена', counts.underpriced, 'var(--color-warning)')}
            </div>

            {anomalies.length === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>✅</div>
                    <div style={{ fontSize: 15, fontWeight: 500 }}>Аномалий не найдено</div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 13, marginTop: 4 }}>
                        Все отгрузки за период со стоимостью в пределах нормы по складам
                    </div>
                </div>
            ) : (
                <>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>
                        Норма ₽/паллета считается по каждому складу отдельно (медиана ± устойчивый разброс). Показаны до 100 самых выраженных отклонений.
                    </div>
                    <TanStackDataTable
                        columns={columns}
                        data={anomalies}
                        title="Аномальные отправки"
                        exportName="logistics_anomalies"
                        enableSorting
                        enablePagination={anomalies.length > 50}
                        pageSize={50}
                    />
                </>
            )}
        </div>
    );
}

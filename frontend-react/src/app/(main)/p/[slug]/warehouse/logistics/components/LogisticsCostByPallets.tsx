'use client';

import React, { useMemo } from 'react';
import {
    BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip,
    ResponsiveContainer, ScatterChart, Scatter, ZAxis, LabelList,
} from 'recharts';
import { formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { LogisticsPalletBucketStat, LogisticsCostPoint, LogisticsDestStat, LogisticsDestBucketCell } from '@/types/api';

// Палитра для складов на scatter (циклично).
const PALETTE = ['#007aff', '#34c759', '#ff9500', '#af52de', '#ff2d55', '#5ac8fa', '#ffcc00', '#ff3b30', '#5856d6', '#00c7be'];

interface Props {
    buckets: LogisticsPalletBucketStat[];
    points: LogisticsCostPoint[];
    byDestination: LogisticsDestStat[];
    destCells: LogisticsDestBucketCell[];
}

/**
 * Task: стоимость склада в зависимости от количества паллет.
 * Эффект объёма (больше паллет → дешевле паллета) показываем срезами:
 *  1. кривая «размер отгрузки → ₽/паллета» в среднем (bucket bar, с подписью ₽);
 *  2. матрица «склад сдачи × размер отгрузки → ₽/паллета» (per-warehouse — главное);
 *  3. scatter «паллеты vs ₽/паллета» с раскраской по складам;
 *  4. таблица по складам с min/avg/max ₽/паллета.
 */
export default function LogisticsCostByPallets({ buckets, points, byDestination, destCells }: Props) {
    // Топ-складов по числу отгрузок — остальные сворачиваем в «Прочие», чтобы scatter читался.
    const topDests = useMemo(() => {
        const counts = new Map<string, number>();
        for (const p of points) counts.set(p.dest_warehouse, (counts.get(p.dest_warehouse) || 0) + 1);
        return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 9).map(e => e[0]);
    }, [points]);

    const scatterSeries = useMemo(() => {
        const groups = new Map<string, LogisticsCostPoint[]>();
        for (const p of points) {
            const key = topDests.includes(p.dest_warehouse) ? p.dest_warehouse : 'Прочие';
            const arr = groups.get(key) || [];
            arr.push(p);
            groups.set(key, arr);
        }
        return [...groups.entries()].map(([name, pts], i) => ({
            name,
            color: name === 'Прочие' ? '#8e8e93' : PALETTE[i % PALETTE.length],
            data: pts.map(p => ({ pallets: p.pallets, cpp: p.cost_per_pallet, dest: p.dest_warehouse })),
        }));
    }, [points, topDests]);

    const destColumns: Column[] = [
        { key: 'dest_warehouse', label: 'Склад сдачи', sortable: true },
        { key: 'shipments_count', label: 'Отправок', align: 'right', sortable: true, format: 'number' },
        { key: 'avg_pallets', label: 'Ср. паллет/отпр.', align: 'right', sortable: true, render: (v: number) => formatNumber(v, 1) },
        { key: 'total_pallets', label: 'Всего паллет', align: 'right', sortable: true, format: 'number' },
        { key: 'min_cost_per_pallet', label: 'Мин ₽/пал', align: 'right', sortable: true, render: (v: number) => formatNumber(v, 0) },
        { key: 'avg_cost', label: 'Сред ₽/пал', align: 'right', sortable: true, render: (v: number) => <strong>{formatNumber(v, 0)}</strong> },
        { key: 'max_cost_per_pallet', label: 'Макс ₽/пал', align: 'right', sortable: true, render: (v: number) => formatNumber(v, 0) },
        { key: 'total_cost', label: 'Сумма ₽', align: 'right', sortable: true, render: (v: number) => formatNumber(v, 0) },
    ];

    return (
        <div>
            {/* Bucket curve (среднее) */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>Стоимость паллеты от размера отгрузки (в среднем)</div>
                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                    Чем больше паллет в отгрузке, тем дешевле паллета. Над столбцом — ₽/паллета, внутри — число отправок.
                </div>
                {buckets.length === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>Нет данных за период</div>
                ) : (
                    <ResponsiveContainer width="100%" height={300}>
                        <BarChart data={buckets} margin={{ top: 28, right: 20, bottom: 5, left: 20 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                            <XAxis dataKey="bucket" tick={{ fontSize: 12 }} label={{ value: 'Паллет в отгрузке', position: 'insideBottom', offset: -2, fontSize: 11 }} />
                            <YAxis tick={{ fontSize: 12 }} />
                            <RechartsTooltip
                                formatter={(value: number, name) => name === 'avg_cost_per_pallet' ? [formatNumber(value, 0) + ' ₽/пал', 'Средняя'] : [value, name]}
                                contentStyle={{ background: 'var(--color-bg-primary)', border: '1px solid var(--color-border)', borderRadius: 8, fontSize: 13 }}
                            />
                            <Bar dataKey="avg_cost_per_pallet" radius={[4, 4, 0, 0]} fill="#007aff" maxBarSize={120}>
                                <LabelList dataKey="avg_cost_per_pallet" position="top" formatter={(v: number) => formatNumber(v, 0) + ' ₽'} style={{ fontSize: 13, fontWeight: 700, fill: 'var(--color-text)' }} />
                                <LabelList dataKey="shipments_count" position="insideTop" formatter={(v: number) => `${v} шт`} style={{ fontSize: 11, fill: '#fff' }} />
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                )}
            </div>

            {/* Warehouse × bucket heatmap (per-warehouse) */}
            <DestBucketHeatmap cells={destCells} />

            {/* Scatter pallets vs cpp */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Паллеты × ₽/паллета по складам (каждая точка — отгрузка)</div>
                {points.length === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>Нет данных за период</div>
                ) : (
                    <>
                        <ResponsiveContainer width="100%" height={320}>
                            <ScatterChart margin={{ top: 10, right: 20, bottom: 16, left: 20 }}>
                                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                                <XAxis type="number" dataKey="pallets" name="Паллет" tick={{ fontSize: 12 }} label={{ value: 'Паллет в отгрузке', position: 'insideBottom', offset: -8, fontSize: 11 }} />
                                <YAxis type="number" dataKey="cpp" name="₽/паллета" tick={{ fontSize: 12 }} />
                                <ZAxis range={[40, 40]} />
                                <RechartsTooltip
                                    cursor={{ strokeDasharray: '3 3' }}
                                    contentStyle={{ background: 'var(--color-bg-primary)', border: '1px solid var(--color-border)', borderRadius: 8, fontSize: 12 }}
                                    formatter={(value: number, name) => [name === '₽/паллета' ? formatNumber(value, 0) + ' ₽' : value, name]}
                                />
                                {scatterSeries.map(s => (
                                    <Scatter key={s.name} name={s.name} data={s.data} fill={s.color} fillOpacity={0.7} />
                                ))}
                            </ScatterChart>
                        </ResponsiveContainer>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 8 }}>
                            {scatterSeries.map(s => (
                                <span key={s.name} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--color-text-muted)' }}>
                                    <span style={{ width: 10, height: 10, borderRadius: '50%', background: s.color, display: 'inline-block' }} />
                                    {s.name}
                                </span>
                            ))}
                        </div>
                    </>
                )}
            </div>

            {/* Per-warehouse table */}
            {byDestination.length > 0 && (
                <TanStackDataTable
                    columns={destColumns}
                    data={byDestination}
                    title="Стоимость по складам сдачи"
                    exportName="logistics_cost_by_warehouse"
                    enableSorting
                    enablePagination={false}
                />
            )}
        </div>
    );
}

// ─── Warehouse × pallet-bucket heatmap ──────────────────────────────────────

const BUCKET_ORDER = ['1', '2-3', '4-5', '6-10', '11+'];

function DestBucketHeatmap({ cells }: { cells: LogisticsDestBucketCell[] }) {
    const { warehouses, buckets, cellMap, minCpp, maxCpp } = useMemo(() => {
        const map = new Map<string, LogisticsDestBucketCell>();
        const whCount = new Map<string, number>();
        const bucketSet = new Set<string>();
        let mn = Infinity, mx = -Infinity;
        for (const c of cells) {
            map.set(`${c.dest_warehouse}|${c.bucket}`, c);
            whCount.set(c.dest_warehouse, (whCount.get(c.dest_warehouse) || 0) + c.shipments_count);
            bucketSet.add(c.bucket);
            if (c.avg_cost_per_pallet < mn) mn = c.avg_cost_per_pallet;
            if (c.avg_cost_per_pallet > mx) mx = c.avg_cost_per_pallet;
        }
        const whs = [...whCount.entries()].sort((a, b) => b[1] - a[1]).map(e => e[0]);
        const bks = BUCKET_ORDER.filter(b => bucketSet.has(b));
        return { warehouses: whs, buckets: bks, cellMap: map, minCpp: mn, maxCpp: mx };
    }, [cells]);

    // green (#d1fae5 дёшево) → red (#fee2e2 дорого)
    const color = (v: number): string => {
        if (maxCpp === minCpp) return '#d1fae5';
        const t = (v - minCpp) / (maxCpp - minCpp);
        const r = Math.round(209 + (254 - 209) * t);
        const g = Math.round(250 + (202 - 250) * t);
        const b = Math.round(229 + (202 - 229) * t);
        return `rgb(${r}, ${g}, ${b})`;
    };

    if (cells.length === 0) return null;

    return (
        <div className="glass-card" style={{ padding: 16, marginBottom: 16, overflow: 'auto' }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>Стоимость паллеты по складам и размеру отгрузки</div>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                ₽/паллета для каждого склада по размеру отгрузки. Слева направо в строке цена должна падать (эффект объёма); зелёный — дешевле, красный — дороже. Маленькое число — отправок.
            </div>
            <div style={{
                display: 'grid',
                gridTemplateColumns: `minmax(150px, 1.4fr) repeat(${buckets.length}, minmax(72px, 1fr))`,
                gap: 4,
                minWidth: 150 + buckets.length * 80,
            }}>
                {/* Header */}
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', padding: 8, alignSelf: 'end' }}>Склад \ паллет</div>
                {buckets.map(b => (
                    <div key={b} style={{ fontSize: 12, fontWeight: 600, textAlign: 'center', padding: 8, color: 'var(--color-text-muted)' }}>{b}</div>
                ))}

                {/* Rows */}
                {warehouses.map(wh => (
                    <React.Fragment key={wh}>
                        <div style={{ fontSize: 12, fontWeight: 500, padding: 8, display: 'flex', alignItems: 'center', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={wh}>
                            {wh}
                        </div>
                        {buckets.map(b => {
                            const c = cellMap.get(`${wh}|${b}`);
                            if (!c) {
                                return (
                                    <div key={b} style={{ padding: '8px 4px', textAlign: 'center', fontSize: 12, color: 'var(--color-text-muted)', borderRadius: 6, background: 'var(--color-bg-secondary)' }}>—</div>
                                );
                            }
                            return (
                                <div
                                    key={b}
                                    title={`${wh} · ${b} паллет: ${formatNumber(c.avg_cost_per_pallet, 0)} ₽/пал · ${c.shipments_count} отправок`}
                                    style={{ padding: '6px 4px', textAlign: 'center', borderRadius: 6, background: color(c.avg_cost_per_pallet), color: '#1a1a1a' }}
                                >
                                    <div style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.2 }}>{formatNumber(c.avg_cost_per_pallet, 0)}</div>
                                    <div style={{ fontSize: 10, opacity: 0.6 }}>{c.shipments_count}</div>
                                </div>
                            );
                        })}
                    </React.Fragment>
                ))}
            </div>
        </div>
    );
}

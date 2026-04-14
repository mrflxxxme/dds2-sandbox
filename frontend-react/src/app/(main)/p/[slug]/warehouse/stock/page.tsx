'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import TabLayout from '@/components/TabLayout';
import type { Warehouse, StockSummaryRow, UnifiedStockRow, TrendPeriodData } from '@/types/api';
import type { Column } from '@/components/DataTable';

function StockCell({ qty, reserved }: { qty: number; reserved: number }) {
    if (!qty && !reserved) return <span style={{ color: 'var(--color-text-muted)' }}>{'\u2014'}</span>;
    const available = Math.max(0, qty - reserved);
    if (!reserved) return <>{formatNumber(qty)}</>;
    return (
        <>
            <span>{formatNumber(available)}</span>
            <span style={{ color: 'var(--color-warning)' }}> / {formatNumber(reserved)}</span>
        </>
    );
}

// ─── Summary tab (original view) ─────────────────────────────────────────

function SummaryTab({
    warehouses,
    summary,
}: {
    warehouses: Warehouse[];
    summary: StockSummaryRow[];
}) {
    const [search, setSearch] = useState('');
    const [filter, setFilter] = useState<'all' | 'reserved'>('all');

    let filtered = search
        ? summary.filter(r => r.barcode.includes(search))
        : summary;

    if (filter === 'reserved') {
        filtered = filtered.filter(r => (r.total_reserved || 0) > 0);
    }

    const cols: Column[] = [
        { key: 'barcode', label: 'ШК' },
    ];

    for (const wh of warehouses) {
        cols.push({
            key: `wh_${wh.id}`,
            label: wh.name,
            align: 'right',
            render: (_: unknown, row: StockSummaryRow) => {
                const qty = row.warehouses[wh.id] || 0;
                const res = (row.reserved || {})[wh.id] || 0;
                return <StockCell qty={qty} reserved={res} />;
            },
        });
    }

    cols.push(
        {
            key: 'total_in_transit',
            label: 'В пути',
            align: 'right',
            render: (v: number) => v > 0 ? formatNumber(v) : '\u2014',
        },
        {
            key: 'total',
            label: 'Итого',
            align: 'right',
            render: (_: unknown, row: StockSummaryRow) => {
                const res = row.total_reserved || 0;
                if (!res) return <strong>{formatNumber(row.total)}</strong>;
                return (
                    <strong>
                        <span>{formatNumber(row.total_available || 0)}</span>
                        <span style={{ color: 'var(--color-warning)' }}> / {formatNumber(res)}</span>
                    </strong>
                );
            },
        },
    );

    return (
        <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
                <input
                    className="form-input"
                    placeholder="Поиск по баркоду..."
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    style={{ maxWidth: 300 }}
                />
                <div style={{ display: 'flex', border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden' }}>
                    <button
                        onClick={() => setFilter('all')}
                        style={{
                            padding: '6px 14px', fontSize: 13, border: 'none', cursor: 'pointer',
                            background: filter === 'all' ? 'var(--color-primary)' : 'var(--color-bg)',
                            color: filter === 'all' ? '#fff' : 'var(--color-text)',
                        }}
                    >Все остатки</button>
                    <button
                        onClick={() => setFilter('reserved')}
                        style={{
                            padding: '6px 14px', fontSize: 13, border: 'none', cursor: 'pointer',
                            borderLeft: '1px solid var(--color-border)',
                            background: filter === 'reserved' ? 'var(--color-warning)' : 'var(--color-bg)',
                            color: filter === 'reserved' ? '#fff' : 'var(--color-text)',
                        }}
                    >Зарезервировано</button>
                </div>
            </div>

            {filtered.length === 0 ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', opacity: 0.6 }}>
                    <div style={{ fontSize: 48 }}>{'\uD83D\uDCE6'}</div>
                    <div>{filter === 'reserved' ? 'Нет зарезервированных позиций' : 'Нет данных по остаткам'}</div>
                </div>
            ) : (
                <TanStackDataTable
                    columns={cols}
                    data={filtered}
                    exportName="stock_summary"
                    enableSorting
                    enablePagination
                    pageSize={50}
                    actions={
                        <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                            {filtered.length} позиций
                        </span>
                    }
                />
            )}
        </>
    );
}

// ─── Unified tab ─────────────────────────────────────────────────────────

function WbDetailRow({ row, wbWarehouses, mode }: { row: UnifiedStockRow; wbWarehouses: string[]; mode: string }) {
    return (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', padding: '8px 0' }}>
            {wbWarehouses.map(wh => {
                const v = row.wb_stocks[wh] || 0;
                if (v <= 0) return null;
                let display = formatNumber(v, 0);
                let color = '#7c3aed';
                let bg = 'rgba(175, 82, 222, 0.08)';
                if (mode === 'cost' && row.avg_cost > 0) {
                    display = formatNumber(v * row.avg_cost) + '\u00A0\u20BD';
                } else if (mode === 'revenue' && row.avg_price > 0) {
                    display = formatNumber(v * row.avg_price) + '\u00A0\u20BD';
                } else if (mode === 'profit' && row.avg_profit) {
                    const val = v * row.avg_profit;
                    display = formatNumber(val) + '\u00A0\u20BD';
                    color = val >= 0 ? 'var(--color-success)' : 'var(--color-danger)';
                    bg = val >= 0 ? 'rgba(52, 199, 89, 0.08)' : 'rgba(255, 59, 48, 0.08)';
                }
                return (
                    <span key={wh} style={{ fontSize: 12, padding: '2px 8px', borderRadius: 6, background: bg, color, whiteSpace: 'nowrap' }}>
                        {wh}: {display}
                    </span>
                );
            })}
        </div>
    );
}

function UnifiedTab({ data, onRefresh, groupBy, onGroupChange }: {
    data: UnifiedStockRow[];
    onRefresh: () => void;
    groupBy: string;
    onGroupChange: (groupBy: string) => void;
}) {
    const [search, setSearch] = useState('');
    const [expanded, setExpanded] = useState<Set<number>>(new Set());
    const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
    const [expandedSubGroups, setExpandedSubGroups] = useState<Set<string>>(new Set());
    const [syncing, setSyncing] = useState(false);
    const [mode, setMode] = useState<'qty' | 'cost' | 'revenue' | 'profit'>('qty');
    const [variant, setVariant] = useState<1 | 2 | 3>(2);
    const [trendPeriod, setTrendPeriod] = useState<7 | 14 | 30>(14);
    const isGrouped = groupBy !== 'sku' && groupBy !== 'abc';

    const getVariantTotal = useCallback((row: UnifiedStockRow): number => {
        if (variant === 1) return row.total_wb || 0;
        if (variant === 2) return (row.total_own || 0) + (row.total_wb || 0) + (row.in_transit || 0);
        return (row.total_own || 0) + (row.total_wb || 0) + (row.in_transit || 0)
             + (row.factory_qty || 0) + (row.vehicle_forming_qty || 0) + (row.vehicle_transit_qty || 0);
    }, [variant]);

    const getTrendData = useCallback((row: UnifiedStockRow): TrendPeriodData => {
        if (trendPeriod === 7) return row.trend_7 || { avg_daily_qty: 0, revenue: 0, profit: 0 };
        if (trendPeriod === 14) return row.trend_14 || { avg_daily_qty: 0, revenue: 0, profit: 0 };
        return row.trend_30 || { avg_daily_qty: 0, revenue: 0, profit: 0 };
    }, [trendPeriod]);

    // Column visibility based on selected variant
    const showOwn = variant >= 2;
    const showFactory = variant === 3;
    const showVehicles = variant === 3;

    const toggleExpand = (nomId: number) => {
        setExpanded(prev => {
            const next = new Set(prev);
            if (next.has(nomId)) next.delete(nomId); else next.add(nomId);
            return next;
        });
    };

    const toggleGroup = (key: string) => {
        setExpandedGroups(prev => {
            const next = new Set(prev);
            next.has(key) ? next.delete(key) : next.add(key);
            return next;
        });
    };

    const toggleSubGroup = (key: string) => {
        setExpandedSubGroups(prev => {
            const next = new Set(prev);
            next.has(key) ? next.delete(key) : next.add(key);
            return next;
        });
    };

    const handleSync = async () => {
        setSyncing(true);
        try {
            await api.syncWarehouseStocks();
            onRefresh();
        } catch { /* ignore */ }
        setSyncing(false);
    };

    const filtered = useMemo(() => {
        if (!search) return data;
        const q = search.toLowerCase();
        if (isGrouped) {
            return data.filter(r =>
                (r.group_name || '').toLowerCase().includes(q) ||
                (r.children || []).some(c =>
                    (c.barcode || '').toLowerCase().includes(q) ||
                    (c.article_seller || '').toLowerCase().includes(q)
                )
            );
        }
        return data.filter(r =>
            (r.barcode || '').toLowerCase().includes(q) ||
            (r.article_seller || '').toLowerCase().includes(q) ||
            (r.group_name || '').toLowerCase().includes(q)
        );
    }, [data, search, isGrouped]);

    const { ownWarehouses, wbWarehouses } = useMemo(() => {
        const ownSet = new Set<string>();
        const wbSet = new Set<string>();
        for (const row of data) {
            for (const k of Object.keys(row.warehouses)) ownSet.add(k);
            for (const k of Object.keys(row.wb_stocks)) wbSet.add(k);
        }
        return {
            ownWarehouses: Array.from(ownSet).sort(),
            wbWarehouses: Array.from(wbSet).sort(),
        };
    }, [data]);

    const totals = useMemo(() => {
        let ownTotal = 0, wbTotal = 0, inTransit = 0, total = 0;
        let ownMoney = 0, wbMoney = 0, transitMoney = 0, totalMoney = 0;
        let factoryTotal = 0, vehicleFormingTotal = 0, vehicleTransitTotal = 0;
        let factoryMoney = 0, vehicleFormingMoney = 0, vehicleTransitMoney = 0;
        let variantTotal = 0, variantMoney = 0;
        for (const row of filtered) {
            let multiplier = 0;
            if (mode === 'cost') multiplier = row.avg_cost || 0;
            else if (mode === 'revenue') multiplier = row.avg_price || 0;
            else if (mode === 'profit') multiplier = row.avg_profit || 0;
            // Factory uses cost_factory_unit for cost mode
            const factoryMultiplier = mode === 'cost'
                ? (row.cost_factory_unit || row.avg_cost || 0)
                : multiplier;
            ownTotal += row.total_own || 0;
            wbTotal += row.total_wb || 0;
            inTransit += row.in_transit || 0;
            total += row.total || 0;
            factoryTotal += row.factory_qty || 0;
            vehicleFormingTotal += row.vehicle_forming_qty || 0;
            vehicleTransitTotal += row.vehicle_transit_qty || 0;
            variantTotal += getVariantTotal(row);
            if (mode !== 'qty') {
                ownMoney += (row.total_own || 0) * multiplier;
                wbMoney += (row.total_wb || 0) * multiplier;
                transitMoney += (row.in_transit || 0) * multiplier;
                totalMoney += (row.total || 0) * multiplier;
                factoryMoney += (row.factory_qty || 0) * factoryMultiplier;
                vehicleFormingMoney += (row.vehicle_forming_qty || 0) * multiplier;
                vehicleTransitMoney += (row.vehicle_transit_qty || 0) * multiplier;
                // For variant=3 итого, factory uses estimated cost
                let rowVariantMoney = 0;
                if (variant === 1) {
                    rowVariantMoney = (row.total_wb || 0) * multiplier;
                } else if (variant === 2) {
                    rowVariantMoney = ((row.total_own || 0) + (row.total_wb || 0) + (row.in_transit || 0)) * multiplier;
                } else {
                    rowVariantMoney = ((row.total_own || 0) + (row.total_wb || 0) + (row.in_transit || 0)
                        + (row.vehicle_forming_qty || 0) + (row.vehicle_transit_qty || 0)) * multiplier
                        + (row.factory_qty || 0) * factoryMultiplier;
                }
                variantMoney += rowVariantMoney;
            }
        }
        return {
            ownTotal, wbTotal, inTransit, total,
            ownMoney, wbMoney, transitMoney, totalMoney,
            factoryTotal, vehicleFormingTotal, vehicleTransitTotal,
            factoryMoney, vehicleFormingMoney, vehicleTransitMoney,
            variantTotal, variantMoney,
        };
    }, [filtered, mode, variant, getVariantTotal]);

    // Multiplier: qty=1, cost=avg_cost, revenue=qty*avg_price, profit=qty*avg_profit
    /** Get numeric sort value for a cell (used by TanStack sorting) */
    const getSortVal = useCallback((qty: number, row: UnifiedStockRow): number => {
        if (qty <= 0) return 0;
        if (mode === 'qty') return qty;
        if (mode === 'cost') return qty * (row.avg_cost || 0);
        if (mode === 'revenue') return qty * (row.avg_price || 0);
        if (mode === 'profit') return qty * (row.avg_profit || 0);
        return qty;
    }, [mode]);

    const fmtVal = useCallback((qty: number, row: UnifiedStockRow) => {
        if (qty <= 0) return '\u2014';
        if (mode === 'qty') return formatNumber(qty, 0);
        if (mode === 'cost') {
            const cost = row.avg_cost || 0;
            if (cost <= 0) return <span style={{ color: 'var(--color-text-dim)' }}>{formatNumber(qty, 0)}</span>;
            const est = row.is_cost_estimated ? '\u2248\u00A0' : '';
            const color = row.is_cost_estimated ? 'var(--color-text-muted)' : undefined;
            return <span style={{ color, whiteSpace: 'nowrap' }} title={row.is_cost_estimated ? 'Оценка по средней наценке категории' : undefined}>{est}{formatNumber(qty * cost)}{'\u00A0\u20BD'}</span>;
        }
        if (mode === 'revenue') {
            const price = row.avg_price || 0;
            if (price <= 0) return <span style={{ color: 'var(--color-text-dim)' }}>{formatNumber(qty, 0)}</span>;
            return <span style={{ whiteSpace: 'nowrap' }}>{formatNumber(qty * price)}{'\u00A0\u20BD'}</span>;
        }
        if (mode === 'profit') {
            const prof = row.avg_profit || 0;
            if (!prof) return <span style={{ color: 'var(--color-text-dim)' }}>{formatNumber(qty, 0)}</span>;
            const val = qty * prof;
            const color = val >= 0 ? 'var(--color-success)' : 'var(--color-danger)';
            return <span style={{ color, whiteSpace: 'nowrap' }}>{formatNumber(val)}{'\u00A0\u20BD'}</span>;
        }
        return formatNumber(qty, 0);
    }, [mode]);

    const fmtGroupVal = useCallback((qty: number, avgCost: number, row?: UnifiedStockRow): string => {
        if (qty <= 0) return '\u2014';
        if (mode === 'qty') return formatNumber(qty, 0);
        if (mode === 'cost' && avgCost > 0) return formatNumber(qty * avgCost) + '\u00A0\u20BD';
        if (mode === 'revenue') {
            const price = row ? row.avg_price || 0 : 0;
            if (price <= 0) return formatNumber(qty, 0);
            return formatNumber(qty * price) + '\u00A0\u20BD';
        }
        if (mode === 'profit') {
            const prof = row ? row.avg_profit || 0 : 0;
            if (!prof) return formatNumber(qty, 0);
            return formatNumber(qty * prof) + '\u00A0\u20BD';
        }
        return formatNumber(qty, 0);
    }, [mode]);

    const cols: Column[] = useMemo(() => {
        const c: Column[] = [];

        if (isGrouped) {
            c.push(
                { key: 'group_name', label: 'Группа' },
                { key: 'items_count', label: 'Товаров', align: 'right' },
            );
            if (groupBy === 'abc') {
                c.push({
                    key: 'abc_class',
                    label: 'ABC',
                    render: (v: string) => {
                        const colors: Record<string, string> = { A: 'var(--color-success)', B: 'var(--color-warning)', C: 'var(--color-danger)' };
                        return <span className="badge" style={{ background: colors[v] || 'var(--color-text-muted)', color: '#fff' }}>{v}</span>;
                    },
                });
            }
        } else {
            c.push(
                {
                    key: 'article_seller',
                    label: 'Товар',
                    render: (_: unknown, row: UnifiedStockRow) => (
                        <div>
                            <div style={{ fontWeight: 500 }}>{row.article_seller || '\u2014'}</div>
                            {row.subject && (
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{row.subject}</div>
                            )}
                        </div>
                    ),
                },
                { key: 'barcode', label: 'ШК' },
            );
            if (groupBy === 'abc') {
                c.push({
                    key: 'abc_class',
                    label: 'ABC',
                    render: (v: string) => {
                        const colors: Record<string, string> = { A: 'var(--color-success)', B: 'var(--color-warning)', C: 'var(--color-danger)' };
                        return <span className="badge" style={{ background: colors[v] || 'var(--color-text-muted)', color: '#fff', padding: '2px 10px', borderRadius: 12, fontWeight: 600, fontSize: 12 }}>{v}</span>;
                    },
                });
            }
        }

        // Sales metrics — moved to front (right after identity columns)
        c.push({
            key: 'trend_daily',
            label: 'Тренд шт/д',
            align: 'right',
            sortable: true,
            getValue: (row: UnifiedStockRow) => getTrendData(row).avg_daily_qty,
            render: (_: unknown, row: UnifiedStockRow) => {
                const v = getTrendData(row).avg_daily_qty;
                if (v <= 0) return '\u2014';
                return formatNumber(v, 1);
            },
        });
        c.push({
            key: 'bdr_revenue',
            label: 'Реализ. БДР',
            align: 'right',
            sortable: true,
            getValue: (row: UnifiedStockRow) => getTrendData(row).revenue,
            render: (_: unknown, row: UnifiedStockRow) => {
                const v = getTrendData(row).revenue;
                if (v <= 0) return '\u2014';
                const est = row.is_revenue_estimated;
                const prefix = est ? '\u2248\u00A0' : '';
                const color = est ? 'var(--color-text-muted)' : undefined;
                return <span style={{ color, whiteSpace: 'nowrap' }} title={est ? 'Оценка по средней реализации категории' : undefined}>{prefix}{formatNumber(v)}{'\u00A0\u20BD'}</span>;
            },
        });
        c.push({
            key: 'margin_pct',
            label: 'Маржа %',
            align: 'right',
            sortable: true,
            getValue: (row: UnifiedStockRow) => {
                const t = getTrendData(row);
                return t.revenue > 0 ? (t.profit / t.revenue) * 100 : 0;
            },
            render: (_: unknown, row: UnifiedStockRow) => {
                const t = getTrendData(row);
                if (t.revenue <= 0) return '\u2014';
                const margin = (t.profit / t.revenue) * 100;
                const color = margin >= 0 ? 'var(--color-success)' : 'var(--color-danger)';
                const est = row.is_revenue_estimated;
                const prefix = est ? '\u2248\u00A0' : '';
                return <span style={{ color, fontWeight: 600, opacity: est ? 0.7 : 1 }} title={est ? 'Оценка по средней марже категории' : undefined}>{prefix}{formatNumber(margin, 1)}%</span>;
            },
        });
        c.push({
            key: 'stock_days',
            label: 'Запас дн',
            align: 'right',
            sortable: true,
            getValue: (row: UnifiedStockRow) => {
                const daily = getTrendData(row).avg_daily_qty;
                return daily > 0 ? getVariantTotal(row) / daily : 9999;
            },
            render: (_: unknown, row: UnifiedStockRow) => {
                const daily = getTrendData(row).avg_daily_qty;
                if (daily <= 0) return '\u2014';
                const days = getVariantTotal(row) / daily;
                const color = days < 14 ? 'var(--color-danger)' : days < 30 ? 'var(--color-warning)' : 'var(--color-text)';
                return <span style={{ color, fontWeight: 600 }}>{formatNumber(days, 0)}</span>;
            },
        });

        c.push({
            key: 'total',
            label: 'Итого',
            align: 'right',
            sortable: true,
            getValue: (row: UnifiedStockRow) => getSortVal(getVariantTotal(row), row),
            render: (_: unknown, row: UnifiedStockRow) => {
                const v = getVariantTotal(row);
                if (v <= 0) return <strong>{'\u2014'}</strong>;
                const val = fmtVal(v, row);
                if (typeof val === 'string') {
                    return <strong style={{ color: 'var(--color-accent)' }}>{val}</strong>;
                }
                return <strong>{val}</strong>;
            },
        });

        // Own warehouse columns (only for variant 2/3)
        if (showOwn) {
            for (const wh of ownWarehouses) {
                c.push({
                    key: `own_${wh}`,
                    label: `${wh}`,
                    align: 'right',
                    getValue: (row: UnifiedStockRow) => getSortVal(row.warehouses[wh] || 0, row),
                    render: (_: unknown, row: UnifiedStockRow) => fmtVal(row.warehouses[wh] || 0, row),
                });
            }
        }

        // WB stocks — always shown
        c.push({
            key: 'total_wb',
            label: 'WB склады',
            align: 'right',
            getValue: (row: UnifiedStockRow) => getSortVal(row.total_wb || 0, row),
            render: (_: unknown, row: UnifiedStockRow) => {
                const v = row.total_wb || 0;
                if (v <= 0) return <span style={{ color: 'var(--color-text-muted)' }}>{'\u2014'}</span>;
                const isOpen = expanded.has(row.nomenclature_id);
                return (
                    <div>
                        <span
                            onClick={(e) => { e.stopPropagation(); toggleExpand(row.nomenclature_id); }}
                            style={{ color: 'var(--color-accent)', fontWeight: 500, cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}
                        >
                            {fmtVal(v, row)} {isOpen ? '\u25BE' : '\u25B8'}
                        </span>
                        {isOpen && <WbDetailRow row={row} wbWarehouses={wbWarehouses} mode={mode} />}
                    </div>
                );
            },
        });

        // В пути на WB — always shown (assembly requests SHIPPED to WB)
        c.push({
            key: 'in_transit',
            label: 'В пути',
            align: 'right',
            getValue: (row: UnifiedStockRow) => getSortVal(row.in_transit || 0, row),
            render: (_: unknown, row: UnifiedStockRow) => fmtVal(row.in_transit || 0, row),
        });

        // Supply chain columns (only for variant 3)
        if (showFactory) {
            c.push({
                key: 'factory_qty',
                label: 'На фабрике',
                align: 'right',
                sortable: true,
                getValue: (row: UnifiedStockRow) => {
                    const fq = row.factory_qty || 0;
                    if (fq <= 0) return 0;
                    if (mode === 'cost') {
                        const c = row.cost_factory_unit || row.avg_cost || 0;
                        return fq * c;
                    }
                    return getSortVal(fq, row);
                },
                render: (_: unknown, row: UnifiedStockRow) => {
                    const fq = row.factory_qty || 0;
                    if (fq <= 0) return '\u2014';
                    if (mode === 'cost') {
                        const c = row.cost_factory_unit || row.avg_cost || 0;
                        if (c <= 0) return <span style={{ color: 'var(--color-text-dim)' }}>{formatNumber(fq, 0)}</span>;
                        return <span style={{ color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }} title="Оценка по средней наценке категории (только цена закупа × курс × коэф.)">{'\u2248\u00A0'}{formatNumber(fq * c)}{'\u00A0\u20BD'}</span>;
                    }
                    return fmtVal(fq, row);
                },
            });
        }
        if (showVehicles) {
            c.push({
                key: 'vehicle_forming_qty',
                label: 'Маш. (форм.)',
                align: 'right',
                sortable: true,
                getValue: (row: UnifiedStockRow) => getSortVal(row.vehicle_forming_qty || 0, row),
                render: (_: unknown, row: UnifiedStockRow) => fmtVal(row.vehicle_forming_qty || 0, row),
            });
            c.push({
                key: 'vehicle_transit_qty',
                label: 'Маш. (в пути)',
                align: 'right',
                sortable: true,
                getValue: (row: UnifiedStockRow) => getSortVal(row.vehicle_transit_qty || 0, row),
                render: (_: unknown, row: UnifiedStockRow) => fmtVal(row.vehicle_transit_qty || 0, row),
            });
        }

        return c;
    }, [ownWarehouses, wbWarehouses, expanded, mode, fmtVal, getSortVal, isGrouped, groupBy, getTrendData, getVariantTotal, showOwn, showFactory, showVehicles]);

    // Helper: factory cell content (always ≈ in cost mode, using cost_factory_unit)
    const fmtFactoryCell = useCallback((fq: number, row: UnifiedStockRow): React.ReactNode => {
        if (fq <= 0) return '\u2014';
        if (mode === 'qty') return formatNumber(fq, 0);
        if (mode === 'cost') {
            const c = row.cost_factory_unit || row.avg_cost || 0;
            if (c <= 0) return formatNumber(fq, 0);
            return <span style={{ color: 'var(--color-text-muted)' }} title="Оценка по средней наценке категории (только цена закупа × курс × коэф.)">{'\u2248\u00A0'}{formatNumber(fq * c)}{'\u00A0\u20BD'}</span>;
        }
        if (mode === 'revenue') {
            const p = row.avg_price || 0;
            if (p <= 0) return formatNumber(fq, 0);
            return <span>{formatNumber(fq * p)}{'\u00A0\u20BD'}</span>;
        }
        if (mode === 'profit') {
            const p = row.avg_profit || 0;
            if (!p) return formatNumber(fq, 0);
            return <span>{formatNumber(fq * p)}{'\u00A0\u20BD'}</span>;
        }
        return formatNumber(fq, 0);
    }, [mode]);

    // Render a single article row for the grouped table
    const renderArticleRow = (row: UnifiedStockRow, indent: number) => {
        const cost = row.avg_cost || 0;
        const trend = getTrendData(row);
        const variantTotal = getVariantTotal(row);
        const margin = trend.revenue > 0 ? (trend.profit / trend.revenue) * 100 : 0;
        const stockDays = trend.avg_daily_qty > 0 ? variantTotal / trend.avg_daily_qty : 0;
        const stockDaysColor = stockDays > 0
            ? stockDays < 14 ? 'var(--color-danger)' : stockDays < 30 ? 'var(--color-warning)' : 'var(--color-text)'
            : 'var(--color-text-muted)';

        return (
            <tr key={row.barcode || `${row.article_seller}-${row.nomenclature_id}`}>
                <td style={{ paddingLeft: indent }}>
                    <span style={{ fontSize: 13 }}>{row.article_seller || '\u2014'}</span>
                    <span style={{ color: 'var(--color-text-muted)', marginLeft: 8, fontSize: 12 }}>
                        {row.barcode}
                    </span>
                </td>
                <td style={{ textAlign: 'right' }}>{'\u2014'}</td>
                {groupBy === 'abc' && (
                    <td>
                        {row.abc_class && (
                            <span className="badge" style={{
                                background: ({ A: 'var(--color-success)', B: 'var(--color-warning)', C: 'var(--color-danger)' } as Record<string, string>)[row.abc_class] || 'var(--color-text-muted)',
                                color: '#fff',
                            }}>{row.abc_class}</span>
                        )}
                    </td>
                )}
                {/* Sales metrics — moved to front */}
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {trend.avg_daily_qty > 0 ? formatNumber(trend.avg_daily_qty, 1) : '\u2014'}
                </td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {trend.revenue > 0 ? formatNumber(trend.revenue) + '\u00A0\u20BD' : '\u2014'}
                </td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {trend.revenue > 0 ? (
                        <span style={{ color: margin >= 0 ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>
                            {formatNumber(margin, 1)}%
                        </span>
                    ) : '\u2014'}
                </td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {stockDays > 0 ? (
                        <span style={{ color: stockDaysColor, fontWeight: 600 }}>{formatNumber(stockDays, 0)}</span>
                    ) : '\u2014'}
                </td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <strong style={{ color: 'var(--color-accent)' }}>
                        {fmtGroupVal(variantTotal, cost, row)}
                    </strong>
                </td>
                {showOwn && (
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(row.total_own || 0, cost, row)}</td>
                )}
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(row.total_wb || 0, cost, row)}</td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(row.in_transit || 0, cost, row)}</td>
                {showFactory && (
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtFactoryCell(row.factory_qty || 0, row)}</td>
                )}
                {showVehicles && (
                    <>
                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(row.vehicle_forming_qty || 0, cost, row)}</td>
                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(row.vehicle_transit_qty || 0, cost, row)}</td>
                    </>
                )}
            </tr>
        );
    };

    // Render the grouped expandable table
    const renderGroupedTable = () => {
        return (
            <div className="glass-card" style={{ overflow: 'auto' }}>
                <table className="data-table" style={{ width: '100%' }}>
                    <thead>
                        <tr>
                            <th>Группа</th>
                            <th style={{ textAlign: 'right' }}>Товаров</th>
                            {groupBy === 'abc' && <th>ABC</th>}
                            <th style={{ textAlign: 'right' }}>Тренд шт/д</th>
                            <th style={{ textAlign: 'right' }}>Реализ.</th>
                            <th style={{ textAlign: 'right' }}>Маржа %</th>
                            <th style={{ textAlign: 'right' }}>Запас дн</th>
                            <th style={{ textAlign: 'right' }}>Итого</th>
                            {showOwn && <th style={{ textAlign: 'right' }}>Свои</th>}
                            <th style={{ textAlign: 'right' }}>WB</th>
                            <th style={{ textAlign: 'right' }}>В пути</th>
                            {showFactory && <th style={{ textAlign: 'right' }}>На фабрике</th>}
                            {showVehicles && <th style={{ textAlign: 'right' }}>Маш. (форм.)</th>}
                            {showVehicles && <th style={{ textAlign: 'right' }}>Маш. (в пути)</th>}
                        </tr>
                    </thead>
                    <tbody>
                        {/* Итого — первая строка */}
                        <tr style={{ background: 'var(--color-bg)', fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>
                            <td>Итого</td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(filtered.reduce((s, g) => s + (g.items_count || 0), 0), 0)}</td>
                            {groupBy === 'abc' && <td />}
                            <td style={{ textAlign: 'right' }}>{'\u2014'}</td>
                            <td style={{ textAlign: 'right' }}>{'\u2014'}</td>
                            <td style={{ textAlign: 'right' }}>{'\u2014'}</td>
                            <td style={{ textAlign: 'right' }}>{'\u2014'}</td>
                            <td style={{ textAlign: 'right', color: 'var(--color-accent)', whiteSpace: 'nowrap' }}>
                                {mode === 'qty' ? formatNumber(totals.variantTotal, 0) : formatNumber(totals.variantMoney) + '\u00A0\u20BD'}
                            </td>
                            {showOwn && (
                                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                    {mode === 'qty' ? formatNumber(totals.ownTotal, 0) : formatNumber(totals.ownMoney) + '\u00A0\u20BD'}
                                </td>
                            )}
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                {mode === 'qty' ? formatNumber(totals.wbTotal, 0) : formatNumber(totals.wbMoney) + '\u00A0\u20BD'}
                            </td>
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                {mode === 'qty' ? formatNumber(totals.inTransit, 0) : formatNumber(totals.transitMoney) + '\u00A0\u20BD'}
                            </td>
                            {showFactory && (
                                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                    {mode === 'qty' ? formatNumber(totals.factoryTotal, 0) : formatNumber(totals.factoryMoney) + '\u00A0\u20BD'}
                                </td>
                            )}
                            {showVehicles && (
                                <>
                                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                        {mode === 'qty' ? formatNumber(totals.vehicleFormingTotal, 0) : formatNumber(totals.vehicleFormingMoney) + '\u00A0\u20BD'}
                                    </td>
                                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                        {mode === 'qty' ? formatNumber(totals.vehicleTransitTotal, 0) : formatNumber(totals.vehicleTransitMoney) + '\u00A0\u20BD'}
                                    </td>
                                </>
                            )}
                        </tr>
                        {filtered.map(group => {
                            const groupKey = group.group_name || '';
                            const isExp = expandedGroups.has(groupKey);
                            const children = group.children || [];
                            const cost = group.avg_cost || 0;
                            const groupTrend = getTrendData(group as UnifiedStockRow);
                            const groupVariantTotal = getVariantTotal(group as UnifiedStockRow);
                            const groupMargin = groupTrend.revenue > 0 ? (groupTrend.profit / groupTrend.revenue) * 100 : 0;
                            const groupStockDays = groupTrend.avg_daily_qty > 0 ? groupVariantTotal / groupTrend.avg_daily_qty : 0;
                            const groupStockColor = groupStockDays > 0
                                ? groupStockDays < 14 ? 'var(--color-danger)' : groupStockDays < 30 ? 'var(--color-warning)' : 'var(--color-text)'
                                : 'var(--color-text-muted)';

                            return (
                                <React.Fragment key={groupKey}>
                                    {/* Group header row */}
                                    <tr
                                        onClick={() => toggleGroup(groupKey)}
                                        style={{ cursor: 'pointer', background: 'var(--color-bg-hover)' }}
                                    >
                                        <td style={{ fontWeight: 600 }}>
                                            <span style={{ marginRight: 8, fontSize: 12 }}>{isExp ? '\u25BC' : '\u25B6'}</span>
                                            {groupKey}
                                            {group.items_count != null && (
                                                <span style={{ color: 'var(--color-text-muted)', fontWeight: 400, marginLeft: 8, fontSize: 13 }}>
                                                    ({group.items_count})
                                                </span>
                                            )}
                                        </td>
                                        <td style={{ textAlign: 'right' }}>{group.items_count != null ? formatNumber(group.items_count, 0) : '\u2014'}</td>
                                        {groupBy === 'abc' && (
                                            <td>
                                                {group.abc_class && (
                                                    <span className="badge" style={{
                                                        background: ({ A: 'var(--color-success)', B: 'var(--color-warning)', C: 'var(--color-danger)' } as Record<string, string>)[group.abc_class] || 'var(--color-text-muted)',
                                                        color: '#fff',
                                                    }}>{group.abc_class}</span>
                                                )}
                                            </td>
                                        )}
                                        {/* Sales metrics — moved to front */}
                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                            {groupTrend.avg_daily_qty > 0 ? formatNumber(groupTrend.avg_daily_qty, 1) : '\u2014'}
                                        </td>
                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                            {groupTrend.revenue > 0 ? formatNumber(groupTrend.revenue) + '\u00A0\u20BD' : '\u2014'}
                                        </td>
                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                            {groupTrend.revenue > 0 ? (
                                                <span style={{ color: groupMargin >= 0 ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>
                                                    {formatNumber(groupMargin, 1)}%
                                                </span>
                                            ) : '\u2014'}
                                        </td>
                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                            {groupStockDays > 0 ? (
                                                <span style={{ color: groupStockColor, fontWeight: 600 }}>{formatNumber(groupStockDays, 0)}</span>
                                            ) : '\u2014'}
                                        </td>
                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                            <strong style={{ color: 'var(--color-accent)' }}>
                                                {fmtGroupVal(groupVariantTotal, cost, group as UnifiedStockRow)}
                                            </strong>
                                        </td>
                                        {showOwn && (
                                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(group.total_own || 0, cost, group as UnifiedStockRow)}</td>
                                        )}
                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(group.total_wb || 0, cost, group as UnifiedStockRow)}</td>
                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(group.in_transit || 0, cost, group as UnifiedStockRow)}</td>
                                        {showFactory && (
                                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtFactoryCell(group.factory_qty || 0, group as UnifiedStockRow)}</td>
                                        )}
                                        {showVehicles && (
                                            <>
                                                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(group.vehicle_forming_qty || 0, cost, group as UnifiedStockRow)}</td>
                                                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(group.vehicle_transit_qty || 0, cost, group as UnifiedStockRow)}</td>
                                            </>
                                        )}
                                    </tr>

                                    {/* Expanded children */}
                                    {isExp && children.map(child => {
                                        const hasSubChildren = child.children && child.children.length > 0;
                                        const childKey = `${groupKey}:${child.group_name || child.barcode}`;
                                        const subExp = expandedSubGroups.has(childKey);
                                        const childCost = child.avg_cost || 0;
                                        const childTrend = getTrendData(child);
                                        const childVariantTotal = getVariantTotal(child);
                                        const childMargin = childTrend.revenue > 0 ? (childTrend.profit / childTrend.revenue) * 100 : 0;
                                        const childStockDays = childTrend.avg_daily_qty > 0 ? childVariantTotal / childTrend.avg_daily_qty : 0;
                                        const childStockColor = childStockDays > 0
                                            ? childStockDays < 14 ? 'var(--color-danger)' : childStockDays < 30 ? 'var(--color-warning)' : 'var(--color-text)'
                                            : 'var(--color-text-muted)';

                                        if (hasSubChildren) {
                                            // Sub-group row (e.g., subject within brand)
                                            return (
                                                <React.Fragment key={childKey}>
                                                    <tr
                                                        onClick={() => toggleSubGroup(childKey)}
                                                        style={{ cursor: 'pointer' }}
                                                    >
                                                        <td style={{ paddingLeft: 32, fontWeight: 500 }}>
                                                            <span style={{ marginRight: 8, fontSize: 11 }}>{subExp ? '\u25BC' : '\u25B6'}</span>
                                                            {child.group_name || child.article_seller || '\u2014'}
                                                            {child.items_count != null && (
                                                                <span style={{ color: 'var(--color-text-muted)', fontWeight: 400, marginLeft: 8, fontSize: 12 }}>
                                                                    ({child.items_count})
                                                                </span>
                                                            )}
                                                        </td>
                                                        <td style={{ textAlign: 'right' }}>{child.items_count != null ? formatNumber(child.items_count, 0) : '\u2014'}</td>
                                                        {groupBy === 'abc' && <td>{'\u2014'}</td>}
                                                        {/* Sales metrics — moved to front */}
                                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                            {childTrend.avg_daily_qty > 0 ? formatNumber(childTrend.avg_daily_qty, 1) : '\u2014'}
                                                        </td>
                                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                            {childTrend.revenue > 0 ? formatNumber(childTrend.revenue) + '\u00A0\u20BD' : '\u2014'}
                                                        </td>
                                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                            {childTrend.revenue > 0 ? (
                                                                <span style={{ color: childMargin >= 0 ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>
                                                                    {formatNumber(childMargin, 1)}%
                                                                </span>
                                                            ) : '\u2014'}
                                                        </td>
                                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                            {childStockDays > 0 ? (
                                                                <span style={{ color: childStockColor, fontWeight: 600 }}>{formatNumber(childStockDays, 0)}</span>
                                                            ) : '\u2014'}
                                                        </td>
                                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                            <strong style={{ color: 'var(--color-accent)' }}>
                                                                {fmtGroupVal(childVariantTotal, childCost, child as UnifiedStockRow)}
                                                            </strong>
                                                        </td>
                                                        {showOwn && (
                                                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(child.total_own || 0, childCost, child as UnifiedStockRow)}</td>
                                                        )}
                                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(child.total_wb || 0, childCost, child as UnifiedStockRow)}</td>
                                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(child.in_transit || 0, childCost, child as UnifiedStockRow)}</td>
                                                        {showFactory && (
                                                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtFactoryCell(child.factory_qty || 0, child as UnifiedStockRow)}</td>
                                                        )}
                                                        {showVehicles && (
                                                            <>
                                                                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(child.vehicle_forming_qty || 0, childCost, child as UnifiedStockRow)}</td>
                                                                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(child.vehicle_transit_qty || 0, childCost, child as UnifiedStockRow)}</td>
                                                            </>
                                                        )}
                                                    </tr>

                                                    {/* Level 2 articles */}
                                                    {subExp && child.children!.map(article => renderArticleRow(article, 64))}
                                                </React.Fragment>
                                            );
                                        }

                                        // Direct article child (no sub-children)
                                        return renderArticleRow(child, 32);
                                    })}
                                </React.Fragment>
                            );
                        })}
                    </tbody>
                </table>
                <div style={{ padding: '12px 16px', fontSize: 13, color: 'var(--color-text-muted)' }}>
                    {filtered.length} групп
                </div>
            </div>
        );
    };

    return (
        <>
            {/* Summary */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16, display: 'flex', gap: 24, flexWrap: 'wrap', alignItems: 'center' }}>
                {mode === 'qty' ? (
                    <>
                        <span style={{ fontSize: 14, fontWeight: 500 }}>Свои: {formatNumber(totals.ownTotal)}</span>
                        <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--color-accent)' }}>WB: {formatNumber(totals.wbTotal)}</span>
                        {totals.inTransit > 0 && <span style={{ fontSize: 14, fontWeight: 500 }}>В пути: {formatNumber(totals.inTransit)}</span>}
                        {variant === 3 && totals.factoryTotal > 0 && (
                            <span style={{ fontSize: 14, fontWeight: 500 }}>На фабрике: {formatNumber(totals.factoryTotal)}</span>
                        )}
                        {variant === 3 && (totals.vehicleFormingTotal + totals.vehicleTransitTotal) > 0 && (
                            <span style={{ fontSize: 14, fontWeight: 500 }}>В машинах: {formatNumber(totals.vehicleFormingTotal + totals.vehicleTransitTotal)}</span>
                        )}
                        <span style={{ fontSize: 14, fontWeight: 700 }}>Итого: {formatNumber(totals.variantTotal)} шт</span>
                    </>
                ) : (
                    <>
                        <span style={{ fontSize: 14, fontWeight: 500 }}>Свои: {formatNumber(totals.ownMoney)} {'\u20BD'}</span>
                        <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--color-accent)' }}>WB: {formatNumber(totals.wbMoney)} {'\u20BD'}</span>
                        {totals.transitMoney > 0 && <span style={{ fontSize: 14, fontWeight: 500 }}>В пути: {formatNumber(totals.transitMoney)} {'\u20BD'}</span>}
                        {variant === 3 && totals.factoryMoney > 0 && (
                            <span style={{ fontSize: 14, fontWeight: 500 }}>На фабрике: {formatNumber(totals.factoryMoney)} {'\u20BD'}</span>
                        )}
                        {variant === 3 && (totals.vehicleFormingMoney + totals.vehicleTransitMoney) > 0 && (
                            <span style={{ fontSize: 14, fontWeight: 500 }}>В машинах: {formatNumber(totals.vehicleFormingMoney + totals.vehicleTransitMoney)} {'\u20BD'}</span>
                        )}
                        <span style={{ fontSize: 14, fontWeight: 700 }}>Итого: {formatNumber(totals.variantMoney)} {'\u20BD'}</span>
                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                            ({mode === 'cost' ? 'себестоимость' : mode === 'revenue' ? 'выручка при продаже' : 'прибыль при продаже'})
                        </span>
                    </>
                )}
            </div>

            {/* Search + Grouping + Mode toggle + Refresh */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
                <input
                    className="form-input"
                    placeholder="Поиск по баркоду / артикулу..."
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    style={{ maxWidth: 300 }}
                />
                <select
                    className="form-input"
                    value={groupBy}
                    onChange={e => onGroupChange(e.target.value)}
                    style={{ maxWidth: 200 }}
                >
                    <option value="sku">По товарам</option>
                    <option value="brand">По брендам</option>
                    <option value="subject">По категориям</option>
                    <option value="imt">По склейкам</option>
                    <option value="tag">По якорям</option>
                    <option value="abc">ABC анализ</option>
                </select>
                {/* Variant toggle */}
                <div style={{ display: 'flex', border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden' }}>
                    {([
                        { key: 1 as const, label: 'WB', title: 'Только остатки WB' },
                        { key: 2 as const, label: 'WB+Свои', title: 'WB + Свои склады + В пути на WB' },
                        { key: 3 as const, label: 'Всё', title: 'Всё включая фабрику и машины' },
                    ]).map((btn, i) => (
                        <button
                            key={btn.key}
                            onClick={() => setVariant(btn.key)}
                            title={btn.title}
                            style={{
                                padding: '6px 12px', fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer',
                                borderLeft: i > 0 ? '1px solid var(--color-border)' : 'none',
                                background: variant === btn.key ? 'var(--color-accent)' : 'var(--color-bg-card)',
                                color: variant === btn.key ? '#fff' : 'var(--color-text)',
                            }}
                        >{btn.label}</button>
                    ))}
                </div>
                <div style={{ display: 'flex', border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden' }}>
                    {([
                        { key: 'qty', label: 'шт' },
                        { key: 'cost', label: 'Себест.' },
                        { key: 'revenue', label: 'Реализ.' },
                        { key: 'profit', label: 'Прибыль' },
                    ] as { key: typeof mode; label: string }[]).map((btn, i) => (
                        <button
                            key={btn.key}
                            onClick={() => setMode(btn.key)}
                            style={{
                                padding: '6px 12px', fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer',
                                borderLeft: i > 0 ? '1px solid var(--color-border)' : 'none',
                                background: mode === btn.key ? 'var(--color-accent)' : 'var(--color-bg-card)',
                                color: mode === btn.key ? '#fff' : 'var(--color-text)',
                            }}
                        >{btn.label}</button>
                    ))}
                </div>
                {/* Trend period toggle */}
                <div style={{ display: 'flex', border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden' }}>
                    {([7, 14, 30] as const).map((d, i) => (
                        <button
                            key={d}
                            onClick={() => setTrendPeriod(d)}
                            title={`Тренд продаж за ${d} дней`}
                            style={{
                                padding: '6px 10px', fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer',
                                borderLeft: i > 0 ? '1px solid var(--color-border)' : 'none',
                                background: trendPeriod === d ? 'var(--color-accent)' : 'var(--color-bg-card)',
                                color: trendPeriod === d ? '#fff' : 'var(--color-text)',
                            }}
                        >{d}д</button>
                    ))}
                </div>
                <button className="btn btn-secondary btn-sm" onClick={handleSync} disabled={syncing}>
                    {syncing ? '\u23F3 Обновление...' : '\uD83D\uDD04 Обновить WB остатки'}
                </button>
            </div>

            {filtered.length === 0 ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', opacity: 0.6 }}>
                    <div style={{ fontSize: 48 }}>{'\uD83D\uDCE6'}</div>
                    <div>Нет данных по единым остаткам</div>
                </div>
            ) : isGrouped ? (
                renderGroupedTable()
            ) : (
                <TanStackDataTable
                    columns={cols}
                    data={filtered}
                    exportName="unified_stock"
                    enableSorting
                    enablePagination
                    pageSize={50}
                    actions={
                        <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                            {filtered.length} позиций
                        </span>
                    }
                    summaryRow={
                        <tr style={{ background: 'var(--color-bg)', fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>
                            <td>Итого</td>
                            <td />
                            {groupBy === 'abc' && <td />}
                            {/* Sales metrics — moved to front */}
                            <td />
                            <td />
                            <td />
                            <td />
                            <td style={{ textAlign: 'right', color: 'var(--color-accent)', whiteSpace: 'nowrap' }}>
                                {mode === 'qty' ? formatNumber(totals.variantTotal, 0) : formatNumber(totals.variantMoney) + '\u00A0\u20BD'}
                            </td>
                            {showOwn && ownWarehouses.map(wh => {
                                const qty = filtered.reduce((s, r) => s + (r.warehouses[wh] || 0), 0);
                                const money = filtered.reduce((s, r) => {
                                    const q = r.warehouses[wh] || 0;
                                    const m = mode === 'cost' ? r.avg_cost : mode === 'revenue' ? r.avg_price : mode === 'profit' ? r.avg_profit : 0;
                                    return s + q * (m || 0);
                                }, 0);
                                return (
                                    <td key={wh} style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                        {mode === 'qty' ? formatNumber(qty, 0) : formatNumber(money) + '\u00A0\u20BD'}
                                    </td>
                                );
                            })}
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                {mode === 'qty' ? formatNumber(totals.wbTotal, 0) : formatNumber(totals.wbMoney) + '\u00A0\u20BD'}
                            </td>
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                {mode === 'qty' ? formatNumber(totals.inTransit, 0) : formatNumber(totals.transitMoney) + '\u00A0\u20BD'}
                            </td>
                            {showFactory && (
                                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                    {mode === 'qty' ? formatNumber(totals.factoryTotal, 0) : formatNumber(totals.factoryMoney) + '\u00A0\u20BD'}
                                </td>
                            )}
                            {showVehicles && (
                                <>
                                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                        {mode === 'qty' ? formatNumber(totals.vehicleFormingTotal, 0) : formatNumber(totals.vehicleFormingMoney) + '\u00A0\u20BD'}
                                    </td>
                                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                        {mode === 'qty' ? formatNumber(totals.vehicleTransitTotal, 0) : formatNumber(totals.vehicleTransitMoney) + '\u00A0\u20BD'}
                                    </td>
                                </>
                            )}
                        </tr>
                    }
                />
            )}
        </>
    );
}

// ─── Main page ───────────────────────────────────────────────────────────

export default function StockSummaryPage() {
    const [tab, setTab] = useState('summary');
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [summary, setSummary] = useState<StockSummaryRow[]>([]);
    const [unified, setUnified] = useState<UnifiedStockRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [unifiedGroupBy, setUnifiedGroupBy] = useState('sku');

    const handleGroupChange = useCallback(async (gb: string) => {
        setUnified([]);          // clear stale data BEFORE switching mode
        setUnifiedGroupBy(gb);
        try {
            const un = await api.getUnifiedStock(gb);
            setUnified(un);
        } catch { /* ignore */ }
    }, []);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [wh, sm, un] = await Promise.all([
                api.getWarehouses(),
                api.getStockSummary(),
                api.getUnifiedStock('sku'),
            ]);
            setWarehouses(wh);
            setSummary(sm);
            setUnified(un);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => { load(); }, [load]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Остатки</h1>
                    <p className="page-subtitle">Остатки по всем складам</p>
                </div>
            </div>

            <TabLayout
                tabs={[
                    { key: 'summary', label: 'Сводные остатки' },
                    { key: 'unified', label: 'Единые остатки' },
                ]}
                active={tab}
                onChange={setTab}
            />

            {tab === 'summary' && (
                <SummaryTab warehouses={warehouses} summary={summary} />
            )}
            {tab === 'unified' && (
                <UnifiedTab data={unified} onRefresh={load} groupBy={unifiedGroupBy} onGroupChange={handleGroupChange} />
            )}
        </div>
    );
}

'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import TabLayout from '@/components/TabLayout';
import type { Warehouse, StockSummaryRow, UnifiedStockRow } from '@/types/api';
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
    const cost = row.avg_cost || 0;
    const total = row.total || 1;
    return (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', padding: '8px 0' }}>
            {wbWarehouses.map(wh => {
                const v = row.wb_stocks[wh] || 0;
                if (v <= 0) return null;
                let display = formatNumber(v);
                if (mode === 'cost' && cost > 0) display = formatNumber(v * cost);
                else if (mode === 'revenue' && row.avg_daily_revenue) display = formatNumber(row.avg_daily_revenue * v / total);
                else if (mode === 'profit' && row.avg_daily_profit) display = formatNumber(row.avg_daily_profit * v / total);
                return (
                    <span key={wh} style={{ fontSize: 12, padding: '2px 8px', borderRadius: 6, background: 'rgba(175, 82, 222, 0.08)', color: '#7c3aed' }}>
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
    const isGrouped = groupBy !== 'sku' && groupBy !== 'abc';

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
        for (const row of filtered) {
            const t = row.total || 1;
            let multiplier = 0;
            if (mode === 'cost') multiplier = row.avg_cost || 0;
            else if (mode === 'revenue') multiplier = (row.avg_daily_revenue || 0) / t;
            else if (mode === 'profit') multiplier = (row.avg_daily_profit || 0) / t;
            ownTotal += row.total_own || 0;
            wbTotal += row.total_wb || 0;
            inTransit += row.in_transit || 0;
            total += row.total || 0;
            if (mode !== 'qty') {
                ownMoney += (row.total_own || 0) * multiplier;
                wbMoney += (row.total_wb || 0) * multiplier;
                transitMoney += (row.in_transit || 0) * multiplier;
                totalMoney += (row.total || 0) * multiplier;
            }
        }
        return { ownTotal, wbTotal, inTransit, total, ownMoney, wbMoney, transitMoney, totalMoney };
    }, [filtered, mode]);

    // Multiplier based on mode: qty=1, cost=avg_cost, revenue=avg_daily_revenue/total, profit=avg_daily_profit/total
    const fmtVal = useCallback((qty: number, row: UnifiedStockRow) => {
        if (qty <= 0) return '\u2014';
        if (mode === 'qty') return formatNumber(qty);
        if (mode === 'cost') {
            const cost = row.avg_cost || 0;
            if (cost <= 0) return <span style={{ color: 'var(--color-text-dim)' }}>{formatNumber(qty)}</span>;
            return formatNumber(qty * cost);
        }
        // revenue/profit: proportional to qty/total
        const total = row.total || 1;
        const share = qty / total;
        if (mode === 'revenue') {
            const rev = row.avg_daily_revenue || 0;
            return rev > 0 ? formatNumber(rev * share) : '\u2014';
        }
        if (mode === 'profit') {
            const prof = row.avg_daily_profit || 0;
            if (!prof) return '\u2014';
            return formatNumber(prof * share);
        }
        return formatNumber(qty);
    }, [mode]);

    const fmtGroupVal = useCallback((qty: number, avgCost: number, row?: UnifiedStockRow): string => {
        if (qty <= 0) return '\u2014';
        if (mode === 'qty') return formatNumber(qty);
        if (mode === 'cost' && avgCost > 0) return formatNumber(qty * avgCost);
        if (mode === 'revenue' && row) {
            const rev = row.avg_daily_revenue || 0;
            if (rev <= 0) return '\u2014';
            const total = row.total || 1;
            return formatNumber(rev * qty / total);
        }
        if (mode === 'profit' && row) {
            const prof = row.avg_daily_profit || 0;
            if (!prof) return '\u2014';
            const total = row.total || 1;
            return formatNumber(prof * qty / total);
        }
        return formatNumber(qty);
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

        c.push({
            key: 'total',
            label: 'Итого',
            align: 'right',
            sortable: true,
            render: (_: unknown, row: UnifiedStockRow) => {
                const v = row.total;
                if (v <= 0) return <strong>{'\u2014'}</strong>;
                const val = fmtVal(v, row);
                const suffix = mode !== 'qty' ? ' \u20BD' : '';
                return <strong style={{ color: 'var(--color-accent)' }}>{val}{typeof val === 'string' && val !== '\u2014' ? suffix : ''}</strong>;
            },
        });

        // Own warehouse columns
        for (const wh of ownWarehouses) {
            c.push({
                key: `own_${wh}`,
                label: `${wh}`,
                align: 'right',
                render: (_: unknown, row: UnifiedStockRow) => fmtVal(row.warehouses[wh] || 0, row),
            });
        }

        // WB stocks — single column with expand button
        c.push({
            key: 'total_wb',
            label: 'WB склады',
            align: 'right',
            render: (_: unknown, row: UnifiedStockRow) => {
                const v = row.total_wb || 0;
                if (v <= 0) return <span style={{ color: 'var(--color-text-muted)' }}>{'\u2014'}</span>;
                const isOpen = expanded.has(row.nomenclature_id);
                const displayNode = fmtVal(v, row);
                const display = typeof displayNode === 'string' ? displayNode : formatNumber(v);
                return (
                    <div>
                        <span
                            onClick={(e) => { e.stopPropagation(); toggleExpand(row.nomenclature_id); }}
                            style={{ color: 'var(--color-accent)', fontWeight: 500, cursor: 'pointer', userSelect: 'none' }}
                        >
                            {display} {isOpen ? '\u25BE' : '\u25B8'}
                        </span>
                        {isOpen && <WbDetailRow row={row} wbWarehouses={wbWarehouses} mode={mode} />}
                    </div>
                );
            },
        });

        c.push({
            key: 'in_transit',
            label: 'В пути',
            align: 'right',
            render: (_: unknown, row: UnifiedStockRow) => fmtVal(row.in_transit || 0, row),
        });

        return c;
    }, [ownWarehouses, wbWarehouses, expanded, mode, fmtVal, isGrouped, groupBy]);

    // Render a single article row for the grouped table
    const renderArticleRow = (row: UnifiedStockRow, indent: number) => {
        const cost = row.avg_cost || 0;

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
                <td style={{ textAlign: 'right' }}>
                    <strong style={{ color: 'var(--color-accent)' }}>
                        {fmtGroupVal(row.total, cost, row)}
                    </strong>
                </td>
                <td style={{ textAlign: 'right' }}>{fmtGroupVal(row.total_own || 0, cost, row)}</td>
                <td style={{ textAlign: 'right' }}>{fmtGroupVal(row.total_wb || 0, cost, row)}</td>
                <td style={{ textAlign: 'right' }}>{fmtGroupVal(row.in_transit || 0, cost, row)}</td>
            </tr>
        );
    };

    // Render the grouped expandable table
    const renderGroupedTable = () => {
        const modeSuffix = mode !== 'qty' ? ' \u20BD' : '';
        return (
            <div className="glass-card" style={{ overflow: 'auto' }}>
                <table className="data-table" style={{ width: '100%' }}>
                    <thead>
                        <tr>
                            <th>Группа</th>
                            <th style={{ textAlign: 'right' }}>Товаров</th>
                            {groupBy === 'abc' && <th>ABC</th>}
                            <th style={{ textAlign: 'right' }}>Итого</th>
                            <th style={{ textAlign: 'right' }}>Свои</th>
                            <th style={{ textAlign: 'right' }}>WB</th>
                            <th style={{ textAlign: 'right' }}>В пути</th>
                        </tr>
                    </thead>
                    <tbody>
                        {filtered.map(group => {
                            const groupKey = group.group_name || '';
                            const isExp = expandedGroups.has(groupKey);
                            const children = group.children || [];
                            const cost = group.avg_cost || 0;
                            const profitColor = (group.avg_daily_profit || 0) > 0 ? 'var(--color-success)' : 'var(--color-danger)';

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
                                        <td style={{ textAlign: 'right' }}>{group.items_count != null ? formatNumber(group.items_count) : '\u2014'}</td>
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
                                        <td style={{ textAlign: 'right' }}>
                                            <strong style={{ color: 'var(--color-accent)' }}>
                                                {fmtGroupVal(group.total, cost, group as UnifiedStockRow)}{mode !== 'qty' && group.total > 0 ? modeSuffix : ''}
                                            </strong>
                                        </td>
                                        <td style={{ textAlign: 'right' }}>{fmtGroupVal(group.total_own || 0, cost, group as UnifiedStockRow)}</td>
                                        <td style={{ textAlign: 'right' }}>{fmtGroupVal(group.total_wb || 0, cost, group as UnifiedStockRow)}</td>
                                        <td style={{ textAlign: 'right' }}>{fmtGroupVal(group.in_transit || 0, cost, group as UnifiedStockRow)}</td>
                                    </tr>

                                    {/* Expanded children */}
                                    {isExp && children.map(child => {
                                        const hasSubChildren = child.children && child.children.length > 0;
                                        const childKey = `${groupKey}:${child.group_name || child.barcode}`;
                                        const subExp = expandedSubGroups.has(childKey);
                                        const childCost = child.avg_cost || 0;
                                        const childProfitColor = (child.avg_daily_profit || 0) > 0 ? 'var(--color-success)' : 'var(--color-danger)';

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
                                                        <td style={{ textAlign: 'right' }}>{child.items_count != null ? formatNumber(child.items_count) : '\u2014'}</td>
                                                        {groupBy === 'abc' && <td>{'\u2014'}</td>}
                                                        <td style={{ textAlign: 'right' }}>
                                                            <strong style={{ color: 'var(--color-accent)' }}>
                                                                {fmtGroupVal(child.total, childCost, child as UnifiedStockRow)}
                                                            </strong>
                                                        </td>
                                                        <td style={{ textAlign: 'right' }}>{fmtGroupVal(child.total_own || 0, childCost, child as UnifiedStockRow)}</td>
                                                        <td style={{ textAlign: 'right' }}>{fmtGroupVal(child.total_wb || 0, childCost, child as UnifiedStockRow)}</td>
                                                        <td style={{ textAlign: 'right' }}>{fmtGroupVal(child.in_transit || 0, childCost, child as UnifiedStockRow)}</td>
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
                        <span style={{ fontSize: 14, fontWeight: 700 }}>Итого: {formatNumber(totals.total)} шт</span>
                    </>
                ) : (
                    <>
                        <span style={{ fontSize: 14, fontWeight: 500 }}>Свои: {formatNumber(totals.ownMoney)} {'\u20BD'}</span>
                        <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--color-accent)' }}>WB: {formatNumber(totals.wbMoney)} {'\u20BD'}</span>
                        {totals.transitMoney > 0 && <span style={{ fontSize: 14, fontWeight: 500 }}>В пути: {formatNumber(totals.transitMoney)} {'\u20BD'}</span>}
                        <span style={{ fontSize: 14, fontWeight: 700 }}>Итого: {formatNumber(totals.totalMoney)} {'\u20BD'}</span>
                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                            ({mode === 'cost' ? 'себестоимость' : mode === 'revenue' ? 'реализация/день' : 'прибыль/день'})
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

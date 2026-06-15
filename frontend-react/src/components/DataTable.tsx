'use client';

import { useMemo, useState } from 'react';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';

/**
 * Column definition for DataTable.
 * - key: field name in row object
 * - label: column header text
 * - align: 'left' | 'right' | 'center'
 * - format: 'number' | 'date' | 'badge' | custom render
 * - render: custom render function (overrides format)
 * - width: optional CSS width
 */
export interface Column {
    key: string;
    label: string;
    align?: 'left' | 'right' | 'center';
    format?: 'number' | 'date' | 'badge' | 'money' | 'money-color';
    render?: (value: any, row: any, index: number) => React.ReactNode;
    /** Custom accessor for sorting when key doesn't match data shape */
    getValue?: (row: any) => any;
    /** Custom accessor for Excel export — overrides getValue/key. Use for cells with React render or nested data. */
    exportValue?: (row: any) => any;
    width?: string;
    sortable?: boolean;
}

interface DataTableProps {
    columns: Column[];
    data: any[];
    loading?: boolean;
    emptyIcon?: string;
    emptyText?: string;
    title?: string;
    /** Filename for Excel export (without .xlsx). If set, shows export button */
    exportName?: string;
    /** Extra buttons in the toolbar header */
    actions?: React.ReactNode;
    /** Max rows to display before pagination message */
    maxRows?: number;
    /** Callback when a row is clicked */
    onRowClick?: (row: any, index: number) => void;
    /** Currently selected row index (for highlighting) */
    selectedIndex?: number;
    /** Show auto-generated columns from data keys (ignores columns prop) */
    autoColumns?: boolean;
    /** Custom row className */
    rowClassName?: (row: any, index: number) => string;
    /** Max table height with scroll */
    maxHeight?: number;
}

function formatCell(value: any, format?: string): React.ReactNode {
    if (value == null) return '—';
    switch (format) {
        case 'number':
            return typeof value === 'number' ? formatNumber(value) : value;
        case 'date':
            return formatDate(value);
        case 'badge':
            return <span className="badge badge-info">{value}</span>;
        case 'money':
            return typeof value === 'number' ? formatNumber(value) + ' ₽' : value;
        case 'money-color':
            return (
                <span style={{
                    fontWeight: 600, fontFamily: 'monospace',
                    color: (value || 0) >= 0 ? 'var(--color-success)' : 'var(--color-danger)'
                }}>
                    {formatNumber(value)}
                </span>
            );
        default:
            return String(value);
    }
}

/**
 * DataTable — universal table component with:
 * - Column definitions with formatting
 * - Loading/empty states
 * - Excel export button
 * - Row click + highlight
 * - Auto-columns mode
 * - Max-rows pagination hint
 *
 * Usage:
 * ```tsx
 * <DataTable
 *   columns={[
 *     { key: 'date', label: 'Дата', format: 'date' },
 *     { key: 'amount', label: 'Сумма', format: 'money-color', align: 'right' },
 *   ]}
 *   data={transactions}
 *   loading={loading}
 *   exportName="transactions"
 *   title="Операции"
 * />
 * ```
 */
export default function DataTable({
    columns,
    data,
    loading = false,
    emptyIcon = '📋',
    emptyText = 'Нет данных',
    title,
    exportName,
    actions,
    maxRows = 500,
    onRowClick,
    selectedIndex,
    autoColumns = false,
    rowClassName,
    maxHeight,
}: DataTableProps) {
    const effectiveColumns: Column[] = autoColumns && data.length > 0
        ? Object.keys(data[0]).map(k => ({ key: k, label: k }))
        : columns;

    // Opt-in client-side sorting: active only for columns with `sortable: true`.
    const [sortKey, setSortKey] = useState<string | null>(null);
    const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');

    const sortCol = sortKey ? effectiveColumns.find(c => c.key === sortKey && c.sortable) : undefined;

    const sortedData = useMemo(() => {
        if (!sortCol) return data;
        const accessor = (row: any) => (sortCol.getValue ? sortCol.getValue(row) : row[sortCol.key]);
        const dir = sortDir === 'asc' ? 1 : -1;
        return [...data].sort((a, b) => {
            const av = accessor(a);
            const bv = accessor(b);
            // Nulls/undefined/'' always sort last regardless of direction.
            const aEmpty = av == null || av === '';
            const bEmpty = bv == null || bv === '';
            if (aEmpty && bEmpty) return 0;
            if (aEmpty) return 1;
            if (bEmpty) return -1;
            if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
            return String(av).localeCompare(String(bv), undefined, { numeric: true }) * dir;
        });
    }, [data, sortCol, sortDir]);

    const toggleSort = (key: string) => {
        if (sortKey === key) {
            setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
        } else {
            setSortKey(key);
            setSortDir('asc');
        }
    };

    const displayData = sortedData.slice(0, maxRows);

    return (
        <div className="glass-card">
            {/* Toolbar: title + actions + export */}
            {(title || exportName || actions) && (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    {title && <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>{title}{data.length > 0 ? ` (${data.length})` : ''}</h3>}
                    <div style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
                        {exportName && data.length > 0 && (
                            <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(sortedData, exportName, effectiveColumns)}>
                                📥 Excel
                            </button>
                        )}
                        {actions}
                    </div>
                </div>
            )}

            {/* Loading */}
            {loading ? (
                <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    <div className="spinner" style={{ margin: '0 auto 12px' }} />
                    Загрузка...
                </div>
            ) : data.length === 0 ? (
                /* Empty state */
                <div className="empty-state">
                    <div className="empty-state-icon">{emptyIcon}</div>
                    <div className="empty-state-text">{emptyText}</div>
                </div>
            ) : (
                /* Table */
                <div style={{ overflowX: 'auto', ...(maxHeight ? { maxHeight, overflowY: 'auto' } : {}) }}>
                    <table className="data-table">
                        <thead>
                            <tr>
                                {effectiveColumns.map(col => {
                                    const active = sortKey === col.key && !!col.sortable;
                                    return (
                                        <th key={col.key} style={{
                                            textAlign: col.align || 'left',
                                            width: col.width,
                                            fontSize: autoColumns ? 11 : undefined,
                                            cursor: col.sortable ? 'pointer' : undefined,
                                            userSelect: col.sortable ? 'none' : undefined,
                                            whiteSpace: col.sortable ? 'nowrap' : undefined,
                                        }}
                                        onClick={col.sortable ? () => toggleSort(col.key) : undefined}
                                        title={col.sortable ? 'Сортировать' : undefined}
                                        >
                                            {col.label}
                                            {col.sortable && (
                                                <span style={{ marginLeft: 4, opacity: active ? 1 : 0.35, fontSize: 11 }}>
                                                    {active ? (sortDir === 'asc' ? '▲' : '▼') : '⇅'}
                                                </span>
                                            )}
                                        </th>
                                    );
                                })}
                            </tr>
                        </thead>
                        <tbody>
                            {displayData.map((row, i) => (
                                <tr
                                    key={row.id ?? i}
                                    onClick={onRowClick ? () => onRowClick(row, i) : undefined}
                                    className={rowClassName ? rowClassName(row, i) : undefined}
                                    style={{
                                        cursor: onRowClick ? 'pointer' : undefined,
                                        background: selectedIndex === i ? 'rgba(139,92,246,0.1)' : undefined,
                                    }}
                                >
                                    {effectiveColumns.map(col => (
                                        <td key={col.key} style={{
                                            textAlign: col.align || 'left',
                                            fontSize: autoColumns ? 12 : undefined,
                                        }}>
                                            {col.render
                                                ? col.render(row[col.key], row, i)
                                                : formatCell(row[col.key], col.format)}
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    {data.length > maxRows && (
                        <div style={{ padding: 12, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>
                            Показано {maxRows} из {data.length}. Используйте фильтр для уточнения.
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

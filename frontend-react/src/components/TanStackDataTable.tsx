'use client';

import { useState, useMemo } from 'react';
import {
    useReactTable,
    getCoreRowModel,
    getSortedRowModel,
    getFilteredRowModel,
    getPaginationRowModel,
    flexRender,
    type ColumnDef,
    type SortingState,
    type ColumnFiltersState,
} from '@tanstack/react-table';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import type { Column } from './DataTable';

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

interface TanStackDataTableProps {
    columns: Column[];
    data: any[];
    loading?: boolean;
    emptyIcon?: string;
    emptyText?: string;
    title?: string;
    exportName?: string;
    actions?: React.ReactNode;
    onRowClick?: (row: any, index: number) => void;
    selectedIndex?: number;
    rowClassName?: (row: any, index: number) => string;
    maxHeight?: number;
    /** Enable column sorting (default: true) */
    enableSorting?: boolean;
    /** Enable column filters (default: false) */
    enableFiltering?: boolean;
    /** Enable pagination (default: true) */
    enablePagination?: boolean;
    /** Rows per page (default: 50) */
    pageSize?: number;
    /** Optional summary row rendered at the top of tbody (sticky) */
    summaryRow?: React.ReactNode;
}

/* ------------------------------------------------------------------ */
/*  Cell formatting (reused from DataTable)                            */
/* ------------------------------------------------------------------ */

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
                    color: (value || 0) >= 0 ? 'var(--color-success)' : 'var(--color-danger)',
                }}>
                    {formatNumber(value)}
                </span>
            );
        default:
            return String(value);
    }
}

/* ------------------------------------------------------------------ */
/*  Adapt Column[] → ColumnDef[]                                       */
/* ------------------------------------------------------------------ */

function adaptColumns(cols: Column[]): ColumnDef<any, any>[] {
    return cols.map((col) => {
        const def: ColumnDef<any, any> = {
            id: col.key,
            header: col.label,
            size: col.width ? parseInt(col.width) : undefined,
            enableSorting: col.sortable !== false,
            cell: (info: any) => {
                const value = info.getValue();
                const row = info.row.original;
                const index = info.row.index;
                if (col.render) return col.render(value, row, index);
                return formatCell(value, col.format);
            },
            meta: { align: col.align || 'left' },
        };
        if (col.getValue) {
            def.accessorFn = col.getValue;
        } else {
            (def as any).accessorKey = col.key;
        }
        return def;
    });
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function TanStackDataTable({
    columns,
    data,
    loading = false,
    emptyIcon = '📋',
    emptyText = 'Нет данных',
    title,
    exportName,
    actions,
    onRowClick,
    selectedIndex,
    rowClassName,
    maxHeight,
    enableSorting = true,
    enableFiltering = false,
    enablePagination = true,
    pageSize = 50,
    summaryRow,
}: TanStackDataTableProps) {
    const [sorting, setSorting] = useState<SortingState>([]);
    const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);

    const tanstackColumns = useMemo(() => adaptColumns(columns), [columns]);

    const table = useReactTable({
        data,
        columns: tanstackColumns,
        state: { sorting, columnFilters },
        onSortingChange: setSorting,
        onColumnFiltersChange: setColumnFilters,
        getCoreRowModel: getCoreRowModel(),
        ...(enableSorting ? { getSortedRowModel: getSortedRowModel() } : {}),
        ...(enableFiltering ? { getFilteredRowModel: getFilteredRowModel() } : {}),
        ...(enablePagination ? { getPaginationRowModel: getPaginationRowModel() } : {}),
        initialState: {
            pagination: { pageSize },
        },
    });

    const totalRows = enableFiltering ? table.getFilteredRowModel().rows.length : data.length;

    return (
        <div className="glass-card">
            {/* Toolbar */}
            {(title || exportName || actions) && (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    {title && (
                        <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>
                            {title}{data.length > 0 ? ` (${totalRows})` : ''}
                        </h3>
                    )}
                    <div style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
                        {exportName && data.length > 0 && (
                            <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, exportName)}>
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
                <div className="empty-state">
                    <div className="empty-state-icon">{emptyIcon}</div>
                    <div className="empty-state-text">{emptyText}</div>
                </div>
            ) : (
                <>
                    <div style={{ overflowX: 'auto', ...(maxHeight ? { maxHeight, overflowY: 'auto' } : {}) }}>
                        <table className="data-table">
                            <thead>
                                {table.getHeaderGroups().map((hg) => (
                                    <tr key={hg.id}>
                                        {hg.headers.map((header) => {
                                            const meta = header.column.columnDef.meta as any;
                                            const canSort = header.column.getCanSort();
                                            const sorted = header.column.getIsSorted();
                                            return (
                                                <th
                                                    key={header.id}
                                                    style={{
                                                        textAlign: meta?.align || 'left',
                                                        cursor: canSort ? 'pointer' : undefined,
                                                        userSelect: canSort ? 'none' : undefined,
                                                        whiteSpace: 'nowrap',
                                                    }}
                                                    onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                                                >
                                                    {flexRender(header.column.columnDef.header, header.getContext())}
                                                    {canSort && (
                                                        <span className="ts-sort-indicator">
                                                            {sorted === 'asc' ? ' ↑' : sorted === 'desc' ? ' ↓' : ' ⇅'}
                                                        </span>
                                                    )}
                                                </th>
                                            );
                                        })}
                                    </tr>
                                ))}

                                {/* Column filters row */}
                                {enableFiltering && (
                                    <tr>
                                        {table.getHeaderGroups()[0].headers.map((header) => (
                                            <th key={`filter-${header.id}`} style={{ padding: '4px 8px' }}>
                                                {header.column.getCanFilter() && (
                                                    <input
                                                        className="ts-filter-input"
                                                        type="text"
                                                        value={(header.column.getFilterValue() as string) ?? ''}
                                                        onChange={(e) => header.column.setFilterValue(e.target.value || undefined)}
                                                        placeholder="Фильтр..."
                                                    />
                                                )}
                                            </th>
                                        ))}
                                    </tr>
                                )}
                            </thead>
                            <tbody>
                                {summaryRow}
                                {table.getRowModel().rows.map((row) => {
                                    const idx = row.index;
                                    const original = row.original;
                                    return (
                                        <tr
                                            key={row.id}
                                            onClick={onRowClick ? () => onRowClick(original, idx) : undefined}
                                            className={rowClassName ? rowClassName(original, idx) : undefined}
                                            style={{
                                                cursor: onRowClick ? 'pointer' : undefined,
                                                background: selectedIndex === idx ? 'rgba(139,92,246,0.1)' : undefined,
                                            }}
                                        >
                                            {row.getVisibleCells().map((cell) => {
                                                const meta = cell.column.columnDef.meta as any;
                                                return (
                                                    <td key={cell.id} style={{ textAlign: meta?.align || 'left' }}>
                                                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                                                    </td>
                                                );
                                            })}
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>

                    {/* Pagination */}
                    {enablePagination && table.getPageCount() > 1 && (
                        <div className="ts-pagination">
                            <span className="ts-pagination-info">
                                Показано {table.getState().pagination.pageIndex * pageSize + 1}–
                                {Math.min((table.getState().pagination.pageIndex + 1) * pageSize, totalRows)} из {totalRows}
                            </span>
                            <div className="ts-pagination-buttons">
                                <button
                                    className="btn btn-secondary btn-sm"
                                    onClick={() => table.previousPage()}
                                    disabled={!table.getCanPreviousPage()}
                                >
                                    ←
                                </button>
                                <span className="ts-pagination-current">
                                    {table.getState().pagination.pageIndex + 1} / {table.getPageCount()}
                                </span>
                                <button
                                    className="btn btn-secondary btn-sm"
                                    onClick={() => table.nextPage()}
                                    disabled={!table.getCanNextPage()}
                                >
                                    →
                                </button>
                            </div>
                        </div>
                    )}
                </>
            )}
        </div>
    );
}

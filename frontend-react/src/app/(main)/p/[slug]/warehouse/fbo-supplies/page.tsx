'use client';
import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatDateTime } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { WbFboSupply, WbFboSupplyItem, Warehouse, FboReturnType, FboPartialSummary } from '@/types/api';

// ─── Status config ──────────────────────────────────────────────────────────

const STATUS_MAP: Record<string, { label: string; className: string }> = {
    ACTIVE:       { label: 'Запланирована',       className: 'badge-warning' },
    ON_DELIVERY:  { label: 'Запланирована',       className: 'badge-warning' },
    IN_PROGRESS:  { label: 'Разгрузка разрешена', className: 'badge-info' },
    ACCEPTED:     { label: 'Принята',             className: 'badge-success' },
    CANCELLED:    { label: 'Отменена',            className: 'badge-secondary' },
};

const STATUS_OPTIONS = [
    { value: '', label: 'Все статусы' },
    { value: 'ACTIVE,ON_DELIVERY', label: 'Запланирована' },
    { value: 'IN_PROGRESS', label: 'Разгрузка разрешена' },
    { value: 'ACCEPTED', label: 'Принята' },
    { value: 'CANCELLED', label: 'Отменена' },
];

// ─── Component ──────────────────────────────────────────────────────────────

export default function FboSuppliesPage() {
    const params = useParams();
    const slug = params.slug as string;

    // Data
    const [supplies, setSupplies] = useState<WbFboSupply[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Filters
    const [search, setSearch] = useState('');
    const [statusFilter, setStatusFilter] = useState('');
    const [warehouseFilter, setWarehouseFilter] = useState('');
    const [warehouseOptions, setWarehouseOptions] = useState<string[]>([]);
    const [withoutAssembly, setWithoutAssembly] = useState(false);
    const [partialOnly, setPartialOnly] = useState(false);
    const [sortBy, setSortBy] = useState('created_at_wb');
    const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
    const [page, setPage] = useState(0);
    const PAGE_SIZE = 50;

    // Summary
    const [summary, setSummary] = useState<{
        total: number; accepted: number; accepted_without_assembly: number; accepted_partial: number;
    } | null>(null);

    // Expanded rows (supply items)
    const [expandedId, setExpandedId] = useState<number | null>(null);
    const [expandedItems, setExpandedItems] = useState<WbFboSupplyItem[]>([]);
    const [loadingItems, setLoadingItems] = useState(false);

    // Return modal
    const [returnState, setReturnState] = useState<{
        supply: WbFboSupply; items: WbFboSupplyItem[];
    } | null>(null);
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);

    useEffect(() => {
        api.getWarehouses().then(setWarehouses).catch(() => setWarehouses([]));
    }, []);

    // Sync
    const [syncing, setSyncing] = useState(false);
    const [syncingStatuses, setSyncingStatuses] = useState(false);
    const [syncMessage, setSyncMessage] = useState('');

    // ─── Load supplies ───────────────────────────────────────────────────

    // Load warehouse options once
    useEffect(() => {
        api.getFboWarehouses().then(setWarehouseOptions).catch(() => {});
    }, []);

    const loadSummary = useCallback(() => {
        api.getFboSuppliesSummary().then(setSummary).catch(() => setSummary(null));
    }, []);

    useEffect(() => { loadSummary(); }, [loadSummary]);

    // Partial-acceptance summary (qty buckets + per-barcode breakdown) —
    // loaded only when the user enables the "С недоприёмкой" filter.
    const [partialSummary, setPartialSummary] = useState<FboPartialSummary | null>(null);
    const [partialBreakdownOpen, setPartialBreakdownOpen] = useState(false);
    const loadPartialSummary = useCallback(() => {
        if (!partialOnly) { setPartialSummary(null); return; }
        api.getFboPartialSummary().then(setPartialSummary).catch(() => setPartialSummary(null));
    }, [partialOnly]);
    useEffect(() => { loadPartialSummary(); }, [loadPartialSummary]);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const resp = await api.getFboSupplies({
                search: search || undefined,
                status: statusFilter || undefined,
                warehouse: warehouseFilter || undefined,
                without_assembly: withoutAssembly || undefined,
                partial_only: partialOnly || undefined,
                sort_by: sortBy,
                sort_order: sortOrder,
                limit: PAGE_SIZE,
                offset: page * PAGE_SIZE,
            });
            setSupplies(resp.items);
            setTotal(resp.total);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [search, statusFilter, warehouseFilter, withoutAssembly, partialOnly, sortBy, sortOrder, page]);

    useEffect(() => { load(); }, [load]);

    // ─── Search with Enter ───────────────────────────────────────────────

    const [searchInput, setSearchInput] = useState('');

    const handleSearchKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter') {
            setSearch(searchInput);
            setPage(0);
        }
    };

    // ─── Expand row → load items ─────────────────────────────────────────

    const toggleExpand = async (supplyId: number) => {
        if (expandedId === supplyId) {
            setExpandedId(null);
            setExpandedItems([]);
            return;
        }
        setExpandedId(supplyId);
        setLoadingItems(true);
        try {
            const items = await api.getFboSupplyItems(supplyId);
            setExpandedItems(items);
            // Update total_qty in the supply row from loaded items
            if (items.length > 0) {
                const totalQty = items.reduce((s, i) => s + i.quantity, 0);
                const acceptedQty = items.reduce((s, i) => s + i.accepted_qty, 0);
                setSupplies(prev => prev.map(s =>
                    s.id === supplyId
                        ? { ...s, total_qty: totalQty, accepted_qty: acceptedQty }
                        : s
                ));
            }
        } catch {
            setExpandedItems([]);
        }
        setLoadingItems(false);
    };

    // ─── Sort toggle ─────────────────────────────────────────────────────

    const toggleSort = (field: string) => {
        if (sortBy === field) {
            setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc');
        } else {
            setSortBy(field);
            setSortOrder('desc');
        }
        setPage(0);
    };

    const sortIcon = (field: string) => {
        if (sortBy !== field) return '';
        return sortOrder === 'asc' ? ' \u2191' : ' \u2193';
    };

    // ─── Sync ────────────────────────────────────────────────────────────

    const handleSync = async () => {
        setSyncing(true);
        setSyncMessage('');
        try {
            const result = await api.syncFboSupplies();
            setSyncMessage(`${result.message}`);
            await load();
        } catch (e: unknown) {
            setSyncMessage(e instanceof Error ? e.message : 'Ошибка синхронизации');
        }
        setSyncing(false);
    };

    const handleSyncStatuses = async () => {
        setSyncingStatuses(true);
        setSyncMessage('');
        try {
            const result = await api.syncFboStatuses();
            setSyncMessage(`${result.message}`);
            await load();
        } catch (e: unknown) {
            setSyncMessage(e instanceof Error ? e.message : 'Ошибка');
        }
        setSyncingStatuses(false);
    };

    // ─── Pagination ──────────────────────────────────────────────────────

    const totalPages = Math.ceil(total / PAGE_SIZE);

    // ─── Render ──────────────────────────────────────────────────────────

    return (
        <div className="animate-in">
            {/* Header */}
            <div className="page-header">
                <div>
                    <h1 className="page-title">Поставки FBO</h1>
                    <p className="page-subtitle">Всего: {total}</p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={handleSyncStatuses}
                        disabled={syncingStatuses}
                    >
                        {syncingStatuses ? 'Синхронизация...' : 'Синхронизировать статусы'}
                    </button>
                    <button
                        className="btn btn-primary btn-sm"
                        onClick={handleSync}
                        disabled={syncing}
                    >
                        {syncing ? 'Обновление...' : 'Обновить'}
                    </button>
                </div>
            </div>

            {syncMessage && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 16, fontSize: 14 }}>
                    {syncMessage}
                </div>
            )}

            {/* Summary cards */}
            {summary && (
                <div className="stats-grid" style={{ marginBottom: 16 }}>
                    <SummaryCard label="Всего поставок" value={summary.total} />
                    <SummaryCard label="Принято WB" value={summary.accepted} />
                    <SummaryCard
                        label="Без заявки на сборку"
                        value={summary.accepted_without_assembly}
                        hint="WB принял, но в DDS нет заявки"
                        active={withoutAssembly}
                        onClick={() => { setWithoutAssembly(v => !v); setPage(0); }}
                        tone="warning"
                    />
                    <SummaryCard
                        label="С недоприёмкой"
                        value={summary.accepted_partial}
                        hint="WB принял не всё — возможно, часть вернулась"
                        active={partialOnly}
                        onClick={() => { setPartialOnly(v => !v); setPage(0); }}
                        tone="danger"
                    />
                </div>
            )}

            {/* Filters */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'end', flexWrap: 'wrap' }}>
                    <div className="form-group" style={{ flex: 1, minWidth: 200 }}>
                        <input
                            className="form-input"
                            placeholder="Поиск по номеру, ID, дате, отгрузке... (Enter)"
                            value={searchInput}
                            onChange={e => setSearchInput(e.target.value)}
                            onKeyDown={handleSearchKeyDown}
                        />
                    </div>
                    <div className="form-group">
                        <select
                            className="form-input"
                            value={statusFilter}
                            onChange={e => { setStatusFilter(e.target.value); setPage(0); }}
                        >
                            {STATUS_OPTIONS.map(o => (
                                <option key={o.value} value={o.value}>{o.label}</option>
                            ))}
                        </select>
                    </div>
                    <div className="form-group">
                        <select
                            className="form-input"
                            value={warehouseFilter}
                            onChange={e => { setWarehouseFilter(e.target.value); setPage(0); }}
                        >
                            <option value="">Все склады</option>
                            {warehouseOptions.map(wh => (
                                <option key={wh} value={wh}>{wh}</option>
                            ))}
                        </select>
                    </div>
                    <label className="form-group" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                        <input
                            type="checkbox"
                            checked={withoutAssembly}
                            onChange={e => { setWithoutAssembly(e.target.checked); setPage(0); }}
                        />
                        Без заявки на сборку
                    </label>
                    <label className="form-group" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                        <input
                            type="checkbox"
                            checked={partialOnly}
                            onChange={e => { setPartialOnly(e.target.checked); setPage(0); }}
                        />
                        С недоприёмкой
                    </label>
                </div>
            </div>

            {/* Partial-acceptance mini-summary */}
            {partialOnly && partialSummary && (
                <PartialSummaryPanel
                    summary={partialSummary}
                    open={partialBreakdownOpen}
                    onToggle={() => setPartialBreakdownOpen(v => !v)}
                />
            )}

            {/* Error */}
            {error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)' }}>
                    {error}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={() => setError('')}>
                        Закрыть
                    </button>
                </div>
            )}

            {/* Table */}
            {loading ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            ) : supplies.length === 0 ? (
                <div className="glass-card" style={{ padding: 64, textAlign: 'center' }}>
                    <div style={{ fontSize: 48, marginBottom: 16 }}>📮</div>
                    <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>Нет поставок</div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                        Нажмите &laquo;Обновить&raquo; для загрузки данных из WB
                    </div>
                </div>
            ) : (
                <div className="glass-card" style={{ overflow: 'auto' }}>
                    {/* TODO: migrate to TanStackDataTable — has expandable rows with React.Fragment and inline action buttons */}
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th style={{ width: 40 }}></th>
                                <th>Поставка</th>
                                <th>Статус</th>
                                <th>Склад</th>
                                <th style={{ textAlign: 'right' }}>Кол-во</th>
                                <th>Отгрузка</th>
                                <th
                                    style={{ cursor: 'pointer' }}
                                    onClick={() => toggleSort('created_at_wb')}
                                >
                                    Создана{sortIcon('created_at_wb')}
                                </th>
                                <th
                                    style={{ cursor: 'pointer' }}
                                    onClick={() => toggleSort('planned_date')}
                                >
                                    План{sortIcon('planned_date')}
                                </th>
                                <th
                                    style={{ cursor: 'pointer' }}
                                    onClick={() => toggleSort('actual_date')}
                                >
                                    Факт{sortIcon('actual_date')}
                                </th>
                            </tr>
                        </thead>
                        <tbody>
                            {supplies.map(supply => {
                                const status = STATUS_MAP[supply.wb_status] || { label: supply.wb_status, className: '' };
                                const isExpanded = expandedId === supply.id;

                                return (
                                    <React.Fragment key={supply.id}>
                                        <tr
                                            style={{ cursor: 'pointer' }}
                                            onClick={() => toggleExpand(supply.id)}
                                        >
                                            <td style={{ textAlign: 'center', fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                {isExpanded ? '\u25BC' : '\u25B6'}
                                            </td>
                                            <td>
                                                <div style={{ fontWeight: 500 }}>
                                                    {supply.wb_supply_id}
                                                </div>
                                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                    {supply.cargo_type || ''}{supply.cargo_type && supply.name ? ' · ' : ''}{supply.name || ''}
                                                </div>
                                            </td>
                                            <td>
                                                <span className={`badge ${status.className}`}>
                                                    {status.label}
                                                </span>
                                            </td>
                                            <td style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                                {supply.warehouse_name || '\u2014'}
                                            </td>
                                            <td style={{ textAlign: 'right', fontWeight: 500 }}>
                                                {supply.total_qty || 0}
                                                {supply.accepted_qty > 0 && supply.accepted_qty !== supply.total_qty && (
                                                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)', fontWeight: 400 }}>
                                                        {' '}/ {supply.accepted_qty}
                                                    </span>
                                                )}
                                            </td>
                                            <td onClick={e => e.stopPropagation()}>
                                                {supply.assembly_request_id ? (
                                                    <Link href={`/p/${slug}/warehouse/assembly/${supply.assembly_request_id}`}>
                                                        <span className="badge" style={{
                                                            background: supply.assembly_request_status === 'SHIPPED' ? 'var(--color-success)' :
                                                                supply.assembly_request_status === 'VEHICLE_ASSIGNED' ? 'var(--color-info)' :
                                                                supply.assembly_request_status === 'READY' ? 'var(--color-warning)' :
                                                                'var(--color-bg-secondary)',
                                                            color: ['SHIPPED', 'VEHICLE_ASSIGNED', 'READY'].includes(supply.assembly_request_status || '') ? '#fff' : 'var(--color-text)',
                                                            padding: '4px 8px',
                                                            borderRadius: 4,
                                                            fontSize: 12,
                                                            cursor: 'pointer',
                                                        }}>
                                                            {supply.assembly_request_number}
                                                        </span>
                                                    </Link>
                                                ) : supply.outbound_shipment_id && supply.outbound_shipment_warehouse_id ? (
                                                    <Link href={`/p/${slug}/warehouse/${supply.outbound_shipment_warehouse_id}/shipment/${supply.outbound_shipment_id}`}>
                                                        <span className="badge" style={{
                                                            background: supply.outbound_shipment_status === 'DELIVERED' ? 'var(--color-success)' :
                                                                supply.outbound_shipment_status === 'SHIPPED' ? 'var(--color-info)' :
                                                                supply.outbound_shipment_status === 'CANCELLED' ? 'var(--color-danger)' :
                                                                'var(--color-bg-secondary)',
                                                            color: ['DELIVERED', 'SHIPPED', 'CANCELLED'].includes(supply.outbound_shipment_status || '') ? '#fff' : 'var(--color-text)',
                                                            padding: '4px 8px',
                                                            borderRadius: 4,
                                                            fontSize: 12,
                                                            cursor: 'pointer',
                                                        }}>
                                                            {supply.outbound_shipment_number || 'Отгружена'}
                                                        </span>
                                                    </Link>
                                                ) : supply.wb_status === 'ACCEPTED' ? (
                                                    <Link href={`/p/${slug}/warehouse/assembly/new?fbo_supply_id=${supply.id}`}>
                                                        <button className="btn btn-primary btn-sm">
                                                            Создать заявку
                                                        </button>
                                                    </Link>
                                                ) : (
                                                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                                        &mdash;
                                                    </span>
                                                )}
                                            </td>
                                            <td>{formatDateTime(supply.created_at_wb)}</td>
                                            <td>{formatDate(supply.planned_date)}</td>
                                            <td>{supply.actual_date ? formatDateTime(supply.actual_date) : '\u2014'}</td>
                                        </tr>

                                        {/* Expanded items row */}
                                        {isExpanded && (
                                            <tr>
                                                <td colSpan={9} style={{ padding: 0, background: 'var(--color-bg-secondary)' }}>
                                                    <SupplyItemsPanel
                                                        supply={supply}
                                                        items={expandedItems}
                                                        loading={loadingItems}
                                                        slug={slug}
                                                        onOpenReturn={() => setReturnState({ supply, items: expandedItems })}
                                                    />
                                                </td>
                                            </tr>
                                        )}
                                    </React.Fragment>
                                );
                            })}
                        </tbody>
                    </table>

                    {/* Pagination */}
                    {totalPages > 1 && (
                        <div style={{
                            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                            padding: '12px 16px', borderTop: '1px solid var(--color-border)',
                        }}>
                            <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                Показано {page * PAGE_SIZE + 1}\u2013{Math.min((page + 1) * PAGE_SIZE, total)} из {total}
                            </span>
                            <div style={{ display: 'flex', gap: 4 }}>
                                <button
                                    className="btn btn-secondary btn-sm"
                                    disabled={page === 0}
                                    onClick={() => setPage(p => p - 1)}
                                >
                                    &larr;
                                </button>
                                <button
                                    className="btn btn-secondary btn-sm"
                                    disabled={page >= totalPages - 1}
                                    onClick={() => setPage(p => p + 1)}
                                >
                                    &rarr;
                                </button>
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* Return Modal */}
            {returnState !== null && (
                <FboReturnModal
                    supply={returnState.supply}
                    items={returnState.items}
                    warehouses={warehouses}
                    onClose={() => setReturnState(null)}
                    onDone={async () => {
                        setReturnState(null);
                        // Close the expanded row — accepted_qty/return_processed_at
                        // on the cached supply object is now stale; re-expand will
                        // re-fetch items from the refreshed backend row.
                        setExpandedId(null);
                        setExpandedItems([]);
                        await load();
                        loadSummary();
                        loadPartialSummary();
                    }}
                />
            )}
        </div>
    );
}


// ─── Return modal ────────────────────────────────────────────────────────────

function FboReturnModal({
    supply, items, warehouses, onClose, onDone,
}: {
    supply: WbFboSupply;
    items: WbFboSupplyItem[];
    warehouses: Warehouse[];
    onClose: () => void;
    onDone: () => Promise<void> | void;
}) {
    const deltaItems = items
        .map(i => ({ ...i, delta: Math.max(0, i.quantity - i.accepted_qty) }))
        .filter(i => i.delta > 0);

    // Warehouse auto-selected from the linked AR source. Fallback to first
    // available only if supply has no AR yet.
    const [warehouseId, setWarehouseId] = useState<number | ''>(
        supply.source_warehouse_id ?? warehouses[0]?.id ?? ''
    );
    // Per-row split: how many go to stock, how many to utilize. By default
    // all delta goes to stock (the common case).
    const [rows, setRows] = useState<Record<string, { stock: number; utilize: number }>>(
        Object.fromEntries(deltaItems.map(i => [i.barcode, { stock: i.delta, utilize: 0 }]))
    );
    const [comment, setComment] = useState('');
    const [saving, setSaving] = useState(false);
    const [err, setErr] = useState('');

    const totalDelta = deltaItems.reduce((s, i) => s + i.delta, 0);
    const totalStock = Object.values(rows).reduce((s, r) => s + (r.stock || 0), 0);
    const totalUtilize = Object.values(rows).reduce((s, r) => s + (r.utilize || 0), 0);
    const totalReturn = totalStock + totalUtilize;
    const hasStock = totalStock > 0;

    // Keep sum (stock + utilize) ≤ delta for the row. User enters one value;
    // the other is auto-capped.
    const setStock = (barcode: string, delta: number, v: number) => {
        setRows(r => {
            const current = r[barcode] || { stock: 0, utilize: 0 };
            const newStock = Math.max(0, Math.min(delta - (current.utilize || 0), v));
            return { ...r, [barcode]: { ...current, stock: newStock } };
        });
    };
    const setUtilize = (barcode: string, delta: number, v: number) => {
        setRows(r => {
            const current = r[barcode] || { stock: 0, utilize: 0 };
            const newUtilize = Math.max(0, Math.min(delta - (current.stock || 0), v));
            return { ...r, [barcode]: { ...current, utilize: newUtilize } };
        });
    };

    const allToStock = () => {
        setRows(Object.fromEntries(deltaItems.map(i => [i.barcode, { stock: i.delta, utilize: 0 }])));
    };
    const allToUtilize = () => {
        setRows(Object.fromEntries(deltaItems.map(i => [i.barcode, { stock: 0, utilize: i.delta }])));
    };
    const resetAll = () => {
        setRows(Object.fromEntries(deltaItems.map(i => [i.barcode, { stock: 0, utilize: 0 }])));
    };

    const submit = async () => {
        setErr('');
        if (totalReturn === 0) { setErr('Укажи количество к возврату или утилизации'); return; }
        if (hasStock && !warehouseId) { setErr('Выберите склад для возврата на склад'); return; }

        // Flatten per-row split into items[] with return_type per row.
        const payloadItems: { barcode: string; quantity: number; return_type: FboReturnType }[] = [];
        for (const i of deltaItems) {
            const r = rows[i.barcode] || { stock: 0, utilize: 0 };
            if (r.stock > 0) payloadItems.push({ barcode: i.barcode, quantity: r.stock, return_type: 'GOODS' });
            if (r.utilize > 0) payloadItems.push({ barcode: i.barcode, quantity: r.utilize, return_type: 'UTILIZED' });
        }

        setSaving(true);
        try {
            await api.createFboReturn(supply.id, {
                warehouse_id: hasStock ? (warehouseId as number) : null,
                items: payloadItems,
                comment: comment.trim() || null,
            });
            await onDone();
        } catch (e: unknown) {
            setErr(e instanceof Error ? e.message : 'Ошибка');
        } finally {
            setSaving(false);
        }
    };

    const submitLabel =
        totalStock > 0 && totalUtilize > 0 ? 'Оформить возврат и списание' :
            totalUtilize > 0 ? 'Утилизировать' :
                'Оформить возврат';

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div
                className="modal-card"
                onClick={e => e.stopPropagation()}
                style={{
                    maxWidth: 860, width: '92vw', maxHeight: '90vh',
                    display: 'flex', flexDirection: 'column',
                    background: '#ffffff',
                    backdropFilter: 'none',
                    WebkitBackdropFilter: 'none',
                }}
            >
                {/* Header */}
                <div style={{ marginBottom: 16 }}>
                    <h2 className="modal-title" style={{ marginBottom: 4 }}>
                        Возврат по поставке FBW-{supply.wb_supply_id}
                    </h2>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        WB принял {supply.accepted_qty} из {supply.total_qty} шт. Непринято — {totalDelta} шт.
                    </div>
                </div>

                {/* Status strip: where the qty is going */}
                <div style={{
                    display: 'flex', gap: 16, alignItems: 'center',
                    padding: '10px 14px', marginBottom: 16,
                    background: 'var(--color-bg)', borderRadius: 12,
                    fontSize: 13, flexWrap: 'wrap',
                }}>
                    <div>
                        <span style={{ color: 'var(--color-text-muted)' }}>На склад: </span>
                        <strong style={{ color: 'var(--color-success)' }}>{totalStock} шт</strong>
                    </div>
                    <div>
                        <span style={{ color: 'var(--color-text-muted)' }}>Утилизировать: </span>
                        <strong style={{ color: 'var(--color-danger)' }}>{totalUtilize} шт</strong>
                    </div>
                    <div>
                        <span style={{ color: 'var(--color-text-muted)' }}>Из </span>
                        <strong>{totalDelta}</strong>
                        <span style={{ color: 'var(--color-text-muted)' }}> непринятых</span>
                    </div>
                    <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                        <button type="button" className="btn btn-secondary btn-sm" onClick={allToStock}>
                            Всё на склад
                        </button>
                        <button type="button" className="btn btn-secondary btn-sm" onClick={allToUtilize}>
                            Всё утилизировать
                        </button>
                        <button type="button" className="btn btn-secondary btn-sm" onClick={resetAll}>
                            Сбросить
                        </button>
                    </div>
                </div>

                {/* Scrollable content */}
                <div style={{ overflowY: 'auto', flex: 1, paddingRight: 4 }}>
                    {/* Warehouse picker (only when any row has stock > 0) */}
                    {hasStock && (
                        <div className="form-group" style={{ marginBottom: 16 }}>
                            <label style={{ display: 'block', fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
                                Склад возврата
                            </label>
                            <select
                                className="form-input"
                                value={warehouseId}
                                onChange={e => setWarehouseId(e.target.value ? Number(e.target.value) : '')}
                            >
                                <option value="">Выберите склад</option>
                                {warehouses.map(w => (
                                    <option key={w.id} value={w.id}>{w.name}</option>
                                ))}
                            </select>
                        </div>
                    )}

                    {/* Items */}
                    <div style={{ marginBottom: 16 }}>
                        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
                            Позиции ({deltaItems.length})
                        </div>
                        {deltaItems.length === 0 ? (
                            <div style={{
                                padding: 24, textAlign: 'center', borderRadius: 8,
                                background: 'var(--color-bg)', color: 'var(--color-text-muted)', fontSize: 13,
                            }}>
                                Нет непринятых позиций
                            </div>
                        ) : (
                            <div style={{ border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden' }}>
                                <table className="data-table" style={{ fontSize: 13, margin: 0 }}>
                                    <thead>
                                        <tr>
                                            <th style={{ paddingLeft: 12 }}>Товар / ШК</th>
                                            <th style={{ textAlign: 'right', width: 90 }}>Не принято</th>
                                            <th style={{ textAlign: 'right', width: 140, color: 'var(--color-success)' }}>
                                                На склад
                                            </th>
                                            <th style={{ textAlign: 'right', width: 140, paddingRight: 12, color: 'var(--color-danger)' }}>
                                                Утилизировать
                                            </th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {deltaItems.map(i => {
                                            const row = rows[i.barcode] || { stock: 0, utilize: 0 };
                                            const placed = row.stock + row.utilize;
                                            const overCapped = placed >= i.delta;
                                            return (
                                                <tr key={i.barcode}>
                                                    <td style={{ paddingLeft: 12 }}>
                                                        <div style={{ fontWeight: 500 }}>
                                                            {i.article_seller || i.product_name || '—'}
                                                        </div>
                                                        <div style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--color-text-muted)' }}>
                                                            {i.barcode}
                                                        </div>
                                                    </td>
                                                    <td style={{
                                                        textAlign: 'right',
                                                        color: overCapped ? 'var(--color-success)' : 'var(--color-text-muted)',
                                                        fontWeight: overCapped ? 600 : 400,
                                                    }}>
                                                        {i.delta}
                                                    </td>
                                                    <td style={{ textAlign: 'right' }}>
                                                        <input
                                                            className="form-input"
                                                            type="number"
                                                            min={0}
                                                            max={i.delta}
                                                            value={row.stock}
                                                            onChange={e => setStock(i.barcode, i.delta, Number(e.target.value) || 0)}
                                                            style={{ textAlign: 'right', padding: '4px 8px', height: 32, width: 120 }}
                                                        />
                                                    </td>
                                                    <td style={{ textAlign: 'right', paddingRight: 12 }}>
                                                        <input
                                                            className="form-input"
                                                            type="number"
                                                            min={0}
                                                            max={i.delta}
                                                            value={row.utilize}
                                                            onChange={e => setUtilize(i.barcode, i.delta, Number(e.target.value) || 0)}
                                                            style={{ textAlign: 'right', padding: '4px 8px', height: 32, width: 120 }}
                                                        />
                                                    </td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>

                    {/* Comment */}
                    <div className="form-group" style={{ marginBottom: 8 }}>
                        <label style={{ display: 'block', fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
                            Комментарий (необязательно)
                        </label>
                        <input
                            className="form-input"
                            value={comment}
                            onChange={e => setComment(e.target.value)}
                            placeholder="Например: вернулись после отказа клиента"
                        />
                    </div>
                </div>

                {/* Footer */}
                {err && (
                    <div style={{
                        padding: '8px 12px', marginTop: 12,
                        background: 'color-mix(in srgb, var(--color-danger) 8%, transparent)',
                        border: '1px solid color-mix(in srgb, var(--color-danger) 30%, transparent)',
                        borderRadius: 8, color: 'var(--color-danger)', fontSize: 13,
                    }}>
                        {err}
                    </div>
                )}
                <div style={{
                    display: 'flex', gap: 12, justifyContent: 'flex-end', alignItems: 'center',
                    marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--color-border)',
                }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={saving}>
                        Отмена
                    </button>
                    <button
                        className="btn btn-primary"
                        onClick={submit}
                        disabled={saving || deltaItems.length === 0 || totalReturn === 0}
                    >
                        {saving ? 'Сохранение...' : `${submitLabel} (${totalReturn} шт)`}
                    </button>
                </div>
            </div>
        </div>
    );
}


// ─── Partial-acceptance mini-summary ─────────────────────────────────────────

function PartialSummaryPanel({
    summary,
    open,
    onToggle,
}: {
    summary: FboPartialSummary;
    open: boolean;
    onToggle: () => void;
}) {
    const buckets: { label: string; value: number; color: string; hint: string }[] = [
        { label: 'Не приняли', value: summary.unaccepted_total, color: 'var(--color-danger)', hint: 'Всего шт не принято WB' },
        { label: 'Нераспределено', value: summary.unprocessed, color: 'var(--color-warning)', hint: 'Решение не принято' },
        { label: 'Возвращено на склад', value: summary.returned_to_stock, color: 'var(--color-success)', hint: 'Годные + брак' },
        { label: 'Списано', value: summary.utilized, color: 'var(--color-text-muted)', hint: 'Утилизировано WB' },
    ];
    const totalItems = summary.items_breakdown.reduce((s, i) => s + i.delta, 0);

    return (
        <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: summary.items_breakdown.length > 0 ? 12 : 0 }}>
                {buckets.map(b => (
                    <div key={b.label} style={{ padding: 12, borderRadius: 12, background: 'var(--color-bg)' }}>
                        <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 4 }}>
                            {b.label}
                        </div>
                        <div style={{ fontSize: 22, fontWeight: 700, color: b.color, letterSpacing: '-0.02em' }}>
                            {b.value.toLocaleString('ru-RU')} <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-text-muted)' }}>шт</span>
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 2 }}>{b.hint}</div>
                    </div>
                ))}
            </div>

            {summary.items_breakdown.length > 0 && (
                <>
                    <button
                        type="button"
                        onClick={onToggle}
                        style={{
                            display: 'flex', alignItems: 'center', gap: 6, width: '100%',
                            padding: '8px 12px', borderRadius: 8, border: '1px solid var(--color-border)',
                            background: open ? 'var(--color-bg)' : 'transparent',
                            cursor: 'pointer', fontSize: 13, fontWeight: 500, color: 'var(--color-text)',
                            transition: 'background 0.15s',
                        }}
                    >
                        <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                            {open ? '\u25BC' : '\u25B6'}
                        </span>
                        {open ? 'Скрыть' : 'Показать'} позиции ({summary.items_breakdown.length} артикулов, {totalItems} шт)
                    </button>
                    {open && (
                        <div style={{ marginTop: 8, maxHeight: 320, overflowY: 'auto', border: '1px solid var(--color-border)', borderRadius: 8 }}>
                            <table className="data-table" style={{ fontSize: 13, margin: 0 }}>
                                <thead>
                                    <tr>
                                        <th style={{ paddingLeft: 12 }}>Товар / Артикул</th>
                                        <th>ШК</th>
                                        <th style={{ textAlign: 'right', paddingRight: 12, width: 110 }}>Не принято</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {summary.items_breakdown.map(i => (
                                        <tr key={i.barcode}>
                                            <td style={{ paddingLeft: 12 }}>
                                                <div style={{ fontWeight: 500 }}>
                                                    {i.product_name || i.article_seller || '—'}
                                                </div>
                                                {i.article_seller && i.product_name && (
                                                    <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                                        {i.article_seller}
                                                    </div>
                                                )}
                                            </td>
                                            <td style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                {i.barcode}
                                            </td>
                                            <td style={{ textAlign: 'right', paddingRight: 12, fontWeight: 600, color: 'var(--color-danger)' }}>
                                                {i.delta}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </>
            )}
        </div>
    );
}


// ─── Summary card (clickable filter chip) ───────────────────────────────────

function SummaryCard({
    label,
    value,
    hint,
    active,
    onClick,
    tone,
}: {
    label: string;
    value: number;
    hint?: string;
    active?: boolean;
    onClick?: () => void;
    tone?: 'warning' | 'danger';
}) {
    const clickable = Boolean(onClick);
    const toneColor =
        tone === 'warning' ? 'var(--color-warning)' :
        tone === 'danger' ? 'var(--color-danger)' :
        'var(--color-text)';
    return (
        <div
            className="glass-card"
            onClick={onClick}
            style={{
                padding: 16,
                cursor: clickable ? 'pointer' : 'default',
                borderColor: active ? toneColor : undefined,
                borderWidth: active ? 2 : undefined,
                borderStyle: active ? 'solid' : undefined,
            }}
        >
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>
                {label}
            </div>
            <div style={{ fontSize: 28, fontWeight: 700, letterSpacing: '-0.03em', color: toneColor }}>
                {value.toLocaleString('ru-RU')}
            </div>
            {hint && (
                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 4 }}>
                    {hint}
                </div>
            )}
        </div>
    );
}


// ─── Expanded Supply Items Panel ─────────────────────────────────────────────

function SupplyItemsPanel({
    supply,
    items,
    loading,
    slug,
    onOpenReturn,
}: {
    supply: WbFboSupply;
    items: WbFboSupplyItem[];
    loading: boolean;
    slug: string;
    onOpenReturn: () => void;
}) {
    if (loading) {
        return <div style={{ padding: 24, textAlign: 'center' }}>Загрузка позиций...</div>;
    }

    // Use items if present, fall back to supply-level counters
    // (items may be missing for supplies that haven't been enriched yet).
    const hasItems = items.length > 0;
    const totalQty = hasItems ? items.reduce((s, i) => s + i.quantity, 0) : supply.total_qty;
    const totalAccepted = hasItems ? items.reduce((s, i) => s + i.accepted_qty, 0) : supply.accepted_qty;
    const unacceptedDelta = Math.max(0, totalQty - totalAccepted);
    const showReturnBanner = supply.wb_status === 'ACCEPTED' && unacceptedDelta > 0 && !supply.return_processed_at;

    return (
        <div style={{ padding: '16px 24px 20px' }}>
            {/* Недоприёмка banner */}
            {showReturnBanner && (
                <div style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
                    padding: '10px 14px', marginBottom: 12,
                    background: 'color-mix(in srgb, var(--color-danger) 8%, transparent)',
                    border: '1px solid color-mix(in srgb, var(--color-danger) 40%, transparent)',
                    borderRadius: 8, fontSize: 14,
                }}>
                    <div>
                        <strong style={{ color: 'var(--color-danger)' }}>Недоприёмка:</strong>{' '}
                        {unacceptedDelta} шт из {totalQty} — WB принял {totalAccepted}
                    </div>
                    <button className="btn btn-primary btn-sm" onClick={onOpenReturn}>
                        Оформить возврат
                    </button>
                </div>
            )}
            {supply.return_processed_at && (
                <div style={{
                    padding: '8px 12px', marginBottom: 12,
                    background: 'color-mix(in srgb, var(--color-success) 10%, transparent)',
                    borderRadius: 8, fontSize: 13, color: 'var(--color-text-muted)',
                }}>
                    ✓ Недоприёмка обработана {formatDate(supply.return_processed_at)}
                </div>
            )}

            {/* Warehouse info */}
            {supply.warehouse_name && (
                <div style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    padding: '8px 12px', marginBottom: 12,
                    background: 'var(--color-bg)', borderRadius: 8,
                    fontSize: 14,
                }}>
                    <span style={{ fontSize: 16 }}>&#128230;</span>
                    <span>Склад WB: <strong>{supply.warehouse_name}</strong></span>
                </div>
            )}

            {/* Items header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <span style={{ fontWeight: 500, fontSize: 14 }}>
                    Позиции поставки ({items.length})
                </span>
                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                    Всего: {totalQty} шт., принято: {totalAccepted} шт.
                </span>
            </div>

            {items.length === 0 ? (
                <div style={{ textAlign: 'center', padding: 16, color: 'var(--color-text-muted)', fontSize: 14 }}>
                    Нет позиций
                </div>
            ) : (
                (() => {
                    const itemsCols: Column[] = [
                        { key: 'product_name', label: 'Товар', render: (v: string) => <div style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v || '\u2014'}</div> },
                        { key: 'article_seller', label: 'Артикул', render: (v: string) => <span style={{ color: 'var(--color-text-muted)' }}>{v || '\u2014'}</span> },
                        { key: 'barcode', label: 'ШК', render: (v: string) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span> },
                        { key: 'quantity', label: 'Кол-во', align: 'right', format: 'number' },
                        { key: 'accepted_qty', label: 'Принято', align: 'right', render: (v: number) => <span style={{ fontWeight: 500 }}>{v}</span> },
                    ];
                    return (
                        <TanStackDataTable
                            columns={itemsCols}
                            data={items}
                            enableSorting
                            enablePagination={false}
                        />
                    );
                })()
            )}
        </div>
    );
}

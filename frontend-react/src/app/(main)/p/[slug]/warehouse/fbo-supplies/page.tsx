'use client';
import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatDateTime } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { WbFboSupply, WbFboSupplyItem, Warehouse, FboReturnType, FboPartialSummary, FboReassignCandidate, FboAuditEntry } from '@/types/api';

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
    const [excessOnly, setExcessOnly] = useState(false);
    // Default period: last 30 days by created_at_wb. User can extend or clear.
    const [dateFrom, setDateFrom] = useState(() => {
        const d = new Date();
        d.setDate(d.getDate() - 30);
        return d.toISOString().slice(0, 10);
    });
    const [dateTo, setDateTo] = useState('');
    const [sortBy, setSortBy] = useState('created_at_wb');
    const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
    const [page, setPage] = useState(0);
    const PAGE_SIZE = 50;

    // Summary
    const [summary, setSummary] = useState<{
        total: number; accepted: number; accepted_without_assembly: number; accepted_partial: number; accepted_excess: number;
    } | null>(null);

    // Expanded rows (supply items)
    const [expandedId, setExpandedId] = useState<number | null>(null);
    const [expandedItems, setExpandedItems] = useState<WbFboSupplyItem[]>([]);
    const [loadingItems, setLoadingItems] = useState(false);

    // Return modal
    const [returnState, setReturnState] = useState<{
        supply: WbFboSupply; items: WbFboSupplyItem[];
    } | null>(null);
    // Excess modal (overshoot: WB accepted more than we shipped)
    const [excessState, setExcessState] = useState<{
        supply: WbFboSupply; items: WbFboSupplyItem[];
    } | null>(null);
    // Reassign modal (доприёмка → поставка с недоприёмкой того же SKU)
    const [reassignState, setReassignState] = useState<{
        supply: WbFboSupply;
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
        api.getFboSuppliesSummary({
            date_from: dateFrom || undefined,
            date_to: dateTo || undefined,
            warehouse: warehouseFilter || undefined,
            status: statusFilter || undefined,
        }).then(setSummary).catch(() => setSummary(null));
    }, [dateFrom, dateTo, warehouseFilter, statusFilter]);

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
                date_from: dateFrom || undefined,
                date_to: dateTo || undefined,
                without_assembly: withoutAssembly || undefined,
                partial_only: partialOnly || undefined,
                excess_only: excessOnly || undefined,
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
    }, [search, statusFilter, warehouseFilter, dateFrom, dateTo, withoutAssembly, partialOnly, excessOnly, sortBy, sortOrder, page]);

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
                // If partial_only filter is active and the supply turned out to
                // be fully accepted after auto-refresh — drop it from the list
                // (it no longer matches the server-side filter, but the list
                // we got was a snapshot before refresh).
                const fullyAccepted = totalQty > 0 && acceptedQty >= totalQty;
                setSupplies(prev => {
                    if (partialOnly && fullyAccepted) {
                        return prev.filter(s => s.id !== supplyId);
                    }
                    return prev.map(s =>
                        s.id === supplyId
                            ? { ...s, total_qty: totalQty, accepted_qty: acceptedQty }
                            : s
                    );
                });
                if (partialOnly && fullyAccepted) {
                    setExpandedId(null);
                    setExpandedItems([]);
                }
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
                    <SummaryCard
                        label="С излишком"
                        value={summary.accepted_excess}
                        hint="WB принял больше отправленного — списать со склада"
                        active={excessOnly}
                        onClick={() => { setExcessOnly(v => !v); setPage(0); }}
                        tone="warning"
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
                    <div className="form-group" title="Дата создания поставки в WB">
                        <input
                            type="date"
                            className="form-input"
                            value={dateFrom}
                            onChange={e => { setDateFrom(e.target.value); setPage(0); }}
                            placeholder="С"
                        />
                    </div>
                    <div className="form-group">
                        <input
                            type="date"
                            className="form-input"
                            value={dateTo}
                            onChange={e => { setDateTo(e.target.value); setPage(0); }}
                            placeholder="По"
                        />
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
                    <label className="form-group" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                        <input
                            type="checkbox"
                            checked={excessOnly}
                            onChange={e => { setExcessOnly(e.target.checked); setPage(0); }}
                        />
                        С излишком
                    </label>
                    <Link href={`/p/${slug}/warehouse/fbo-supplies/archive`} className="form-group" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text-muted)', textDecoration: 'none' }}>
                        📁 Архив
                    </Link>
                    <Link href={`/p/${slug}/warehouse/fbo-supplies/history`} className="form-group" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text-muted)', textDecoration: 'none' }}>
                        🕐 История
                    </Link>
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
                                                {(supply.assembly_request_id || supply.outbound_shipment_id) ? (
                                                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-start' }}>
                                                        {supply.assembly_request_id && (
                                                            <Link href={`/p/${slug}/warehouse/assembly/${supply.assembly_request_id}`}>
                                                                <span className={`badge ${
                                                                    supply.assembly_request_status === 'DELIVERED' ? 'badge-success' :
                                                                    supply.assembly_request_status === 'SHIPPED' ? 'badge-success' :
                                                                    supply.assembly_request_status === 'VEHICLE_ASSIGNED' ? 'badge-info' :
                                                                    supply.assembly_request_status === 'READY' ? 'badge-warning' :
                                                                    supply.assembly_request_status === 'CANCELLED' ? 'badge-danger' :
                                                                    'badge-secondary'
                                                                }`} style={{ cursor: 'pointer' }}>
                                                                    {supply.assembly_request_number}
                                                                </span>
                                                            </Link>
                                                        )}
                                                        {supply.outbound_shipment_id && supply.outbound_shipment_warehouse_id && (
                                                            <Link href={`/p/${slug}/warehouse/${supply.outbound_shipment_warehouse_id}/shipment/${supply.outbound_shipment_id}`}>
                                                                <span className={`badge ${
                                                                    supply.outbound_shipment_status === 'DELIVERED' ? 'badge-success' :
                                                                    supply.outbound_shipment_status === 'SHIPPED' ? 'badge-info' :
                                                                    supply.outbound_shipment_status === 'CANCELLED' ? 'badge-danger' :
                                                                    'badge-secondary'
                                                                }`} style={{ cursor: 'pointer' }}>
                                                                    {supply.outbound_shipment_number || 'Отгружена'}
                                                                </span>
                                                            </Link>
                                                        )}
                                                    </div>
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
                                                        onOpenExcess={() => setExcessState({ supply, items: expandedItems })}
                                                        onOpenReassign={() => setReassignState({ supply })}
                                                        onAfterRevert={async () => {
                                                            setExpandedId(null);
                                                            setExpandedItems([]);
                                                            await load();
                                                            loadSummary();
                                                            loadPartialSummary();
                                                        }}
                                                        onArchive={async () => {
                                                            try {
                                                                await api.setFboSupplyArchive(supply.id, true);
                                                                setSupplies(prev => prev.filter(s => s.id !== supply.id));
                                                                setExpandedId(null);
                                                                setExpandedItems([]);
                                                                loadSummary();
                                                            } catch (e) {
                                                                alert('Не удалось отправить в архив: ' + (e instanceof Error ? e.message : 'ошибка'));
                                                            }
                                                        }}
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

            {/* Excess Modal: списать излишек со склада */}
            {excessState !== null && (
                <FboExcessModal
                    supply={excessState.supply}
                    items={excessState.items}
                    warehouses={warehouses}
                    onClose={() => setExcessState(null)}
                    onDone={async () => {
                        setExcessState(null);
                        setExpandedId(null);
                        setExpandedItems([]);
                        await load();
                        loadSummary();
                    }}
                />
            )}

            {/* Reassign Modal: привязать доприёмку к поставке с недоприёмкой */}
            {reassignState !== null && (
                <FboReassignModal
                    supply={reassignState.supply}
                    onClose={() => setReassignState(null)}
                    onDone={async () => {
                        setReassignState(null);
                        setExpandedId(null);
                        setExpandedItems([]);
                        await load();
                        loadSummary();
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


// ─── Excess (overshoot) modal ────────────────────────────────────────────────

function FboExcessModal({
    supply, items, warehouses, onClose, onDone,
}: {
    supply: WbFboSupply;
    items: WbFboSupplyItem[];
    warehouses: Warehouse[];
    onClose: () => void;
    onDone: () => Promise<void> | void;
}) {
    // Excess delta per barcode = max(0, accepted - quantity)
    const deltaItems = items
        .map(i => ({ ...i, delta: Math.max(0, i.accepted_qty - i.quantity) }))
        .filter(i => i.delta > 0);

    const [warehouseId, setWarehouseId] = useState<number | ''>(
        supply.source_warehouse_id ?? warehouses[0]?.id ?? ''
    );
    const [rows, setRows] = useState<Record<string, number>>(
        Object.fromEntries(deltaItems.map(i => [i.barcode, i.delta]))
    );
    const [comment, setComment] = useState('');
    const [saving, setSaving] = useState(false);
    const [err, setErr] = useState('');

    const totalDelta = deltaItems.reduce((s, i) => s + i.delta, 0);
    const totalWriteOff = Object.values(rows).reduce((s, v) => s + (v || 0), 0);

    const setQty = (barcode: string, delta: number, v: number) => {
        setRows(r => ({ ...r, [barcode]: Math.max(0, Math.min(delta, v)) }));
    };

    const submit = async () => {
        setErr('');
        if (totalWriteOff === 0) { setErr('Укажи количество к списанию'); return; }
        if (!warehouseId) { setErr('Выбери склад для списания'); return; }
        const payloadItems = deltaItems
            .map(i => ({ barcode: i.barcode, quantity: rows[i.barcode] || 0 }))
            .filter(it => it.quantity > 0);

        setSaving(true);
        try {
            await api.processFboExcess(supply.id, {
                warehouse_id: warehouseId as number,
                items: payloadItems,
                comment: comment.trim() || undefined,
            });
            await onDone();
        } catch (e: unknown) {
            setErr(e instanceof Error ? e.message : 'Ошибка');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div
                className="modal-card"
                onClick={e => e.stopPropagation()}
                style={{
                    maxWidth: 720, width: '92vw', maxHeight: '90vh',
                    display: 'flex', flexDirection: 'column',
                    background: '#ffffff',
                    backdropFilter: 'none',
                    WebkitBackdropFilter: 'none',
                }}
            >
                {/* Header */}
                <div style={{ marginBottom: 16 }}>
                    <h2 className="modal-title" style={{ marginBottom: 4 }}>
                        Списать излишек со склада
                    </h2>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        Поставка <strong>FBW-{supply.wb_supply_id}</strong>: WB принял на {totalDelta} шт больше, чем мы отправили.
                    </div>
                </div>

                {/* Warehouse selector */}
                <div style={{ marginBottom: 16 }}>
                    <label style={{ fontSize: 13, fontWeight: 600, display: 'block', marginBottom: 6 }}>
                        Склад списания
                    </label>
                    <select
                        className="form-input"
                        value={warehouseId}
                        onChange={e => setWarehouseId(e.target.value ? Number(e.target.value) : '')}
                        style={{ width: '100%' }}
                    >
                        <option value="">— выбрать —</option>
                        {warehouses.map(w => (
                            <option key={w.id} value={w.id}>{w.name}</option>
                        ))}
                    </select>
                </div>

                {/* Items table (scrollable) */}
                <div style={{
                    marginBottom: 16, border: '1px solid var(--color-border)',
                    borderRadius: 12, overflow: 'auto', flex: '1 1 auto', maxHeight: '50vh',
                    background: '#ffffff',
                }}>
                    <table className="data-table" style={{ fontSize: 13, margin: 0 }}>
                        <thead>
                            <tr>
                                <th style={{ paddingLeft: 12 }}>Товар / ШК</th>
                                <th style={{ textAlign: 'right' }}>Излишек</th>
                                <th style={{ textAlign: 'right', paddingRight: 12, width: 140 }}>Списать</th>
                            </tr>
                        </thead>
                        <tbody>
                            {deltaItems.map(i => (
                                <tr key={i.barcode}>
                                    <td style={{ paddingLeft: 12 }}>
                                        <div style={{ fontWeight: 500 }}>
                                            {i.product_name || i.article_seller || '—'}
                                        </div>
                                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)', fontFamily: 'monospace' }}>
                                            {i.barcode}
                                        </div>
                                    </td>
                                    <td style={{ textAlign: 'right', color: 'var(--color-warning)', fontWeight: 600 }}>
                                        +{i.delta}
                                    </td>
                                    <td style={{ textAlign: 'right', paddingRight: 12 }}>
                                        <input
                                            className="form-input"
                                            type="number"
                                            min={0}
                                            max={i.delta}
                                            value={rows[i.barcode] || 0}
                                            onChange={e => setQty(i.barcode, i.delta, Number(e.target.value) || 0)}
                                            style={{ textAlign: 'right', padding: '4px 8px', height: 32, width: 120 }}
                                        />
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>

                {/* Comment */}
                <div style={{ marginBottom: 16 }}>
                    <label style={{ fontSize: 13, fontWeight: 600, display: 'block', marginBottom: 6 }}>
                        Комментарий (опционально)
                    </label>
                    <input
                        type="text"
                        className="form-input"
                        value={comment}
                        onChange={e => setComment(e.target.value)}
                        placeholder="Например: ошибка склада при отгрузке"
                        style={{ width: '100%' }}
                    />
                </div>

                {err && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 12 }}>{err}</div>}

                {/* Footer */}
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={saving}>Отмена</button>
                    <button
                        className="btn btn-primary"
                        onClick={submit}
                        disabled={saving || deltaItems.length === 0 || totalWriteOff === 0}
                    >
                        {saving ? 'Сохранение...' : `Списать (${totalWriteOff} шт)`}
                    </button>
                </div>
            </div>
        </div>
    );
}


// ─── Reassign modal ──────────────────────────────────────────────────────────

function FboReassignModal({
    supply, onClose, onDone,
}: {
    supply: WbFboSupply;
    onClose: () => void;
    onDone: () => Promise<void> | void;
}) {
    const [loading, setLoading] = useState(true);
    const [candidates, setCandidates] = useState<FboReassignCandidate[]>([]);
    const [err, setErr] = useState('');
    const [saving, setSaving] = useState<number | null>(null);

    useEffect(() => {
        let active = true;
        (async () => {
            try {
                const resp = await api.getFboReassignCandidates(supply.id);
                if (active) {
                    setCandidates(resp.candidates);
                }
            } catch (e: unknown) {
                if (active) setErr(e instanceof Error ? e.message : 'Ошибка загрузки');
            } finally {
                if (active) setLoading(false);
            }
        })();
        return () => { active = false; };
    }, [supply.id]);

    const submit = async (targetId: number) => {
        setErr('');
        setSaving(targetId);
        try {
            await api.reassignFboSupply(supply.id, { target_supply_id: targetId });
            await onDone();
        } catch (e: unknown) {
            setErr(e instanceof Error ? e.message : 'Ошибка');
            setSaving(null);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div
                className="modal-card"
                onClick={e => e.stopPropagation()}
                style={{
                    maxWidth: 720, width: '92vw', maxHeight: '90vh',
                    display: 'flex', flexDirection: 'column',
                    background: '#ffffff',
                    backdropFilter: 'none',
                    WebkitBackdropFilter: 'none',
                }}
            >
                <div style={{ marginBottom: 16 }}>
                    <h2 className="modal-title" style={{ marginBottom: 4 }}>
                        Привязать доприёмку к поставке с недоприёмкой
                    </h2>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        Поставка <strong>FBW-{supply.wb_supply_id}</strong> ({supply.accepted_qty} шт, {supply.warehouse_name || '—'})
                        {' '}→ будет добавлена к accepted у выбранной поставки и уйдёт в архив.
                    </div>
                </div>

                <div style={{
                    flex: '1 1 auto', overflow: 'auto', marginBottom: 16,
                    border: '1px solid var(--color-border)', borderRadius: 12,
                    background: '#ffffff',
                }}>
                    {loading ? (
                        <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                            Поиск кандидатов...
                        </div>
                    ) : candidates.length === 0 ? (
                        <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                            Нет поставок с недоприёмкой по тем же артикулам.
                            <br /><small>Попробуй «В архив», если эта доприёмка никуда не подходит.</small>
                        </div>
                    ) : (
                        <div style={{ display: 'flex', flexDirection: 'column' }}>
                            {candidates.map(c => (
                                <div
                                    key={c.id}
                                    style={{
                                        padding: 14, borderBottom: '1px solid var(--color-border)',
                                        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
                                    }}
                                >
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, flexWrap: 'wrap' }}>
                                            <strong>FBW-{c.wb_supply_id}</strong>
                                            {c.same_warehouse && (
                                                <span className="badge badge-success" style={{ fontSize: 11 }}>
                                                    тот же склад
                                                </span>
                                            )}
                                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                {c.warehouse_name || '—'} · {formatDateTime(c.created_at_wb)}
                                            </span>
                                        </div>
                                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                            Принято {c.accepted_qty} из {c.total_qty} шт.
                                            {' '}Совпадает по артикулу: <strong style={{ color: 'var(--color-text)' }}>{c.matched_qty} шт</strong>
                                        </div>
                                        {c.matched_items.length > 0 && (
                                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 4 }}>
                                                {c.matched_items.slice(0, 3).map(m =>
                                                    `${m.product_name || m.article_seller || m.barcode} (−${m.unaccepted_delta})`
                                                ).join(', ')}
                                                {c.matched_items.length > 3 && ` и ещё ${c.matched_items.length - 3}`}
                                            </div>
                                        )}
                                    </div>
                                    <button
                                        className="btn btn-primary btn-sm"
                                        onClick={() => submit(c.id)}
                                        disabled={saving !== null}
                                    >
                                        {saving === c.id ? 'Привязка...' : 'Привязать'}
                                    </button>
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                {err && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 12 }}>{err}</div>}

                <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={saving !== null}>
                        Отмена
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
                        <div style={{ marginTop: 8, maxHeight: 720, overflowY: 'auto', border: '1px solid var(--color-border)', borderRadius: 8 }}>
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
    onOpenExcess,
    onOpenReassign,
    onAfterRevert,
    onArchive,
}: {
    supply: WbFboSupply;
    items: WbFboSupplyItem[];
    loading: boolean;
    slug: string;
    onOpenReturn: () => void;
    onOpenExcess?: () => void;
    onOpenReassign?: () => void;
    onAfterRevert?: () => void | Promise<void>;
    onArchive?: () => void;
}) {
    if (loading) {
        return <div style={{ padding: 24, textAlign: 'center' }}>Загрузка позиций...</div>;
    }

    // Use items if present, fall back to supply-level counters
    // (items may be missing for supplies that haven't been enriched yet).
    const hasItems = items.length > 0;
    const totalQty = hasItems ? items.reduce((s, i) => s + i.quantity, 0) : supply.total_qty;
    const totalAccepted = hasItems ? items.reduce((s, i) => s + i.accepted_qty, 0) : supply.accepted_qty;
    // Per-item deltas — important for пересорт (одни товары приняли меньше,
    // другие больше). Aggregated total - accepted скрыл бы один из них.
    const unacceptedDelta = hasItems
        ? items.reduce((s, i) => s + Math.max(0, i.quantity - i.accepted_qty), 0)
        : Math.max(0, totalQty - totalAccepted);
    const excessDelta = hasItems
        ? items.reduce((s, i) => s + Math.max(0, i.accepted_qty - i.quantity), 0)
        : Math.max(0, totalAccepted - totalQty);
    const showReturnBanner = supply.wb_status === 'ACCEPTED' && unacceptedDelta > 0 && !supply.return_processed_at;
    const showExcessBanner = supply.wb_status === 'ACCEPTED' && excessDelta > 0 && !supply.excess_processed_at;

    const reassignedToWb = supply.reassigned_to_wb_supply_id;
    const reassignedFromWb = supply.reassigned_from_wb_supply_ids ?? [];
    // Hide reassign button when: already reassigned, has no items, not ACCEPTED,
    // or when supply has under-acceptance itself (those are targets, not sources).
    const canReassign = !reassignedToWb
        && supply.wb_status === 'ACCEPTED'
        && hasItems
        && unacceptedDelta === 0
        && reassignedFromWb.length === 0;

    return (
        <div style={{ padding: '16px 24px 20px' }}>
            {/* Info: эта поставка — доприёмка, уже привязана */}
            {reassignedToWb && (
                <div style={{
                    padding: '10px 14px', marginBottom: 12,
                    background: 'color-mix(in srgb, var(--color-accent) 8%, transparent)',
                    border: '1px solid color-mix(in srgb, var(--color-accent) 40%, transparent)',
                    borderRadius: 8, fontSize: 13,
                }}>
                    📦 Эта доприёмка привязана к поставке <strong>#{reassignedToWb}</strong>
                </div>
            )}

            {/* Info: в эту поставку добавлены доприёмки */}
            {reassignedFromWb.length > 0 && (
                <div style={{
                    padding: '10px 14px', marginBottom: 12,
                    background: 'color-mix(in srgb, var(--color-success) 8%, transparent)',
                    border: '1px solid color-mix(in srgb, var(--color-success) 40%, transparent)',
                    borderRadius: 8, fontSize: 13,
                }}>
                    ✓ В эту поставку добавлены доприёмки:{' '}
                    <strong>{reassignedFromWb.map(id => `#${id}`).join(', ')}</strong>
                </div>
            )}

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

            {/* Излишек banner: WB принял БОЛЬШЕ чем отправлено */}
            {showExcessBanner && onOpenExcess && (
                <div style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
                    padding: '10px 14px', marginBottom: 12,
                    background: 'color-mix(in srgb, var(--color-warning) 10%, transparent)',
                    border: '1px solid color-mix(in srgb, var(--color-warning) 40%, transparent)',
                    borderRadius: 8, fontSize: 14,
                }}>
                    <div>
                        <strong style={{ color: 'var(--color-warning)' }}>Излишек:</strong>{' '}
                        {excessDelta} шт — WB принял больше, чем отправлено
                    </div>
                    <button className="btn btn-primary btn-sm" onClick={onOpenExcess}>
                        Списать со склада
                    </button>
                </div>
            )}
            {supply.excess_processed_at && (
                <div style={{
                    padding: '8px 12px', marginBottom: 12,
                    background: 'color-mix(in srgb, var(--color-success) 10%, transparent)',
                    borderRadius: 8, fontSize: 13, color: 'var(--color-text-muted)',
                }}>
                    ✓ Излишек ({supply.excess_qty} шт) списан {formatDate(supply.excess_processed_at)}
                </div>
            )}

            {(onArchive || onOpenReassign) && (
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginBottom: 8 }}>
                    {onOpenReassign && canReassign && (
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={onOpenReassign}
                            title="Привязать эту поставку как доприёмку к поставке с недоприёмкой того же артикула"
                        >
                            📦 Привязать к недоприёмке
                        </button>
                    )}
                    {onArchive && !reassignedToWb && (
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={onArchive}
                            title="Скрыть из основного списка (можно вернуть из архива)"
                        >
                            📁 В архив
                        </button>
                    )}
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

            {/* Audit trail (история действий) */}
            <SupplyAuditPanel supplyId={supply.id} onAfterRevert={onAfterRevert} />
        </div>
    );
}


// ─── Audit trail panel ──────────────────────────────────────────────────────

const ACTION_LABEL: Record<string, string> = {
    ARCHIVE: 'В архив',
    UNARCHIVE: 'Из архива / автоправило',
    BULK_RESTORE: 'Массовое восстановление',
    RETURN: 'Обработана недоприёмка',
    EXCESS: 'Списан излишек',
    REASSIGN: 'Привязана как доприёмка',
    REVERT: 'Откат действия',
};

const ACTION_ICON: Record<string, string> = {
    ARCHIVE: '📁',
    UNARCHIVE: '♻️',
    BULK_RESTORE: '♻️',
    RETURN: '↩️',
    EXCESS: '📤',
    REASSIGN: '📦',
    REVERT: '↶',
};

function formatAuditPayload(entry: FboAuditEntry): string {
    const p = entry.payload || {};
    switch (entry.action) {
        case 'ARCHIVE':
        case 'UNARCHIVE': {
            const nv = p.new;
            const ov = p.old;
            const fmt = (v: unknown) => v === true ? 'в архиве' : v === false ? 'активна' : 'автоправило';
            return `${fmt(ov)} → ${fmt(nv)}`;
        }
        case 'REASSIGN': {
            const tgt = p.target_wb_supply_id;
            const qty = p.reassigned_qty;
            return `${qty} шт → в поставку FBW-${tgt}`;
        }
        case 'RETURN': {
            const type = p.return_type || '—';
            const qty = p.return_qty || 0;
            const receipt = p.receipt_number;
            return `${qty} шт, тип: ${type}${receipt ? `, приёмка ${receipt}` : ''}`;
        }
        case 'EXCESS': {
            const qty = p.excess_qty || 0;
            const num = p.shipment_number;
            return `${qty} шт, отгрузка ${num || '—'}`;
        }
        case 'REVERT': {
            const orig = p.action || '';
            return `откат действия «${ACTION_LABEL[orig as string] || orig}»`;
        }
        case 'BULK_RESTORE':
            return 'восстановлена из архива (bulk)';
        default:
            return '';
    }
}

function SupplyAuditPanel({
    supplyId,
    onAfterRevert,
}: {
    supplyId: number;
    onAfterRevert?: () => void | Promise<void>;
}) {
    const [open, setOpen] = useState(false);
    const [entries, setEntries] = useState<FboAuditEntry[]>([]);
    const [loading, setLoading] = useState(false);
    const [reverting, setReverting] = useState<number | null>(null);
    const [err, setErr] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setErr('');
        try {
            const resp = await api.getFboSupplyAudit(supplyId);
            setEntries(resp.entries);
        } catch (e: unknown) {
            setErr(e instanceof Error ? e.message : 'Ошибка');
        } finally {
            setLoading(false);
        }
    }, [supplyId]);

    useEffect(() => {
        if (open) load();
    }, [open, load]);

    const revert = async (auditId: number) => {
        if (!confirm('Откатить это действие?')) return;
        setReverting(auditId);
        setErr('');
        try {
            await api.revertFboAudit(auditId);
            await load();
            if (onAfterRevert) await onAfterRevert();
        } catch (e: unknown) {
            setErr(e instanceof Error ? e.message : 'Ошибка');
        } finally {
            setReverting(null);
        }
    };

    return (
        <div style={{ marginTop: 16 }}>
            <button
                type="button"
                onClick={() => setOpen(o => !o)}
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
                🕐 История действий {entries.length > 0 && `(${entries.length})`}
            </button>
            {open && (
                <div style={{ marginTop: 8, border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden' }}>
                    {loading ? (
                        <div style={{ padding: 16, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>
                            Загрузка...
                        </div>
                    ) : err ? (
                        <div style={{ padding: 12, color: 'var(--color-danger)', fontSize: 13 }}>{err}</div>
                    ) : entries.length === 0 ? (
                        <div style={{ padding: 16, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>
                            Действий пока не было
                        </div>
                    ) : (
                        <table className="data-table" style={{ fontSize: 13, margin: 0 }}>
                            <thead>
                                <tr>
                                    <th style={{ paddingLeft: 12, width: 40 }}></th>
                                    <th>Действие</th>
                                    <th>Детали</th>
                                    <th style={{ textAlign: 'right', paddingRight: 12, width: 140 }}>Когда</th>
                                    <th style={{ textAlign: 'right', paddingRight: 12, width: 120 }}></th>
                                </tr>
                            </thead>
                            <tbody>
                                {entries.map(e => {
                                    const isReverted = e.reverted_at !== null;
                                    return (
                                        <tr key={e.id} style={isReverted ? { opacity: 0.55 } : undefined}>
                                            <td style={{ paddingLeft: 12, fontSize: 18 }}>{ACTION_ICON[e.action] || '•'}</td>
                                            <td>
                                                <div style={{ fontWeight: 500 }}>
                                                    {ACTION_LABEL[e.action] || e.action}
                                                </div>
                                                {isReverted && (
                                                    <span className="badge badge-secondary" style={{ fontSize: 10 }}>
                                                        откачено
                                                    </span>
                                                )}
                                            </td>
                                            <td style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                {formatAuditPayload(e)}
                                            </td>
                                            <td style={{ textAlign: 'right', paddingRight: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                {formatDateTime(e.created_at)}
                                            </td>
                                            <td style={{ textAlign: 'right', paddingRight: 12 }}>
                                                {e.revertible && !isReverted && (
                                                    <button
                                                        className="btn btn-secondary btn-sm"
                                                        onClick={() => revert(e.id)}
                                                        disabled={reverting !== null}
                                                        title="Откатить это действие"
                                                    >
                                                        {reverting === e.id ? '...' : '↶ Откатить'}
                                                    </button>
                                                )}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    )}
                </div>
            )}
        </div>
    );
}

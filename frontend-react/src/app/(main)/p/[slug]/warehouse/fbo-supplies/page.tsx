'use client';
import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatDateTime } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { WbFboSupply, WbFboSupplyItem, OutboundShipment } from '@/types/api';

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

    // Link modal
    const [linkSupplyId, setLinkSupplyId] = useState<number | null>(null);
    const [shipments, setShipments] = useState<OutboundShipment[]>([]);
    const [loadingShipments, setLoadingShipments] = useState(false);
    const [selectedShipmentId, setSelectedShipmentId] = useState<number | null>(null);
    const [linking, setLinking] = useState(false);

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

    // ─── Link modal ──────────────────────────────────────────────────────

    const openLinkModal = async (supplyId: number) => {
        setLinkSupplyId(supplyId);
        setSelectedShipmentId(null);
        setLoadingShipments(true);
        try {
            // Load all warehouses, then shipments from each
            const warehouses = await api.getWarehouses();
            const allShipments: OutboundShipment[] = [];
            for (const wh of warehouses) {
                try {
                    const ships = await api.getShipments(wh.id);
                    allShipments.push(...ships);
                } catch { /* skip */ }
            }
            // Show only SHIPPED/DRAFT (not yet linked)
            setShipments(allShipments.filter(s =>
                (s.status === 'SHIPPED' || s.status === 'DRAFT') && !s.wb_supply_id
            ));
        } catch {
            setShipments([]);
        }
        setLoadingShipments(false);
    };

    const handleLink = async () => {
        if (!linkSupplyId || !selectedShipmentId) return;
        setLinking(true);
        try {
            await api.linkFboSupply(linkSupplyId, selectedShipmentId);
            setLinkSupplyId(null);
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка привязки');
        }
        setLinking(false);
    };

    const handleUnlink = async (supplyId: number) => {
        try {
            await api.unlinkFboSupply(supplyId);
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка отвязки');
        }
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
                                                ) : supply.outbound_shipment_id ? (
                                                    <button
                                                        className="btn btn-secondary btn-sm"
                                                        onClick={() => handleUnlink(supply.id)}
                                                        title="Отвязать"
                                                    >
                                                        Связана #{supply.outbound_shipment_id}
                                                    </button>
                                                ) : (
                                                    <Link href={`/p/${slug}/warehouse/assembly/new?fbo_supply_id=${supply.id}`}>
                                                        <button className="btn btn-primary btn-sm">
                                                            Создать заявку
                                                        </button>
                                                    </Link>
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

            {/* Link Modal */}
            {linkSupplyId !== null && (
                <div className="modal-overlay" onClick={() => setLinkSupplyId(null)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 520 }}>
                        <h2 className="modal-title">Связать с отгрузкой</h2>
                        {loadingShipments ? (
                            <div style={{ textAlign: 'center', padding: 24 }}>Загрузка отгрузок...</div>
                        ) : shipments.length === 0 ? (
                            <div style={{ textAlign: 'center', padding: 24, color: 'var(--color-text-muted)' }}>
                                Нет доступных отгрузок (DRAFT/SHIPPED без привязки)
                            </div>
                        ) : (
                            <div style={{ maxHeight: 300, overflow: 'auto' }}>
                                {shipments.map(s => (
                                    <div
                                        key={s.id}
                                        onClick={() => setSelectedShipmentId(s.id)}
                                        style={{
                                            padding: '10px 12px',
                                            borderRadius: 8,
                                            cursor: 'pointer',
                                            border: selectedShipmentId === s.id
                                                ? '2px solid var(--color-primary)'
                                                : '1px solid var(--color-border)',
                                            marginBottom: 8,
                                            background: selectedShipmentId === s.id
                                                ? 'var(--color-primary-light)'
                                                : 'transparent',
                                        }}
                                    >
                                        <div style={{ fontWeight: 500 }}>{s.number}</div>
                                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                            {s.destination || 'Без назначения'} &middot; {s.status} &middot; {s.items?.length || 0} поз.
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                            <button className="btn btn-secondary" onClick={() => setLinkSupplyId(null)}>
                                Отмена
                            </button>
                            <button
                                className="btn btn-primary"
                                onClick={handleLink}
                                disabled={linking || !selectedShipmentId}
                            >
                                {linking ? 'Привязка...' : 'Связать'}
                            </button>
                        </div>
                    </div>
                </div>
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
}: {
    supply: WbFboSupply;
    items: WbFboSupplyItem[];
    loading: boolean;
    slug: string;
}) {
    if (loading) {
        return <div style={{ padding: 24, textAlign: 'center' }}>Загрузка позиций...</div>;
    }

    const totalQty = items.reduce((s, i) => s + i.quantity, 0);
    const totalAccepted = items.reduce((s, i) => s + i.accepted_qty, 0);

    return (
        <div style={{ padding: '16px 24px 20px' }}>
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

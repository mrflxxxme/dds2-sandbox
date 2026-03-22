'use client';
import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatNumber, exportToExcel } from '@/lib/utils';
import type { AssemblyRequest, AssemblyStatus } from '@/types/api';

// ─── Status config ──────────────────────────────────────────────────────────

const STATUS_MAP: Record<AssemblyStatus, { label: string; className: string }> = {
    PENDING:          { label: 'Ожидает сборку',    className: 'badge-warning' },
    IN_PROGRESS:      { label: 'В сборке',          className: 'badge-info' },
    READY:            { label: 'Готово',             className: 'badge-success' },
    VEHICLE_ASSIGNED: { label: 'Машина назначена',   className: 'badge-info' },
    SHIPPED:          { label: 'Отгружена',          className: 'badge-success' },
    DELIVERED:        { label: 'Принята WB',         className: 'badge-success' },
    CANCELLED:        { label: 'Отменена',           className: 'badge-secondary' },
};

const WB_STATUS_MAP: Record<string, { label: string; className: string }> = {
    ACTIVE:       { label: 'Запланирована',  className: 'badge-warning' },
    ON_DELIVERY:  { label: 'В пути',         className: 'badge-info' },
    IN_PROGRESS:  { label: 'Разгрузка',      className: 'badge-info' },
    ACCEPTED:     { label: 'Принята',         className: 'badge-success' },
    CANCELLED:    { label: 'Отменена',        className: 'badge-secondary' },
};

type GroupBy = 'wb_warehouse' | 'warehouse';

// ─── Component ──────────────────────────────────────────────────────────────

export default function LogisticsPage() {
    const params = useParams();
    const slug = params.slug as string;

    const [items, setItems] = useState<AssemblyRequest[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Tab
    const [activeTab, setActiveTab] = useState<'active' | 'history'>('active');
    // History tab
    const [historyItems, setHistoryItems] = useState<AssemblyRequest[]>([]);
    const [historyTotal, setHistoryTotal] = useState(0);
    const [historyLoading, setHistoryLoading] = useState(false);

    // Filters
    const [groupBy, setGroupBy] = useState<GroupBy>('wb_warehouse');
    const [showSoonReady, setShowSoonReady] = useState(false);
    const [warehouseFilter, setWarehouseFilter] = useState('');

    // View mode
    const [viewMode, setViewMode] = useState<'cards' | 'table'>(() => {
        if (typeof window !== 'undefined') {
            return (localStorage.getItem('logistics_view') as 'cards' | 'table') || 'cards';
        }
        return 'cards';
    });
    const switchViewMode = (mode: 'cards' | 'table') => {
        setViewMode(mode);
        localStorage.setItem('logistics_view', mode);
    };

    // Checkboxes for bulk selection
    const [checkedIds, setCheckedIds] = useState<Set<number>>(new Set());

    // Vehicle modal
    const [showVehicleModal, setShowVehicleModal] = useState(false);
    const [vehicleInfo, setVehicleInfo] = useState('');
    const [vehicleBrand, setVehicleBrand] = useState('');
    const [driverPhone, setDriverPhone] = useState('');
    const [selectedIds, setSelectedIds] = useState<number[]>([]);
    const [actionLoading, setActionLoading] = useState(false);

    // Per-request params in modal: { [requestId]: { pickup_date, pickup_time_slot, pickup_cost, delivery_date } }
    interface PerRequestParams {
        pickup_date: string;
        pickup_time_slot: string;
        pickup_cost: number | '';
        delivery_date: string;
    }
    const [perRequestParams, setPerRequestParams] = useState<Record<number, PerRequestParams>>({});

    // ─── Load data ────────────────────────────────────────────────────────

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            // Load READY and VEHICLE_ASSIGNED
            const readyResp = await api.getAssemblyRequests({ status: 'READY', limit: 500 });
            const vehicleResp = await api.getAssemblyRequests({ status: 'VEHICLE_ASSIGNED', limit: 500 });

            let allItems = [...readyResp.items, ...vehicleResp.items];

            // Optionally load "soon ready" (PENDING/IN_PROGRESS with estimated_ready_date <= today+2)
            if (showSoonReady) {
                const today = new Date();
                const twoDaysLater = new Date(today);
                twoDaysLater.setDate(twoDaysLater.getDate() + 2);
                const dateTo = twoDaysLater.toISOString().split('T')[0];

                const pendingResp = await api.getAssemblyRequests({ status: 'PENDING', date_to: dateTo, limit: 500 });
                const inProgressResp = await api.getAssemblyRequests({ status: 'IN_PROGRESS', date_to: dateTo, limit: 500 });

                // Filter by estimated_ready_date
                const soonItems = [...pendingResp.items, ...inProgressResp.items].filter(item => {
                    if (!item.estimated_ready_date) return false;
                    return new Date(item.estimated_ready_date) <= twoDaysLater;
                });

                allItems = [...allItems, ...soonItems];
            }

            setItems(allItems);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [showSoonReady]);

    useEffect(() => { load(); }, [load]);

    // ─── Load history ───────────────────────────────────────────────────────

    const loadHistory = useCallback(async () => {
        setHistoryLoading(true);
        try {
            const resp = await api.getAssemblyRequests({
                status: 'SHIPPED,DELIVERED',
                limit: 50,
                offset: 0,
            });
            setHistoryItems(resp.items);
            setHistoryTotal(resp.total);
        } catch { /* ignore */ }
        setHistoryLoading(false);
    }, []);

    useEffect(() => { if (activeTab === 'history') loadHistory(); }, [activeTab, loadHistory]);

    // ─── Warehouse list for filter ──────────────────────────────────────────

    const warehouseOptions = (() => {
        const key = groupBy === 'wb_warehouse'
            ? (i: AssemblyRequest) => i.wb_warehouse_name || ''
            : (i: AssemblyRequest) => i.warehouse_name || '';
        const names = new Set(items.map(key).filter(Boolean));
        return Array.from(names).sort();
    })();

    // ─── Filtered items ─────────────────────────────────────────────────

    const filteredItems = warehouseFilter
        ? items.filter(i => {
            const val = groupBy === 'wb_warehouse' ? i.wb_warehouse_name : i.warehouse_name;
            return val === warehouseFilter;
        })
        : items;

    // ─── Grouping ─────────────────────────────────────────────────────────

    const grouped = groupItems(filteredItems, groupBy);

    // ─── Summary ──────────────────────────────────────────────────────────

    const totalRequests = filteredItems.length;
    const totalPallets = filteredItems.reduce((s, i) => s + i.pallets_count, 0);
    const totalWeight = filteredItems.reduce((s, i) => s + (i.total_weight_kg || 0), 0);

    // ─── Checked items summary ──────────────────────────────────────────

    const checkedItems = filteredItems.filter(i => checkedIds.has(i.id));
    const checkedPallets = checkedItems.reduce((s, i) => s + i.pallets_count, 0);
    const checkedWeight = checkedItems.reduce((s, i) => s + (i.total_weight_kg || 0), 0);

    const toggleChecked = (id: number) => {
        setCheckedIds(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };

    // ─── Actions ──────────────────────────────────────────────────────────

    const openVehicleModal = (ids: number[]) => {
        setSelectedIds(ids);
        setVehicleInfo('');
        setVehicleBrand('');
        setDriverPhone('');
        const params: Record<number, PerRequestParams> = {};
        for (const id of ids) {
            params[id] = { pickup_date: '', pickup_time_slot: '', pickup_cost: '', delivery_date: '' };
        }
        setPerRequestParams(params);
        setShowVehicleModal(true);
    };

    const vehicleFormValid = vehicleInfo.trim() && vehicleBrand.trim() && driverPhone.trim()
        && selectedIds.every(id => {
            const p = perRequestParams[id];
            return p && p.pickup_date && p.pickup_time_slot && p.pickup_cost !== '' && p.delivery_date;
        });

    const updateParam = (id: number, field: keyof PerRequestParams, value: string | number) => {
        setPerRequestParams(prev => ({
            ...prev,
            [id]: { ...prev[id], [field]: value },
        }));
    };

    const handleAssignVehicle = async () => {
        if (!vehicleFormValid || selectedIds.length === 0) return;
        setActionLoading(true);
        setError('');
        try {
            const bulkItems = selectedIds.map(id => {
                const p = perRequestParams[id];
                return {
                    request_id: id,
                    pickup_date: p.pickup_date,
                    pickup_time_slot: p.pickup_time_slot,
                    pickup_cost: Number(p.pickup_cost),
                    delivery_date: p.delivery_date,
                };
            });
            if (selectedIds.length === 1) {
                await api.assignVehicle(selectedIds[0], {
                    vehicle_info: vehicleInfo.trim(),
                    vehicle_brand: vehicleBrand.trim(),
                    driver_phone: driverPhone.trim(),
                    ...bulkItems[0],
                });
            } else {
                await api.assignVehicleBulk({
                    vehicle_info: vehicleInfo.trim(),
                    vehicle_brand: vehicleBrand.trim(),
                    driver_phone: driverPhone.trim(),
                    items: bulkItems,
                });
            }
            setShowVehicleModal(false);
            setCheckedIds(new Set());
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
    };

    const handleShip = async (id: number) => {
        setActionLoading(true);
        setError('');
        try {
            await api.shipAssembly(id);
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка отгрузки');
        }
        setActionLoading(false);
    };

    // ─── Export ───────────────────────────────────────────────────────────

    const handleExport = () => {
        const data = items.map(i => ({
            '№': i.number,
            'Статус': STATUS_MAP[i.status]?.label || i.status,
            'Склад': i.warehouse_name || '',
            'Склад WB': i.wb_warehouse_name || '',
            'Поставка FBO': i.wb_supply_name || '',
            'Палеты': i.pallets_count,
            'Общий вес': i.total_weight_kg || 0,
            'Машина': i.vehicle_info || '',
            'Дата готовности': i.estimated_ready_date || '',
        }));
        exportToExcel(data, 'logistics_sheet');
    };

    // ─── Render ───────────────────────────────────────────────────────────

    const isSoonReady = (item: AssemblyRequest) =>
        item.status === 'PENDING' || item.status === 'IN_PROGRESS';

    return (
        <div className="animate-in">
            {/* Header */}
            <div className="page-header">
                <div>
                    <h1 className="page-title">Лист логиста</h1>
                </div>
                <button className="btn btn-secondary" onClick={handleExport}>
                    Excel
                </button>
            </div>

            {/* Tab switcher + view toggle */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 0 }}>
                    <button
                        className={`btn ${activeTab === 'active' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setActiveTab('active')}
                        style={{ borderRadius: '8px 0 0 8px' }}
                    >
                        Активные
                    </button>
                    <button
                        className={`btn ${activeTab === 'history' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setActiveTab('history')}
                        style={{ borderRadius: '0 8px 8px 0' }}
                    >
                        История отправок
                    </button>
                </div>
                {activeTab === 'active' && (
                    <div style={{ display: 'flex', gap: 0 }}>
                        <button
                            className={`btn btn-sm ${viewMode === 'cards' ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => switchViewMode('cards')}
                            style={{ borderRadius: '8px 0 0 8px' }}
                        >
                            Карточки
                        </button>
                        <button
                            className={`btn btn-sm ${viewMode === 'table' ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => switchViewMode('table')}
                            style={{ borderRadius: '0 8px 8px 0' }}
                        >
                            Таблица
                        </button>
                    </div>
                )}
            </div>

            {/* Error */}
            {error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)', whiteSpace: 'pre-line' }}>
                    {error}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={() => setError('')}>
                        Закрыть
                    </button>
                </div>
            )}

            {activeTab === 'active' ? (
                <>
                    {/* Filters */}
                    <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                        <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                            <div className="form-group" style={{ margin: 0 }}>
                                <select
                                    className="form-input"
                                    value={groupBy}
                                    onChange={e => { setGroupBy(e.target.value as GroupBy); setWarehouseFilter(''); }}
                                >
                                    <option value="wb_warehouse">По складу сдачи WB</option>
                                    <option value="warehouse">По складу забора</option>
                                </select>
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <select
                                    className="form-input"
                                    value={warehouseFilter}
                                    onChange={e => setWarehouseFilter(e.target.value)}
                                >
                                    <option value="">Все склады</option>
                                    {warehouseOptions.map(w => (
                                        <option key={w} value={w}>{w}</option>
                                    ))}
                                </select>
                            </div>
                            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 14 }}>
                                <input
                                    type="checkbox"
                                    checked={showSoonReady}
                                    onChange={e => setShowSoonReady(e.target.checked)}
                                />
                                Показывать скоро готовые
                            </label>
                        </div>
                    </div>

                    {/* Summary */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 16 }}>
                        <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                            <div style={{ fontSize: 24, fontWeight: 600 }}>{totalRequests}</div>
                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Заявок</div>
                        </div>
                        <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                            <div style={{ fontSize: 24, fontWeight: 600 }}>{totalPallets}</div>
                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Палет</div>
                        </div>
                        <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                            <div style={{ fontSize: 24, fontWeight: 600 }}>{totalWeight > 0 ? formatNumber(totalWeight, 0) : '0'}</div>
                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Общий вес (кг)</div>
                        </div>
                    </div>

                    {/* Content */}
                    {loading ? (
                        <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
                    ) : items.length === 0 ? (
                        <div className="glass-card" style={{ padding: 64, textAlign: 'center' }}>
                            <div style={{ fontSize: 48, marginBottom: 16 }}>🚛</div>
                            <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>Нет заявок для логистики</div>
                            <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                                Здесь появятся заявки в статусе &laquo;Готово&raquo; и &laquo;Машина назначена&raquo;
                            </div>
                        </div>
                    ) : viewMode === 'table' ? (
                        /* ─── Table view ─── */
                        <div className="glass-card" style={{ overflow: 'auto' }}>
                            <table className="data-table" style={{ fontSize: 13 }}>
                                <thead>
                                    <tr>
                                        <th style={{ width: 32 }}></th>
                                        <th>Заявка</th>
                                        <th>Забор</th>
                                        <th>Сдача WB</th>
                                        <th style={{ textAlign: 'right' }}>Палет</th>
                                        <th style={{ textAlign: 'right' }}>Вес</th>
                                        <th style={{ textAlign: 'right' }}>Позиции</th>
                                        <th>Статус</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {grouped.map(group => (
                                        <React.Fragment key={group.key}>
                                            <tr>
                                                <td colSpan={8} style={{ background: 'var(--color-bg-secondary)', fontWeight: 600, fontSize: 13, padding: '8px 12px' }}>
                                                    {group.label || 'Без склада'}
                                                    <span style={{ fontWeight: 400, color: 'var(--color-text-muted)', marginLeft: 8 }}>
                                                        {group.items.length} заявок, {group.items.reduce((s, i) => s + i.pallets_count, 0)} палет
                                                    </span>
                                                </td>
                                            </tr>
                                            {group.items.map(item => {
                                                const statusCfg = STATUS_MAP[item.status] || { label: item.status, className: '' };
                                                const canCheck = item.status === 'READY';
                                                const isChecked = checkedIds.has(item.id);
                                                return (
                                                    <tr
                                                        key={item.id}
                                                        style={{
                                                            cursor: 'pointer',
                                                            background: isChecked ? 'rgba(59, 130, 246, 0.06)' : undefined,
                                                        }}
                                                        onClick={() => canCheck && toggleChecked(item.id)}
                                                    >
                                                        <td onClick={e => e.stopPropagation()} style={{ textAlign: 'center' }}>
                                                            {canCheck && (
                                                                <input
                                                                    type="checkbox"
                                                                    checked={isChecked}
                                                                    onChange={() => toggleChecked(item.id)}
                                                                    style={{ width: 16, height: 16, accentColor: 'var(--color-primary)', cursor: 'pointer' }}
                                                                />
                                                            )}
                                                        </td>
                                                        <td onClick={e => e.stopPropagation()}>
                                                            <Link
                                                                href={`/p/${slug}/warehouse/assembly/${item.id}`}
                                                                style={{ fontWeight: 600, textDecoration: 'none', color: 'var(--color-primary)' }}
                                                            >
                                                                {item.number}
                                                            </Link>
                                                        </td>
                                                        <td style={{ color: 'var(--color-text-muted)' }}>{item.warehouse_name || '\u2014'}</td>
                                                        <td>{item.wb_warehouse_name || '\u2014'}</td>
                                                        <td style={{ textAlign: 'right' }}>{item.pallets_count}</td>
                                                        <td style={{ textAlign: 'right' }}>{item.total_weight_kg ? formatNumber(item.total_weight_kg, 0) + ' кг' : '\u2014'}</td>
                                                        <td style={{ textAlign: 'right' }}>{item.items?.length || 0}</td>
                                                        <td><span className={`badge ${statusCfg.className}`}>{statusCfg.label}</span></td>
                                                    </tr>
                                                );
                                            })}
                                        </React.Fragment>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    ) : (
                        /* ─── Cards view ─── */
                        <div>
                            {grouped.map(group => (
                                <div key={group.key} style={{ marginBottom: 24 }}>
                                    <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12, padding: '0 4px' }}>
                                        {group.label || 'Без склада'}
                                        <span style={{ fontWeight: 400, color: 'var(--color-text-muted)', marginLeft: 8, fontSize: 14 }}>
                                            ({group.items.length} заявок, {group.items.reduce((s, i) => s + i.pallets_count, 0)} палет)
                                        </span>
                                    </h2>

                                    {group.subGroups.map(sub => (
                                        <div key={sub.key} style={{ marginBottom: 16 }}>
                                            <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 8, padding: '0 4px', color: 'var(--color-text-muted)' }}>
                                                {sub.label || 'Без склада'}
                                            </div>

                                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
                                                {sub.items.map(item => {
                                                    const soon = isSoonReady(item);
                                                    const statusCfg = STATUS_MAP[item.status] || { label: item.status, className: '' };
                                                    const isChecked = checkedIds.has(item.id);
                                                    const canCheck = item.status === 'READY';

                                                    return (
                                                        <div
                                                            key={item.id}
                                                            className="glass-card"
                                                            style={{
                                                                padding: 16,
                                                                opacity: soon ? 0.5 : 1,
                                                                border: isChecked ? '2px solid var(--color-primary)' : undefined,
                                                                cursor: canCheck ? 'pointer' : undefined,
                                                            }}
                                                            onClick={canCheck ? () => toggleChecked(item.id) : undefined}
                                                        >
                                                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                                                                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                                                    {canCheck && (
                                                                        <input
                                                                            type="checkbox"
                                                                            checked={isChecked}
                                                                            onChange={() => toggleChecked(item.id)}
                                                                            onClick={e => e.stopPropagation()}
                                                                            style={{ width: 18, height: 18, accentColor: 'var(--color-primary)', cursor: 'pointer' }}
                                                                        />
                                                                    )}
                                                                    <Link
                                                                        href={`/p/${slug}/warehouse/assembly/${item.id}`}
                                                                        style={{ fontWeight: 600, textDecoration: 'none', color: 'var(--color-text)' }}
                                                                        onClick={e => e.stopPropagation()}
                                                                    >
                                                                        {item.number}
                                                                    </Link>
                                                                </div>
                                                                <span className={`badge ${statusCfg.className}`}>
                                                                    {soon && item.estimated_ready_date
                                                                        ? formatDate(item.estimated_ready_date)
                                                                        : statusCfg.label}
                                                                </span>
                                                            </div>

                                                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 8 }}>
                                                                <div>Палет: {item.pallets_count} &middot; Вес: {item.total_weight_kg ? formatNumber(item.total_weight_kg, 0) + ' кг' : '\u2014'}</div>
                                                                <div>Позиций: {item.items?.length || 0}</div>
                                                                {item.wb_warehouse_name && (
                                                                    <div>WB: {item.wb_warehouse_name}</div>
                                                                )}
                                                            </div>

                                                            {!soon && (
                                                                <div style={{ display: 'flex', gap: 8 }}>
                                                                    {item.status === 'READY' && (
                                                                        <button
                                                                            className="btn btn-primary btn-sm"
                                                                            onClick={() => openVehicleModal([item.id])}
                                                                            disabled={actionLoading}
                                                                        >
                                                                            Назначить машину
                                                                        </button>
                                                                    )}
                                                                    {item.status === 'VEHICLE_ASSIGNED' && (
                                                                        <>
                                                                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', flex: 1 }}>
                                                                                {item.vehicle_info}
                                                                            </div>
                                                                            <button
                                                                                className="btn btn-primary btn-sm"
                                                                                onClick={() => handleShip(item.id)}
                                                                                disabled={actionLoading}
                                                                            >
                                                                                Отгрузить
                                                                            </button>
                                                                        </>
                                                                    )}
                                                                </div>
                                                            )}
                                                        </div>
                                                    );
                                                })}
                                            </div>

                                            {/* Bulk assign for READY items in this sub-group */}
                                            {(() => {
                                                const readyIds = sub.items.filter(i => i.status === 'READY').map(i => i.id);
                                                if (readyIds.length > 1) {
                                                    return (
                                                        <div style={{ marginTop: 8, padding: '0 4px' }}>
                                                            <button
                                                                className="btn btn-secondary btn-sm"
                                                                onClick={() => openVehicleModal(readyIds)}
                                                                disabled={actionLoading}
                                                            >
                                                                Назначить машину для всех ({readyIds.length})
                                                            </button>
                                                        </div>
                                                    );
                                                }
                                                return null;
                                            })()}
                                        </div>
                                    ))}
                                </div>
                            ))}
                        </div>
                    )}
                </>
            ) : (
                /* History tab */
                historyLoading ? (
                    <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
                ) : historyItems.length === 0 ? (
                    <div className="glass-card" style={{ padding: 64, textAlign: 'center' }}>
                        <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>Нет отправок</div>
                        <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                            Здесь появятся отгруженные и принятые WB заявки
                        </div>
                    </div>
                ) : (
                    <div className="glass-card" style={{ overflow: 'auto' }}>
                        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--color-border)', fontSize: 13, color: 'var(--color-text-muted)' }}>
                            Всего: {historyTotal}
                        </div>
                        <table className="data-table" style={{ fontSize: 13 }}>
                            <thead>
                                <tr>
                                    <th>Статус</th>
                                    <th>№</th>
                                    <th>Поставка WB</th>
                                    <th>Статус WB</th>
                                    <th>Склад забора</th>
                                    <th>Склад сдачи</th>
                                    <th>Дата отгрузки</th>
                                    <th>Дата сдачи</th>
                                    <th>План сдачи</th>
                                    <th style={{ textAlign: 'right' }}>Палеты</th>
                                    <th style={{ textAlign: 'right' }}>Вес</th>
                                </tr>
                            </thead>
                            <tbody>
                                {historyItems.map(item => {
                                    const statusCfg = STATUS_MAP[item.status] || { label: item.status, className: '' };
                                    const wbStatusCfg = item.wb_fbo_status ? WB_STATUS_MAP[item.wb_fbo_status] : null;
                                    return (
                                        <tr
                                            key={item.id}
                                            style={{ cursor: 'pointer' }}
                                            onClick={() => window.location.href = `/p/${slug}/warehouse/assembly/${item.id}`}
                                        >
                                            <td>
                                                <span className={`badge ${statusCfg.className}`}>{statusCfg.label}</span>
                                            </td>
                                            <td style={{ fontWeight: 500 }}>
                                                {item.number}
                                            </td>
                                            <td style={{ fontFamily: 'monospace', fontSize: 12 }}>
                                                {item.wb_supply_id_wb || '\u2014'}
                                            </td>
                                            <td>
                                                {wbStatusCfg ? (
                                                    <span className={`badge ${wbStatusCfg.className}`}>{wbStatusCfg.label}</span>
                                                ) : '\u2014'}
                                            </td>
                                            <td style={{ color: 'var(--color-text-muted)' }}>
                                                {item.warehouse_name || '\u2014'}
                                            </td>
                                            <td style={{ color: 'var(--color-text-muted)' }}>
                                                {item.wb_warehouse_name || '\u2014'}
                                            </td>
                                            <td>{formatDate(item.shipped_at)}</td>
                                            <td>{item.wb_fbo_actual_date ? formatDate(item.wb_fbo_actual_date) : '\u2014'}</td>
                                            <td>{item.wb_fbo_planned_date ? formatDate(item.wb_fbo_planned_date) : '\u2014'}</td>
                                            <td style={{ textAlign: 'right' }}>{item.pallets_count}</td>
                                            <td style={{ textAlign: 'right' }}>
                                                {item.total_weight_kg ? formatNumber(item.total_weight_kg, 1) + ' кг' : '\u2014'}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )
            )}

            {/* Floating action bar */}
            {checkedIds.size > 0 && (
                <div style={{
                    position: 'fixed',
                    bottom: 0,
                    left: 0,
                    right: 0,
                    background: 'rgba(30, 30, 30, 0.95)',
                    backdropFilter: 'blur(8px)',
                    color: '#fff',
                    padding: '12px 24px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    zIndex: 1000,
                    borderTop: '1px solid rgba(255,255,255,0.1)',
                }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14 }}>
                        <span style={{ color: 'var(--color-success)' }}>&#10003;</span>
                        Выбрано: {checkedItems.length} заявки &middot; {checkedPallets} палет &middot; {checkedWeight > 0 ? formatNumber(checkedWeight, 0) : 0} кг
                    </div>
                    <button
                        className="btn btn-primary"
                        onClick={() => openVehicleModal(Array.from(checkedIds))}
                        disabled={actionLoading}
                    >
                        Назначить машину
                    </button>
                </div>
            )}

            {/* Vehicle modal */}
            {showVehicleModal && (
                <div className="modal-overlay" onClick={() => setShowVehicleModal(false)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 960, width: '95vw', maxHeight: '90vh', overflow: 'auto', background: '#fff' }}>
                        <h2 className="modal-title">Назначить машину</h2>

                        {/* Block 1: Common vehicle data */}
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 16 }}>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label">Описание машины *</label>
                                <input className="form-input" value={vehicleInfo} onChange={e => setVehicleInfo(e.target.value)} placeholder="Номер, водитель, ТК..." autoFocus />
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label">Марка машины *</label>
                                <input className="form-input" value={vehicleBrand} onChange={e => setVehicleBrand(e.target.value)} placeholder="ГАЗ-330, КАМАЗ..." />
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label">Телефон водителя *</label>
                                <input className="form-input" value={driverPhone} onChange={e => setDriverPhone(e.target.value)} placeholder="+7 999 123-45-67" />
                            </div>
                        </div>

                        {/* Block 2: Per-request params */}
                        <div style={{ borderTop: '1px solid var(--color-border)', paddingTop: 12 }}>
                            <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 8 }}>Параметры по заявкам ({selectedIds.length})</div>
                            <div style={{ maxHeight: 300, overflow: 'auto' }}>
                                <table className="data-table" style={{ fontSize: 13, marginBottom: 0 }}>
                                    <thead>
                                        <tr>
                                            <th>Заявка</th>
                                            <th>Склад WB</th>
                                            <th>Дата забора *</th>
                                            <th>Интервал *</th>
                                            <th>Стоимость, ₽ *</th>
                                            <th>Дата сдачи *</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {selectedIds.map(id => {
                                            const item = items.find(i => i.id === id);
                                            const p = perRequestParams[id] || { pickup_date: '', pickup_time_slot: '', pickup_cost: '', delivery_date: '' };
                                            return (
                                                <tr key={id}>
                                                    <td style={{ fontWeight: 500, whiteSpace: 'nowrap' }}>{item?.number || `#${id}`}</td>
                                                    <td style={{ fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>{item?.wb_warehouse_name || '\u2014'}</td>
                                                    <td><input className="form-input" type="date" value={p.pickup_date} onChange={e => updateParam(id, 'pickup_date', e.target.value)} style={{ minWidth: 130, padding: '4px 6px', fontSize: 13 }} /></td>
                                                    <td>
                                                        <select className="form-input" value={p.pickup_time_slot} onChange={e => updateParam(id, 'pickup_time_slot', e.target.value)} style={{ minWidth: 110, padding: '4px 6px', fontSize: 13 }}>
                                                            <option value="">...</option>
                                                            <option value="08:00-12:00">08-12</option>
                                                            <option value="12:00-16:00">12-16</option>
                                                            <option value="16:00-20:00">16-20</option>
                                                            <option value="20:00-00:00">20-00</option>
                                                        </select>
                                                    </td>
                                                    <td><input className="form-input" type="number" min={0} value={p.pickup_cost} onChange={e => updateParam(id, 'pickup_cost', e.target.value ? Number(e.target.value) : '')} placeholder="0" style={{ minWidth: 80, padding: '4px 6px', fontSize: 13 }} /></td>
                                                    <td><input className="form-input" type="date" value={p.delivery_date} onChange={e => updateParam(id, 'delivery_date', e.target.value)} style={{ minWidth: 130, padding: '4px 6px', fontSize: 13 }} /></td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                            <button className="btn btn-secondary" onClick={() => setShowVehicleModal(false)}>Отмена</button>
                            <button className="btn btn-primary" onClick={handleAssignVehicle} disabled={actionLoading || !vehicleFormValid}>
                                {actionLoading ? 'Назначение...' : `Назначить (${selectedIds.length})`}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

// ─── Grouping helper ─────────────────────────────────────────────────────────

interface SubGroup {
    key: string;
    label: string;
    items: AssemblyRequest[];
}

interface Group {
    key: string;
    label: string;
    items: AssemblyRequest[];
    subGroups: SubGroup[];
}

function groupItems(items: AssemblyRequest[], groupBy: GroupBy): Group[] {
    const level1Key = groupBy === 'wb_warehouse'
        ? (i: AssemblyRequest) => i.wb_warehouse_name || ''
        : (i: AssemblyRequest) => i.warehouse_name || '';

    const level2Key = groupBy === 'wb_warehouse'
        ? (i: AssemblyRequest) => i.warehouse_name || ''
        : (i: AssemblyRequest) => i.wb_warehouse_name || '';

    // Group level 1
    const map1 = new Map<string, AssemblyRequest[]>();
    for (const item of items) {
        const k = level1Key(item);
        const arr = map1.get(k) || [];
        arr.push(item);
        map1.set(k, arr);
    }

    const groups: Group[] = [];
    for (const [key, groupItems] of map1) {
        // Sub-group level 2
        const map2 = new Map<string, AssemblyRequest[]>();
        for (const item of groupItems) {
            const k = level2Key(item);
            const arr = map2.get(k) || [];
            arr.push(item);
            map2.set(k, arr);
        }

        const subGroups: SubGroup[] = [];
        for (const [subKey, subItems] of map2) {
            subGroups.push({ key: subKey, label: subKey, items: subItems });
        }

        groups.push({
            key,
            label: key,
            items: groupItems,
            subGroups,
        });
    }

    return groups;
}

'use client';
import { useCallback, useEffect, useState } from 'react';
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

    // Vehicle modal
    const [showVehicleModal, setShowVehicleModal] = useState(false);
    const [vehicleInfo, setVehicleInfo] = useState('');
    const [selectedIds, setSelectedIds] = useState<number[]>([]);
    const [actionLoading, setActionLoading] = useState(false);

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

    // ─── Grouping ─────────────────────────────────────────────────────────

    const grouped = groupItems(items, groupBy);

    // ─── Summary ──────────────────────────────────────────────────────────

    const totalRequests = items.length;
    const totalPallets = items.reduce((s, i) => s + i.pallets_count, 0);
    const totalWeight = items.reduce((s, i) => s + (i.total_weight_kg || 0), 0);

    // ─── Actions ──────────────────────────────────────────────────────────

    const openVehicleModal = (ids: number[]) => {
        setSelectedIds(ids);
        setVehicleInfo('');
        setShowVehicleModal(true);
    };

    const handleAssignVehicle = async () => {
        if (!vehicleInfo.trim() || selectedIds.length === 0) return;
        setActionLoading(true);
        setError('');
        try {
            if (selectedIds.length === 1) {
                await api.assignVehicle(selectedIds[0], vehicleInfo.trim());
            } else {
                await api.assignVehicleBulk(selectedIds, vehicleInfo.trim());
            }
            setShowVehicleModal(false);
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

            {/* Tab switcher */}
            <div style={{ display: 'flex', gap: 0, marginBottom: 16 }}>
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
                                    onChange={e => setGroupBy(e.target.value as GroupBy)}
                                >
                                    <option value="wb_warehouse">По складу сдачи WB</option>
                                    <option value="warehouse">По складу забора</option>
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
                    ) : (
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

                                                    return (
                                                        <div
                                                            key={item.id}
                                                            className="glass-card"
                                                            style={{
                                                                padding: 16,
                                                                opacity: soon ? 0.5 : 1,
                                                            }}
                                                        >
                                                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                                                                <Link
                                                                    href={`/p/${slug}/warehouse/assembly/${item.id}`}
                                                                    style={{ fontWeight: 600, textDecoration: 'none', color: 'var(--color-text)' }}
                                                                >
                                                                    {item.number}
                                                                </Link>
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
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Статус</th>
                                    <th>№</th>
                                    <th>Склад забора</th>
                                    <th>Склад сдачи</th>
                                    <th>Дата отгрузки</th>
                                    <th style={{ textAlign: 'right' }}>Палеты</th>
                                    <th style={{ textAlign: 'right' }}>Вес</th>
                                </tr>
                            </thead>
                            <tbody>
                                {historyItems.map(item => {
                                    const statusCfg = STATUS_MAP[item.status] || { label: item.status, className: '' };
                                    return (
                                        <tr key={item.id}>
                                            <td>
                                                <span className={`badge ${statusCfg.className}`}>{statusCfg.label}</span>
                                            </td>
                                            <td>
                                                <Link
                                                    href={`/p/${slug}/warehouse/assembly/${item.id}`}
                                                    style={{ fontWeight: 500, textDecoration: 'none', color: 'var(--color-text)' }}
                                                >
                                                    {item.number}
                                                </Link>
                                            </td>
                                            <td style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
                                                {item.warehouse_name || '\u2014'}
                                            </td>
                                            <td style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
                                                {item.wb_warehouse_name || '\u2014'}
                                            </td>
                                            <td>{formatDate(item.shipped_at)}</td>
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

            {/* Vehicle modal */}
            {showVehicleModal && (
                <div className="modal-overlay" onClick={() => setShowVehicleModal(false)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 440 }}>
                        <h2 className="modal-title">Назначить машину</h2>
                        <div style={{ marginBottom: 12, fontSize: 14, color: 'var(--color-text-muted)' }}>
                            Заявок: {selectedIds.length}
                        </div>
                        <div className="form-group" style={{ marginBottom: 16 }}>
                            <label className="form-label">Информация о машине</label>
                            <input
                                className="form-input"
                                value={vehicleInfo}
                                onChange={e => setVehicleInfo(e.target.value)}
                                placeholder="Номер, водитель, ТК..."
                                autoFocus
                            />
                        </div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary" onClick={() => setShowVehicleModal(false)}>
                                Отмена
                            </button>
                            <button
                                className="btn btn-primary"
                                onClick={handleAssignVehicle}
                                disabled={actionLoading || !vehicleInfo.trim()}
                            >
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

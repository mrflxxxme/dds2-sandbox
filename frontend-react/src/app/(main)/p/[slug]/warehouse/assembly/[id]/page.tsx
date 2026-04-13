'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatDateTime, formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { AssemblyHistoryEntry, AssemblyRequest, AssemblyStatus, RefreshFromFboResponse, WbFboSupply } from '@/types/api';

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

// ─── Component ──────────────────────────────────────────────────────────────

export default function AssemblyDetailPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const id = Number(params.id);

    const [assembly, setAssembly] = useState<AssemblyRequest | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [actionLoading, setActionLoading] = useState(false);

    // Modals
    const [showVehicleModal, setShowVehicleModal] = useState(false);
    const [vehicleInfo, setVehicleInfo] = useState('');
    const [vehicleBrand, setVehicleBrand] = useState('');
    const [driverPhone, setDriverPhone] = useState('');
    const [pickupDate, setPickupDate] = useState('');
    const [pickupTimeSlot, setPickupTimeSlot] = useState('');
    const [pickupCost, setPickupCost] = useState<number | ''>('');
    const [deliveryDate, setDeliveryDate] = useState('');
    const [showShipModal, setShowShipModal] = useState(false);
    const [showCancelModal, setShowCancelModal] = useState(false);

    // Refresh from FBO result
    const [refreshResult, setRefreshResult] = useState<RefreshFromFboResponse | null>(null);

    // History
    const [history, setHistory] = useState<AssemblyHistoryEntry[]>([]);

    // FBO supply editing
    const [editingFbo, setEditingFbo] = useState(false);
    const [fboSupplies, setFboSupplies] = useState<WbFboSupply[]>([]);
    const [fboSearchInput, setFboSearchInput] = useState('');
    const [fboDropdownOpen, setFboDropdownOpen] = useState(false);
    const [loadingFboList, setLoadingFboList] = useState(false);
    const fboDropdownRef = useRef<HTMLDivElement>(null);

    // ─── Load ─────────────────────────────────────────────────────────────

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const data = await api.getAssemblyRequest(id);
            setAssembly(data);
            api.getAssemblyHistory(id).then(setHistory).catch(() => {});
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [id]);

    useEffect(() => { load(); }, [load]);

    // Close FBO dropdown on outside click
    useEffect(() => {
        const handler = (e: MouseEvent) => {
            if (fboDropdownRef.current && !fboDropdownRef.current.contains(e.target as Node)) {
                setFboDropdownOpen(false);
            }
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, []);

    // Load FBO supplies when editing
    const loadFboSupplies = useCallback(async () => {
        setLoadingFboList(true);
        try {
            const resp = await api.getFboSupplies({
                status: 'ACTIVE,ON_DELIVERY,IN_PROGRESS,ACCEPTED',
                search: fboSearchInput || undefined,
                limit: 100,
                exclude_with_assembly: true,
            });
            setFboSupplies(resp.items);
        } catch {
            setFboSupplies([]);
        }
        setLoadingFboList(false);
    }, [fboSearchInput]);

    useEffect(() => {
        if (!editingFbo) return;
        const timer = setTimeout(() => { loadFboSupplies(); }, 300);
        return () => clearTimeout(timer);
    }, [editingFbo, loadFboSupplies]);

    const handleFboSave = async (fboId: number | null) => {
        if (!assembly) return;
        const oldFboId = assembly.wb_fbo_supply_id;
        const oldName = assembly.wb_supply_name;
        try {
            setAssembly({ ...assembly, wb_fbo_supply_id: fboId, wb_supply_name: fboId ? (fboSupplies.find(s => s.id === fboId)?.wb_supply_id || String(fboId)) : undefined });
            await api.updateAssemblyRequest(id, { wb_fbo_supply_id: fboId });
            setEditingFbo(false);
            setFboSearchInput('');
            // Reload to get fresh data
            await load();
        } catch (e: unknown) {
            setAssembly({ ...assembly, wb_fbo_supply_id: oldFboId, wb_supply_name: oldName });
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
        }
    };

    // ─── Actions ──────────────────────────────────────────────────────────

    const doAction = async (action: () => Promise<AssemblyRequest>) => {
        setActionLoading(true);
        setError('');
        try {
            const updated = await action();
            setAssembly(updated);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
    };

    const handleStart = () => doAction(() => api.startAssembly(id));
    const handleReady = () => doAction(() => api.markAssemblyReady(id));

    const vehicleFormValid = vehicleInfo.trim() && vehicleBrand.trim() && driverPhone.trim()
        && pickupDate && pickupTimeSlot && pickupCost !== '' && deliveryDate;

    const handleAssignVehicle = async () => {
        if (!vehicleFormValid) return;
        setActionLoading(true);
        setError('');
        try {
            const updated = await api.assignVehicle(id, {
                vehicle_info: vehicleInfo.trim(),
                vehicle_brand: vehicleBrand.trim(),
                driver_phone: driverPhone.trim(),
                pickup_date: pickupDate,
                pickup_time_slot: pickupTimeSlot,
                pickup_cost: Number(pickupCost),
                delivery_date: deliveryDate,
            });
            setAssembly(updated);
            setShowVehicleModal(false);
            setVehicleInfo('');
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
    };

    const handleShip = async () => {
        setActionLoading(true);
        setError('');
        try {
            const updated = await api.shipAssembly(id);
            setAssembly(updated);
            setShowShipModal(false);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
    };

    const handleCancel = async () => {
        setActionLoading(true);
        setError('');
        try {
            const updated = await api.cancelAssembly(id);
            setAssembly(updated);
            setShowCancelModal(false);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
    };

    const handleRefreshFromFbo = async () => {
        setActionLoading(true);
        setError('');
        setRefreshResult(null);
        try {
            const result = await api.refreshFromFbo(id);
            setRefreshResult(result);
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка обновления из FBO');
        }
        setActionLoading(false);
    };

    const canEditFields = assembly && !['SHIPPED', 'DELIVERED', 'CANCELLED'].includes(assembly.status);
    const canEditAlways = assembly && assembly.status !== 'CANCELLED';
    const canEditFbo = assembly && ['PENDING', 'IN_PROGRESS'].includes(assembly.status);

    const handleFieldSave = async (field: string, value: number | string) => {
        if (!assembly) return;
        const oldAssembly = { ...assembly };
        try {
            const update: Record<string, unknown> = {};
            if (field === 'pallets_count') {
                const newPallets = Number(value);
                const weight = assembly.pallet_weight_kg || 0;
                setAssembly({ ...assembly, pallets_count: newPallets, total_weight_kg: newPallets * weight });
                update.pallets_count = newPallets;
                update.pallet_weight_kg = weight;
            } else if (field === 'pickup_cost') {
                const cost = Number(value);
                setAssembly({ ...assembly, pickup_cost: cost });
                update.pickup_cost = cost;
            } else if (field === 'vehicle_info') {
                setAssembly({ ...assembly, vehicle_info: String(value) });
                update.vehicle_info = String(value);
            } else if (field === 'vehicle_brand') {
                setAssembly({ ...assembly, vehicle_brand: String(value) });
                update.vehicle_brand = String(value);
            } else if (field === 'driver_phone') {
                setAssembly({ ...assembly, driver_phone: String(value) });
                update.driver_phone = String(value);
            } else {
                setAssembly({ ...assembly, [field]: value });
                update[field] = value || undefined;
            }
            await api.updateAssemblyRequest(id, update);
        } catch (e: unknown) {
            setAssembly(oldAssembly);
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
        }
    };

    // ─── Render ───────────────────────────────────────────────────────────

    if (loading) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            </div>
        );
    }

    if (error && !assembly) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-danger)' }}>
                    {error}
                    <div style={{ marginTop: 16 }}>
                        <Link href={`/p/${slug}/warehouse/assembly`}>
                            <button className="btn btn-secondary">Назад к списку</button>
                        </Link>
                    </div>
                </div>
            </div>
        );
    }

    if (!assembly) return null;

    const status = STATUS_MAP[assembly.status] || { label: assembly.status, className: '' };

    // ─── Action buttons by status ─────────────────────────────────────────

    const renderActions = () => {
        const buttons: React.ReactNode[] = [];

        switch (assembly.status) {
            case 'PENDING':
                buttons.push(
                    <button key="start" className="btn btn-primary" onClick={handleStart} disabled={actionLoading}>
                        Начать сборку
                    </button>,
                    <Link key="edit" href={`/p/${slug}/warehouse/assembly/${assembly.id}/edit`}>
                        <button className="btn btn-secondary">Редактировать</button>
                    </Link>,
                    <button key="fbo" className="btn btn-secondary" onClick={handleRefreshFromFbo} disabled={actionLoading}>
                        Из FBO
                    </button>,
                    <button key="cancel" className="btn btn-secondary" onClick={() => setShowCancelModal(true)} disabled={actionLoading}
                        style={{ color: 'var(--color-danger)' }}>
                        Отменить
                    </button>,
                );
                break;
            case 'IN_PROGRESS':
                buttons.push(
                    <button key="ready" className="btn btn-primary" onClick={handleReady} disabled={actionLoading}>
                        Сборка готова
                    </button>,
                    <Link key="edit" href={`/p/${slug}/warehouse/assembly/${assembly.id}/edit`}>
                        <button className="btn btn-secondary">Редактировать</button>
                    </Link>,
                    <button key="fbo" className="btn btn-secondary" onClick={handleRefreshFromFbo} disabled={actionLoading}>
                        Из FBO
                    </button>,
                    <button key="cancel" className="btn btn-secondary" onClick={() => setShowCancelModal(true)} disabled={actionLoading}
                        style={{ color: 'var(--color-danger)' }}>
                        Отменить
                    </button>,
                );
                break;
            case 'READY':
                buttons.push(
                    <button key="vehicle" className="btn btn-primary" onClick={() => setShowVehicleModal(true)} disabled={actionLoading}>
                        Назначить машину
                    </button>,
                    <button key="fbo" className="btn btn-secondary" onClick={handleRefreshFromFbo} disabled={actionLoading}>
                        Из FBO
                    </button>,
                    <button key="cancel" className="btn btn-secondary" onClick={() => setShowCancelModal(true)} disabled={actionLoading}
                        style={{ color: 'var(--color-danger)' }}>
                        Отменить
                    </button>,
                );
                break;
            case 'VEHICLE_ASSIGNED':
                buttons.push(
                    <button key="ship" className="btn btn-primary" onClick={() => setShowShipModal(true)} disabled={actionLoading}>
                        Отгрузить
                    </button>,
                    <button key="fbo" className="btn btn-secondary" onClick={handleRefreshFromFbo} disabled={actionLoading}>
                        Из FBO
                    </button>,
                    <button key="cancel" className="btn btn-secondary" onClick={() => setShowCancelModal(true)} disabled={actionLoading}
                        style={{ color: 'var(--color-danger)' }}>
                        Отменить
                    </button>,
                );
                break;
            case 'SHIPPED':
                // No primary actions for shipped
                break;
            case 'DELIVERED':
                break;
            case 'CANCELLED':
                break;
        }

        return buttons;
    };

    return (
        <div className="animate-in">
            {/* Header */}
            <div className="page-header">
                <div>
                    <Link href={`/p/${slug}/warehouse/assembly`} style={{ color: 'var(--color-text-muted)', textDecoration: 'none', fontSize: 14 }}>
                        &larr; Заявки на сборку
                    </Link>
                    <h1 className="page-title" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        {assembly.number}
                        <span className={`badge ${status.className}`} style={assembly.status === 'SHIPPED' ? { opacity: 0.6 } : undefined}>
                            {status.label}
                        </span>
                    </h1>
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {renderActions()}
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

            {/* Refresh from FBO result */}
            {refreshResult && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                    <div style={{ fontWeight: 500, marginBottom: 8 }}>Результат обновления из FBO:</div>
                    <div style={{ display: 'flex', gap: 16, fontSize: 14 }}>
                        <span>Добавлено: {refreshResult.added}</span>
                        <span>Удалено: {refreshResult.removed}</span>
                        <span>Изменено: {refreshResult.changed}</span>
                    </div>
                    <button className="btn btn-secondary btn-sm" style={{ marginTop: 8 }} onClick={() => setRefreshResult(null)}>
                        Скрыть
                    </button>
                </div>
            )}

            {/* Info card */}
            <div className="glass-card" style={{ padding: 24, marginBottom: 16 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 16 }}>
                    <InfoField label="Склад" value={assembly.warehouse_name || '\u2014'} />
                    {/* FBO supply — editable in PENDING/IN_PROGRESS */}
                    {canEditFbo && editingFbo ? (
                        <div ref={fboDropdownRef} style={{ position: 'relative' }}>
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>FBO поставка</div>
                            <input
                                className="form-input"
                                type="text"
                                placeholder="Поиск поставки..."
                                value={fboDropdownOpen ? fboSearchInput : ''}
                                onChange={e => {
                                    setFboSearchInput(e.target.value);
                                    if (!fboDropdownOpen) setFboDropdownOpen(true);
                                }}
                                onFocus={() => setFboDropdownOpen(true)}
                                autoFocus
                                style={{ fontSize: 13 }}
                            />
                            {fboDropdownOpen && (
                                <div style={{
                                    position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
                                    background: 'var(--color-bg)', border: '1px solid var(--color-border)',
                                    borderRadius: 8, maxHeight: 200, overflowY: 'auto',
                                    boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
                                }}>
                                    {/* Option to clear FBO */}
                                    <div
                                        onClick={() => { handleFboSave(null); setFboDropdownOpen(false); }}
                                        style={{ padding: '8px 12px', cursor: 'pointer', fontSize: 13, color: 'var(--color-text-muted)', fontStyle: 'italic' }}
                                        onMouseEnter={e => (e.currentTarget.style.background = 'var(--color-bg-secondary)')}
                                        onMouseLeave={e => (e.currentTarget.style.background = '')}
                                    >
                                        Без поставки
                                    </div>
                                    {loadingFboList ? (
                                        <div style={{ padding: 12, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
                                    ) : fboSupplies.length === 0 ? (
                                        <div style={{ padding: 12, textAlign: 'center', color: 'var(--color-text-muted)' }}>Поставки не найдены</div>
                                    ) : (
                                        fboSupplies.map(s => (
                                            <div
                                                key={s.id}
                                                onClick={() => { handleFboSave(s.id); setFboDropdownOpen(false); }}
                                                style={{ padding: '8px 12px', cursor: 'pointer', fontSize: 13 }}
                                                onMouseEnter={e => (e.currentTarget.style.background = 'var(--color-bg-secondary)')}
                                                onMouseLeave={e => (e.currentTarget.style.background = '')}
                                            >
                                                <strong>{s.wb_supply_id}</strong> — {s.warehouse_name || 'Без склада'} ({s.total_qty} шт.)
                                            </div>
                                        ))
                                    )}
                                </div>
                            )}
                            <div style={{ marginTop: 4 }}>
                                <button className="btn btn-secondary btn-sm" onClick={() => { setEditingFbo(false); setFboSearchInput(''); }}>
                                    Отмена
                                </button>
                            </div>
                        </div>
                    ) : (
                        <div
                            onClick={canEditFbo ? () => { setEditingFbo(true); } : undefined}
                            style={{ cursor: canEditFbo ? 'pointer' : undefined }}
                            title={canEditFbo ? 'Нажмите для изменения' : undefined}
                            onMouseEnter={canEditFbo ? (e) => { e.currentTarget.style.background = 'rgba(59,130,246,0.08)'; } : undefined}
                            onMouseLeave={canEditFbo ? (e) => { e.currentTarget.style.background = ''; } : undefined}
                        >
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>FBO поставка</div>
                            <div style={{ fontSize: 14, fontWeight: 500, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                                {assembly.wb_fbo_supply_id ? (assembly.wb_supply_name || String(assembly.wb_fbo_supply_id)) : 'Без поставки'}
                                {canEditFbo && (
                                    <span style={{ color: 'var(--color-primary, #3b82f6)', fontSize: 13, opacity: 0.6 }}>&#x270E;</span>
                                )}
                            </div>
                        </div>
                    )}
                    {assembly.wb_fbo_supply_id ? (
                        <InfoField label="Склад WB" value={assembly.wb_warehouse_name || '\u2014'} />
                    ) : (
                        <EditableInfoField
                            label="Склад WB"
                            value={assembly.wb_warehouse_name_manual || ''}
                            displayValue={assembly.wb_warehouse_name_manual || assembly.wb_warehouse_name || '\u2014'}
                            type="text"
                            editable={!!canEditFields}
                            onSave={async (v) => {
                                if (!assembly) return;
                                const old = assembly.wb_warehouse_name_manual;
                                try {
                                    setAssembly({ ...assembly, wb_warehouse_name_manual: v || null });
                                    await api.updateAssemblyRequest(id, { wb_warehouse_name_manual: v || null });
                                } catch (e: unknown) {
                                    setAssembly({ ...assembly, wb_warehouse_name_manual: old });
                                    setError(e instanceof Error ? e.message : 'Ошибка сохранения');
                                }
                            }}
                        />
                    )}
                    <InfoField label="Создана" value={formatDateTime(assembly.created_at)} />
                    <EditableInfoField
                        label="Дата готовности (план)"
                        value={assembly.estimated_ready_date?.slice(0, 10) || ''}
                        displayValue={formatDate(assembly.estimated_ready_date)}
                        type="date"
                        editable={!!canEditFields}
                        onSave={(v) => handleFieldSave('estimated_ready_date', v)}
                    />
                    <InfoField label="Дата готовности (факт)" value={formatDate(assembly.actual_ready_date)} />
                    <EditableInfoField
                        label="Палеты"
                        value={String(assembly.pallets_count || '')}
                        displayValue={String(assembly.pallets_count)}
                        type="number"
                        editable={!!canEditAlways}
                        onSave={(v) => handleFieldSave('pallets_count', Number(v))}
                    />
                    <InfoField label="Вес 1 палеты" value={assembly.pallet_weight_kg ? formatNumber(assembly.pallet_weight_kg, 1) + ' кг' : '\u2014'} />
                    <InfoField label="Общий вес" value={assembly.total_weight_kg ? formatNumber(assembly.total_weight_kg, 1) + ' кг' : '\u2014'} />
                    {(assembly.vehicle_info || canEditAlways) && (
                        <EditableInfoField
                            label="Машина"
                            value={assembly.vehicle_info || ''}
                            displayValue={assembly.vehicle_info || '\u2014'}
                            type="text"
                            editable={!!canEditAlways}
                            onSave={(v) => handleFieldSave('vehicle_info', v)}
                        />
                    )}
                    {(assembly.vehicle_brand || canEditAlways) && (
                        <EditableInfoField
                            label="Марка"
                            value={assembly.vehicle_brand || ''}
                            displayValue={assembly.vehicle_brand || '\u2014'}
                            type="text"
                            editable={!!canEditAlways}
                            onSave={(v) => handleFieldSave('vehicle_brand', v)}
                        />
                    )}
                    {(assembly.driver_phone || canEditAlways) && (
                        <EditableInfoField
                            label="Телефон"
                            value={assembly.driver_phone || ''}
                            displayValue={assembly.driver_phone || '\u2014'}
                            type="text"
                            editable={!!canEditAlways}
                            onSave={(v) => handleFieldSave('driver_phone', v)}
                        />
                    )}
                    {assembly.pickup_date && (
                        <InfoField label="Забор" value={`${formatDate(assembly.pickup_date)}${assembly.pickup_time_slot ? ', ' + assembly.pickup_time_slot : ''}`} />
                    )}
                    <EditableInfoField
                        label="Стоимость"
                        value={String(assembly.pickup_cost ?? '')}
                        displayValue={assembly.pickup_cost != null ? formatNumber(assembly.pickup_cost) + ' \u20BD' : '\u2014'}
                        type="number"
                        editable={!!canEditAlways}
                        onSave={(v) => handleFieldSave('pickup_cost', Number(v))}
                    />
                    {assembly.delivery_date && (
                        <InfoField label="Сдача на WB" value={formatDate(assembly.delivery_date)} />
                    )}
                    {assembly.vehicle_assigned_at && (
                        <InfoField label="Машина назначена" value={formatDateTime(assembly.vehicle_assigned_at)} />
                    )}
                    {assembly.shipped_at && (
                        <InfoField label="Отгружена" value={formatDateTime(assembly.shipped_at)} />
                    )}
                    {assembly.comment && (
                        <div style={{ gridColumn: '1 / -1' }}>
                            <InfoField label="Комментарий" value={assembly.comment} />
                        </div>
                    )}
                </div>
            </div>

            {/* Items table */}
            {(() => {
                const itemCols: Column[] = [
                    { key: 'barcode', label: 'ШК', render: (v: string) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span> },
                    { key: 'product_name', label: 'Товар', render: (v: string) => <span style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block' }}>{v || '\u2014'}</span> },
                    { key: 'quantity', label: 'В поставке', align: 'right', render: (v: number) => <span style={{ fontWeight: 500 }}>{v}</span> },
                    { key: 'stock_quantity', label: 'На складе', align: 'right', render: (v: number, row: any) => <span style={{ fontWeight: 500, color: v < row.quantity ? 'var(--color-danger)' : 'var(--color-success)' }}>{v}</span> },
                ];
                return (
                    <TanStackDataTable
                        columns={itemCols}
                        data={assembly.items || []}
                        title="Позиции"
                        enableSorting
                        enablePagination={false}
                        emptyText="Нет позиций"
                    />
                );
            })()}

            {/* History timeline */}
            {history.length > 0 && (
                <div className="glass-card" style={{ padding: 24, marginTop: 16 }}>
                    <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>
                        История
                    </h2>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
                        {history.map((entry, idx) => {
                            const statusInfo = STATUS_MAP[entry.new_status as AssemblyStatus] || { label: entry.new_status, className: '' };
                            const isLast = idx === history.length - 1;
                            return (
                                <div key={entry.id} style={{ display: 'flex', gap: 12, position: 'relative' }}>
                                    {/* Timeline line */}
                                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: 20 }}>
                                        <div style={{
                                            width: 10, height: 10, borderRadius: '50%',
                                            background: isLast ? 'var(--color-primary)' : 'var(--color-border)',
                                            flexShrink: 0, marginTop: 6,
                                        }} />
                                        {!isLast && (
                                            <div style={{ width: 2, flex: 1, background: 'var(--color-border)' }} />
                                        )}
                                    </div>
                                    {/* Content */}
                                    <div style={{ paddingBottom: isLast ? 0 : 16, flex: 1 }}>
                                        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                                            <span className={`badge ${statusInfo.className}`} style={{ fontSize: 11 }}>
                                                {statusInfo.label}
                                            </span>
                                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                {formatDateTime(entry.changed_at)}
                                            </span>
                                            {entry.changed_by && (
                                                <span style={{ fontSize: 11, color: 'var(--color-text-muted)', fontStyle: 'italic' }}>
                                                    ({entry.changed_by})
                                                </span>
                                            )}
                                        </div>
                                        {entry.comment && (
                                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 4 }}>
                                                {entry.comment}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {/* ─── Modals ──────────────────────────────────────────────────── */}

            {/* Vehicle modal */}
            {showVehicleModal && (
                <div className="modal-overlay" onClick={() => setShowVehicleModal(false)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 520 }}>
                        <h2 className="modal-title">Назначить машину</h2>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                            <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                                <label className="form-label">Описание машины *</label>
                                <input className="form-input" value={vehicleInfo} onChange={e => setVehicleInfo(e.target.value)} placeholder="Номер, водитель, ТК..." autoFocus />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Марка машины *</label>
                                <input className="form-input" value={vehicleBrand} onChange={e => setVehicleBrand(e.target.value)} placeholder="ГАЗ-330, КАМАЗ..." />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Телефон водителя *</label>
                                <input className="form-input" value={driverPhone} onChange={e => setDriverPhone(e.target.value)} placeholder="+7 999 123-45-67" />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Дата забора *</label>
                                <input className="form-input" type="date" value={pickupDate} onChange={e => setPickupDate(e.target.value)} />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Интервал *</label>
                                <select className="form-input" value={pickupTimeSlot} onChange={e => setPickupTimeSlot(e.target.value)}>
                                    <option value="">Выберите...</option>
                                    <option value="08:00-12:00">08:00 — 12:00</option>
                                    <option value="12:00-16:00">12:00 — 16:00</option>
                                    <option value="16:00-20:00">16:00 — 20:00</option>
                                    <option value="20:00-00:00">20:00 — 00:00</option>
                                </select>
                            </div>
                            <div className="form-group">
                                <label className="form-label">Стоимость забора, \u20BD *</label>
                                <input className="form-input" type="number" min={0} value={pickupCost} onChange={e => setPickupCost(e.target.value ? Number(e.target.value) : '')} placeholder="15000" />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Дата сдачи на WB *</label>
                                <input className="form-input" type="date" value={deliveryDate} onChange={e => setDeliveryDate(e.target.value)} />
                            </div>
                        </div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                            <button className="btn btn-secondary" onClick={() => setShowVehicleModal(false)}>Отмена</button>
                            <button className="btn btn-primary" onClick={handleAssignVehicle} disabled={actionLoading || !vehicleFormValid}>
                                {actionLoading ? 'Назначение...' : 'Назначить'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Ship confirmation modal */}
            {showShipModal && (
                <div className="modal-overlay" onClick={() => setShowShipModal(false)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 480 }}>
                        <h2 className="modal-title">Подтверждение отгрузки</h2>
                        <div style={{ marginBottom: 16, fontSize: 14 }}>
                            <p>Вы уверены, что хотите отгрузить заявку <strong>{assembly.number}</strong>?</p>
                            {assembly.vehicle_info && (
                                <p style={{ color: 'var(--color-text-muted)' }}>Машина: {assembly.vehicle_info}</p>
                            )}
                            <p style={{ color: 'var(--color-text-muted)' }}>
                                Позиций: {assembly.items?.length || 0}, палет: {assembly.pallets_count}
                            </p>
                        </div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary" onClick={() => setShowShipModal(false)}>
                                Отмена
                            </button>
                            <button
                                className="btn btn-primary"
                                onClick={handleShip}
                                disabled={actionLoading}
                            >
                                {actionLoading ? 'Отгрузка...' : 'Отгрузить'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Cancel confirmation modal */}
            {showCancelModal && (
                <div className="modal-overlay" onClick={() => setShowCancelModal(false)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 440 }}>
                        <h2 className="modal-title">Отменить заявку</h2>
                        <div style={{ marginBottom: 16, fontSize: 14 }}>
                            <p>Вы уверены, что хотите отменить заявку <strong>{assembly.number}</strong>?</p>
                            <p style={{ color: 'var(--color-danger)' }}>Это действие нельзя отменить.</p>
                        </div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary" onClick={() => setShowCancelModal(false)}>
                                Нет, оставить
                            </button>
                            <button
                                className="btn btn-primary"
                                onClick={handleCancel}
                                disabled={actionLoading}
                                style={{ background: 'var(--color-danger)' }}
                            >
                                {actionLoading ? 'Отмена...' : 'Да, отменить'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

// ─── Helper ──────────────────────────────────────────────────────────────────

function InfoField({ label, value }: { label: string; value: string }) {
    return (
        <div>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 14, fontWeight: 500 }}>{value}</div>
        </div>
    );
}

function EditableInfoField({
    label, value, displayValue, type, editable, onSave,
}: {
    label: string; value: string; displayValue: string;
    type: 'date' | 'number' | 'text'; editable: boolean;
    onSave: (val: string) => void;
}) {
    const [editing, setEditing] = useState(false);
    const [inputVal, setInputVal] = useState(value);
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (editing && inputRef.current) {
            inputRef.current.focus();
            if (type === 'number') inputRef.current.select();
        }
    }, [editing, type]);

    const handleSave = () => {
        setEditing(false);
        if (inputVal !== value) onSave(inputVal);
    };

    return (
        <div>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>{label}</div>
            {editing && editable ? (
                <input
                    ref={inputRef}
                    type={type}
                    min={type === 'number' ? 0 : undefined}
                    value={inputVal}
                    onChange={(e) => setInputVal(e.target.value)}
                    onBlur={handleSave}
                    onKeyDown={(e) => {
                        if (e.key === 'Enter') inputRef.current?.blur();
                        if (e.key === 'Escape') { setInputVal(value); setEditing(false); }
                    }}
                    className="form-input"
                    style={{ width: type === 'number' ? 80 : 160, padding: '4px 8px', fontSize: 14, fontWeight: 500 }}
                />
            ) : (
                <div
                    onClick={editable ? () => { setInputVal(value); setEditing(true); } : undefined}
                    style={{
                        fontSize: 14,
                        fontWeight: 500,
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 6,
                        cursor: editable ? 'pointer' : undefined,
                        padding: editable ? '2px 8px 2px 0' : undefined,
                        borderRadius: editable ? 6 : undefined,
                        transition: 'background 0.15s',
                    }}
                    title={editable ? 'Нажмите для редактирования' : undefined}
                    onMouseEnter={editable ? (e) => { e.currentTarget.style.background = 'rgba(59,130,246,0.08)'; } : undefined}
                    onMouseLeave={editable ? (e) => { e.currentTarget.style.background = ''; } : undefined}
                >
                    {displayValue || '\u2014'}
                    {editable && (
                        <span style={{ color: 'var(--color-primary, #3b82f6)', fontSize: 13, opacity: 0.6 }}>✎</span>
                    )}
                </div>
            )}
        </div>
    );
}

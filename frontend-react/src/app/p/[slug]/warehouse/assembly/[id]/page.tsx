'use client';
import { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatDateTime, formatNumber } from '@/lib/utils';
import type { AssemblyRequest, AssemblyStatus, RefreshFromFboResponse } from '@/types/api';

// ─── Status config ──────────────────────────────────────────────────────────

const STATUS_MAP: Record<AssemblyStatus, { label: string; className: string }> = {
    PENDING:          { label: 'Ожидает сборку',    className: 'badge-warning' },
    IN_PROGRESS:      { label: 'В сборке',          className: 'badge-info' },
    READY:            { label: 'Готово',             className: 'badge-success' },
    VEHICLE_ASSIGNED: { label: 'Машина назначена',   className: 'badge-info' },
    SHIPPED:          { label: 'Отгружена',          className: 'badge-success' },
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
    const [showShipModal, setShowShipModal] = useState(false);
    const [showCancelModal, setShowCancelModal] = useState(false);

    // Refresh from FBO result
    const [refreshResult, setRefreshResult] = useState<RefreshFromFboResponse | null>(null);

    // ─── Load ─────────────────────────────────────────────────────────────

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const data = await api.getAssemblyRequest(id);
            setAssembly(data);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [id]);

    useEffect(() => { load(); }, [load]);

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

    const handleAssignVehicle = async () => {
        if (!vehicleInfo.trim()) return;
        setActionLoading(true);
        setError('');
        try {
            const updated = await api.assignVehicle(id, vehicleInfo.trim());
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
                    <Link key="edit" href={`/p/${slug}/warehouse/assembly/new?fbo_supply_id=${assembly.wb_fbo_supply_id}`}>
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
                    <InfoField label="FBO поставка" value={assembly.wb_supply_name || String(assembly.wb_fbo_supply_id)} />
                    <InfoField label="Склад WB" value={assembly.wb_warehouse_name || '\u2014'} />
                    <InfoField label="Создана" value={formatDateTime(assembly.created_at)} />
                    <InfoField label="Дата готовности (план)" value={formatDate(assembly.estimated_ready_date)} />
                    <InfoField label="Дата готовности (факт)" value={formatDate(assembly.actual_ready_date)} />
                    <InfoField label="Палеты" value={String(assembly.pallets_count)} />
                    <InfoField label="Вес 1 палеты" value={assembly.pallet_weight_kg ? formatNumber(assembly.pallet_weight_kg, 1) + ' кг' : '\u2014'} />
                    <InfoField label="Общий вес" value={assembly.total_weight_kg ? formatNumber(assembly.total_weight_kg, 1) + ' кг' : '\u2014'} />
                    {assembly.vehicle_info && (
                        <InfoField label="Машина" value={assembly.vehicle_info} />
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
            <div className="glass-card" style={{ padding: 24 }}>
                <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>
                    Позиции ({assembly.items?.length || 0})
                </h2>

                {!assembly.items || assembly.items.length === 0 ? (
                    <div style={{ textAlign: 'center', padding: 32, color: 'var(--color-text-muted)' }}>
                        Нет позиций
                    </div>
                ) : (
                    <table className="data-table" style={{ fontSize: 13 }}>
                        <thead>
                            <tr>
                                <th>ШК</th>
                                <th>Товар</th>
                                <th style={{ textAlign: 'right' }}>Кол-во</th>
                            </tr>
                        </thead>
                        <tbody>
                            {assembly.items.map(item => (
                                <tr key={item.id}>
                                    <td>
                                        <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{item.barcode}</span>
                                    </td>
                                    <td style={{ color: 'var(--color-text-muted)' }}>
                                        ID: {item.nomenclature_id}
                                    </td>
                                    <td style={{ textAlign: 'right', fontWeight: 500 }}>{item.quantity}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {/* ─── Modals ──────────────────────────────────────────────────── */}

            {/* Vehicle modal */}
            {showVehicleModal && (
                <div className="modal-overlay" onClick={() => setShowVehicleModal(false)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 440 }}>
                        <h2 className="modal-title">Назначить машину</h2>
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

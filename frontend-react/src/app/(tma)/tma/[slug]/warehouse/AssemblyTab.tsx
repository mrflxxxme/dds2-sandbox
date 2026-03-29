'use client';

import { useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { haptic } from '@/lib/telegram';
import type { AssemblyRequest, AssemblyListResponse, AssignVehicleData } from './types';
import {
    STATUS_LABELS, STATUS_COLORS, NEXT_STATUS,
    compactNumber, formatShortDate, totalItems, todayISO,
} from './helpers';

// ─── Vehicle assignment bottom-sheet ─────────────────────────────────────────

function VehicleForm({ onSubmit, onCancel, submitting }: {
    onSubmit: (data: AssignVehicleData) => void;
    onCancel: () => void;
    submitting: boolean;
}) {
    const today = todayISO();
    const [form, setForm] = useState<AssignVehicleData>({
        vehicle_info: '',
        vehicle_brand: '',
        driver_phone: '',
        pickup_date: today,
        pickup_time_slot: '',
        pickup_cost: '0',
        delivery_date: today,
    });

    const set = (key: keyof AssignVehicleData, val: string) =>
        setForm((p) => ({ ...p, [key]: val }));

    const valid = form.vehicle_info && form.driver_phone && form.pickup_date && form.delivery_date;

    return (
        <div className="tma-modal-overlay" onClick={onCancel}>
            <div className="tma-modal" onClick={(e) => e.stopPropagation()}>
                <div className="tma-modal-header">
                    <span className="tma-modal-title">Назначить машину</span>
                    <button className="tma-modal-close" onClick={onCancel}>&times;</button>
                </div>

                <div className="tma-modal-body">
                    <label className="tma-field">
                        <span className="tma-field-label">Транспорт *</span>
                        <input className="tma-input" placeholder="ГАЗель, Фура, и т.д."
                            value={form.vehicle_info} onChange={(e) => set('vehicle_info', e.target.value)} />
                    </label>
                    <label className="tma-field">
                        <span className="tma-field-label">Марка</span>
                        <input className="tma-input" placeholder="МАН, Scania..."
                            value={form.vehicle_brand} onChange={(e) => set('vehicle_brand', e.target.value)} />
                    </label>
                    <label className="tma-field">
                        <span className="tma-field-label">Телефон водителя *</span>
                        <input className="tma-input" type="tel" placeholder="+7..."
                            value={form.driver_phone} onChange={(e) => set('driver_phone', e.target.value)} />
                    </label>

                    <div className="tma-field-row">
                        <label className="tma-field tma-field-half">
                            <span className="tma-field-label">Забор *</span>
                            <input className="tma-input" type="date"
                                value={form.pickup_date} onChange={(e) => set('pickup_date', e.target.value)} />
                        </label>
                        <label className="tma-field tma-field-half">
                            <span className="tma-field-label">Слот</span>
                            <input className="tma-input" placeholder="10:00-14:00"
                                value={form.pickup_time_slot} onChange={(e) => set('pickup_time_slot', e.target.value)} />
                        </label>
                    </div>

                    <div className="tma-field-row">
                        <label className="tma-field tma-field-half">
                            <span className="tma-field-label">Доставка *</span>
                            <input className="tma-input" type="date"
                                value={form.delivery_date} onChange={(e) => set('delivery_date', e.target.value)} />
                        </label>
                        <label className="tma-field tma-field-half">
                            <span className="tma-field-label">Стоимость</span>
                            <input className="tma-input" type="number" inputMode="decimal" placeholder="0"
                                value={form.pickup_cost} onChange={(e) => set('pickup_cost', e.target.value)} />
                        </label>
                    </div>
                </div>

                <div className="tma-modal-footer">
                    <button className="tma-btn tma-btn-secondary" onClick={onCancel}>Отмена</button>
                    <button
                        className="tma-btn tma-btn-primary"
                        disabled={!valid || submitting}
                        onClick={() => onSubmit(form)}
                    >
                        {submitting ? 'Сохраняю...' : 'Назначить'}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ─── Pallets inline editor ───────────────────────────────────────────────────

function PalletsEditor({ req, onSave }: {
    req: AssemblyRequest;
    onSave: (id: number, pallets: number, weight: number) => Promise<void>;
}) {
    const [editing, setEditing] = useState(false);
    const [pallets, setPallets] = useState(String(req.pallets_count));
    const [weight, setWeight] = useState(String(req.pallet_weight_kg));
    const [saving, setSaving] = useState(false);

    const handleSave = useCallback(async () => {
        setSaving(true);
        try {
            await onSave(req.id, Number(pallets), Number(weight));
            haptic('success');
            setEditing(false);
        } catch {
            haptic('error');
        } finally {
            setSaving(false);
        }
    }, [req.id, pallets, weight, onSave]);

    if (!editing) {
        return (
            <div className="tma-pallets-display" onClick={() => { haptic('light'); setEditing(true); }}>
                <span>{req.pallets_count} палл. &times; {formatNumber(req.pallet_weight_kg, 1)} кг</span>
                <span className="tma-pallets-edit-icon">✏️</span>
            </div>
        );
    }

    return (
        <div className="tma-pallets-edit">
            <div className="tma-field-row">
                <label className="tma-field tma-field-half">
                    <span className="tma-field-label">Паллеты</span>
                    <input className="tma-input tma-input-sm" type="number" inputMode="numeric"
                        value={pallets} onChange={(e) => setPallets(e.target.value)} />
                </label>
                <label className="tma-field tma-field-half">
                    <span className="tma-field-label">Вес (кг)</span>
                    <input className="tma-input tma-input-sm" type="number" inputMode="decimal"
                        value={weight} onChange={(e) => setWeight(e.target.value)} />
                </label>
            </div>
            <div className="tma-pallets-actions">
                <button className="tma-btn-sm tma-btn-secondary" onClick={() => setEditing(false)}>Отмена</button>
                <button className="tma-btn-sm tma-btn-primary" disabled={saving} onClick={handleSave}>
                    {saving ? '...' : 'Сохранить'}
                </button>
            </div>
        </div>
    );
}

// ─── Single assembly card ────────────────────────────────────────────────────

function AssemblyCard({ req, expanded, onToggle, onStatusChange, onUpdatePallets }: {
    req: AssemblyRequest;
    expanded: boolean;
    onToggle: () => void;
    onStatusChange: (id: number, action: string) => void;
    onUpdatePallets: (id: number, pallets: number, weight: number) => Promise<void>;
}) {
    const next = NEXT_STATUS[req.status];
    const itemCount = totalItems(req);

    return (
        <div className={`tma-asm-card ${expanded ? 'tma-asm-expanded' : ''}`}>
            {/* Header — always visible, tap to expand */}
            <div className="tma-asm-header" onClick={() => { haptic('light'); onToggle(); }}>
                <div className="tma-asm-header-left">
                    <div className="tma-asm-number">{req.number}</div>
                    <span className="tma-status-badge" style={{ background: STATUS_COLORS[req.status] }}>
                        {STATUS_LABELS[req.status] || req.status}
                    </span>
                </div>
                <div className="tma-asm-header-right">
                    <div className="tma-asm-qty">{itemCount} шт</div>
                    <span className={`tma-asm-chevron ${expanded ? 'tma-asm-chevron-up' : ''}`}>&#x276F;</span>
                </div>
            </div>

            {/* Summary line */}
            <div className="tma-asm-summary" onClick={() => { haptic('light'); onToggle(); }}>
                {req.wb_supply_id_wb && <span className="tma-asm-tag">{req.wb_supply_id_wb}</span>}
                {req.wb_warehouse_name && <span className="tma-asm-meta">{req.wb_warehouse_name}</span>}
                {req.brands && <span className="tma-asm-meta">{req.brands}</span>}
            </div>

            {/* Expanded details */}
            {expanded && (
                <div className="tma-asm-details">
                    {/* Items */}
                    <div className="tma-asm-section">
                        <div className="tma-asm-section-title">Товары ({req.items.length})</div>
                        {req.items.map((item) => (
                            <div key={item.id} className="tma-asm-item">
                                <div className="tma-asm-item-name">
                                    {item.product_name || item.barcode}
                                    {item.brand && <span className="tma-asm-item-brand">{item.brand}</span>}
                                </div>
                                <div className="tma-asm-item-qty">
                                    {item.quantity} шт
                                    <span className="tma-asm-item-stock">(склад: {item.stock_quantity})</span>
                                </div>
                            </div>
                        ))}
                    </div>

                    {/* Pallets */}
                    <div className="tma-asm-section">
                        <div className="tma-asm-section-title">Паллеты и вес</div>
                        <PalletsEditor req={req} onSave={onUpdatePallets} />
                        {req.total_weight_kg > 0 && (
                            <div className="tma-asm-meta-line">Общий вес: {formatNumber(req.total_weight_kg, 1)} кг</div>
                        )}
                    </div>

                    {/* Vehicle info */}
                    {req.vehicle_info && (
                        <div className="tma-asm-section">
                            <div className="tma-asm-section-title">Транспорт</div>
                            <div className="tma-asm-meta-line">{req.vehicle_info} {req.vehicle_brand || ''}</div>
                            {req.driver_phone && <div className="tma-asm-meta-line">Водитель: {req.driver_phone}</div>}
                            {req.pickup_date && <div className="tma-asm-meta-line">Забор: {formatShortDate(req.pickup_date)} {req.pickup_time_slot || ''}</div>}
                            {req.delivery_date && <div className="tma-asm-meta-line">Доставка: {formatShortDate(req.delivery_date)}</div>}
                            {req.pickup_cost != null && req.pickup_cost > 0 && (
                                <div className="tma-asm-meta-line">Стоимость: {formatNumber(req.pickup_cost, 0)} ₽</div>
                            )}
                        </div>
                    )}

                    {/* Dates */}
                    <div className="tma-asm-section">
                        <div className="tma-asm-section-title">Даты</div>
                        <div className="tma-asm-meta-line">Создана: {formatShortDate(req.created_at)}</div>
                        {req.estimated_ready_date && <div className="tma-asm-meta-line">Готовность (план): {formatShortDate(req.estimated_ready_date)}</div>}
                        {req.actual_ready_date && <div className="tma-asm-meta-line">Готовность (факт): {formatShortDate(req.actual_ready_date)}</div>}
                        {req.shipped_at && <div className="tma-asm-meta-line">Отправлена: {formatShortDate(req.shipped_at)}</div>}
                    </div>

                    {req.comment && (
                        <div className="tma-asm-section">
                            <div className="tma-asm-section-title">Комментарий</div>
                            <div className="tma-asm-meta-line">{req.comment}</div>
                        </div>
                    )}

                    {/* Actions */}
                    {next && (
                        <div className="tma-asm-actions">
                            <button
                                className="tma-btn tma-btn-primary tma-btn-block"
                                onClick={(e) => { e.stopPropagation(); haptic('light'); onStatusChange(req.id, next.action); }}
                            >
                                {next.label}
                            </button>
                            <button
                                className="tma-btn tma-btn-danger tma-btn-sm-text"
                                onClick={(e) => { e.stopPropagation(); haptic('light'); onStatusChange(req.id, 'cancel'); }}
                            >
                                Отменить
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

// ─── Main Assembly Tab ───────────────────────────────────────────────────────

export default function AssemblyTab({ data, loading, error, onRetry, onDataUpdate }: {
    data: AssemblyListResponse | null;
    loading: boolean;
    error: string;
    onRetry: () => void;
    onDataUpdate: () => void;
}) {
    const [expandedId, setExpandedId] = useState<number | null>(null);
    const [vehicleForId, setVehicleForId] = useState<number | null>(null);
    const [actionLoading, setActionLoading] = useState(false);

    const handleStatusChange = useCallback(async (id: number, action: string) => {
        if (action === 'assign') {
            setVehicleForId(id);
            return;
        }

        const confirmActions = ['ship', 'cancel'];
        if (confirmActions.includes(action)) {
            const label = action === 'ship' ? 'Отправить заявку?' : 'Отменить заявку?';
            if (!window.confirm(label)) return;
        }

        setActionLoading(true);
        try {
            await api.request('POST', `/api/v1/warehouse/assembly/${id}/${action === 'cancel' ? 'cancel' : NEXT_STATUS[data?.items.find(r => r.id === id)?.status || '']?.endpoint || action}`);
            haptic('success');
            onDataUpdate();
        } catch (err) {
            haptic('error');
            alert(err instanceof Error ? err.message : 'Ошибка');
        } finally {
            setActionLoading(false);
        }
    }, [data, onDataUpdate]);

    const handleVehicleSubmit = useCallback(async (form: AssignVehicleData) => {
        if (!vehicleForId) return;
        setActionLoading(true);
        try {
            await api.request('POST', `/api/v1/warehouse/assembly/${vehicleForId}/assign-vehicle`, {
                vehicle_info: form.vehicle_info,
                vehicle_brand: form.vehicle_brand,
                driver_phone: form.driver_phone,
                pickup_date: form.pickup_date,
                pickup_time_slot: form.pickup_time_slot,
                pickup_cost: Number(form.pickup_cost) || 0,
                delivery_date: form.delivery_date,
            });
            haptic('success');
            setVehicleForId(null);
            onDataUpdate();
        } catch (err) {
            haptic('error');
            alert(err instanceof Error ? err.message : 'Ошибка');
        } finally {
            setActionLoading(false);
        }
    }, [vehicleForId, onDataUpdate]);

    const handleUpdatePallets = useCallback(async (id: number, pallets: number, weight: number) => {
        await api.request('PUT', `/api/v1/warehouse/assembly/${id}`, {
            pallets_count: pallets,
            pallet_weight_kg: weight,
        });
        onDataUpdate();
    }, [onDataUpdate]);

    if (loading) return <div className="tma-loading"><div className="tma-spinner" /><div className="tma-loading-text">Загрузка...</div></div>;
    if (error) return (
        <div className="tma-empty">
            <div className="tma-empty-icon">⚠️</div>
            <div className="tma-empty-text">{error}</div>
            <button className="tma-btn tma-btn-primary" style={{ marginTop: 16 }} onClick={() => { haptic('light'); onRetry(); }}>Повторить</button>
        </div>
    );
    if (!data || data.items.length === 0) return (
        <div className="tma-empty"><div className="tma-empty-icon">📦</div><div className="tma-empty-text">Нет активных заявок</div></div>
    );

    // Group by status
    const statusOrder = ['PENDING', 'IN_PROGRESS', 'READY', 'VEHICLE_ASSIGNED'];
    const byStatus: Record<string, AssemblyRequest[]> = {};
    for (const req of data.items) {
        if (!byStatus[req.status]) byStatus[req.status] = [];
        byStatus[req.status].push(req);
    }

    const readyCount = (byStatus['READY']?.length || 0) + (byStatus['VEHICLE_ASSIGNED']?.length || 0);

    return (
        <>
            {/* Quick stats */}
            <div className="tma-stat-grid">
                <div className="tma-stat-cell">
                    <div className="tma-stat-cell-value">{data.total}</div>
                    <div className="tma-stat-cell-label">Активных</div>
                </div>
                <div className="tma-stat-cell">
                    <div className={`tma-stat-cell-value ${readyCount > 0 ? 'tma-stat-positive' : ''}`}>{readyCount}</div>
                    <div className="tma-stat-cell-label">К отправке</div>
                </div>
            </div>

            {/* Cards grouped by status */}
            {statusOrder.map((status) => {
                const items = byStatus[status];
                if (!items || items.length === 0) return null;
                return (
                    <div key={status} className="tma-asm-group">
                        <div className="tma-asm-group-header">
                            <span className="tma-status-dot" style={{ background: STATUS_COLORS[status] }} />
                            {STATUS_LABELS[status]} ({items.length})
                        </div>
                        {items.map((req) => (
                            <AssemblyCard
                                key={req.id}
                                req={req}
                                expanded={expandedId === req.id}
                                onToggle={() => setExpandedId(expandedId === req.id ? null : req.id)}
                                onStatusChange={handleStatusChange}
                                onUpdatePallets={handleUpdatePallets}
                            />
                        ))}
                    </div>
                );
            })}

            {/* Vehicle modal */}
            {vehicleForId !== null && (
                <VehicleForm
                    onSubmit={handleVehicleSubmit}
                    onCancel={() => setVehicleForId(null)}
                    submitting={actionLoading}
                />
            )}
        </>
    );
}

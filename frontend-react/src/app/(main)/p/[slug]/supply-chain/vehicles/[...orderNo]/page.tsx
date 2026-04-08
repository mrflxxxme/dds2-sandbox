'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate, formatDateTime, exportToExcel } from '@/lib/utils';
import PageHeader from '@/components/PageHeader';
import TabLayout from '@/components/TabLayout';
import type {
    VehicleSchema,
    VehicleStatus,
    VehicleCostSummary,
    VehicleItemSchema,
    AvailableItemGroup,
    AvailableItem,
    VehicleDocument,
    VehicleStatusHistoryEntry,
} from '@/types/api';
import { CONTAINERS } from '@/app/(main)/p/[slug]/container-loader/lib/packer';

// ─── Constants ─────────────────────────────────────────────────────────────

const STATUS_LABELS: Record<VehicleStatus, string> = {
    FORMING: 'Формируется',
    SHIPPED: 'Отгружен',
    CUSTOMS: 'Таможня',
    DISPATCHED: 'Отправлена',
    DELIVERED: 'Принята',
};

const STATUS_COLORS: Record<VehicleStatus, string> = {
    FORMING: '#6b7280',
    SHIPPED: '#3b82f6',
    CUSTOMS: '#f59e0b',
    DISPATCHED: '#8b5cf6',
    DELIVERED: '#22c55e',
};

const STATUSES: VehicleStatus[] = ['FORMING', 'SHIPPED', 'CUSTOMS', 'DISPATCHED', 'DELIVERED'];

const CONTAINER_LABELS: Record<string, string> = {
    truck1: 'Авто 13.5м', truck2: 'Авто 13.6м',
    '20ft': '20 фут', '40ft': '40 фут', '40ft_hc': '40 фут HC',
};

const DOC_TYPE_LABELS: Record<string, string> = {
    invoice: 'Инвойс',
    order: 'Заказ',
    customs_declaration: 'ДТ',
    contract: 'Контракт',
    other: 'Прочее',
};

// ─── Helpers ───────────────────────────────────────────────────────────────

const parseNum = (s: string) => (s || '').trim().replace(/\s/g, '').replace(',', '.').replace(/[^\d.]/g, '');

function StatusBadge({ status }: { status: string }) {
    const s = status as VehicleStatus;
    return (
        <span style={{
            display: 'inline-block', padding: '4px 14px', borderRadius: 12,
            fontSize: 13, fontWeight: 600, color: '#fff',
            background: STATUS_COLORS[s] || '#6b7280',
        }}>
            {STATUS_LABELS[s] || status}
        </span>
    );
}

function Timeline({ status }: { status?: VehicleStatus }) {
    const currentIdx = status ? STATUSES.indexOf(status) : -1;
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 0, marginBottom: 20 }}>
            {STATUSES.map((s, i) => {
                const done = i <= currentIdx;
                const isCurrent = i === currentIdx;
                const color = done ? STATUS_COLORS[s] : 'var(--color-border)';
                return (
                    <React.Fragment key={s}>
                        {i > 0 && <div style={{ width: 40, height: 2, background: done ? STATUS_COLORS[s] : 'var(--color-border)' }} />}
                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                            <div style={{
                                width: isCurrent ? 14 : 10, height: isCurrent ? 14 : 10,
                                borderRadius: '50%', background: color,
                                boxShadow: isCurrent ? `0 0 6px ${color}66` : 'none',
                            }} />
                            <span style={{ fontSize: 10, color: done ? 'var(--color-text)' : 'var(--color-text-muted)', fontWeight: isCurrent ? 600 : 400 }}>
                                {STATUS_LABELS[s]}
                            </span>
                        </div>
                    </React.Fragment>
                );
            })}
        </div>
    );
}

// ─── Shared UI helpers ─────────────────────────────────────────────────────

function InfoField({ label, value, editing, input }: {
    label: string; value?: string | null; editing?: boolean; input?: React.ReactNode;
}) {
    return (
        <div>
            <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginBottom: 2 }}>{label}</div>
            {editing && input ? input : (
                <div style={{ fontSize: 13, fontWeight: 600, color: value ? 'var(--color-text)' : 'var(--color-text-muted)' }}>
                    {value || '—'}
                </div>
            )}
        </div>
    );
}

function SummaryKpi({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
    return (
        <div style={{ textAlign: 'center', padding: '6px 4px', background: accent ? 'rgba(59,130,246,0.04)' : 'var(--color-bg-secondary)', borderRadius: 8 }}>
            <div style={{ fontSize: 9, color: 'var(--color-text-muted)', marginBottom: 2, textTransform: 'uppercase', letterSpacing: '0.3px' }}>{label}</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: accent ? 'var(--color-primary)' : 'var(--color-text)' }}>{value}</div>
        </div>
    );
}

function CostKpi({ label, value, color, pct }: { label: string; value: string; color?: string; pct?: string }) {
    return (
        <div style={{ padding: '12px 16px', borderRadius: 12, border: '1px solid var(--color-border)', background: 'var(--color-bg)' }}>
            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: color || 'var(--color-text)' }}>{value}</div>
            {pct && <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 2 }}>↑ {pct}</div>}
        </div>
    );
}

// ─── Vehicle Info Card (editable header) ───────────────────────────────────

function VehicleInfoCard({ vehicle, containerLabel, totalBoxes, isForming, warehouses, onUpdated }: {
    vehicle: VehicleSchema; containerLabel: string; totalBoxes: number; isForming: boolean; warehouses: { id: number; name: string }[]; onUpdated: () => void;
}) {
    const canEdit = true; // always editable — backend controls field restrictions
    const [editing, setEditing] = useState(false);
    const [saving, setSaving] = useState(false);
    const [form, setForm] = useState({
        invoice_no: vehicle.invoice_no || '',
        dt_number: vehicle.dt_number || '',
        ship_date: vehicle.ship_date || '',
        actual_ship_date: vehicle.actual_ship_date || '',
        delivery_cost_cny: vehicle.delivery_cost_cny ? String(vehicle.delivery_cost_cny) : '',
        estimated_arrival_date: vehicle.estimated_arrival_date || '',
        target_warehouse_id: vehicle.target_warehouse_id ? String(vehicle.target_warehouse_id) : '',
        rate_cny: vehicle.rate_cny ? String(vehicle.rate_cny) : '',
        rate_usd: vehicle.rate_usd ? String(vehicle.rate_usd) : '',
        rate_eur: vehicle.rate_eur ? String(vehicle.rate_eur) : '',
        payment_ref: vehicle.payment_ref || '',
    });

    useEffect(() => {
        setForm({
            invoice_no: vehicle.invoice_no || '',
            dt_number: vehicle.dt_number || '',
            ship_date: vehicle.ship_date || '',
            actual_ship_date: vehicle.actual_ship_date || '',
            delivery_cost_cny: vehicle.delivery_cost_cny ? String(vehicle.delivery_cost_cny) : '',
            estimated_arrival_date: vehicle.estimated_arrival_date || '',
            target_warehouse_id: vehicle.target_warehouse_id ? String(vehicle.target_warehouse_id) : '',
            rate_cny: vehicle.rate_cny ? String(vehicle.rate_cny) : '',
            rate_usd: vehicle.rate_usd ? String(vehicle.rate_usd) : '',
            rate_eur: vehicle.rate_eur ? String(vehicle.rate_eur) : '',
            payment_ref: vehicle.payment_ref || '',
        });
    }, [vehicle]);

    const handleSave = async () => {
        setSaving(true);
        try {
            await api.updateVehicle(vehicle.order_no, {
                invoice_no: form.invoice_no || undefined,
                dt_number: form.dt_number || undefined,
                ship_date: form.ship_date || undefined,
                actual_ship_date: form.actual_ship_date || undefined,
                delivery_cost_cny: form.delivery_cost_cny ? Number(form.delivery_cost_cny) : undefined,
                estimated_arrival_date: form.estimated_arrival_date || undefined,
                target_warehouse_id: form.target_warehouse_id ? Number(form.target_warehouse_id) : undefined,
                rate_cny: form.rate_cny ? Number(form.rate_cny) : undefined,
                rate_usd: form.rate_usd ? Number(form.rate_usd) : undefined,
                rate_eur: form.rate_eur ? Number(form.rate_eur) : undefined,
                payment_ref: form.payment_ref || undefined,
            } as any);
            setEditing(false);
            onUpdated();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка');
        }
        setSaving(false);
    };

    const labelStyle: React.CSSProperties = { fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 2 };
    const valueStyle: React.CSSProperties = { fontSize: 14, fontWeight: 600, color: 'var(--color-text)' };
    const editInput: React.CSSProperties = {
        padding: '4px 8px', borderRadius: 6, border: '1px solid var(--color-border)',
        background: 'var(--color-bg)', color: 'var(--color-text)', fontSize: 13, width: '100%',
    };

    const deliveryCost = Number(vehicle.delivery_cost_cny) > 0
        ? `${formatNumber(Number(vehicle.delivery_cost_cny), 0)} ¥`
        : Number(vehicle.delivery_cost_usd) > 0
        ? `${formatNumber(Number(vehicle.delivery_cost_usd), 0)} $`
        : '—';

    return (
        <div className="glass-card" style={{ padding: '14px 16px', marginBottom: 12 }}>
            {/* Header row with actions */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Информация о машине</div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    {isForming && !editing && vehicle.items.length > 0 && (
                        <>
                            <RecalcButton orderNo={vehicle.order_no} onDone={onUpdated} />
                            <ShipButton orderNo={vehicle.order_no} onDone={onUpdated} />
                        </>
                    )}
                    {vehicle.status === 'SHIPPED' && (
                        <StatusTransitionButton orderNo={vehicle.order_no} nextStatus="CUSTOMS" label="На таможню" icon="🏛" onDone={onUpdated} />
                    )}
                    {vehicle.status === 'CUSTOMS' && (
                        <StatusTransitionButton orderNo={vehicle.order_no} nextStatus="DISPATCHED" label="Отправлена" icon="🚛" onDone={onUpdated} />
                    )}
                    {canEdit && !editing && (
                        <button className="btn btn-secondary btn-sm" onClick={() => setEditing(true)}>Редактировать</button>
                    )}
                    {editing && (
                        <>
                            <button className="btn btn-secondary btn-sm" onClick={() => setEditing(false)}>Отмена</button>
                            <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={saving}>
                                {saving ? '...' : 'Сохранить'}
                            </button>
                        </>
                    )}
                </div>
            </div>

            {/* Fields grid — 5 columns */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '10px 16px' }}>
                <InfoField label="Тип транспорта" value={containerLabel} />
                <InfoField label="Инвойс" value={vehicle.invoice_no} editing={editing}
                    input={<input value={form.invoice_no} onChange={e => setForm(f => ({ ...f, invoice_no: e.target.value }))} placeholder="CC20260011" style={editInput} />} />
                <InfoField label="Номер заказа" value={vehicle.payment_ref} editing={editing}
                    input={<input value={form.payment_ref} onChange={e => setForm(f => ({ ...f, payment_ref: e.target.value }))} placeholder="ENV-001" style={editInput} />} />
                <InfoField label="Номер ДТ" value={vehicle.dt_number} editing={editing}
                    input={<input value={form.dt_number} onChange={e => setForm(f => ({ ...f, dt_number: e.target.value }))} placeholder="—" style={editInput} />} />
                <InfoField label="Перевозка" value={deliveryCost} editing={editing}
                    input={<input value={form.delivery_cost_cny} onChange={e => setForm(f => ({ ...f, delivery_cost_cny: e.target.value }))} placeholder="0" style={editInput} />} />
                <InfoField label="Дата забора (план)" value={vehicle.ship_date ? formatDate(vehicle.ship_date) : undefined} editing={editing}
                    input={<input type="date" value={form.ship_date} onChange={e => setForm(f => ({ ...f, ship_date: e.target.value }))} style={editInput} />} />
                <InfoField label="Отгрузка (факт)" value={vehicle.actual_ship_date ? formatDate(vehicle.actual_ship_date) : undefined} editing={editing}
                    input={<input type="date" value={form.actual_ship_date} onChange={e => setForm(f => ({ ...f, actual_ship_date: e.target.value }))} style={editInput} />} />
                <InfoField label="Прибытие (прогноз)" value={vehicle.estimated_arrival_date ? formatDate(vehicle.estimated_arrival_date) : undefined} editing={editing}
                    input={<input type="date" value={form.estimated_arrival_date} onChange={e => setForm(f => ({ ...f, estimated_arrival_date: e.target.value }))} style={editInput} />} />
                <InfoField label="Склад назначения" value={warehouses.find(w => w.id === vehicle.target_warehouse_id)?.name} editing={editing}
                    input={
                        <select value={form.target_warehouse_id} onChange={e => setForm(f => ({ ...f, target_warehouse_id: e.target.value }))} style={editInput}>
                            <option value="">— Не выбран —</option>
                            {warehouses.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
                        </select>
                    } />
                <InfoField label="Курс ¥/₽" value={Number(vehicle.rate_cny) > 0 ? String(vehicle.rate_cny) : undefined} editing={editing}
                    input={<input value={form.rate_cny} onChange={e => setForm(f => ({ ...f, rate_cny: e.target.value }))} placeholder="12.5" style={editInput} />} />
                <InfoField label="Курс $/₽" value={Number(vehicle.rate_usd) > 0 ? String(vehicle.rate_usd) : undefined} editing={editing}
                    input={<input value={form.rate_usd} onChange={e => setForm(f => ({ ...f, rate_usd: e.target.value }))} placeholder="92" style={editInput} />} />
                <InfoField label="Курс €/₽" value={Number(vehicle.rate_eur) > 0 ? String(vehicle.rate_eur) : undefined} editing={editing}
                    input={<input value={form.rate_eur} onChange={e => setForm(f => ({ ...f, rate_eur: e.target.value }))} placeholder="98" style={editInput} />} />
            </div>

            {/* Summary KPIs */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 8, marginTop: 12, paddingTop: 10, borderTop: '1px solid var(--color-border)' }}>
                <SummaryKpi label="Артикулов" value={String(vehicle.items_count)} />
                <SummaryKpi label="Товаров" value={`${formatNumber(vehicle.total_qty, 0)} шт`} />
                <SummaryKpi label="Мест" value={totalBoxes > 0 ? String(totalBoxes) : '—'} />
                <SummaryKpi label="Вес" value={vehicle.total_weight_kg ? `${formatNumber(Number(vehicle.total_weight_kg), 0)} кг` : '—'} />
                <SummaryKpi label="Стоимость" value={Number(vehicle.total_cny) > 0 ? `${formatNumber(Number(vehicle.total_cny), 0)} ¥` : '—'} accent />
            </div>
        </div>
    );
}

// ─── Main Page ─────────────────────────────────────────────────────────────

export default function VehicleDetailPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const segments = params.orderNo as string[];
    const orderNo = segments.map(decodeURIComponent).join('/');

    const [vehicle, setVehicle] = useState<VehicleSchema | null>(null);
    const [warehouses, setWarehouses] = useState<{ id: number; name: string }[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [tab, setTab] = useState('main');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [data, wh] = await Promise.all([
                api.getVehicle(orderNo),
                api.getWarehouses(),
            ]);
            setVehicle(data);
            setWarehouses(wh);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [orderNo]);

    useEffect(() => { load(); }, [load]);

    const goBack = () => router.push(`/p/${slug}/supply-chain?tab=vehicles`);
    const [deleting, setDeleting] = useState(false);
    const handleDelete = async () => {
        if (!vehicle) return;
        if (!confirm(`Удалить машину ${vehicle.order_no}? Все позиции будут возвращены в доступные.`)) return;
        setDeleting(true);
        try {
            await api.deleteVehicle(vehicle.order_no);
            goBack();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка удаления');
        }
        setDeleting(false);
    };

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}><div className="spinner" style={{ margin: '0 auto 12px' }} />Загрузка...</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error} <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={load}>Повторить</button></div>;
    if (!vehicle) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Машина не найдена <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={goBack}>Назад</button></div>;

    const isForming = vehicle.status === 'FORMING';
    const containerLabel = CONTAINER_LABELS[vehicle.container_type || ''] || vehicle.transport_type || 'AUTO';
    const cs = vehicle.cost_summary;

    // Calculate totals for header
    const totalBoxes = vehicle.items.reduce((sum, item) => {
        if (item.pcs_per_box && item.pcs_per_box > 0) {
            return sum + Math.ceil(item.qty / item.pcs_per_box);
        }
        return sum;
    }, 0);

    return (
        <div className="animate-in">
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 20 }}>
                <button className="btn btn-secondary btn-sm" onClick={goBack} style={{ fontSize: 13 }}>
                    ← Назад
                </button>
                <div style={{ flex: 1 }}>
                    <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>
                        Машина {vehicle.order_no}
                    </h1>
                </div>
                {isForming && (
                    <button className="btn btn-danger btn-sm" onClick={handleDelete} disabled={deleting} style={{ fontSize: 13 }}>
                        {deleting ? 'Удаляем...' : 'Удалить'}
                    </button>
                )}
                <StatusBadge status={vehicle.status || 'FORMING'} />
            </div>

            {/* Timeline */}
            <Timeline status={vehicle.status as VehicleStatus} />

            {/* Tabs */}
            <TabLayout
                tabs={[
                    { key: 'main', label: 'Основное' },
                    { key: 'documents', label: 'Документы' },
                    { key: 'history', label: 'История' },
                ]}
                active={tab}
                onChange={setTab}
            />

            {/* Tab: Main */}
            {tab === 'main' && (
                <>
                    {/* Info card — editable fields + action buttons */}
                    <VehicleInfoCard vehicle={vehicle} containerLabel={containerLabel} totalBoxes={totalBoxes} isForming={isForming} warehouses={warehouses} onUpdated={load} />

                    {/* Cost summary — detailed breakdown */}
                    {cs && (
                        <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
                                <span style={{ fontSize: 16 }}>📊</span> Расчёт себестоимости по позициям
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12, marginBottom: 12 }}>
                                <CostKpi label="Кол-во (шт)" value={formatNumber(vehicle.total_qty, 0)} />
                                <CostKpi label="Себестоимость" value={`${formatNumber(Number(cs.total_cost_rub))} ₽`} color="var(--color-success)" pct={Number(cs.total_rub) > 0 ? `${((Number(cs.total_cost_rub) / Number(cs.total_rub)) * 100).toFixed(1)}%` : undefined} />
                                <CostKpi label="Доставка" value={`${formatNumber(Number(cs.total_delivery_rub))} ₽`} color="var(--color-warning)" pct={Number(cs.total_rub) > 0 ? `${((Number(cs.total_delivery_rub) / Number(cs.total_rub)) * 100).toFixed(1)}%` : undefined} />
                                <CostKpi label="Пошлина" value={`${formatNumber(Number(cs.total_duty_rub))} ₽`} color="var(--color-warning)" pct={Number(cs.total_rub) > 0 ? `${((Number(cs.total_duty_rub) / Number(cs.total_rub)) * 100).toFixed(1)}%` : undefined} />
                                <CostKpi label="НДС" value={`${formatNumber(Number(cs.total_vat_rub))} ₽`} color="var(--color-danger)" pct={Number(cs.total_rub) > 0 ? `${((Number(cs.total_vat_rub) / Number(cs.total_rub)) * 100).toFixed(1)}%` : undefined} />
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
                                <div style={{ padding: 16, borderRadius: 12, background: 'linear-gradient(135deg, rgba(255,182,193,0.15), rgba(255,105,180,0.08))', textAlign: 'center' }}>
                                    <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}>
                                        <span style={{ fontSize: 13 }}>🔥</span> ИТОГО
                                    </div>
                                    <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--color-success)' }}>
                                        {formatNumber(Number(cs.total_rub))} ₽
                                    </div>
                                </div>
                                <CostKpi label="Курс ¥/₽" value={String(vehicle.rate_cny)} />
                                <CostKpi label="Доставка ¥" value={Number(vehicle.delivery_cost_cny) > 0 ? `${formatNumber(Number(vehicle.delivery_cost_cny), 0)}` : '—'} />
                                <CostKpi label="Вес" value={vehicle.total_weight_kg ? `${formatNumber(Number(vehicle.total_weight_kg), 0)} кг` : '—'} />
                            </div>
                        </div>
                    )}

                    {/* Capacity bar */}
                    <CapacityBar vehicle={vehicle} />

                    {/* Items table (TOP) */}
                    <div className="glass-card" style={{ padding: 16, overflow: 'auto' }}>
                        <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>
                            Позиции ({vehicle.items.length})
                        </h3>
                        {vehicle.items.length === 0 ? (
                            <div style={{ textAlign: 'center', padding: 24, opacity: 0.5 }}>Нет позиций. Добавьте товары ниже.</div>
                        ) : (
                            <ItemsTable items={vehicle.items} isForming={isForming} vehicleOrderNo={vehicle.order_no} totalQty={vehicle.total_qty} totalCny={vehicle.total_cny} onRemoved={load} />
                        )}
                    </div>

                    {/* Add items (FORMING only) — collapsible */}
                    {isForming && (
                        <CollapsibleAddItems vehicleOrderNo={vehicle.order_no} onAdded={load} />
                    )}
                </>
            )}

            {/* Tab: Documents */}
            {tab === 'documents' && (
                <DocumentsTab orderNo={vehicle.order_no} />
            )}

            {/* Tab: History */}
            {tab === 'history' && (
                <HistoryTab orderNo={vehicle.order_no} />
            )}
        </div>
    );
}

// ─── Items Table ───────────────────────────────────────────────────────────

function parseBoxDims(boxSize: string | undefined): { l: number; w: number; h: number } | null {
    if (!boxSize) return null;
    const parts = boxSize.split(/[*xXхХ×]/).map(Number);
    if (parts.length === 3 && parts.every(p => p > 0)) return { l: parts[0], w: parts[1], h: parts[2] };
    return null;
}

function ItemsTable({ items, isForming, vehicleOrderNo, totalQty, totalCny, onRemoved }: {
    items: VehicleItemSchema[]; isForming: boolean; vehicleOrderNo: string;
    totalQty: number; totalCny: number; onRemoved: () => void;
}) {
    const [removingId, setRemovingId] = useState<number | null>(null);
    const [perUnit, setPerUnit] = useState(false); // false = общая, true = за 1 шт
    const hasCosts = items.some(i => i.total_rub);

    const handleRemove = async (itemId: number) => {
        if (!confirm('Удалить позицию из машины?')) return;
        setRemovingId(itemId);
        try {
            await api.removeItemFromVehicle(vehicleOrderNo, itemId);
            onRemoved();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка');
        }
        setRemovingId(null);
    };

    const handleExport = () => {
        const rows = items.map(item => {
            const ppb = item.pcs_per_box || 0;
            const boxes = ppb > 0 ? Math.ceil(item.qty / ppb) : 0;
            const dims = parseBoxDims(item.box_size);
            const vol = dims && ppb > 0 ? (dims.l * dims.w * dims.h) / 1e6 * boxes : 0;
            return {
                'Баркод': item.barcode,
                'Артикул': item.article_seller || '',
                'Категория': item.subject || '',
                'Кол-во': item.qty,
                'Мест': boxes || '',
                'Вес 1шт, кг': item.weight_kg || '',
                'Вес общ, кг': item.weight_kg ? Number(((item.weight_kg || 0) * item.qty).toFixed(1)) : '',
                'Коробка': item.box_size || '',
                'V м³': vol > 0 ? Number(vol.toFixed(2)) : '',
                'Цена ¥': Number(item.price_cny) || '',
                'Сумма ¥': Number(item.price_cny) * item.qty || '',
                'Доставка ₽': item.delivery_rub ? Number((item.delivery_rub * item.qty).toFixed(0)) : '',
                'Пошлина ₽': item.duty_rub ? Number((item.duty_rub * item.qty).toFixed(0)) : '',
                'НДС ₽': item.vat_rub ? Number((item.vat_rub * item.qty).toFixed(0)) : '',
                'Итого ₽': item.total_rub ? Number((item.total_rub * item.qty).toFixed(0)) : '',
            };
        });
        exportToExcel(rows, `Машина_${vehicleOrderNo}`);
    };

    const th: React.CSSProperties = { textAlign: 'left', padding: '8px 6px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500, whiteSpace: 'nowrap' };
    const thR: React.CSSProperties = { ...th, textAlign: 'right' };
    const thF: React.CSSProperties = { ...thR, background: 'rgba(59,130,246,0.04)' };
    const td: React.CSSProperties = { padding: '8px 6px', fontSize: 13 };
    const tdR: React.CSSProperties = { ...td, textAlign: 'right' };
    const tdF: React.CSSProperties = { ...tdR, background: 'rgba(59,130,246,0.02)' };

    // Totals
    let totalBoxes = 0, totalWeight = 0, totalVolume = 0;
    let totalRub = 0, totalDelivery = 0, totalDuty = 0, totalVat = 0;
    for (const item of items) {
        const ppb = item.pcs_per_box || 0;
        if (ppb > 0) totalBoxes += Math.ceil(item.qty / ppb);
        totalWeight += (item.weight_kg || 0) * item.qty;
        const dims = parseBoxDims(item.box_size);
        if (dims && ppb > 0) totalVolume += (dims.l * dims.w * dims.h) / 1e6 * Math.ceil(item.qty / ppb);
        if (item.total_rub) totalRub += item.total_rub * item.qty;
        if (item.delivery_rub) totalDelivery += item.delivery_rub * item.qty;
        if (item.duty_rub) totalDuty += item.duty_rub * item.qty;
        if (item.vat_rub) totalVat += item.vat_rub * item.qty;
    }

    const costCols = hasCosts ? 4 : 0;
    const baseCols = 11 + (isForming ? 1 : 0);

    return (
        <>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
            <button className="btn btn-secondary btn-sm" onClick={handleExport} style={{ fontSize: 12 }}>
                📊 Excel
            </button>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            {/* Column group headers */}
            {hasCosts && (
                <colgroup>
                    <col span={baseCols} />
                    <col span={costCols} style={{ background: 'rgba(59,130,246,0.02)' }} />
                </colgroup>
            )}
            <thead>
                {hasCosts && (
                    <tr>
                        <th colSpan={11} style={{ padding: '4px 6px', fontSize: 10, color: 'var(--color-text-muted)', borderBottom: 'none', fontWeight: 400 }}>Товар и логистика</th>
                        <th colSpan={costCols} style={{ padding: '4px 6px', fontSize: 10, color: 'var(--color-primary)', borderBottom: 'none', fontWeight: 500, textAlign: 'center', background: 'rgba(59,130,246,0.04)' }}>
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
                                <span>Себестоимость (₽)</span>
                                <div style={{ display: 'inline-flex', borderRadius: 6, overflow: 'hidden', border: '1px solid #cbd5e1' }}>
                                    <button onClick={() => setPerUnit(false)}
                                        style={{ fontSize: 11, padding: '3px 12px', border: 'none', cursor: 'pointer', fontWeight: 600,
                                            background: !perUnit ? '#3b82f6' : '#f1f5f9',
                                            color: !perUnit ? '#fff' : '#64748b' }}>
                                        Общая
                                    </button>
                                    <button onClick={() => setPerUnit(true)}
                                        style={{ fontSize: 11, padding: '3px 12px', border: 'none', borderLeft: '1px solid #cbd5e1', cursor: 'pointer', fontWeight: 600,
                                            background: perUnit ? '#3b82f6' : '#f1f5f9',
                                            color: perUnit ? '#fff' : '#64748b' }}>
                                        За 1 шт
                                    </button>
                                </div>
                            </div>
                        </th>
                        {isForming && <th style={{ borderBottom: 'none' }} />}
                    </tr>
                )}
                <tr style={{ borderBottom: '2px solid var(--color-border)' }}>
                    <th style={th}>Баркод</th>
                    <th style={th}>Артикул</th>
                    <th style={th}>Категория</th>
                    <th style={thR}>Кол-во</th>
                    <th style={thR}>Мест</th>
                    <th style={thR}>Вес 1шт</th>
                    <th style={thR}>Вес общ</th>
                    <th style={th}>Коробка</th>
                    <th style={thR}>V м³</th>
                    <th style={thR}>Цена ¥</th>
                    <th style={thR}>Сумма ¥</th>
                    {hasCosts && <th style={thF}>Доставка</th>}
                    {hasCosts && <th style={thF}>Пошлина</th>}
                    {hasCosts && <th style={thF}>НДС</th>}
                    {hasCosts && <th style={{ ...thF, fontWeight: 600 }}>Итого</th>}
                    {isForming && <th style={{ width: 28 }} />}
                </tr>
            </thead>
            <tbody>
                {items.map(item => {
                    const ppb = item.pcs_per_box || 0;
                    const boxes = ppb > 0 ? Math.ceil(item.qty / ppb) : 0;
                    const notFull = ppb > 0 && item.qty % ppb !== 0;
                    const dims = parseBoxDims(item.box_size);
                    const boxVol = dims ? (dims.l * dims.w * dims.h) / 1e6 : 0;
                    const itemTotalVol = boxVol * boxes;
                    const weight = item.weight_kg ? item.weight_kg * item.qty : 0;
                    const boxLabel = item.box_size ? `${item.box_size}${ppb ? ` (${ppb}шт)` : ''}` : '—';

                    return (
                        <tr key={item.id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                            <td style={{ ...td, fontFamily: 'monospace', fontSize: 12 }}>{item.barcode}</td>
                            <td style={{ ...td, fontSize: 12, maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={item.article_seller || ''}>{item.article_seller || '—'}</td>
                            <td style={{ ...td, fontSize: 12 }}>{item.subject || '—'}</td>
                            <td style={{ ...tdR, fontWeight: 600 }}>{formatNumber(item.qty, 0)}</td>
                            <td style={tdR}>
                                {boxes > 0 ? (
                                    <span style={{ color: notFull ? '#f59e0b' : 'inherit', fontWeight: notFull ? 600 : 400 }}>
                                        {boxes}{notFull && ' ⚠'}
                                    </span>
                                ) : '—'}
                            </td>
                            <td style={{ ...tdR, color: 'var(--color-text-muted)' }}>{item.weight_kg ? formatNumber(item.weight_kg, 2) : '—'}</td>
                            <td style={tdR}>{weight > 0 ? formatNumber(weight, 1) : '—'}</td>
                            <td style={{ ...td, fontSize: 11, color: 'var(--color-text-muted)' }}>{boxLabel}</td>
                            <td style={{ ...tdR, color: 'var(--color-text-muted)' }}>{itemTotalVol > 0 ? itemTotalVol.toFixed(2) : '—'}</td>
                            <td style={tdR}>{formatNumber(item.price_cny, 2)}</td>
                            <td style={tdR}>{formatNumber(item.qty * item.price_cny, 0)}</td>
                            {hasCosts && <td style={tdF}>{formatNumber(perUnit ? (item.delivery_rub || 0) : (item.delivery_rub || 0) * item.qty, perUnit ? 2 : 0)}</td>}
                            {hasCosts && <td style={tdF}>{formatNumber(perUnit ? (item.duty_rub || 0) : (item.duty_rub || 0) * item.qty, perUnit ? 2 : 0)}</td>}
                            {hasCosts && <td style={tdF}>{formatNumber(perUnit ? (item.vat_rub || 0) : (item.vat_rub || 0) * item.qty, perUnit ? 2 : 0)}</td>}
                            {hasCosts && <td style={{ ...tdF, fontWeight: 700 }}>{formatNumber(perUnit ? (item.total_rub || 0) : (item.total_rub || 0) * item.qty, perUnit ? 2 : 0)}</td>}
                            {isForming && (
                                <td style={{ ...td, textAlign: 'center' }}>
                                    <button onClick={() => handleRemove(item.id)} disabled={removingId === item.id}
                                        style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-danger)', fontSize: 14, opacity: removingId === item.id ? 0.3 : 0.6, padding: 2 }}
                                        title="Удалить">✕</button>
                                </td>
                            )}
                        </tr>
                    );
                })}
            </tbody>
            <tfoot>
                <tr style={{ borderTop: '2px solid var(--color-border)', fontWeight: 600, whiteSpace: 'nowrap' }}>
                    <td colSpan={3} style={{ padding: '12px 6px', fontSize: 13 }}>Итого</td>
                    <td style={{ padding: '12px 6px', textAlign: 'right', fontSize: 13 }}>{formatNumber(totalQty, 0)}</td>
                    <td style={{ padding: '12px 6px', textAlign: 'right' }}>{totalBoxes || ''}</td>
                    <td />
                    <td style={{ padding: '12px 6px', textAlign: 'right' }}>{totalWeight > 0 ? formatNumber(totalWeight, 0) : ''} кг</td>
                    <td />
                    <td style={{ padding: '12px 6px', textAlign: 'right' }}>{totalVolume > 0 ? totalVolume.toFixed(2) : ''}</td>
                    <td />
                    <td style={{ padding: '12px 6px', textAlign: 'right', whiteSpace: 'nowrap' }}>{formatNumber(Number(totalCny), 0)} ¥</td>
                    {hasCosts && <td style={{ ...tdF, padding: '12px 6px', whiteSpace: 'nowrap' }}>{perUnit ? '' : `${formatNumber(totalDelivery, 0)} ₽`}</td>}
                    {hasCosts && <td style={{ ...tdF, padding: '12px 6px', whiteSpace: 'nowrap' }}>{perUnit ? '' : `${formatNumber(totalDuty, 0)} ₽`}</td>}
                    {hasCosts && <td style={{ ...tdF, padding: '12px 6px', whiteSpace: 'nowrap' }}>{perUnit ? '' : `${formatNumber(totalVat, 0)} ₽`}</td>}
                    {hasCosts && <td style={{ ...tdF, padding: '12px 6px', fontWeight: 700, fontSize: 15, color: 'var(--color-primary)', whiteSpace: 'nowrap' }}>{perUnit ? '' : `${formatNumber(totalRub, 0)} ₽`}</td>}
                    {isForming && <td />}
                </tr>
            </tfoot>
        </table>
        </>
    );
}

// ─── Capacity Bar ──────────────────────────────────────────────────────────

function CapacityBar({ vehicle }: { vehicle: VehicleSchema }) {
    const containerKey = vehicle.container_type || 'truck1';
    const container = CONTAINERS[containerKey];
    if (!container) return null;

    const containerVol = container.l * container.w * container.h;
    let usedVol = 0;
    for (const item of vehicle.items) {
        if (item.box_size && item.pcs_per_box && item.pcs_per_box > 0) {
            const parts = item.box_size.split(/[*xх×]/).map(Number);
            if (parts.length === 3 && parts.every(p => p > 0)) {
                const boxVol = (parts[0] * parts[1] * parts[2]) / 1e6;
                const boxes = Math.ceil(item.qty / item.pcs_per_box);
                usedVol += boxVol * boxes;
            }
        }
    }
    if (usedVol === 0 && !vehicle.total_volume_m3) return null;
    const totalVol = usedVol || Number(vehicle.total_volume_m3 || 0);
    const pct = Math.min(100, (totalVol / containerVol) * 100);
    const color = pct > 85 ? '#22c55e' : pct > 60 ? '#f59e0b' : '#3b82f6';

    return (
        <div className="glass-card" style={{ padding: 12, marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>
                {totalVol.toFixed(1)} / {containerVol.toFixed(1)} m³ ({container.name})
            </div>
            <div style={{ height: 8, borderRadius: 4, background: 'var(--color-border)', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 4, transition: 'width 0.3s' }} />
            </div>
            <div style={{ fontSize: 11, color, fontWeight: 600, marginTop: 2 }}>{pct.toFixed(1)}%</div>
        </div>
    );
}

// ─── Add Items Section (two modes: list + paste) ──────────────────────────

interface PasteRow { barcode: string; qty: string; article: string }
const emptyRow = (): PasteRow => ({ barcode: '', qty: '', article: '' });

function calcBoxes(qty: number, pcsPerBox: number | undefined): { boxes: number; notFull: boolean } {
    if (!pcsPerBox || pcsPerBox <= 0) return { boxes: 0, notFull: false };
    const boxes = Math.ceil(qty / pcsPerBox);
    const notFull = qty % pcsPerBox !== 0;
    return { boxes, notFull };
}

function CollapsibleAddItems({ vehicleOrderNo, onAdded }: { vehicleOrderNo: string; onAdded: () => void }) {
    const [open, setOpen] = useState(false);
    if (!open) {
        return (
            <div style={{ textAlign: 'center', padding: 16 }}>
                <button className="btn btn-primary" onClick={() => setOpen(true)}>
                    + Добавить товары
                </button>
            </div>
        );
    }
    return (
        <div>
            <AddItemsSection
                vehicleOrderNo={vehicleOrderNo}
                onAdded={() => { onAdded(); setOpen(false); }}
                onPartialAdded={onAdded}
            />
            <div style={{ textAlign: 'center', marginTop: 8 }}>
                <button className="btn btn-secondary btn-sm" onClick={() => setOpen(false)}>Свернуть</button>
            </div>
        </div>
    );
}

function AddItemsSection({ vehicleOrderNo, onAdded, onPartialAdded }: { vehicleOrderNo: string; onAdded: () => void; onPartialAdded: () => void }) {
    const [mode, setMode] = useState<'list' | 'paste'>('list');
    const [groups, setGroups] = useState<AvailableItemGroup[]>([]);
    const [selectedOrder, setSelectedOrder] = useState<string>('');
    const [loadingGroups, setLoadingGroups] = useState(true);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState('');

    // List mode state
    const [checked, setChecked] = useState<Record<number, boolean>>({});
    const [quantities, setQuantities] = useState<Record<number, string>>({});

    // Paste mode state
    const [rows, setRows] = useState<PasteRow[]>(Array.from({ length: 5 }, emptyRow));

    useEffect(() => {
        (async () => {
            try {
                const data = await api.getAvailableItems();
                setGroups(data);
                if (data.length > 0) setSelectedOrder(data[0].order_number);
            } catch { /* empty */ }
            setLoadingGroups(false);
        })();
    }, []);

    const selectedGroup = groups.find(g => g.order_number === selectedOrder);
    const itemMap: Record<string, AvailableItem> = {};
    if (selectedGroup) {
        for (const item of selectedGroup.items) {
            itemMap[item.barcode] = item;
        }
    }

    // Cross-order map for paste mode: search across ALL orders (FIFO — first order wins)
    const allItemMap: Record<string, AvailableItem & { source_order: string; source_factory: string }> = {};
    for (const group of groups) {
        for (const item of group.items) {
            if (!(item.barcode in allItemMap)) {
                allItemMap[item.barcode] = {
                    ...item,
                    source_order: group.order_number,
                    source_factory: group.factory_name || '',
                };
            }
        }
    }

    const inputStyle: React.CSSProperties = {
        width: '100%', padding: '8px 12px', borderRadius: 8,
        border: '1px solid var(--color-border)', background: 'var(--color-bg)',
        color: 'var(--color-text)', fontSize: 13,
    };

    const cellInput: React.CSSProperties = {
        width: '100%', background: 'var(--color-bg)',
        border: '1px solid var(--color-border)',
        borderRadius: 6, padding: '6px 8px', fontSize: 13,
        color: 'var(--color-text)',
    };

    // ─── List mode helpers ───
    const listItems = selectedGroup?.items || [];
    const checkedItems = listItems.filter(item => checked[item.id] && (parseInt(quantities[item.id]?.toString() || '0') || 0) > 0);
    const listCanSave = checkedItems.length > 0 && checkedItems.every(item => {
        const qty = parseInt(quantities[item.id]?.toString() || '0') || 0;
        return qty > 0 && qty <= item.remaining_qty;
    });

    const allChecked = listItems.length > 0 && listItems.every(item => checked[item.id]);
    const toggleAll = () => {
        if (allChecked) {
            setChecked({});
            setQuantities({});
        } else {
            const newChecked: Record<number, boolean> = {};
            const newQty: Record<number, string> = {};
            for (const item of listItems) {
                newChecked[item.id] = true;
                newQty[item.id] = String(item.remaining_qty);
            }
            setChecked(newChecked);
            setQuantities(newQty);
        }
    };

    const handleListSubmit = async () => {
        if (!listCanSave) return;
        setSubmitting(true);
        setError('');
        try {
            const items = checkedItems.map(item => ({
                factory_order_item_id: item.id,
                qty: parseInt(quantities[item.id]?.toString() || '0') || 0,
            }));
            await api.addItemsToVehicle(vehicleOrderNo, items);
            setChecked({});
            setQuantities({});
            onAdded();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setSubmitting(false);
    };

    // ─── Paste mode helpers ───
    const resolveArticle = (bc: string): string => {
        const item = allItemMap[bc];
        return item ? (item.article_seller || item.subject || '') : '';
    };

    const updateRow = (idx: number, field: keyof PasteRow, value: string) => {
        setRows(prev => {
            const next = [...prev];
            next[idx] = { ...next[idx], [field]: value };
            if (field === 'barcode' && value.trim()) next[idx].article = resolveArticle(value.trim());
            return next;
        });
        if (idx === rows.length - 1) setRows(prev => [...prev, emptyRow()]);
    };

    const handlePaste = (e: React.ClipboardEvent) => {
        const text = e.clipboardData.getData('text/plain');
        if (!text.includes('\t') && !text.includes('\n')) return;
        e.preventDefault();
        const lines = text.trim().split('\n').map(l => l.split('\t'));
        const newRows: PasteRow[] = [];
        for (const cols of lines) {
            if (cols.length < 1) continue;
            const barcode = cols[0]?.trim() || '';
            const qty = cols.length >= 2 ? parseNum(cols[1]) : '';
            if (barcode) newRows.push({ barcode, qty, article: resolveArticle(barcode) });
        }
        if (newRows.length > 0) setRows([...newRows, emptyRow(), emptyRow()]);
    };

    const filledRows = rows.filter(r => r.barcode.trim());
    const invalidRows = filledRows.filter(r => !(r.barcode.trim() in allItemMap));
    const validRows = filledRows.filter(r => {
        const item = allItemMap[r.barcode.trim()];
        const qty = parseInt(r.qty) || 0;
        return item && qty > 0 && qty <= item.remaining_qty;
    });
    // Allow adding found items even if some are not found (fallback A)
    const pasteCanSave = validRows.length > 0;
    const hasUnfound = invalidRows.length > 0;

    const handlePasteSubmit = async () => {
        if (!pasteCanSave) return;
        setSubmitting(true);
        setError('');
        try {
            const items = validRows.map(r => ({
                factory_order_item_id: allItemMap[r.barcode.trim()].id,
                qty: parseInt(r.qty) || 0,
            }));
            const validBarcodes = new Set(validRows.map(r => r.barcode.trim()));
            await api.addItemsToVehicle(vehicleOrderNo, items);

            // Keep rows that weren't successfully added (unfound + exceeded qty)
            const remainingRows = rows.filter(r => {
                const bc = r.barcode.trim();
                return bc && !validBarcodes.has(bc);
            });

            if (remainingRows.length > 0) {
                setRows([...remainingRows, emptyRow(), emptyRow()]);
                // Refresh vehicle data + available items, but keep section open
                onPartialAdded();
                const freshData = await api.getAvailableItems();
                setGroups(freshData);
            } else {
                setRows(Array.from({ length: 5 }, emptyRow));
                onAdded(); // all done — close section
            }
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setSubmitting(false);
    };

    return (
        <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
            <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>Добавить товары</h3>

            {loadingGroups ? (
                <div style={{ textAlign: 'center', padding: 16 }}><div className="spinner" style={{ margin: '0 auto' }} /></div>
            ) : groups.length === 0 ? (
                <div style={{ textAlign: 'center', padding: 16, opacity: 0.5 }}>Нет доступных фабричных заказов</div>
            ) : (
                <>
                    {/* Order selector — only for list mode (paste searches all orders) */}
                    {mode === 'list' && (
                        <div style={{ marginBottom: 12 }}>
                            <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>Фабричный заказ</label>
                            <select value={selectedOrder} onChange={e => { setSelectedOrder(e.target.value); setChecked({}); setQuantities({}); }} style={inputStyle}>
                                {groups.map(g => (
                                    <option key={g.order_number} value={g.order_number}>
                                        {g.order_number}{g.factory_name ? ` — ${g.factory_name}` : ''} ({g.items.length} поз.)
                                    </option>
                                ))}
                            </select>
                        </div>
                    )}

                    {/* Mode tabs */}
                    <div style={{ display: 'flex', gap: 4, marginBottom: 12 }}>
                        {(['list', 'paste'] as const).map(m => (
                            <button key={m} onClick={() => setMode(m)}
                                style={{
                                    padding: '6px 16px', borderRadius: 8, fontSize: 13, fontWeight: 500, cursor: 'pointer',
                                    border: mode === m ? '2px solid var(--color-primary)' : '1px solid var(--color-border)',
                                    background: mode === m ? 'var(--color-primary-bg)' : 'var(--color-bg)',
                                    color: mode === m ? 'var(--color-primary)' : 'var(--color-text)',
                                }}>
                                {m === 'list' ? '📋 Выбрать из списка' : '📎 Вставить из буфера'}
                            </button>
                        ))}
                    </div>

                    {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 8 }}>{error}</div>}

                    {/* ═══ LIST MODE ═══ */}
                    {mode === 'list' && (
                        <>
                            {listItems.length === 0 ? (
                                <div style={{ textAlign: 'center', padding: 16, opacity: 0.5 }}>Нет доступных позиций</div>
                            ) : (
                                <div style={{ overflow: 'auto', maxHeight: 400 }}>
                                    <table className="data-table" style={{ marginBottom: 0, fontSize: 13 }}>
                                        <thead>
                                            <tr>
                                                <th style={{ width: 36, textAlign: 'center' }}>
                                                    <input type="checkbox" checked={allChecked} onChange={toggleAll} title="Выбрать все" />
                                                </th>
                                                <th>Баркод</th>
                                                <th>Артикул</th>
                                                <th>Категория</th>
                                                <th style={{ textAlign: 'right' }}>Шт/кор</th>
                                                <th style={{ textAlign: 'right' }}>Доступно</th>
                                                <th style={{ width: 90 }}>Кол-во</th>
                                                <th style={{ textAlign: 'right' }}>Мест</th>
                                                <th style={{ textAlign: 'right' }}>Цена ¥</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {listItems.map(item => {
                                                const isChecked = !!checked[item.id];
                                                const qty = parseInt(quantities[item.id]?.toString() || '0') || 0;
                                                const { boxes, notFull } = calcBoxes(qty, item.pcs_per_box);
                                                const exceeds = qty > item.remaining_qty;
                                                return (
                                                    <tr key={item.id} style={{ background: isChecked ? 'rgba(59,130,246,0.04)' : undefined }}>
                                                        <td style={{ textAlign: 'center' }}>
                                                            <input type="checkbox" checked={isChecked}
                                                                onChange={e => setChecked(prev => ({ ...prev, [item.id]: e.target.checked }))} />
                                                        </td>
                                                        <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{item.barcode}</td>
                                                        <td style={{ fontSize: 12 }}>{item.article_seller || '—'}</td>
                                                        <td style={{ fontSize: 12 }}>{item.subject || '—'}</td>
                                                        <td style={{ textAlign: 'right', fontSize: 12, color: 'var(--color-text-muted)' }}>{item.pcs_per_box || '—'}</td>
                                                        <td style={{ textAlign: 'right', fontSize: 12 }}>{item.remaining_qty}</td>
                                                        <td>
                                                            <input type="number" min={0} max={item.remaining_qty}
                                                                value={quantities[item.id] || ''}
                                                                onChange={e => {
                                                                    setQuantities(prev => ({ ...prev, [item.id]: e.target.value }));
                                                                    if (parseInt(e.target.value) > 0) setChecked(prev => ({ ...prev, [item.id]: true }));
                                                                }}
                                                                placeholder="0"
                                                                style={{ ...cellInput, width: 80, borderColor: exceeds ? '#f59e0b' : 'var(--color-border)' }} />
                                                        </td>
                                                        <td style={{ textAlign: 'right', fontSize: 12 }}>
                                                            {qty > 0 && item.pcs_per_box ? (
                                                                <span style={{ color: notFull ? '#f59e0b' : 'var(--color-text)' }}>
                                                                    {boxes}
                                                                    {notFull && <span title="Не кратно коробке" style={{ marginLeft: 2 }}>⚠</span>}
                                                                </span>
                                                            ) : '—'}
                                                        </td>
                                                        <td style={{ textAlign: 'right', fontSize: 12 }}>{formatNumber(parseFloat(item.price_cny))}</td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--color-border)' }}>
                                <button className="btn btn-primary" onClick={handleListSubmit} disabled={!listCanSave || submitting}
                                    style={{ minWidth: 160, opacity: listCanSave ? 1 : 0.5 }}>
                                    {submitting ? 'Добавляем...' : `Добавить (${checkedItems.length})`}
                                </button>
                            </div>
                        </>
                    )}

                    {/* ═══ PASTE MODE ═══ */}
                    {mode === 'paste' && (
                        <>
                            <div style={{ padding: 10, marginBottom: 12, fontSize: 13, color: 'var(--color-text-muted)', background: 'var(--color-bg-secondary)', borderRadius: 8 }}>
                                Скопируйте из Excel и вставьте (Ctrl+V). Формат: <b>Баркод, Кол-во</b>.
                            </div>

                            {invalidRows.length > 0 && (
                                <div style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 8, padding: 10, marginBottom: 12, fontSize: 13, color: '#ef4444' }}>
                                    Не найдено ни в одном заказе: <b>{invalidRows.length}</b>
                                </div>
                            )}

                            <div style={{ overflow: 'auto', maxHeight: 360 }} onPaste={handlePaste}>
                                <table className="data-table" style={{ marginBottom: 0, fontSize: 13 }}>
                                    <thead>
                                        <tr>
                                            <th style={{ width: 36, textAlign: 'center' }}>#</th>
                                            <th style={{ minWidth: 140 }}>Артикул</th>
                                            <th>Категория</th>
                                            <th style={{ minWidth: 160 }}>Баркод</th>
                                            <th style={{ textAlign: 'right' }}>Шт/кор</th>
                                            <th style={{ textAlign: 'right' }}>Доступно</th>
                                            <th style={{ width: 90 }}>Кол-во</th>
                                            <th style={{ textAlign: 'right' }}>Мест</th>
                                            <th>Заказ</th>
                                            <th style={{ width: 50, textAlign: 'center' }}></th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {rows.map((row, i) => {
                                            const bc = row.barcode.trim();
                                            const item = allItemMap[bc];
                                            const unknown = bc && !item;
                                            const qty = parseInt(row.qty) || 0;
                                            const exceeds = item && qty > item.remaining_qty;
                                            const { boxes, notFull } = calcBoxes(qty, item?.pcs_per_box);
                                            return (
                                                <tr key={i} style={{ background: unknown ? 'rgba(239,68,68,0.06)' : exceeds ? 'rgba(245,158,11,0.06)' : undefined }}>
                                                    <td style={{ fontSize: 12, color: 'var(--color-text-muted)', textAlign: 'center' }}>{i + 1}</td>
                                                    <td style={{ fontSize: 12 }}>{row.article || (bc ? '—' : '')}</td>
                                                    <td style={{ fontSize: 12 }}>{item?.subject || (bc ? '—' : '')}</td>
                                                    <td>
                                                        <input value={row.barcode} onChange={e => updateRow(i, 'barcode', e.target.value)}
                                                            placeholder="Баркод"
                                                            style={{ ...cellInput, borderColor: unknown ? '#ef4444' : 'var(--color-border)', color: unknown ? '#ef4444' : 'var(--color-text)', fontFamily: 'monospace' }}
                                                            autoComplete="off" />
                                                    </td>
                                                    <td style={{ fontSize: 12, textAlign: 'right', color: 'var(--color-text-muted)' }}>
                                                        {item?.pcs_per_box || (bc ? '—' : '')}
                                                    </td>
                                                    <td style={{ fontSize: 12, textAlign: 'right', color: 'var(--color-text-muted)' }}>
                                                        {item ? item.remaining_qty : bc ? '—' : ''}
                                                    </td>
                                                    <td>
                                                        <input type="number" value={row.qty} onChange={e => updateRow(i, 'qty', e.target.value)}
                                                            placeholder="0" min={0}
                                                            style={{ ...cellInput, borderColor: exceeds ? '#f59e0b' : 'var(--color-border)' }}
                                                            autoComplete="off" />
                                                    </td>
                                                    <td style={{ fontSize: 12, textAlign: 'right' }}>
                                                        {qty > 0 && item?.pcs_per_box ? (
                                                            <span style={{ color: notFull ? '#f59e0b' : 'var(--color-text)' }}>
                                                                {boxes}{notFull && <span title="Не кратно коробке"> ⚠</span>}
                                                            </span>
                                                        ) : '—'}
                                                    </td>
                                                    <td style={{ fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
                                                        {item ? item.source_order : ''}
                                                    </td>
                                                    <td style={{ textAlign: 'center' }}>
                                                        {!bc ? '' :
                                                         unknown ? <span style={{ color: '#ef4444', fontSize: 11 }}>✗</span> :
                                                         exceeds ? <span style={{ color: '#f59e0b', fontSize: 11 }}>!</span> :
                                                         notFull && qty > 0 ? <span style={{ color: '#f59e0b', fontSize: 11 }}>⚠</span> :
                                                         qty > 0 ? <span style={{ color: '#22c55e', fontSize: 11 }}>✓</span> : ''}
                                                    </td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>

                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--color-border)' }}>
                                <div style={{ display: 'flex', gap: 8 }}>
                                    <button className="btn btn-secondary btn-sm" onClick={() => { setRows(Array.from({ length: 5 }, emptyRow)); setError(''); }}>
                                        Очистить
                                    </button>
                                    {invalidRows.length > 0 && (
                                        <button className="btn btn-secondary btn-sm" onClick={() => {
                                            const text = invalidRows.map(r => `${r.barcode.trim()}\t${r.qty}`).join('\n');
                                            navigator.clipboard.writeText(text);
                                            alert(`Скопировано ${invalidRows.length} ненайденных баркодов`);
                                        }}>
                                            Скопировать ненайденные ({invalidRows.length})
                                        </button>
                                    )}
                                </div>
                                <button className="btn btn-primary" onClick={handlePasteSubmit} disabled={!pasteCanSave || submitting}
                                    style={{ minWidth: 160, opacity: pasteCanSave ? 1 : 0.5 }}>
                                    {submitting ? 'Добавляем...' : hasUnfound ? `Добавить найденные (${validRows.length})` : `Добавить (${validRows.length})`}
                                </button>
                            </div>
                        </>
                    )}
                </>
            )}
        </div>
    );
}

// ─── Documents Tab ────────────────────────────────────────────────────────

function formatFileSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function DocumentsTab({ orderNo }: { orderNo: string }) {
    const [docs, setDocs] = useState<VehicleDocument[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [uploading, setUploading] = useState(false);
    const [docType, setDocType] = useState('invoice');
    const [note, setNote] = useState('');
    const [file, setFile] = useState<File | null>(null);
    const [deletingId, setDeletingId] = useState<number | null>(null);

    const loadDocs = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setDocs(await api.getVehicleDocuments(orderNo));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [orderNo]);

    useEffect(() => { loadDocs(); }, [loadDocs]);

    const handleUpload = async () => {
        if (!file) return;
        setUploading(true);
        try {
            await api.uploadVehicleDocument(orderNo, file, docType, note || undefined);
            setFile(null);
            setNote('');
            setDocType('invoice');
            loadDocs();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setUploading(false);
    };

    const handleDownload = async (doc: VehicleDocument) => {
        try {
            const blob = await api.downloadVehicleDocument(orderNo, doc.id);
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = doc.filename;
            a.click();
            URL.revokeObjectURL(url);
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка скачивания');
        }
    };

    const handleDelete = async (docId: number) => {
        if (!confirm('Удалить документ?')) return;
        setDeletingId(docId);
        try {
            await api.deleteVehicleDocument(orderNo, docId);
            loadDocs();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка удаления');
        }
        setDeletingId(null);
    };

    const inputStyle: React.CSSProperties = {
        padding: '8px 12px', borderRadius: 8,
        border: '1px solid var(--color-border)', background: 'var(--color-bg)',
        color: 'var(--color-text)', fontSize: 13,
    };

    return (
        <div>
            {/* Upload area */}
            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 12 }}>
                    Загрузить документ
                </div>
                <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
                    <div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Тип документа</div>
                        <select value={docType} onChange={e => setDocType(e.target.value)} style={inputStyle}>
                            {Object.entries(DOC_TYPE_LABELS).map(([k, v]) => (
                                <option key={k} value={k}>{v}</option>
                            ))}
                        </select>
                    </div>
                    <div style={{ flex: 1, minWidth: 200 }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Файл</div>
                        <input type="file" onChange={e => setFile(e.target.files?.[0] || null)} style={{ fontSize: 13 }} />
                    </div>
                    <div style={{ flex: 1, minWidth: 150 }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Заметка</div>
                        <input value={note} onChange={e => setNote(e.target.value)} placeholder="Необязательно" style={{ ...inputStyle, width: '100%' }} />
                    </div>
                    <button className="btn btn-primary btn-sm" onClick={handleUpload} disabled={!file || uploading}>
                        {uploading ? 'Загрузка...' : 'Загрузить'}
                    </button>
                </div>
            </div>

            {/* Documents list */}
            <div className="glass-card" style={{ padding: 20 }}>
                {loading ? (
                    <div style={{ textAlign: 'center', padding: 32 }}><div className="spinner" style={{ margin: '0 auto' }} /></div>
                ) : error ? (
                    <div style={{ color: 'var(--color-danger)', padding: 16 }}>{error}</div>
                ) : docs.length === 0 ? (
                    <div style={{ textAlign: 'center', padding: 32, color: 'var(--color-text-muted)' }}>Нет документов</div>
                ) : (
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                        <thead>
                            <tr style={{ borderBottom: '2px solid var(--color-border)' }}>
                                <th style={{ textAlign: 'left', padding: '8px 6px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>Файл</th>
                                <th style={{ textAlign: 'left', padding: '8px 6px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>Тип</th>
                                <th style={{ textAlign: 'right', padding: '8px 6px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>Размер</th>
                                <th style={{ textAlign: 'left', padding: '8px 6px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>Заметка</th>
                                <th style={{ textAlign: 'left', padding: '8px 6px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>Дата</th>
                                <th style={{ width: 100, padding: '8px 6px' }} />
                            </tr>
                        </thead>
                        <tbody>
                            {docs.map(doc => (
                                <tr key={doc.id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                    <td style={{ padding: '8px 6px', fontWeight: 500 }}>{doc.filename}</td>
                                    <td style={{ padding: '8px 6px' }}>
                                        <span style={{
                                            display: 'inline-block', padding: '2px 10px', borderRadius: 12,
                                            fontSize: 11, fontWeight: 500, background: 'var(--color-bg-secondary)',
                                            color: 'var(--color-text-muted)',
                                        }}>
                                            {DOC_TYPE_LABELS[doc.doc_type] || doc.doc_type}
                                        </span>
                                    </td>
                                    <td style={{ padding: '8px 6px', textAlign: 'right', color: 'var(--color-text-muted)' }}>{formatFileSize(doc.file_size)}</td>
                                    <td style={{ padding: '8px 6px', color: 'var(--color-text-muted)', fontSize: 12 }}>{doc.note || '—'}</td>
                                    <td style={{ padding: '8px 6px', color: 'var(--color-text-muted)', fontSize: 12 }}>{formatDate(doc.created_at)}</td>
                                    <td style={{ padding: '8px 6px', textAlign: 'right' }}>
                                        <div style={{ display: 'flex', gap: 4, justifyContent: 'flex-end' }}>
                                            <button className="btn btn-secondary btn-sm" onClick={() => handleDownload(doc)} style={{ fontSize: 11, padding: '3px 8px' }}>
                                                Скачать
                                            </button>
                                            <button className="btn btn-danger btn-sm" onClick={() => handleDelete(doc.id)} disabled={deletingId === doc.id}
                                                style={{ fontSize: 11, padding: '3px 8px', opacity: deletingId === doc.id ? 0.4 : 1 }}>
                                                {deletingId === doc.id ? '...' : 'Удалить'}
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>
        </div>
    );
}

// ─── History Tab ──────────────────────────────────────────────────────────

function HistoryTab({ orderNo }: { orderNo: string }) {
    const [entries, setEntries] = useState<VehicleStatusHistoryEntry[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const loadHistory = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const data = await api.getVehicleHistory(orderNo);
            // Most recent first
            setEntries(data.sort((a, b) => new Date(b.changed_at).getTime() - new Date(a.changed_at).getTime()));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [orderNo]);

    useEffect(() => { loadHistory(); }, [loadHistory]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}><div className="spinner" style={{ margin: '0 auto' }} /></div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;
    if (entries.length === 0) return <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>Нет записей</div>;

    return (
        <div className="glass-card" style={{ padding: 24 }}>
            <div style={{ position: 'relative', paddingLeft: 28 }}>
                {/* Vertical line */}
                <div style={{
                    position: 'absolute', left: 7, top: 8, bottom: 8, width: 2,
                    background: 'var(--color-border)',
                }} />

                {entries.map((entry, i) => {
                    const newStatus = entry.new_status as VehicleStatus;
                    const oldStatus = entry.old_status as VehicleStatus | null;
                    const color = STATUS_COLORS[newStatus] || 'var(--color-text-muted)';

                    return (
                        <div key={entry.id} style={{ position: 'relative', marginBottom: i < entries.length - 1 ? 24 : 0 }}>
                            {/* Dot */}
                            <div style={{
                                position: 'absolute', left: -24, top: 4,
                                width: 12, height: 12, borderRadius: '50%',
                                background: color, border: '2px solid var(--color-bg-card)',
                                boxShadow: `0 0 0 2px ${color}33`,
                            }} />

                            {/* Content */}
                            <div>
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 8 }}>
                                    <span>{formatDateTime(entry.changed_at)}</span>
                                    {entry.changed_by && (
                                        <span style={{ fontSize: 11, padding: '1px 8px', borderRadius: 8, background: 'var(--color-bg-secondary)', color: 'var(--color-text)' }}>
                                            {entry.changed_by}
                                        </span>
                                    )}
                                </div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: entry.comment ? 4 : 0 }}>
                                    {oldStatus && (
                                        <>
                                            <span style={{
                                                display: 'inline-block', padding: '2px 10px', borderRadius: 12,
                                                fontSize: 11, fontWeight: 600, color: '#fff',
                                                background: STATUS_COLORS[oldStatus] || '#6b7280',
                                            }}>
                                                {STATUS_LABELS[oldStatus] || entry.old_status}
                                            </span>
                                            <span style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>→</span>
                                        </>
                                    )}
                                    <span style={{
                                        display: 'inline-block', padding: '2px 10px', borderRadius: 12,
                                        fontSize: 11, fontWeight: 600, color: '#fff',
                                        background: STATUS_COLORS[newStatus] || '#6b7280',
                                    }}>
                                        {STATUS_LABELS[newStatus] || entry.new_status}
                                    </span>
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
    );
}

// ─── Action Buttons ────────────────────────────────────────────────────────

function RecalcButton({ orderNo, onDone }: { orderNo: string; onDone: () => void }) {
    const [loading, setLoading] = useState(false);
    const handleClick = async () => {
        setLoading(true);
        try {
            await api.recalcVehicle(orderNo);
            onDone();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    };
    return (
        <button className="btn btn-secondary btn-sm" onClick={handleClick} disabled={loading}>
            {loading ? 'Пересчёт...' : '🔄 Пересчитать'}
        </button>
    );
}

function ShipButton({ orderNo, onDone }: { orderNo: string; onDone: () => void }) {
    const [loading, setLoading] = useState(false);
    const handleClick = async () => {
        if (!confirm('Отгрузить машину? Будет зафиксирована дата и рассчитана себестоимость.')) return;
        setLoading(true);
        try {
            await api.updateVehicleStatus(orderNo, { status: 'SHIPPED' });
            onDone();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    };
    return (
        <button className="btn btn-primary btn-sm" onClick={handleClick} disabled={loading}>
            {loading ? 'Отгрузка...' : '🚛 Отгрузить'}
        </button>
    );
}

function StatusTransitionButton({ orderNo, nextStatus, label, icon, onDone }: {
    orderNo: string; nextStatus: VehicleStatus; label: string; icon: string; onDone: () => void;
}) {
    const [loading, setLoading] = useState(false);
    const handleClick = async () => {
        let dt_number: string | undefined;
        if (nextStatus === 'CUSTOMS') {
            dt_number = prompt('Номер ДТ (необязательно):') || undefined;
        }
        setLoading(true);
        try {
            await api.updateVehicleStatus(orderNo, { status: nextStatus, dt_number });
            onDone();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    };
    return (
        <button className="btn btn-secondary btn-sm" onClick={handleClick} disabled={loading}>
            {loading ? '...' : `${icon} ${label}`}
        </button>
    );
}

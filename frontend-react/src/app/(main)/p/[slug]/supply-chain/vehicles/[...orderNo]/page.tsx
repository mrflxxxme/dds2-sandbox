'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate, formatDateTime, exportToExcel, calcTotalBoxesWithMix } from '@/lib/utils';
import PageHeader from '@/components/PageHeader';
import TabLayout from '@/components/TabLayout';
import BoxDetailCell, { BoxDetailExpandRow, BoxDropdown } from '@/components/BoxDetailCell';
import type {
    VehicleSchema,
    VehicleStatus,
    VehicleCostSummary,
    VehicleItemSchema,
    VehiclePriceResyncPreview,
    AvailableItemGroup,
    AvailableItem,
    VehicleDocument,
    VehicleStatusHistoryEntry,
    FactoryQtyExceededDetail,
} from '@/types/api';
import { FactoryQtyExceededError } from '@/types/api';
import { CONTAINERS } from '@/app/(main)/p/[slug]/container-loader/lib/packer';
import { LanguageProvider, useT, LanguageToggle } from '../../i18n';

// ─── Types ─────────────────────────────────────────────────────────────────

// Drift state for inline qty edit confirmation (sc17).
// Combines server detail with locally captured oldQty (for revert) and newQty
// (for the extend_plan retry call).
type DriftState = FactoryQtyExceededDetail & { oldQty: number; newQty: number };

// ─── Constants ─────────────────────────────────────────────────────────────

const getStatusLabels = (t: (k: string) => string): Record<VehicleStatus, string> => ({
    FORMING: t('status_forming'),
    SHIPPED: t('status_shipped'),
    CUSTOMS: t('status_customs'),
    DISPATCHED: t('status_dispatched'),
    DELIVERED: t('status_delivered'),
});

const STATUS_COLORS: Record<VehicleStatus, string> = {
    FORMING: '#6b7280',
    SHIPPED: '#3b82f6',
    CUSTOMS: '#f59e0b',
    DISPATCHED: '#8b5cf6',
    DELIVERED: '#22c55e',
};

const STATUSES: VehicleStatus[] = ['FORMING', 'SHIPPED', 'CUSTOMS', 'DISPATCHED', 'DELIVERED'];

const getContainerLabels = (t: (k: string) => string): Record<string, string> => ({
    truck1: t('container_truck1'), truck2: t('container_truck2'),
    '20ft': t('container_20ft'), '40ft': t('container_40ft'), '40ft_hc': t('container_40ft_hc'),
});

const getDocTypeLabels = (t: (k: string) => string): Record<string, string> => ({
    invoice: t('vdetail_doc_type_invoice'),
    order: t('vdetail_doc_type_order'),
    customs_declaration: t('vdetail_doc_type_customs'),
    contract: t('vdetail_doc_type_contract'),
    other: t('vdetail_doc_type_other'),
});

// ─── Helpers ───────────────────────────────────────────────────────────────

const parseNum = (s: string) => (s || '').trim().replace(/\s/g, '').replace(',', '.').replace(/[^\d.]/g, '');

function StatusBadge({ status }: { status: string }) {
    const { t } = useT();
    const STATUS_LABELS = getStatusLabels(t);
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
    const { t } = useT();
    const STATUS_LABELS = getStatusLabels(t);
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

function VehicleInfoCard({ vehicle, containerLabel, totalBoxes, isForming, warehouses, onUpdated, pendingDriftCount = 0 }: {
    vehicle: VehicleSchema; containerLabel: string; totalBoxes: number; isForming: boolean; warehouses: { id: number; name: string }[]; onUpdated: () => void;
    pendingDriftCount?: number;
}) {
    const { t } = useT();
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
        vehicle_name: vehicle.vehicle_name || '',
        plate_number: vehicle.plate_number || '',
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
            vehicle_name: vehicle.vehicle_name || '',
            plate_number: vehicle.plate_number || '',
        });
    }, [vehicle]);

    const handleSave = async () => {
        setSaving(true);
        const num = (s: string) => {
            const n = Number(String(s).replace(',', '.'));
            return isFinite(n) ? n : undefined;
        };
        try {
            await api.updateVehicle(vehicle.order_no, {
                invoice_no: form.invoice_no || undefined,
                dt_number: form.dt_number || undefined,
                ship_date: form.ship_date || undefined,
                actual_ship_date: form.actual_ship_date || undefined,
                delivery_cost_cny: form.delivery_cost_cny ? num(form.delivery_cost_cny) : undefined,
                estimated_arrival_date: form.estimated_arrival_date || undefined,
                target_warehouse_id: form.target_warehouse_id ? Number(form.target_warehouse_id) : undefined,
                rate_cny: form.rate_cny ? num(form.rate_cny) : undefined,
                rate_usd: form.rate_usd ? num(form.rate_usd) : undefined,
                rate_eur: form.rate_eur ? num(form.rate_eur) : undefined,
                payment_ref: form.payment_ref || undefined,
                vehicle_name: form.vehicle_name || undefined,
                plate_number: form.plate_number || undefined,
            } as any);
            setEditing(false);
            onUpdated();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_error'));
        }
        setSaving(false);
    };

    const labelStyle: React.CSSProperties = { fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 2 };
    const valueStyle: React.CSSProperties = { fontSize: 14, fontWeight: 600, color: 'var(--color-text)' };
    const editInput: React.CSSProperties = {
        padding: '4px 8px', borderRadius: 6, border: '1px solid var(--color-border)',
        background: 'var(--color-bg)', color: 'var(--color-text)', fontSize: 13, width: '100%',
    };

    const isRussia = vehicle.country === 'RUSSIA';

    const deliveryCost = isRussia
        ? (Number(vehicle.delivery_cost_rub) > 0
            ? `${formatNumber(Number(vehicle.delivery_cost_rub), 0)} ₽`
            : '—')
        : Number(vehicle.delivery_cost_cny) > 0
        ? `${formatNumber(Number(vehicle.delivery_cost_cny), 0)} ¥`
        : Number(vehicle.delivery_cost_usd) > 0
        ? `${formatNumber(Number(vehicle.delivery_cost_usd), 0)} $`
        : '—';

    return (
        <div className="glass-card" style={{ padding: '14px 16px', marginBottom: 12 }}>
            {/* Header row with actions */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>{t('vdetail_vehicle')}</div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    {!editing && vehicle.items.length > 0 && (
                        <RecalcButton orderNo={vehicle.order_no} onDone={onUpdated} />
                    )}
                    {pendingDriftCount > 0 && (
                        <span className="badge badge-danger" style={{ fontSize: 12 }} title={t('drift_status_blocked_tooltip')}>
                            ⚠ {t('drift_pending_badge').replace('{count}', String(pendingDriftCount))}
                        </span>
                    )}
                    {isForming && !editing && vehicle.items.length > 0 && (
                        <ShipButton orderNo={vehicle.order_no} onDone={onUpdated} disabled={pendingDriftCount > 0} disabledTooltip={t('drift_status_blocked_tooltip')} />
                    )}
                    {vehicle.status === 'SHIPPED' && (
                        <StatusTransitionButton orderNo={vehicle.order_no} nextStatus="CUSTOMS" label={t('transition_to_customs')} icon="🏛" onDone={onUpdated} disabled={pendingDriftCount > 0} disabledTooltip={t('drift_status_blocked_tooltip')} />
                    )}
                    {vehicle.status === 'CUSTOMS' && (
                        <StatusTransitionButton orderNo={vehicle.order_no} nextStatus="DISPATCHED" label={t('transition_dispatched')} icon="🚛" onDone={onUpdated} disabled={pendingDriftCount > 0} disabledTooltip={t('drift_status_blocked_tooltip')} />
                    )}
                    {canEdit && !editing && (
                        <button className="btn btn-secondary btn-sm" onClick={() => setEditing(true)}>{t('btn_edit')}</button>
                    )}
                    {editing && (
                        <>
                            <button className="btn btn-secondary btn-sm" onClick={() => setEditing(false)}>{t('btn_cancel')}</button>
                            <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={saving}>
                                {saving ? '...' : t('btn_save')}
                            </button>
                        </>
                    )}
                </div>
            </div>

            {/* Fields grid — 5 columns (China) / 3 columns (Russia) */}
            <div style={{ display: 'grid', gridTemplateColumns: isRussia ? 'repeat(3, 1fr)' : 'repeat(5, 1fr)', gap: '10px 16px' }}>
                <InfoField label={t('vehicle_form_vehicle_name')} value={vehicle.vehicle_name} editing={editing}
                    input={<input value={form.vehicle_name} onChange={e => setForm(f => ({ ...f, vehicle_name: e.target.value }))} placeholder={t('vehicle_form_vehicle_name_placeholder')} style={editInput} />} />
                <InfoField label={t('vehicle_form_plate_number')} value={vehicle.plate_number} editing={editing}
                    input={<input value={form.plate_number} onChange={e => setForm(f => ({ ...f, plate_number: e.target.value }))} placeholder={t('vehicle_form_plate_placeholder')} style={editInput} />} />
                <InfoField label={t('vdetail_field_container')} value={containerLabel} />
                {!isRussia && (
                    <InfoField label={t('cost_invoice')} value={vehicle.invoice_no} editing={editing}
                        input={<input value={form.invoice_no} onChange={e => setForm(f => ({ ...f, invoice_no: e.target.value }))} placeholder="CC20260011" style={editInput} />} />
                )}
                {!isRussia && (
                    <InfoField label={t('vehicle_form_order_no')} value={vehicle.payment_ref} editing={editing}
                        input={<input value={form.payment_ref} onChange={e => setForm(f => ({ ...f, payment_ref: e.target.value }))} placeholder="ENV-001" style={editInput} />} />
                )}
                {!isRussia && (
                    <InfoField label={t('vdetail_field_customs_no')} value={vehicle.dt_number} editing={editing}
                        input={<input value={form.dt_number} onChange={e => setForm(f => ({ ...f, dt_number: e.target.value }))} placeholder="—" style={editInput} />} />
                )}
                <InfoField label={t('vdetail_field_delivery_cost')} value={deliveryCost} editing={editing}
                    input={<input value={form.delivery_cost_cny} onChange={e => setForm(f => ({ ...f, delivery_cost_cny: e.target.value }))} placeholder="0" style={editInput} />} />
                <InfoField label={t('vehicle_form_pickup_date')} value={vehicle.ship_date ? formatDate(vehicle.ship_date) : undefined} editing={editing}
                    input={<input type="date" value={form.ship_date} onChange={e => setForm(f => ({ ...f, ship_date: e.target.value }))} style={editInput} />} />
                <InfoField label={t('vdetail_field_ship_date')} value={vehicle.actual_ship_date ? formatDate(vehicle.actual_ship_date) : undefined} editing={editing}
                    input={<input type="date" value={form.actual_ship_date} onChange={e => setForm(f => ({ ...f, actual_ship_date: e.target.value }))} style={editInput} />} />
                <InfoField label={t('vdetail_field_arrival')} value={vehicle.estimated_arrival_date ? formatDate(vehicle.estimated_arrival_date) : undefined} editing={editing}
                    input={<input type="date" value={form.estimated_arrival_date} onChange={e => setForm(f => ({ ...f, estimated_arrival_date: e.target.value }))} style={editInput} />} />
                <InfoField label={t('vehicle_form_warehouse')} value={warehouses.find(w => w.id === vehicle.target_warehouse_id)?.name} editing={editing}
                    input={
                        <select value={form.target_warehouse_id} onChange={e => setForm(f => ({ ...f, target_warehouse_id: e.target.value }))} style={editInput}>
                            <option value="">{t('create_order_unselected')}</option>
                            {warehouses.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
                        </select>
                    } />
                {!isRussia && (
                    <InfoField label={t('vehicle_form_rate_cny')} value={Number(vehicle.rate_cny) > 0 ? String(vehicle.rate_cny) : undefined} editing={editing}
                        input={<input value={form.rate_cny} onChange={e => setForm(f => ({ ...f, rate_cny: e.target.value }))} placeholder="12.5" style={editInput} />} />
                )}
                {!isRussia && (
                    <InfoField label={t('vehicle_form_rate_usd')} value={Number(vehicle.rate_usd) > 0 ? String(vehicle.rate_usd) : undefined} editing={editing}
                        input={<input value={form.rate_usd} onChange={e => setForm(f => ({ ...f, rate_usd: e.target.value }))} placeholder="92" style={editInput} />} />
                )}
                {!isRussia && (
                    <InfoField label={t('vehicle_form_rate_eur')} value={Number(vehicle.rate_eur) > 0 ? String(vehicle.rate_eur) : undefined} editing={editing}
                        input={<input value={form.rate_eur} onChange={e => setForm(f => ({ ...f, rate_eur: e.target.value }))} placeholder="98" style={editInput} />} />
                )}
            </div>

            {/* Summary KPIs */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 8, marginTop: 12, paddingTop: 10, borderTop: '1px solid var(--color-border)' }}>
                <SummaryKpi label={t('col_items_count')} value={String(vehicle.items_count)} />
                <SummaryKpi label={t('col_qty')} value={`${formatNumber(vehicle.total_qty, 0)} ${t('unit_pcs')}`} />
                <SummaryKpi label={t('col_boxes')} value={totalBoxes > 0 ? String(totalBoxes) : '—'} />
                <SummaryKpi label={t('cost_weight')} value={vehicle.total_weight_kg ? `${formatNumber(Number(vehicle.total_weight_kg), 0)} kg` : '—'} />
                <SummaryKpi label={t('col_amount')} value={Number(vehicle.total_cny) > 0 ? `${formatNumber(Number(vehicle.total_cny), 0)} ${isRussia ? '₽' : '¥'}` : '—'} accent />
            </div>
        </div>
    );
}

// ─── Main Page ─────────────────────────────────────────────────────────────

export default function VehicleDetailPage() {
    return (
        <LanguageProvider>
            <VehicleDetailContent />
        </LanguageProvider>
    );
}

function VehicleDetailContent() {
    const { t } = useT();
    const CONTAINER_LABELS = getContainerLabels(t);
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
    // Drift state per item (sc17). Pending = unsaved drift confirmation needed.
    // Set by QtyEditCell on FactoryQtyExceededError; cleared on extend / revert.
    const [driftStateById, setDriftStateById] = useState<Record<number, DriftState>>({});

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
            setError(e instanceof Error ? e.message : t('msg_loading_error'));
        }
        setLoading(false);
    }, [orderNo, t]);

    useEffect(() => { load(); }, [load]);

    const goBack = () => router.push(`/p/${slug}/supply-chain?tab=vehicles`);
    const [deleting, setDeleting] = useState(false);
    const handleDelete = async () => {
        if (!vehicle) return;
        if (!confirm(`${t('vdetail_confirm_delete')} ${vehicle.order_no}?`)) return;
        setDeleting(true);
        try {
            await api.deleteVehicle(vehicle.order_no);
            goBack();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_delete_error'));
        }
        setDeleting(false);
    };

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}><div className="spinner" style={{ margin: '0 auto 12px' }} />{t('msg_loading')}</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error} <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={load}>{t('btn_retry')}</button></div>;
    if (!vehicle) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>{t('vdetail_not_found')} <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={goBack}>{t('vdetail_back')}</button></div>;

    const isForming = vehicle.status === 'FORMING';
    const containerLabel = CONTAINER_LABELS[vehicle.container_type || ''] || vehicle.transport_type || 'AUTO';
    const cs = vehicle.cost_summary;

    // Calculate totals for header (mix groups deduplicated by max boxes)
    const totalBoxes = calcTotalBoxesWithMix(vehicle.items);

    return (
        <div className="animate-in">
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 20 }}>
                <button className="btn btn-secondary btn-sm" onClick={goBack} style={{ fontSize: 13 }}>
                    {t('vdetail_back')}
                </button>
                <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>
                        {t('vdetail_vehicle')} {vehicle.vehicle_name || vehicle.order_no}
                    </h1>
                    {vehicle.plate_number && (
                        <span className="badge badge-secondary" style={{ fontSize: 12 }}>
                            🚛 {vehicle.plate_number}
                        </span>
                    )}
                    {vehicle.country === 'RUSSIA' && (
                        <span className="badge badge-info" style={{ fontSize: 12 }}>
                            {t('country_russia')}
                        </span>
                    )}
                </div>
                <LanguageToggle />
                {isForming && (
                    <button className="btn btn-danger btn-sm" onClick={handleDelete} disabled={deleting} style={{ fontSize: 13 }}>
                        {deleting ? t('vdetail_deleting') : t('btn_delete')}
                    </button>
                )}
                <StatusBadge status={vehicle.status || 'FORMING'} />
            </div>

            {/* Timeline */}
            <Timeline status={vehicle.status as VehicleStatus} />

            {/* Tabs */}
            <TabLayout
                tabs={[
                    { key: 'main', label: t('vdetail_tab_items') },
                    { key: 'documents', label: t('vdetail_tab_docs') },
                    { key: 'history', label: t('vdetail_tab_history') },
                ]}
                active={tab}
                onChange={setTab}
            />

            {/* Tab: Main */}
            {tab === 'main' && (
                <>
                    {/* Info card — editable fields + action buttons */}
                    <VehicleInfoCard
                        vehicle={vehicle}
                        containerLabel={containerLabel}
                        totalBoxes={totalBoxes}
                        isForming={isForming}
                        warehouses={warehouses}
                        onUpdated={load}
                        pendingDriftCount={Object.keys(driftStateById).length}
                    />

                    {/* Cost summary — detailed breakdown */}
                    {cs && (
                        <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
                                <span style={{ fontSize: 16 }}>📊</span> {t('vdetail_tab_cost')}
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: vehicle.country === 'RUSSIA' ? 'repeat(3, 1fr)' : 'repeat(5, 1fr)', gap: 12, marginBottom: 12 }}>
                                <CostKpi label={`${t('col_qty')} (${t('unit_pcs')})`} value={formatNumber(vehicle.total_qty, 0)} />
                                <CostKpi label={t('cost_goods')} value={`${formatNumber(Number(cs.total_cost_rub))} ₽`} color="var(--color-success)" pct={Number(cs.total_rub) > 0 ? `${((Number(cs.total_cost_rub) / Number(cs.total_rub)) * 100).toFixed(1)}%` : undefined} />
                                <CostKpi label={t('cost_delivery')} value={`${formatNumber(Number(cs.total_delivery_rub))} ₽`} color="var(--color-warning)" pct={Number(cs.total_rub) > 0 ? `${((Number(cs.total_delivery_rub) / Number(cs.total_rub)) * 100).toFixed(1)}%` : undefined} />
                                {vehicle.country !== 'RUSSIA' && (
                                    <CostKpi label={t('cost_duty')} value={`${formatNumber(Number(cs.total_duty_rub))} ₽`} color="var(--color-warning)" pct={Number(cs.total_rub) > 0 ? `${((Number(cs.total_duty_rub) / Number(cs.total_rub)) * 100).toFixed(1)}%` : undefined} />
                                )}
                                {vehicle.country !== 'RUSSIA' && (
                                    <CostKpi label={t('cost_vat')} value={`${formatNumber(Number(cs.total_vat_rub))} ₽`} color="var(--color-danger)" pct={Number(cs.total_rub) > 0 ? `${((Number(cs.total_vat_rub) / Number(cs.total_rub)) * 100).toFixed(1)}%` : undefined} />
                                )}
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
                                <div style={{ padding: 16, borderRadius: 12, background: 'linear-gradient(135deg, rgba(255,182,193,0.15), rgba(255,105,180,0.08))', textAlign: 'center' }}>
                                    <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}>
                                        <span style={{ fontSize: 13 }}>🔥</span> {t('cost_total')}
                                    </div>
                                    <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--color-success)' }}>
                                        {formatNumber(Number(cs.total_rub))} ₽
                                    </div>
                                </div>
                                {vehicle.country === 'RUSSIA' ? (
                                    <>
                                        <CostKpi label={t('vehicle_form_delivery_rub')} value={`${formatNumber(Number(vehicle.delivery_cost_rub || 0), 0)} ₽`} />
                                        <CostKpi label={t('vehicle_country')} value={t('country_russia')} />
                                    </>
                                ) : (
                                    <>
                                        <CostKpi label={t('vehicle_form_rate_cny')} value={String(vehicle.rate_cny)} />
                                        <CostKpi label={t('vehicle_form_delivery_cny')} value={Number(vehicle.delivery_cost_cny) > 0 ? `${formatNumber(Number(vehicle.delivery_cost_cny), 0)}` : '—'} />
                                    </>
                                )}
                                <CostKpi label={t('cost_weight')} value={vehicle.total_weight_kg ? `${formatNumber(Number(vehicle.total_weight_kg), 0)} kg` : '—'} />
                            </div>
                        </div>
                    )}

                    {/* Capacity bar */}
                    <CapacityBar vehicle={vehicle} />

                    {/* Items table (TOP) */}
                    <div className="glass-card" style={{ padding: 16, overflow: 'auto' }}>
                        <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>
                            {t('vdetail_tab_items')} ({vehicle.items.length})
                        </h3>
                        {vehicle.items.length === 0 ? (
                            <div style={{ textAlign: 'center', padding: 24, opacity: 0.5 }}>{t('vehicle_items_empty')}</div>
                        ) : (
                            <ItemsTable
                                items={vehicle.items}
                                isForming={isForming}
                                vehicleOrderNo={vehicle.order_no}
                                vehicleStatus={vehicle.status || ''}
                                totalQty={vehicle.total_qty}
                                totalCny={vehicle.total_cny}
                                onRemoved={load}
                                driftStateById={driftStateById}
                                setDriftStateById={setDriftStateById}
                            />
                        )}
                    </div>

                    {/* Add items: FORMING — обычный flow; SHIPPED/CUSTOMS/DISPATCHED/DELIVERED — post-shipment (sc18) */}
                    {(isForming || ['SHIPPED', 'CUSTOMS', 'DISPATCHED', 'DELIVERED'].includes(vehicle.status || '')) && (
                        <CollapsibleAddItems
                            vehicleOrderNo={vehicle.order_no}
                            onAdded={load}
                            isPostShipment={!isForming}
                            vehicleStatus={vehicle.status || ''}
                        />
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

function QtyEditCell({ vehicleOrderNo, item, onSaved, hasDrift, onOpenExistingDrift, onDriftDetected }: {
    vehicleOrderNo: string; item: VehicleItemSchema; onSaved: () => void;
    hasDrift?: boolean;
    onOpenExistingDrift?: (item: VehicleItemSchema) => void;
    onDriftDetected?: (detail: FactoryQtyExceededDetail, oldQty: number, newQty: number) => void;
}) {
    const { t } = useT();
    const [editing, setEditing] = useState(false);
    const [value, setValue] = useState<string>(String(item.qty));
    const [saving, setSaving] = useState(false);
    const isMix = Boolean(item.mix_group_id);
    const existingDrift = item.qty_drift != null && item.qty_drift > 0;

    useEffect(() => { setValue(String(item.qty)); }, [item.qty]);

    const save = async () => {
        const next = parseInt(value, 10);
        if (isNaN(next) || next < 0) { setValue(String(item.qty)); setEditing(false); return; }
        if (next === item.qty) { setEditing(false); return; }
        setSaving(true);
        try {
            await api.updateVehicleItem(vehicleOrderNo, item.id, { qty: next, mode: 'strict' });
            setEditing(false);
            onSaved();
        } catch (e: unknown) {
            if (e instanceof FactoryQtyExceededError && onDriftDetected) {
                // Server detected qty > available — surface DriftConfirmRow under the row.
                onDriftDetected(e.detail, item.qty, next);
                setEditing(false);
            } else {
                alert(e instanceof Error ? e.message : 'Error');
                setValue(String(item.qty));
            }
        }
        setSaving(false);
    };

    const cancel = () => { setValue(String(item.qty)); setEditing(false); };

    if (editing) {
        return (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end' }}>
                <input
                    type="number"
                    min="0"
                    value={value}
                    onChange={e => setValue(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') cancel(); }}
                    autoFocus
                    disabled={saving}
                    style={{ width: 70, padding: '3px 6px', fontSize: 13, textAlign: 'right', border: `1px solid ${hasDrift ? 'var(--color-warning)' : 'var(--color-accent)'}`, borderRadius: 6, background: 'var(--color-bg)', color: 'var(--color-text)' }}
                />
                <button type="button" onClick={save} disabled={saving} title={t('qty_edit_save')}
                    style={{ border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 14, padding: '0 2px', color: 'var(--color-success)' }}>✓</button>
                <button type="button" onClick={cancel} disabled={saving} title={t('qty_edit_cancel')}
                    style={{ border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 14, padding: '0 2px', color: 'var(--color-text-muted)' }}>✕</button>
            </span>
        );
    }

    return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, justifyContent: 'flex-end', position: 'relative' }}>
            <span style={{ fontWeight: 600 }}>{formatNumber(item.qty, 0)}</span>
            {existingDrift && (
                <button
                    type="button"
                    onClick={() => onOpenExistingDrift?.(item)}
                    title={`${t('drift_existing_dot_tooltip')}: +${item.qty_drift}`}
                    aria-label={t('drift_existing_dot_tooltip')}
                    style={{
                        border: 'none', background: 'var(--color-warning)', cursor: 'pointer',
                        width: 8, height: 8, borderRadius: '50%', padding: 0, flexShrink: 0,
                    }}
                />
            )}
            {!isMix && (
                <button
                    type="button"
                    onClick={() => setEditing(true)}
                    title={t('qty_edit_title')}
                    style={{ border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 12, padding: 0, opacity: 0.4, color: 'var(--color-text-muted)' }}
                    onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
                    onMouseLeave={e => (e.currentTarget.style.opacity = '0.4')}
                >
                    ✏️
                </button>
            )}
        </span>
    );
}

// ─── Drift Confirm Row (sc17) ──────────────────────────────────────────────

function DriftConfirmRow({ detail, vehicleOrderNo, itemId, colSpan, onResolved }: {
    detail: DriftState;
    vehicleOrderNo: string;
    itemId: number;
    colSpan: number;
    onResolved: (action: 'extended' | 'reverted') => void;
}) {
    const { t } = useT();
    const [working, setWorking] = useState(false);
    const overflow = detail.overflow;
    const newPlanQty = detail.fo_qty + overflow;
    const isLargeDelta = overflow > 1000;

    const onExtend = async () => {
        setWorking(true);
        try {
            await api.updateVehicleItem(vehicleOrderNo, itemId, {
                qty: detail.newQty,
                mode: 'extend_plan',
            });
            const fo = detail.fo_number || `#${detail.fo_id}`;
            // Toast component is not standardized for this page — use alert for visibility.
            // Replace with toast() if a global toast provider becomes available.
            alert(`${t('drift_extend_toast').replace('{fo_number}', fo).replace('{count}', String(overflow))}`);
            onResolved('extended');
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Error');
        }
        setWorking(false);
    };

    const onRevert = () => {
        // Local revert only — qty in DB never changed (strict PATCH was rejected).
        onResolved('reverted');
    };

    return (
        <tr style={{ background: 'rgba(255, 159, 10, 0.08)', borderLeft: '4px solid var(--color-warning)' }}>
            <td colSpan={colSpan} style={{ padding: 12 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <div style={{ fontWeight: 600, color: 'var(--color-warning)', fontSize: 13 }}>
                        ⚠ {t('drift_warning_title')
                            .replace('{fo_number}', detail.fo_number || `#${detail.fo_id}`)
                            .replace('{subject}', detail.subject || detail.barcode)
                            .replace('{count}', String(overflow))}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                        {t('drift_warning_details')
                            .replace('{plan}', String(detail.fo_qty))
                            .replace('{assigned}', String(detail.fo_assigned))
                            .replace('{available}', String(detail.available))
                            .replace('{old}', String(detail.oldQty))
                            .replace('{new}', String(detail.newQty))}
                    </div>
                    {detail.in_mix_group && (
                        <div style={{ fontSize: 12, color: 'var(--color-accent)', background: 'rgba(0,113,227,0.06)', padding: '6px 10px', borderRadius: 8 }}>
                            ⓘ {t('drift_mix_group_note').replace('{barcode}', detail.barcode)}
                        </div>
                    )}
                    {isLargeDelta && (
                        <div style={{ fontSize: 12, color: 'var(--color-danger)', background: 'rgba(255,59,48,0.08)', padding: '6px 10px', borderRadius: 8 }}>
                            ⚠ {t('drift_large_delta_warning').replace('{count}', String(overflow))}
                        </div>
                    )}
                    <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                        <button
                            type="button"
                            className="btn btn-primary btn-sm"
                            onClick={onExtend}
                            disabled={working}
                        >
                            {working ? '...' : `➕ ${t('drift_extend_button').replace('{newQty}', String(newPlanQty))}`}
                        </button>
                        <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            onClick={onRevert}
                            disabled={working}
                        >
                            ↩ {t('drift_revert_button').replace('{old}', String(detail.oldQty))}
                        </button>
                    </div>
                </div>
            </td>
        </tr>
    );
}

function ItemsTable({ items, isForming, vehicleOrderNo, vehicleStatus, totalQty, totalCny, onRemoved, driftStateById, setDriftStateById }: {
    items: VehicleItemSchema[]; isForming: boolean; vehicleOrderNo: string;
    vehicleStatus: string;
    totalQty: number; totalCny: number; onRemoved: () => void;
    driftStateById: Record<number, DriftState>;
    setDriftStateById: React.Dispatch<React.SetStateAction<Record<number, DriftState>>>;
}) {
    const { t } = useT();
    const [removingId, setRemovingId] = useState<number | null>(null);
    const [clearingAll, setClearingAll] = useState(false);
    const [perUnit, setPerUnit] = useState(false); // false = total, true = per unit
    const [expandedId, setExpandedId] = useState<number | null>(null);
    const [resyncOpen, setResyncOpen] = useState(false);
    const [categoryFilter, setCategoryFilter] = useState<string>('');
    const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
    const [bulkDeleting, setBulkDeleting] = useState(false);
    const hasCosts = items.some(i => i.total_rub);

    // Уникальные категории (subject) для фильтра.
    const categories = Array.from(new Set(items.map(i => i.subject).filter((s): s is string => Boolean(s)))).sort();
    const filteredItems = categoryFilter ? items.filter(i => i.subject === categoryFilter) : items;
    const visibleIds = filteredItems.map(i => i.id);
    const allVisibleSelected = visibleIds.length > 0 && visibleIds.every(id => selectedIds.has(id));
    const toggleAll = () => {
        setSelectedIds(prev => {
            if (allVisibleSelected) {
                const next = new Set(prev);
                visibleIds.forEach(id => next.delete(id));
                return next;
            }
            const next = new Set(prev);
            visibleIds.forEach(id => next.add(id));
            return next;
        });
    };
    const toggleOne = (id: number) => {
        setSelectedIds(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id); else next.add(id);
            return next;
        });
    };

    const handleRemove = async (itemId: number, isMix?: boolean) => {
        // sc19: для post-shipment removal — расширенный warning о последствиях.
        const msg = isForming
            ? t('vehicle_items_confirm_delete')
            : t('vehicle_items_confirm_delete_post_shipment').replace('{status}', vehicleStatus);
        if (!confirm(msg)) return;
        setRemovingId(itemId);
        try {
            await api.removeItemFromVehicle(vehicleOrderNo, itemId);
            onRemoved();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_error'));
        }
        setRemovingId(null);
    };

    const handleBulkDelete = async () => {
        if (selectedIds.size === 0) return;
        const msg = isForming
            ? t('vehicle_items_confirm_bulk_delete').replace('{count}', String(selectedIds.size))
            : t('vehicle_items_confirm_bulk_delete_post_shipment')
                .replace('{count}', String(selectedIds.size))
                .replace('{status}', vehicleStatus);
        if (!confirm(msg)) return;
        setBulkDeleting(true);
        const errors: string[] = [];
        for (const id of selectedIds) {
            try {
                await api.removeItemFromVehicle(vehicleOrderNo, id);
            } catch (e: unknown) {
                errors.push(`${id}: ${e instanceof Error ? e.message : 'error'}`);
            }
        }
        setBulkDeleting(false);
        setSelectedIds(new Set());
        if (errors.length > 0) {
            alert(`${t('vehicle_items_bulk_delete_partial')}\n${errors.join('\n')}`);
        }
        onRemoved();
    };

    const handleClearAll = async () => {
        if (!confirm(t('vehicle_items_confirm_clear_all'))) return;
        setClearingAll(true);
        try {
            await api.clearAllVehicleItems(vehicleOrderNo);
            onRemoved();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_error'));
        }
        setClearingAll(false);
    };

    const handleExport = () => {
        const rows = items.map(item => {
            const ppb = item.pcs_per_box || 0;
            const boxes = ppb > 0 ? Math.ceil(item.qty / ppb) : 0;
            const dims = parseBoxDims(item.box_size);
            const vol = dims && ppb > 0 ? (dims.l * dims.w * dims.h) / 1e6 * boxes : 0;
            return {
                [t('col_barcode')]: item.barcode,
                [t('col_article')]: item.article_seller || '',
                [t('col_category')]: item.subject || '',
                [t('col_qty')]: item.qty,
                [t('col_boxes')]: item.mix_group_id ? t('detail_col_mix') : (boxes || ''),
                [`${t('col_weight_1pc')} kg`]: item.weight_kg || '',
                [`${t('col_weight')} kg`]: item.weight_kg ? Number(((item.weight_kg || 0) * item.qty).toFixed(1)) : '',
                [t('col_box_spec')]: item.box_size || '',
                'V m³': vol > 0 ? Number(vol.toFixed(2)) : '',
                [t('col_price_cny')]: Number(item.price_cny) || '',
                [t('col_sum_cny')]: Number(item.price_cny) * item.qty || '',
                [`${t('cost_delivery')} ₽`]: item.delivery_rub ? Number((item.delivery_rub * item.qty).toFixed(0)) : '',
                [`${t('cost_duty')} ₽`]: item.duty_rub ? Number((item.duty_rub * item.qty).toFixed(0)) : '',
                [`${t('cost_vat')} ₽`]: item.vat_rub ? Number((item.vat_rub * item.qty).toFixed(0)) : '',
                [`${t('cost_total')} ₽`]: item.total_rub ? Number((item.total_rub * item.qty).toFixed(0)) : '',
            };
        });
        exportToExcel(rows, `${t('vdetail_vehicle')}_${vehicleOrderNo}`);
    };

    const th: React.CSSProperties = { textAlign: 'left', padding: '8px 6px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500, whiteSpace: 'nowrap' };
    const thR: React.CSSProperties = { ...th, textAlign: 'right' };
    const thF: React.CSSProperties = { ...thR, background: 'rgba(59,130,246,0.04)' };
    const td: React.CSSProperties = { padding: '8px 6px', fontSize: 13 };
    const tdR: React.CSSProperties = { ...td, textAlign: 'right' };
    const tdF: React.CSSProperties = { ...tdR, background: 'rgba(59,130,246,0.02)' };

    // Totals — считаем по filteredItems чтобы footer соответствовал активному фильтру.
    // Когда фильтр выключен, filteredItems === items.
    const totalBoxes = calcTotalBoxesWithMix(filteredItems);
    let totalWeight = 0, totalVolume = 0;
    let totalRub = 0, totalDelivery = 0, totalDuty = 0, totalVat = 0;
    let totalQtyVisible = 0;
    let totalCnyVisible = 0;
    for (const item of filteredItems) {
        const ppb = item.pcs_per_box || 0;
        totalQtyVisible += item.qty;
        totalCnyVisible += Number(item.price_cny) * item.qty;
        totalWeight += (item.weight_kg || 0) * item.qty;
        const dims = parseBoxDims(item.box_size);
        if (dims && ppb > 0) totalVolume += (dims.l * dims.w * dims.h) / 1e6 * Math.ceil(item.qty / ppb);
        if (item.total_rub) totalRub += item.total_rub * item.qty;
        if (item.delivery_rub) totalDelivery += item.delivery_rub * item.qty;
        if (item.duty_rub) totalDuty += item.duty_rub * item.qty;
        if (item.vat_rub) totalVat += item.vat_rub * item.qty;
    }

    const costCols = hasCosts ? 4 : 0;
    // sc19: kebab/delete column visible for all statuses except DELIVERED.
    const canDelete = vehicleStatus !== 'DELIVERED';
    // +1 for checkbox column (when canDelete), already counts the delete button column.
    const baseCols = 11 + (canDelete ? 2 : 0);

    return (
        <>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
            {/* sc20: фильтр по категории + индикатор bulk-выбора */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {categories.length > 0 && (
                    <>
                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{t('vehicle_items_filter_category')}</span>
                        <select
                            value={categoryFilter}
                            onChange={e => setCategoryFilter(e.target.value)}
                            style={{ fontSize: 12, padding: '4px 8px', borderRadius: 8, border: '1px solid var(--color-border)' }}
                        >
                            <option value="">{t('vehicle_items_filter_all_categories')}</option>
                            {categories.map(cat => (
                                <option key={cat} value={cat}>{cat}</option>
                            ))}
                        </select>
                    </>
                )}
                {selectedIds.size > 0 && (
                    <span style={{ fontSize: 12, color: 'var(--color-accent)', fontWeight: 600 }}>
                        {t('vehicle_items_selected_count').replace('{count}', String(selectedIds.size))}
                    </span>
                )}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
            {canDelete && selectedIds.size > 0 && (
                <button
                    className="btn btn-danger btn-sm"
                    onClick={handleBulkDelete}
                    disabled={bulkDeleting}
                    style={{ fontSize: 12 }}
                >
                    {bulkDeleting
                        ? '...'
                        : t('vehicle_items_delete_selected').replace('{count}', String(selectedIds.size))}
                </button>
            )}
            {isForming && items.length > 0 && (
                <button className="btn btn-danger btn-sm" onClick={handleClearAll} disabled={clearingAll} style={{ fontSize: 12 }}>
                    {clearingAll ? '...' : t('vehicle_items_clear_all')}
                </button>
            )}
            {items.length > 0 && (
                <button className="btn btn-secondary btn-sm" onClick={() => setResyncOpen(true)} style={{ fontSize: 12 }}>
                    {t('price_resync_btn')}
                </button>
            )}
            <button className="btn btn-secondary btn-sm" onClick={handleExport} style={{ fontSize: 12 }}>
                📊 Excel
            </button>
            </div>
        </div>
        {resyncOpen && (
            <PriceResyncModal
                vehicleOrderNo={vehicleOrderNo}
                onClose={() => setResyncOpen(false)}
                onApplied={() => { setResyncOpen(false); onRemoved(); }}
            />
        )}
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
                        <th colSpan={11 + (canDelete ? 1 : 0)} style={{ padding: '4px 6px', fontSize: 10, color: 'var(--color-text-muted)', borderBottom: 'none', fontWeight: 400 }}>{t('cost_goods')}</th>
                        <th colSpan={costCols} style={{ padding: '4px 6px', fontSize: 10, color: 'var(--color-primary)', borderBottom: 'none', fontWeight: 500, textAlign: 'center', background: 'rgba(59,130,246,0.04)' }}>
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
                                <span>{t('col_cost')} (₽)</span>
                                <div style={{ display: 'inline-flex', borderRadius: 6, overflow: 'hidden', border: '1px solid #cbd5e1' }}>
                                    <button onClick={() => setPerUnit(false)}
                                        style={{ fontSize: 11, padding: '3px 12px', border: 'none', cursor: 'pointer', fontWeight: 600,
                                            background: !perUnit ? '#3b82f6' : '#f1f5f9',
                                            color: !perUnit ? '#fff' : '#64748b' }}>
                                        {t('col_total')}
                                    </button>
                                    <button onClick={() => setPerUnit(true)}
                                        style={{ fontSize: 11, padding: '3px 12px', border: 'none', borderLeft: '1px solid #cbd5e1', cursor: 'pointer', fontWeight: 600,
                                            background: perUnit ? '#3b82f6' : '#f1f5f9',
                                            color: perUnit ? '#fff' : '#64748b' }}>
                                        1 {t('unit_pcs')}
                                    </button>
                                </div>
                            </div>
                        </th>
                        {canDelete && <th style={{ borderBottom: 'none' }} />}
                    </tr>
                )}
                <tr style={{ borderBottom: '2px solid var(--color-border)' }}>
                    {canDelete && (
                        <th style={{ ...th, width: 28, textAlign: 'center' }}>
                            <input
                                type="checkbox"
                                checked={allVisibleSelected}
                                onChange={toggleAll}
                                title={t('vehicle_items_select_all')}
                            />
                        </th>
                    )}
                    <th style={th}>{t('col_barcode')}</th>
                    <th style={th}>{t('col_article')}</th>
                    <th style={th}>{t('col_category')}</th>
                    <th style={thR}>{t('col_qty')}</th>
                    <th style={thR}>{t('col_boxes')}</th>
                    <th style={thR}>{t('col_weight_1pc')}</th>
                    <th style={thR}>{t('col_weight')}</th>
                    <th style={th}>{t('col_box_spec')}</th>
                    <th style={thR}>V m³</th>
                    <th style={thR}>{t('col_price_cny')}</th>
                    <th style={thR}>{t('col_sum_cny')}</th>
                    {hasCosts && <th style={thF}>{t('cost_delivery')}</th>}
                    {hasCosts && <th style={thF}>{t('cost_duty')}</th>}
                    {hasCosts && <th style={thF}>{t('cost_vat')}</th>}
                    {hasCosts && <th style={{ ...thF, fontWeight: 600 }}>{t('cost_total')}</th>}
                    {canDelete && <th style={{ width: 28 }} />}
                </tr>
            </thead>
            <tbody>
                {filteredItems.map(item => {
                    const ppb = item.pcs_per_box || 0;
                    const boxes = ppb > 0 ? Math.ceil(item.qty / ppb) : 0;
                    const notFull = ppb > 0 && item.qty % ppb !== 0;
                    const dims = parseBoxDims(item.box_size);
                    const boxVol = dims ? (dims.l * dims.w * dims.h) / 1e6 : 0;
                    const itemTotalVol = boxVol * boxes;
                    const weight = item.weight_kg ? item.weight_kg * item.qty : 0;
                    const boxLabel = item.box_size ? `${item.box_size}${ppb ? ` (${ppb}${t('unit_pcs')})` : ''}` : '—';

                    return (
                    <React.Fragment key={item.id}>
                        <tr style={{ borderBottom: expandedId === item.id ? 'none' : '1px solid var(--color-border)', background: selectedIds.has(item.id) ? 'rgba(0,113,227,0.04)' : undefined }}>
                            {canDelete && (
                                <td style={{ ...td, textAlign: 'center', width: 28 }}>
                                    <input
                                        type="checkbox"
                                        checked={selectedIds.has(item.id)}
                                        onChange={() => toggleOne(item.id)}
                                    />
                                </td>
                            )}
                            <td style={{ ...td, fontFamily: 'monospace', fontSize: 12 }}>
                                {item.barcode}
                                {item.added_after_ship && (
                                    <span
                                        className="badge badge-warning"
                                        style={{ marginLeft: 6, fontSize: 10, padding: '1px 6px' }}
                                        title={t('vdetail_post_shipment_badge_tooltip')}
                                    >
                                        {t('vdetail_post_shipment_badge')}
                                    </span>
                                )}
                            </td>
                            <td style={{ ...td, fontSize: 12, maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={item.article_seller || ''}>{item.article_seller || '—'}</td>
                            <td style={{ ...td, fontSize: 12 }}>{item.subject || '—'}</td>
                            <td style={tdR}>
                                <QtyEditCell
                                    vehicleOrderNo={vehicleOrderNo}
                                    item={item}
                                    onSaved={onRemoved}
                                    hasDrift={Boolean(driftStateById[item.id])}
                                    onOpenExistingDrift={(it) => {
                                        // Synthesize drift detail from server-enriched fields
                                        // (set by backend _enrich_vehicle for existing DB drift).
                                        if (it.qty_drift == null || it.qty_drift <= 0) return;
                                        const fo_qty = it.fo_qty ?? 0;
                                        const fo_assigned = it.fo_assigned ?? 0;
                                        // Existing drift: overflow is the qty_drift itself (not delta-available).
                                        // oldQty (revert target) is current cost.qty - drift = qty that fits the plan.
                                        const oldQtyFit = Math.max(0, it.qty - it.qty_drift);
                                        const synthDetail: DriftState = {
                                            error: 'exceeds_factory_qty',
                                            fo_id: it.factory_order_id ?? 0,
                                            fo_number: it.factory_order_number ?? null,
                                            foi_id: it.factory_order_item_id ?? 0,
                                            barcode: it.barcode,
                                            subject: it.subject ?? null,
                                            fo_qty,
                                            fo_assigned,
                                            available: Math.max(0, fo_qty - fo_assigned),
                                            attempted_delta: it.qty_drift,
                                            overflow: it.qty_drift,
                                            in_mix_group: Boolean(it.mix_group_id),
                                            mix_group_id: it.mix_group_id ?? null,
                                            oldQty: oldQtyFit,
                                            newQty: it.qty,
                                        };
                                        setDriftStateById(prev => ({ ...prev, [it.id]: synthDetail }));
                                    }}
                                    onDriftDetected={(detail, oldQty, newQty) => {
                                        setDriftStateById(prev => ({
                                            ...prev,
                                            [item.id]: { ...detail, oldQty, newQty },
                                        }));
                                    }}
                                />
                            </td>
                            <td style={tdR}>
                                {item.mix_group_id ? (
                                    <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-accent)' }}
                                        title={`${t('detail_col_mix')}: ${item.mix_group_id.slice(0, 8)}`}>{t('detail_col_mix')}</span>
                                ) : (
                                    <BoxDetailCell
                                        qty={item.qty}
                                        pcsPerBox={item.pcs_per_box}
                                        boxDetail={item.box_detail}
                                        expanded={expandedId === item.id}
                                        onToggle={ppb > 0 ? () => setExpandedId(expandedId === item.id ? null : item.id) : undefined}
                                    />
                                )}
                            </td>
                            <td style={{ ...tdR, color: 'var(--color-text-muted)' }}>{item.weight_kg ? formatNumber(item.weight_kg, 2) : '—'}</td>
                            <td style={tdR}>{weight > 0 ? formatNumber(weight, 1) : '—'}</td>
                            <td style={{ ...td, fontSize: 11, color: 'var(--color-text-muted)' }}>{boxLabel}</td>
                            <td style={{ ...tdR, color: 'var(--color-text-muted)' }}>{itemTotalVol > 0 ? itemTotalVol.toFixed(2) : '—'}</td>
                            <td style={tdR}>{formatNumber(item.price_cny, 2)}</td>
                            <td style={tdR}>{formatNumber(item.qty * item.price_cny, 2)}</td>
                            {hasCosts && <td style={tdF}>{formatNumber(perUnit ? (item.delivery_rub || 0) : (item.delivery_rub || 0) * item.qty, perUnit ? 2 : 0)}</td>}
                            {hasCosts && <td style={tdF}>{formatNumber(perUnit ? (item.duty_rub || 0) : (item.duty_rub || 0) * item.qty, perUnit ? 2 : 0)}</td>}
                            {hasCosts && <td style={tdF}>{formatNumber(perUnit ? (item.vat_rub || 0) : (item.vat_rub || 0) * item.qty, perUnit ? 2 : 0)}</td>}
                            {hasCosts && <td style={{ ...tdF, fontWeight: 700 }}>{formatNumber(perUnit ? (item.total_rub || 0) : (item.total_rub || 0) * item.qty, perUnit ? 2 : 0)}</td>}
                            {canDelete && (
                                <td style={{ ...td, textAlign: 'center' }}>
                                    <button
                                        onClick={() => handleRemove(item.id, !!item.mix_group_id)}
                                        disabled={removingId === item.id}
                                        style={{
                                            background: 'none', border: 'none', cursor: 'pointer',
                                            color: item.mix_group_id ? 'var(--color-accent)' : 'var(--color-danger)',
                                            fontSize: item.mix_group_id ? 11 : 14,
                                            opacity: removingId === item.id ? 0.3 : 0.6, padding: 2,
                                        }}
                                        title={item.mix_group_id ? t('vehicle_items_remove') : t('btn_delete')}>
                                        {item.mix_group_id ? t('vehicle_items_remove') : '✕'}
                                    </button>
                                </td>
                            )}
                        </tr>
                        {expandedId === item.id && ppb > 0 && (
                            <tr>
                                <BoxDetailExpandRow
                                    qty={item.qty}
                                    pcsPerBox={ppb}
                                    boxDetail={item.box_detail}
                                    colSpan={baseCols + costCols}
                                    editable
                                    onSaveOverride={async ({ box_detail, pcs_per_box }) => {
                                        await api.updateVehicleItem(vehicleOrderNo, item.id, {
                                            box_detail_override: box_detail,
                                            pcs_per_box_override: pcs_per_box,
                                        });
                                    }}
                                    onSaved={onRemoved}
                                />
                            </tr>
                        )}
                        {driftStateById[item.id] && (
                            <DriftConfirmRow
                                detail={driftStateById[item.id]}
                                vehicleOrderNo={vehicleOrderNo}
                                itemId={item.id}
                                colSpan={baseCols + costCols}
                                onResolved={(action) => {
                                    setDriftStateById(prev => {
                                        const next = { ...prev };
                                        delete next[item.id];
                                        return next;
                                    });
                                    if (action === 'extended') onRemoved();
                                }}
                            />
                        )}
                    </React.Fragment>
                    );
                })}
            </tbody>
            <tfoot>
                <tr style={{ borderTop: '2px solid var(--color-border)', fontWeight: 600, whiteSpace: 'nowrap' }}>
                    {canDelete && <td />}
                    <td colSpan={3} style={{ padding: '12px 6px', fontSize: 13 }}>{t('cost_total')}</td>
                    <td style={{ padding: '12px 6px', textAlign: 'right', fontSize: 13 }}>{formatNumber(totalQtyVisible, 0)}</td>
                    <td style={{ padding: '12px 6px', textAlign: 'right' }}>{totalBoxes || ''}</td>
                    <td />
                    <td style={{ padding: '12px 6px', textAlign: 'right' }}>{totalWeight > 0 ? formatNumber(totalWeight, 0) : ''} kg</td>
                    <td />
                    <td style={{ padding: '12px 6px', textAlign: 'right' }}>{totalVolume > 0 ? totalVolume.toFixed(2) : ''}</td>
                    <td />
                    <td style={{ padding: '12px 6px', textAlign: 'right', whiteSpace: 'nowrap' }}>{formatNumber(totalCnyVisible, 2)} ¥</td>
                    {hasCosts && <td style={{ ...tdF, padding: '12px 6px', whiteSpace: 'nowrap' }}>{perUnit ? '' : `${formatNumber(totalDelivery, 0)} ₽`}</td>}
                    {hasCosts && <td style={{ ...tdF, padding: '12px 6px', whiteSpace: 'nowrap' }}>{perUnit ? '' : `${formatNumber(totalDuty, 0)} ₽`}</td>}
                    {hasCosts && <td style={{ ...tdF, padding: '12px 6px', whiteSpace: 'nowrap' }}>{perUnit ? '' : `${formatNumber(totalVat, 0)} ₽`}</td>}
                    {hasCosts && <td style={{ ...tdF, padding: '12px 6px', fontWeight: 700, fontSize: 15, color: 'var(--color-primary)', whiteSpace: 'nowrap' }}>{perUnit ? '' : `${formatNumber(totalRub, 0)} ₽`}</td>}
                    {canDelete && <td />}
                </tr>
            </tfoot>
        </table>
        </>
    );
}

// ─── Price Resync Modal ───────────────────────────────────────────────────

function PriceResyncModal({ vehicleOrderNo, onClose, onApplied }: {
    vehicleOrderNo: string; onClose: () => void; onApplied: () => void;
}) {
    const { t } = useT();
    const [preview, setPreview] = useState<VehiclePriceResyncPreview | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [applying, setApplying] = useState(false);
    const [mounted, setMounted] = useState(false);

    useEffect(() => { setMounted(true); }, []);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const data = await api.previewVehiclePriceResync(vehicleOrderNo);
                if (!cancelled) setPreview(data);
            } catch (e: unknown) {
                if (!cancelled) setError(e instanceof Error ? e.message : t('msg_error'));
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [vehicleOrderNo, t]);

    const handleApply = async () => {
        setApplying(true);
        try {
            const res = await api.applyVehiclePriceResync(vehicleOrderNo);
            alert(`${t('price_resync_applied')} ${res.applied}`);
            onApplied();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_error'));
            setApplying(false);
        }
    };

    const isForming = preview?.vehicle_status === 'FORMING';
    const hasChanges = (preview?.changed_items || 0) > 0;

    if (!mounted) return null;

    return createPortal(
        <div
            onClick={onClose}
            style={{
                position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                zIndex: 10000, padding: 16,
            }}
        >
            <div
                onClick={e => e.stopPropagation()}
                className="glass-card"
                style={{
                    maxWidth: 900, width: '100%', maxHeight: '85vh', overflowY: 'auto',
                    padding: 24, background: 'var(--color-bg)',
                }}
            >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <h3 style={{ margin: 0, fontSize: 18, fontWeight: 700 }}>{t('price_resync_title')}</h3>
                    <button onClick={onClose} style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer', color: 'var(--color-text-muted)', padding: 4 }}>✕</button>
                </div>

                {loading && <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>{t('price_resync_loading')}</div>}

                {error && (
                    <>
                        <div style={{ padding: 16, color: 'var(--color-danger)', fontWeight: 500 }}>⚠️ {error}</div>
                        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
                            <button className="btn btn-secondary btn-sm" onClick={onClose}>{t('price_resync_cancel')}</button>
                        </div>
                    </>
                )}

                {preview && !loading && !error && (
                    <>
                        {!isForming && (
                            <div style={{
                                padding: 12, marginBottom: 16, borderRadius: 8,
                                background: 'rgba(255,59,48,0.08)', color: 'var(--color-danger)',
                                fontSize: 13, fontWeight: 500,
                            }}>
                                ⚠️ {t('price_resync_warning_status')}
                            </div>
                        )}

                        <div style={{ display: 'flex', gap: 24, fontSize: 13, marginBottom: 16, color: 'var(--color-text-muted)' }}>
                            <div>{t('price_resync_changes')}: <b style={{ color: 'var(--color-text)' }}>{preview.changed_items} / {preview.total_items}</b></div>
                            {preview.unlinked_items > 0 && (
                                <div>{preview.unlinked_items} {t('price_resync_unlinked')}</div>
                            )}
                            {hasChanges && (
                                <div>{t('price_resync_delta')}: <b style={{ color: preview.sum_delta_cny >= 0 ? 'var(--color-danger)' : 'var(--color-success)' }}>
                                    {preview.sum_delta_cny >= 0 ? '+' : ''}{formatNumber(preview.sum_delta_cny, 2)} ¥
                                </b></div>
                            )}
                        </div>

                        {!hasChanges ? (
                            <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                                ✅ {t('price_resync_empty')}
                            </div>
                        ) : (
                            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                                <thead>
                                    <tr style={{ borderBottom: '2px solid var(--color-border)', color: 'var(--color-text-muted)' }}>
                                        <th style={{ textAlign: 'left', padding: '8px 6px', fontWeight: 500 }}>{t('col_barcode')}</th>
                                        <th style={{ textAlign: 'left', padding: '8px 6px', fontWeight: 500 }}>{t('col_article')}</th>
                                        <th style={{ textAlign: 'right', padding: '8px 6px', fontWeight: 500 }}>{t('col_qty')}</th>
                                        <th style={{ textAlign: 'right', padding: '8px 6px', fontWeight: 500 }}>{t('price_resync_col_old')}</th>
                                        <th style={{ textAlign: 'right', padding: '8px 6px', fontWeight: 500 }}>{t('price_resync_col_new')}</th>
                                        <th style={{ textAlign: 'right', padding: '8px 6px', fontWeight: 500 }}>{t('price_resync_col_delta')}</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {preview.items.map(it => (
                                        <tr key={it.cost_item_id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                            <td style={{ padding: '6px', fontFamily: 'monospace' }}>{it.barcode}</td>
                                            <td style={{ padding: '6px', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={it.article_seller || ''}>{it.article_seller || '—'}</td>
                                            <td style={{ padding: '6px', textAlign: 'right' }}>{formatNumber(it.qty, 0)}</td>
                                            <td style={{ padding: '6px', textAlign: 'right', color: 'var(--color-text-muted)' }}>{formatNumber(it.old_price_cny, 2)}</td>
                                            <td style={{ padding: '6px', textAlign: 'right', fontWeight: 600 }}>{formatNumber(it.new_price_cny, 2)}</td>
                                            <td style={{ padding: '6px', textAlign: 'right', fontWeight: 600, color: it.delta_sum_cny >= 0 ? 'var(--color-danger)' : 'var(--color-success)' }}>
                                                {it.delta_sum_cny >= 0 ? '+' : ''}{formatNumber(it.delta_sum_cny, 2)}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}

                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                            <button className="btn btn-secondary btn-sm" onClick={onClose} disabled={applying}>{t('price_resync_cancel')}</button>
                            {hasChanges && (
                                <button className="btn btn-primary btn-sm" onClick={handleApply} disabled={applying}>
                                    {applying ? t('price_resync_applying') : t('price_resync_apply')}
                                </button>
                            )}
                        </div>
                    </>
                )}
            </div>
        </div>,
        document.body
    );
}

// ─── Capacity Bar ──────────────────────────────────────────────────────────

function CapacityBar({ vehicle }: { vehicle: VehicleSchema }) {
    const containerKey = vehicle.container_type || 'truck1';
    const container = CONTAINERS[containerKey];
    if (!container) return null;

    const containerVol = container.l * container.w * container.h;
    let usedVol = 0;
    const mixGroupSeen = new Set<string>();
    for (const item of vehicle.items) {
        if (item.mix_group_id) {
            // Only count volume once per unique mix group, using mix_box_size
            if (!mixGroupSeen.has(item.mix_group_id)) {
                mixGroupSeen.add(item.mix_group_id);
                const effectiveBoxSize = item.mix_box_size || item.box_size;
                const effectivePpb = item.mix_pcs_per_box || item.pcs_per_box;
                if (effectiveBoxSize && effectivePpb && effectivePpb > 0) {
                    const parts = effectiveBoxSize.split(/[*xх×]/).map(Number);
                    if (parts.length === 3 && parts.every(p => p > 0)) {
                        const boxVol = (parts[0] * parts[1] * parts[2]) / 1e6;
                        const boxes = Math.ceil(item.qty / effectivePpb);
                        usedVol += boxVol * boxes;
                    }
                }
            }
        } else if (item.box_size && item.pcs_per_box && item.pcs_per_box > 0) {
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

interface PasteRow { barcode: string; qty: string; price: string; article: string; box_size: string; pcs_per_box: string; autoAdded?: boolean; mixGroupId?: string | null }
const emptyRow = (): PasteRow => ({ barcode: '', qty: '', price: '', article: '', box_size: '', pcs_per_box: '' });

function calcBoxes(qty: number, pcsPerBox: number | undefined): { boxes: number; notFull: boolean } {
    if (!pcsPerBox || pcsPerBox <= 0) return { boxes: 0, notFull: false };
    const boxes = Math.ceil(qty / pcsPerBox);
    const notFull = qty % pcsPerBox !== 0;
    return { boxes, notFull };
}

function resolveMixSiblings(
    rows: PasteRow[],
    allItemMap: Record<string, AvailableItem & { source_order: string; source_factory: string }>,
): PasteRow[] {
    const result = [...rows];
    const existingBarcodes = new Set(rows.map(r => r.barcode.trim()).filter(Boolean));

    for (let i = 0; i < result.length; i++) {
        const row = result[i];
        const bc = row.barcode.trim();
        if (!bc || row.autoAdded) continue;
        const item = allItemMap[bc];
        if (!item?.mix_group_id) continue;

        const ppb = item.mix_pcs_per_box || item.pcs_per_box || 0;
        if (ppb <= 0) continue;
        const qty = parseInt(row.qty) || 0;
        if (qty <= 0) continue;
        const boxes = Math.floor(qty / ppb);
        if (boxes <= 0) continue;

        // Find siblings
        for (const [siblingBc, siblingItem] of Object.entries(allItemMap)) {
            if (siblingBc === bc) continue;
            if (siblingItem.mix_group_id !== item.mix_group_id) continue;
            if (existingBarcodes.has(siblingBc)) continue;

            const siblingPpb = siblingItem.mix_pcs_per_box || siblingItem.pcs_per_box || 0;
            const autoQty = siblingPpb > 0 ? boxes * siblingPpb : 0;
            if (autoQty <= 0) continue;

            result.push({
                barcode: siblingBc,
                qty: String(autoQty),
                price: String(siblingItem.price_cny || ''),
                article: siblingItem.article_seller || siblingItem.subject || '',
                box_size: siblingItem.mix_box_size || siblingItem.box_size || '',
                pcs_per_box: String(siblingPpb || ''),
                autoAdded: true,
                mixGroupId: item.mix_group_id,
            });
            existingBarcodes.add(siblingBc);
        }
    }

    return result;
}

function CollapsibleAddItems({ vehicleOrderNo, onAdded, isPostShipment = false, vehicleStatus = '' }: {
    vehicleOrderNo: string; onAdded: () => void; isPostShipment?: boolean; vehicleStatus?: string;
}) {
    const { t } = useT();
    const [open, setOpen] = useState(false);
    if (!open) {
        return (
            <div style={{ textAlign: 'center', padding: 16 }}>
                <button className={isPostShipment ? "btn btn-secondary" : "btn btn-primary"} onClick={() => setOpen(true)}>
                    {isPostShipment ? t('vdetail_items_add_post_shipment') : t('vdetail_items_add')}
                </button>
            </div>
        );
    }
    return (
        <div>
            {isPostShipment && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 12, borderLeft: '4px solid var(--color-warning)', background: 'rgba(255, 159, 10, 0.06)' }}>
                    <div style={{ fontWeight: 600, color: 'var(--color-warning)', fontSize: 13, marginBottom: 4 }}>
                        ⚠ {t('vdetail_post_shipment_title')}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                        {t('vdetail_post_shipment_warning').replace('{status}', vehicleStatus)}
                    </div>
                </div>
            )}
            <AddItemsSection
                vehicleOrderNo={vehicleOrderNo}
                onAdded={() => { onAdded(); setOpen(false); }}
                onPartialAdded={onAdded}
                isPostShipment={isPostShipment}
            />
            <div style={{ textAlign: 'center', marginTop: 8 }}>
                <button className="btn btn-secondary btn-sm" onClick={() => setOpen(false)}>{t('items_collapse')}</button>
            </div>
        </div>
    );
}

function AddItemsSection({ vehicleOrderNo, onAdded, onPartialAdded, isPostShipment = false }: { vehicleOrderNo: string; onAdded: () => void; onPartialAdded: () => void; isPostShipment?: boolean }) {
    const { t } = useT();
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
    const [showMismatchModal, setShowMismatchModal] = useState(false);

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
            if (isPostShipment) {
                await api.addPostShipmentItems(vehicleOrderNo, {
                    items: items.map(it => ({ ...it, mode: 'strict' as const })),
                });
            } else {
                await api.addItemsToVehicle(vehicleOrderNo, items);
            }
            setChecked({});
            setQuantities({});
            onAdded();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : t('msg_error'));
        }
        setSubmitting(false);
    };

    // ─── Paste mode helpers ───
    const resolveArticle = (bc: string): string => {
        const item = allItemMap[bc];
        return item ? (item.article_seller || item.subject || '') : '';
    };
    const resolveBoxSize = (bc: string): string => {
        const item = allItemMap[bc];
        return item ? (item.box_size || '') : '';
    };
    const resolvePpb = (bc: string): string => {
        const item = allItemMap[bc];
        return item && item.pcs_per_box ? String(item.pcs_per_box) : '';
    };

    const updateRow = (idx: number, field: keyof PasteRow, value: string) => {
        setRows(prev => {
            const next = [...prev];
            next[idx] = { ...next[idx], [field]: value };
            if (field === 'barcode' && value.trim()) {
                const bc = value.trim();
                next[idx].article = resolveArticle(bc);
                // Auto-fill box_size and pcs_per_box from order if empty
                if (!next[idx].box_size) next[idx].box_size = resolveBoxSize(bc);
                if (!next[idx].pcs_per_box) next[idx].pcs_per_box = resolvePpb(bc);
            }
            // Re-run mix sibling resolution when barcode or qty changes
            if (field === 'barcode' || field === 'qty') {
                const withoutAutoAdded = next.filter(r => !r.autoAdded);
                return resolveMixSiblings(withoutAutoAdded, allItemMap);
            }
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
        // Auto-detect columns: barcode is first, box_size is the column with a dim
        // separator (×/*/x/х), remaining numeric columns are assigned as ppb/qty/price
        // by heuristic (decimal → price, smaller int → ppb, larger int → qty).
        const BOX_RE = /\d\s*[*xXхХ×]\s*\d/;
        for (const cols of lines) {
            if (cols.length < 1) continue;
            const barcode = cols[0]?.trim() || '';
            if (!barcode) continue;

            // Find box_size column (contains dim separator between digits)
            let boxIdx = -1;
            for (let i = 1; i < cols.length; i++) {
                if (BOX_RE.test(cols[i] || '')) { boxIdx = i; break; }
            }
            const box_size = boxIdx >= 0 ? (cols[boxIdx] || '').trim() : '';

            // Remaining non-box columns → distribute across ppb/qty/price
            const rest: string[] = [];
            for (let i = 1; i < cols.length; i++) {
                if (i === boxIdx) continue;
                const s = (cols[i] || '').trim();
                if (s) rest.push(s);
            }
            let pcs_per_box = '';
            let qty = '';
            let price = '';
            // Pull out the decimal-looking number as price first
            const intOnly: string[] = [];
            for (const s of rest) {
                const hasDecimal = /[.,]\d/.test(s);
                if (hasDecimal && !price) price = parseNum(s);
                else intOnly.push(s);
            }
            if (intOnly.length >= 2) {
                const nums = intOnly.map(s => parseInt(parseNum(s)) || 0);
                // Smaller value = ppb, larger = qty (typical case)
                if (nums[0] <= nums[1]) {
                    pcs_per_box = String(nums[0]);
                    qty = String(nums[1]);
                } else {
                    qty = String(nums[0]);
                    pcs_per_box = String(nums[1]);
                }
                if (intOnly.length >= 3 && !price) price = parseNum(intOnly[2]);
            } else if (intOnly.length === 1) {
                qty = parseNum(intOnly[0]);
            }

            newRows.push({
                barcode,
                qty,
                price,
                article: resolveArticle(barcode),
                box_size: box_size || resolveBoxSize(barcode),
                pcs_per_box: pcs_per_box || resolvePpb(barcode),
            });
        }
        if (newRows.length > 0) {
            const resolved = resolveMixSiblings(newRows, allItemMap);
            setRows([...resolved, emptyRow(), emptyRow()]);
        }
    };

    const filledRows = rows.filter(r => r.barcode.trim());
    const invalidRows = filledRows.filter(r => !(r.barcode.trim() in allItemMap));
    const exceededRows = filledRows.filter(r => {
        const item = allItemMap[r.barcode.trim()];
        const qty = parseInt(r.qty) || 0;
        return item && qty > 0 && qty > item.remaining_qty;
    });
    const missingPriceRows = filledRows.filter(r => {
        const item = allItemMap[r.barcode.trim()];
        const qty = parseInt(r.qty) || 0;
        return item && qty > 0 && qty <= item.remaining_qty && !(parseFloat(r.price) > 0);
    });
    const validRows = filledRows.filter(r => {
        const item = allItemMap[r.barcode.trim()];
        const qty = parseInt(r.qty) || 0;
        const price = parseFloat(r.price);
        return item && qty > 0 && qty <= item.remaining_qty && price > 0;
    });
    // Allow adding found items even if some are not found (fallback A)
    const pasteCanSave = validRows.length > 0;
    const hasUnfound = invalidRows.length > 0 || exceededRows.length > 0;

    // Normalize box size separators: "62*32*60" / "62×32×60" / "62x32x60" → canonical
    const normalizeBox = (s: string): string => s.trim().toLowerCase().replace(/[*xхх×]/g, '×');

    // Mismatch: price, box_size, or pcs_per_box differs from factory order values
    const rowHasMismatch = (r: PasteRow): boolean => {
        const item = allItemMap[r.barcode.trim()];
        if (!item) return false;
        const pastePrice = parseFloat(r.price);
        const orderPrice = parseFloat(item.price_cny) || 0;
        if (Math.abs(pastePrice - orderPrice) > 0.0001) return true;
        const orderBox = normalizeBox(item.box_size || '');
        const pasteBox = normalizeBox(r.box_size || '');
        if (pasteBox && pasteBox !== orderBox) return true;
        const orderPpb = item.pcs_per_box || 0;
        const pastePpb = parseInt(r.pcs_per_box) || 0;
        if (pastePpb && pastePpb !== orderPpb) return true;
        return false;
    };
    const mismatchRows = validRows.filter(rowHasMismatch);

    const performAdd = async (overridesFromPaste = false) => {
        const items = validRows.map(r => {
            const item = allItemMap[r.barcode.trim()];
            const base: { factory_order_item_id: number; qty: number; box_size_override?: string; pcs_per_box_override?: number } = {
                factory_order_item_id: item.id,
                qty: parseInt(r.qty) || 0,
            };
            if (overridesFromPaste) {
                const pasteBoxRaw = (r.box_size || '').trim();
                const pasteBoxNorm = normalizeBox(pasteBoxRaw);
                const orderBoxNorm = normalizeBox(item.box_size || '');
                if (pasteBoxRaw && pasteBoxNorm !== orderBoxNorm) base.box_size_override = pasteBoxRaw;
                const pastePpb = parseInt(r.pcs_per_box) || 0;
                const orderPpb = item.pcs_per_box || 0;
                if (pastePpb && pastePpb !== orderPpb) base.pcs_per_box_override = pastePpb;
            }
            return base;
        });
        const validBarcodes = new Set(validRows.map(r => r.barcode.trim()));
        if (isPostShipment) {
            await api.addPostShipmentItems(vehicleOrderNo, {
                items: items.map(it => ({ ...it, mode: 'strict' as const })),
            });
        } else {
            await api.addItemsToVehicle(vehicleOrderNo, items);
        }

        // Keep rows that weren't successfully added (unfound + exceeded qty)
        const remainingRows = rows.filter(r => {
            const bc = r.barcode.trim();
            return bc && !validBarcodes.has(bc);
        });

        if (remainingRows.length > 0) {
            setRows([...remainingRows, emptyRow(), emptyRow()]);
            onPartialAdded();
            const freshData = await api.getAvailableItems();
            setGroups(freshData);
        } else {
            setRows(Array.from({ length: 5 }, emptyRow));
            onAdded();
        }
    };

    const handlePasteSubmit = async () => {
        if (!pasteCanSave) return;
        if (mismatchRows.length > 0) {
            setShowMismatchModal(true);
            return;
        }
        setSubmitting(true);
        setError('');
        try {
            await performAdd(false);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : t('msg_error'));
        }
        setSubmitting(false);
    };

    const handleKeepOrderPrices = async () => {
        setShowMismatchModal(false);
        setSubmitting(true);
        setError('');
        try {
            await performAdd(false);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : t('msg_error'));
        }
        setSubmitting(false);
    };

    const handleOverwriteOrderPrices = async () => {
        setShowMismatchModal(false);
        setSubmitting(true);
        setError('');
        try {
            // Price mismatches → overwrite factory order prices (existing semantic).
            const priceMismatchRows = mismatchRows.filter(r => {
                const item = allItemMap[r.barcode.trim()];
                const p = parseFloat(r.price);
                const op = parseFloat(item?.price_cny || '0') || 0;
                return Math.abs(p - op) > 0.0001;
            });
            if (priceMismatchRows.length > 0) {
                const priceUpdates = priceMismatchRows.map(r => ({
                    factory_order_item_id: allItemMap[r.barcode.trim()].id,
                    new_price_cny: r.price,
                }));
                await api.bulkUpdateFactoryItemPrices(priceUpdates);
            }
            // Box/ppb mismatches → per-vehicle override on CostOrderItem.
            await performAdd(true);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : t('msg_error'));
        }
        setSubmitting(false);
    };

    return (
        <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
            <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>{t('add_items_title')}</h3>

            {loadingGroups ? (
                <div style={{ textAlign: 'center', padding: 16 }}><div className="spinner" style={{ margin: '0 auto' }} /></div>
            ) : groups.length === 0 ? (
                <div style={{ textAlign: 'center', padding: 16, opacity: 0.5 }}>{t('add_items_empty')}</div>
            ) : (
                <>
                    {/* Order selector — only for list mode (paste searches all orders) */}
                    {mode === 'list' && (
                        <div style={{ marginBottom: 12 }}>
                            <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>{t('orders_title')}</label>
                            <select value={selectedOrder} onChange={e => { setSelectedOrder(e.target.value); setChecked({}); setQuantities({}); }} style={inputStyle}>
                                {groups.map(g => (
                                    <option key={g.order_number} value={g.order_number}>
                                        {g.order_number}{g.factory_name ? ` — ${g.factory_name}` : ''} ({g.items.length} {t('add_items_pos')})
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
                                {m === 'list' ? `📋 ${t('add_items_subtitle')}` : `📎 ${t('items_paste_header')}`}
                            </button>
                        ))}
                    </div>

                    {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 8 }}>{error}</div>}

                    {/* ═══ LIST MODE ═══ */}
                    {mode === 'list' && (
                        <>
                            {listItems.length === 0 ? (
                                <div style={{ textAlign: 'center', padding: 16, opacity: 0.5 }}>{t('add_items_empty')}</div>
                            ) : (
                                <div style={{ overflow: 'auto', maxHeight: 400 }}>
                                    <table className="data-table" style={{ marginBottom: 0, fontSize: 13 }}>
                                        <thead>
                                            <tr>
                                                <th style={{ width: 36, textAlign: 'center' }}>
                                                    <input type="checkbox" checked={allChecked} onChange={toggleAll} />
                                                </th>
                                                <th>{t('col_barcode')}</th>
                                                <th>{t('col_article')}</th>
                                                <th>{t('col_category')}</th>
                                                <th style={{ textAlign: 'right' }}>{t('col_pcs_per_box')}</th>
                                                <th style={{ textAlign: 'right' }}>{t('col_available')}</th>
                                                <th style={{ width: 90 }}>{t('col_qty')}</th>
                                                <th style={{ textAlign: 'right' }}>{t('col_boxes')}</th>
                                                <th style={{ textAlign: 'right' }}>{t('col_price_cny')}</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {(() => {
                                                // Group mix items, keep standalone in order
                                                const mixGroups = new Map<string, AvailableItem[]>();
                                                const standaloneItems: AvailableItem[] = [];
                                                for (const item of listItems) {
                                                    if (item.mix_group_id) {
                                                        const group = mixGroups.get(item.mix_group_id) || [];
                                                        group.push(item);
                                                        mixGroups.set(item.mix_group_id, group);
                                                    } else {
                                                        standaloneItems.push(item);
                                                    }
                                                }

                                                const rows: React.ReactNode[] = [];

                                                // Render standalone items
                                                for (const item of standaloneItems) {
                                                    const isChecked = !!checked[item.id];
                                                    const qty = parseInt(quantities[item.id]?.toString() || '0') || 0;
                                                    const { boxes } = calcBoxes(qty, item.pcs_per_box);
                                                    const exceeds = qty > item.remaining_qty;
                                                    rows.push(
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
                                                                    <BoxDropdown qty={qty} pcsPerBox={item.pcs_per_box} />
                                                                ) : '—'}
                                                            </td>
                                                            <td style={{ textAlign: 'right', fontSize: 12 }}>{formatNumber(parseFloat(item.price_cny))}</td>
                                                        </tr>
                                                    );
                                                }

                                                // Render mix groups
                                                for (const [mixGroupId, groupItems] of mixGroups.entries()) {
                                                    const availableBoxes = Math.min(...groupItems.map(i => Math.floor(i.remaining_qty / (i.mix_pcs_per_box || i.pcs_per_box || 1))));
                                                    // Use first item's checkbox as group checkbox
                                                    const firstItem = groupItems[0];
                                                    const groupChecked = groupItems.every(i => !!checked[i.id]);
                                                    const boxQtyStr = quantities[firstItem.id] || '';
                                                    const boxQty = parseInt(boxQtyStr) || 0;

                                                    // Group header row
                                                    rows.push(
                                                        <tr key={`mix-header-${mixGroupId}`} style={{ background: 'rgba(0,113,227,0.05)', borderTop: '2px solid var(--color-accent)' }}>
                                                            <td style={{ textAlign: 'center' }}>
                                                                <input type="checkbox" checked={groupChecked}
                                                                    onChange={e => {
                                                                        const newChecked = { ...checked };
                                                                        const newQty = { ...quantities };
                                                                        for (const gi of groupItems) {
                                                                            newChecked[gi.id] = e.target.checked;
                                                                            if (e.target.checked && boxQty > 0) {
                                                                                newQty[gi.id] = String(boxQty * (gi.mix_pcs_per_box || gi.pcs_per_box || 1));
                                                                            }
                                                                        }
                                                                        setChecked(newChecked);
                                                                        setQuantities(newQty);
                                                                    }} />
                                                            </td>
                                                            <td colSpan={3} style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-accent)', padding: '6px 4px' }}>
                                                                {t('detail_mix_group')} ({groupItems.length} {t('add_items_pos')})
                                                                {groupItems[0]?.mix_box_size && <span style={{ fontWeight: 400, color: 'var(--color-text-muted)', marginLeft: 8 }}>{groupItems[0].mix_box_size}</span>}
                                                            </td>
                                                            <td style={{ textAlign: 'right', fontSize: 12, color: 'var(--color-text-muted)' }}>—</td>
                                                            <td style={{ textAlign: 'right', fontSize: 12, color: 'var(--color-accent)', fontWeight: 600 }}>
                                                                {availableBoxes} {t('col_boxes')}
                                                            </td>
                                                            <td>
                                                                <input type="number" min={0} max={availableBoxes}
                                                                    value={boxQtyStr}
                                                                    placeholder="0"
                                                                    onChange={e => {
                                                                        const boxes = parseInt(e.target.value) || 0;
                                                                        const newQty = { ...quantities };
                                                                        const newChecked = { ...checked };
                                                                        for (const gi of groupItems) {
                                                                            const ppb = gi.mix_pcs_per_box || gi.pcs_per_box || 1;
                                                                            newQty[gi.id] = String(boxes * ppb);
                                                                            newChecked[gi.id] = boxes > 0;
                                                                        }
                                                                        setQuantities(newQty);
                                                                        setChecked(newChecked);
                                                                    }}
                                                                    style={{ ...cellInput, width: 80 }} />
                                                            </td>
                                                            <td style={{ textAlign: 'right', fontSize: 12 }}>
                                                                {boxQty > 0 ? `${boxQty} ${t('col_boxes')}` : '—'}
                                                            </td>
                                                            <td />
                                                        </tr>
                                                    );

                                                    // Individual mix items (indented, no separate checkboxes)
                                                    for (const item of groupItems) {
                                                        const itemQty = parseInt(quantities[item.id]?.toString() || '0') || 0;
                                                        rows.push(
                                                            <tr key={item.id} style={{ borderLeft: '3px solid var(--color-accent)', background: 'rgba(0,113,227,0.02)' }}>
                                                                <td />
                                                                <td style={{ fontFamily: 'monospace', fontSize: 11, paddingLeft: 12 }}>{item.barcode}</td>
                                                                <td style={{ fontSize: 11 }}>{item.article_seller || '—'}</td>
                                                                <td style={{ fontSize: 11 }}>{item.subject || '—'}</td>
                                                                <td style={{ textAlign: 'right', fontSize: 11, color: 'var(--color-text-muted)' }}>{item.mix_pcs_per_box || item.pcs_per_box || '—'}</td>
                                                                <td style={{ textAlign: 'right', fontSize: 11 }}>{item.remaining_qty}</td>
                                                                <td style={{ textAlign: 'right', fontSize: 11, color: 'var(--color-text-muted)', padding: '4px 6px' }}>
                                                                    {itemQty > 0 ? `${itemQty} ${t('unit_pcs')}` : '—'}
                                                                </td>
                                                                <td />
                                                                <td style={{ textAlign: 'right', fontSize: 11 }}>{formatNumber(parseFloat(item.price_cny))}</td>
                                                            </tr>
                                                        );
                                                    }
                                                }

                                                return rows;
                                            })()}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--color-border)' }}>
                                <button className="btn btn-primary" onClick={handleListSubmit} disabled={!listCanSave || submitting}
                                    style={{ minWidth: 160, opacity: listCanSave ? 1 : 0.5 }}>
                                    {submitting ? t('add_items_adding') : `${t('add_items_count')} (${checkedItems.length})`}
                                </button>
                            </div>
                        </>
                    )}

                    {/* ═══ PASTE MODE ═══ */}
                    {mode === 'paste' && (
                        <>
                            <div style={{ padding: 10, marginBottom: 12, fontSize: 13, color: 'var(--color-text-muted)', background: 'var(--color-bg-secondary)', borderRadius: 8 }}>
                                {t('items_paste_sub')}
                            </div>

                            {invalidRows.length > 0 && (
                                <div style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 8, padding: 10, marginBottom: 12, fontSize: 13, color: '#ef4444' }}>
                                    ✗ <b>{invalidRows.length}</b> ({formatNumber(invalidRows.reduce((s, r) => s + (parseInt(r.qty) || 0), 0), 0)} {t('unit_pcs')})
                                </div>
                            )}

                            {exceededRows.length > 0 && (
                                <div style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)', borderRadius: 8, padding: 10, marginBottom: 12, fontSize: 13, color: '#b45309' }}>
                                    ! <b>{exceededRows.length}</b> ({formatNumber(exceededRows.reduce((s, r) => s + (parseInt(r.qty) || 0), 0), 0)} {t('unit_pcs')}, {t('col_available')}: {formatNumber(exceededRows.reduce((s, r) => s + (allItemMap[r.barcode.trim()]?.remaining_qty || 0), 0), 0)} {t('unit_pcs')})
                                </div>
                            )}

                            {missingPriceRows.length > 0 && (
                                <div style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 8, padding: 10, marginBottom: 12, fontSize: 13, color: '#ef4444' }}>
                                    ¥? <b>{missingPriceRows.length}</b> {t('paste_missing_price')}
                                </div>
                            )}

                            {mismatchRows.length > 0 && (
                                <div style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)', borderRadius: 8, padding: 10, marginBottom: 12, fontSize: 13, color: '#b45309' }}>
                                    ⚠️ <b>{mismatchRows.length}</b> {t('paste_price_mismatch')}
                                </div>
                            )}

                            <div style={{ overflow: 'auto', maxHeight: 360 }} onPaste={handlePaste}>
                                <table className="data-table" style={{ marginBottom: 0, fontSize: 13 }}>
                                    <thead>
                                        <tr>
                                            <th style={{ width: 36, textAlign: 'center' }}>#</th>
                                            <th style={{ minWidth: 140 }}>{t('col_article')}</th>
                                            <th>{t('col_category')}</th>
                                            <th style={{ minWidth: 180 }}>{t('col_barcode')}</th>
                                            <th style={{ minWidth: 150 }}>{t('col_box_spec')}</th>
                                            <th style={{ minWidth: 90, textAlign: 'right' }}>{t('col_pcs_per_box')}</th>
                                            <th style={{ textAlign: 'right' }}>{t('col_available')}</th>
                                            <th style={{ minWidth: 100 }}>{t('col_qty')}</th>
                                            <th style={{ minWidth: 120 }}>{t('col_price_cny')}</th>
                                            <th style={{ textAlign: 'right', fontSize: 11, color: 'var(--color-text-muted)' }}>{t('paste_in_order')}</th>
                                            <th style={{ textAlign: 'right' }}>{t('col_boxes')}</th>
                                            <th>{t('col_from_order')}</th>
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
                                            const isAutoAdded = row.autoAdded;
                                            return (
                                                <tr key={i} style={{
                                                    background: isAutoAdded ? 'rgba(0,113,227,0.04)' : unknown ? 'rgba(239,68,68,0.06)' : exceeds ? 'rgba(245,158,11,0.06)' : undefined,
                                                    borderLeft: isAutoAdded ? '3px solid var(--color-accent)' : undefined,
                                                    opacity: isAutoAdded ? 0.85 : 1,
                                                }}>
                                                    <td style={{ fontSize: 12, color: 'var(--color-text-muted)', textAlign: 'center' }}>{i + 1}</td>
                                                    <td style={{ fontSize: 12 }}>
                                                        {row.article || (bc ? '—' : '')}
                                                        {isAutoAdded && <span style={{ fontSize: 10, color: 'var(--color-accent)', marginLeft: 4, fontStyle: 'italic' }}>auto</span>}
                                                    </td>
                                                    <td style={{ fontSize: 12 }}>{item?.subject || (bc ? '—' : '')}</td>
                                                    <td>
                                                        <input value={row.barcode} onChange={e => updateRow(i, 'barcode', e.target.value)}
                                                            placeholder={t('col_barcode')}
                                                            readOnly={isAutoAdded}
                                                            style={{ ...cellInput, borderColor: unknown ? '#ef4444' : 'var(--color-border)', color: unknown ? '#ef4444' : 'var(--color-text)', fontFamily: 'monospace', opacity: isAutoAdded ? 0.7 : 1 }}
                                                            autoComplete="off" />
                                                    </td>
                                                    <td>
                                                        {(() => {
                                                            const orderBox = (item?.box_size || '').trim();
                                                            const pasteBox = (row.box_size || '').trim();
                                                            const differs = !!(item && pasteBox && normalizeBox(pasteBox) !== normalizeBox(orderBox));
                                                            return (
                                                                <input value={row.box_size} onChange={e => updateRow(i, 'box_size', e.target.value)}
                                                                    placeholder={orderBox || 'L*W*H'}
                                                                    readOnly={isAutoAdded}
                                                                    style={{ ...cellInput, fontSize: 12, borderColor: differs ? '#f59e0b' : 'var(--color-border)', opacity: isAutoAdded ? 0.7 : 1 }}
                                                                    autoComplete="off" />
                                                            );
                                                        })()}
                                                    </td>
                                                    <td>
                                                        {(() => {
                                                            const orderPpb = item?.pcs_per_box || 0;
                                                            const pastePpb = parseInt(row.pcs_per_box) || 0;
                                                            const differs = !!(item && pastePpb > 0 && pastePpb !== orderPpb);
                                                            return (
                                                                <input type="number" value={row.pcs_per_box} onChange={e => updateRow(i, 'pcs_per_box', e.target.value)}
                                                                    placeholder={orderPpb ? String(orderPpb) : '0'} min={0}
                                                                    readOnly={isAutoAdded}
                                                                    style={{ ...cellInput, fontSize: 12, textAlign: 'right', borderColor: differs ? '#f59e0b' : 'var(--color-border)', opacity: isAutoAdded ? 0.7 : 1 }}
                                                                    autoComplete="off" />
                                                            );
                                                        })()}
                                                    </td>
                                                    <td style={{ fontSize: 12, textAlign: 'right', color: 'var(--color-text-muted)' }}>
                                                        {item ? item.remaining_qty : bc ? '—' : ''}
                                                    </td>
                                                    <td>
                                                        <input type="number" value={row.qty} onChange={e => updateRow(i, 'qty', e.target.value)}
                                                            placeholder="0" min={0}
                                                            readOnly={isAutoAdded}
                                                            style={{ ...cellInput, borderColor: exceeds ? '#f59e0b' : 'var(--color-border)', opacity: isAutoAdded ? 0.7 : 1 }}
                                                            autoComplete="off" />
                                                    </td>
                                                    <td>
                                                        <input type="number" value={row.price} onChange={e => updateRow(i, 'price', e.target.value)}
                                                            placeholder="0.00" min={0} step="0.0001"
                                                            style={{
                                                                ...cellInput,
                                                                borderColor: bc && item && qty > 0 && !(parseFloat(row.price) > 0) ? '#ef4444'
                                                                    : (() => {
                                                                        const p = parseFloat(row.price);
                                                                        const op = parseFloat(item?.price_cny || '0') || 0;
                                                                        return item && p > 0 && Math.abs(p - op) > 0.0001 ? '#f59e0b' : 'var(--color-border)';
                                                                    })(),
                                                            }}
                                                            autoComplete="off" />
                                                    </td>
                                                    <td style={{ fontSize: 11, textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                        {item ? (() => {
                                                            const op = parseFloat(item.price_cny) || 0;
                                                            const p = parseFloat(row.price);
                                                            const differs = p > 0 && Math.abs(p - op) > 0.0001;
                                                            return (
                                                                <span style={{ color: differs ? '#f59e0b' : 'var(--color-text-muted)', fontWeight: differs ? 600 : 400 }}>
                                                                    {formatNumber(op, 2)}
                                                                </span>
                                                            );
                                                        })() : ''}
                                                    </td>
                                                    <td style={{ fontSize: 12, textAlign: 'right' }}>
                                                        {qty > 0 && item?.pcs_per_box ? (
                                                            <BoxDropdown qty={qty} pcsPerBox={item.pcs_per_box} />
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
                                                         qty > 0 && !(parseFloat(row.price) > 0) ? <span style={{ color: '#ef4444', fontSize: 11 }}>¥?</span> :
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
                                        {t('btn_cancel')}
                                    </button>
                                    {invalidRows.length > 0 && (
                                        <button className="btn btn-secondary btn-sm" onClick={() => {
                                            const text = invalidRows.map(r => `${r.barcode.trim()}\t${r.qty}`).join('\n');
                                            navigator.clipboard.writeText(text);
                                        }}>
                                            ✗ ({invalidRows.length})
                                        </button>
                                    )}
                                    {exceededRows.length > 0 && (
                                        <button className="btn btn-secondary btn-sm" onClick={() => {
                                            const text = exceededRows.map(r => {
                                                const item = allItemMap[r.barcode.trim()];
                                                return `${r.barcode.trim()}\t${r.qty}\t${item?.remaining_qty || 0}`;
                                            }).join('\n');
                                            navigator.clipboard.writeText(`${t('col_barcode')}\t${t('col_qty')}\t${t('col_available')}\n${text}`);
                                        }}>
                                            ! ({exceededRows.length})
                                        </button>
                                    )}
                                </div>
                                <button className="btn btn-primary" onClick={handlePasteSubmit} disabled={!pasteCanSave || submitting}
                                    style={{ minWidth: 160, opacity: pasteCanSave ? 1 : 0.5 }}>
                                    {submitting ? t('add_items_adding') : `${t('add_items_count')} (${validRows.length})`}
                                </button>
                            </div>

                            {showMismatchModal && (
                                <PriceMismatchModal
                                    rows={mismatchRows}
                                    allItemMap={allItemMap}
                                    onCancel={() => setShowMismatchModal(false)}
                                    onKeepOrder={handleKeepOrderPrices}
                                    onOverwrite={handleOverwriteOrderPrices}
                                    submitting={submitting}
                                />
                            )}
                        </>
                    )}
                </>
            )}
        </div>
    );
}

// ─── Price Mismatch Modal ─────────────────────────────────────────────────

function PriceMismatchModal({ rows, allItemMap, onCancel, onKeepOrder, onOverwrite, submitting }: {
    rows: PasteRow[];
    allItemMap: Record<string, AvailableItem & { source_order: string; source_factory: string }>;
    onCancel: () => void;
    onKeepOrder: () => void;
    onOverwrite: () => void;
    submitting: boolean;
}) {
    const { t } = useT();
    const [mounted, setMounted] = useState(false);
    useEffect(() => { setMounted(true); }, []);
    if (!mounted) return null;

    const totalDelta = rows.reduce((s, r) => {
        const item = allItemMap[r.barcode.trim()];
        const p = parseFloat(r.price) || 0;
        const op = parseFloat(item?.price_cny || '0') || 0;
        const qty = parseInt(r.qty) || 0;
        return s + (p - op) * qty;
    }, 0);

    return createPortal(
        <div
            onClick={onCancel}
            style={{
                position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                zIndex: 10000, padding: 16,
            }}
        >
            <div
                onClick={e => e.stopPropagation()}
                className="glass-card"
                style={{
                    maxWidth: 900, width: '100%', maxHeight: '85vh', overflowY: 'auto',
                    padding: 24, background: 'var(--color-bg)',
                }}
            >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <h3 style={{ margin: 0, fontSize: 18, fontWeight: 700 }}>{t('paste_mismatch_title')}</h3>
                    <button onClick={onCancel} disabled={submitting} style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer', color: 'var(--color-text-muted)', padding: 4 }}>✕</button>
                </div>

                <div style={{ display: 'flex', gap: 24, fontSize: 13, marginBottom: 16, color: 'var(--color-text-muted)' }}>
                    <div>{t('paste_mismatch_count')}: <b style={{ color: 'var(--color-text)' }}>{rows.length}</b></div>
                    {Math.abs(totalDelta) > 0.0001 && (
                        <div>{t('price_resync_delta')}: <b style={{ color: totalDelta >= 0 ? 'var(--color-danger)' : 'var(--color-success)' }}>
                            {totalDelta >= 0 ? '+' : ''}{formatNumber(totalDelta, 2)} ¥
                        </b></div>
                    )}
                </div>

                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                        <tr style={{ borderBottom: '2px solid var(--color-border)', color: 'var(--color-text-muted)' }}>
                            <th style={{ textAlign: 'left', padding: '8px 6px', fontWeight: 500 }}>{t('col_barcode')}</th>
                            <th style={{ textAlign: 'left', padding: '8px 6px', fontWeight: 500 }}>{t('col_article')}</th>
                            <th style={{ textAlign: 'right', padding: '8px 6px', fontWeight: 500 }}>{t('col_qty')}</th>
                            <th style={{ textAlign: 'right', padding: '8px 6px', fontWeight: 500 }}>{t('col_price_cny')} ({t('paste_col_order')}→{t('paste_col_paste')})</th>
                            <th style={{ textAlign: 'left', padding: '8px 6px', fontWeight: 500 }}>{t('col_box_spec')}</th>
                            <th style={{ textAlign: 'right', padding: '8px 6px', fontWeight: 500 }}>{t('col_pcs_per_box')}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((r, i) => {
                            const item = allItemMap[r.barcode.trim()];
                            const p = parseFloat(r.price) || 0;
                            const op = parseFloat(item?.price_cny || '0') || 0;
                            const qty = parseInt(r.qty) || 0;
                            const priceDiffers = Math.abs(p - op) > 0.0001;
                            const orderBox = (item?.box_size || '').trim();
                            const pasteBox = (r.box_size || '').trim();
                            const normBox = (s: string) => s.toLowerCase().replace(/[*xхх×]/g, '×');
                            const boxDiffers = !!(pasteBox && normBox(pasteBox) !== normBox(orderBox));
                            const orderPpb = item?.pcs_per_box || 0;
                            const pastePpb = parseInt(r.pcs_per_box) || 0;
                            const ppbDiffers = !!(pastePpb && pastePpb !== orderPpb);
                            return (
                                <tr key={i} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                    <td style={{ padding: '6px', fontFamily: 'monospace' }}>{r.barcode}</td>
                                    <td style={{ padding: '6px', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={item?.article_seller || ''}>{item?.article_seller || '—'}</td>
                                    <td style={{ padding: '6px', textAlign: 'right' }}>{formatNumber(qty, 0)}</td>
                                    <td style={{ padding: '6px', textAlign: 'right', color: priceDiffers ? '#f59e0b' : 'var(--color-text-muted)', fontWeight: priceDiffers ? 600 : 400 }}>
                                        {priceDiffers ? `${formatNumber(op, 2)} → ${formatNumber(p, 2)}` : formatNumber(op, 2)}
                                    </td>
                                    <td style={{ padding: '6px', color: boxDiffers ? '#f59e0b' : 'var(--color-text-muted)', fontWeight: boxDiffers ? 600 : 400 }}>
                                        {boxDiffers ? `${orderBox || '—'} → ${pasteBox}` : (orderBox || '—')}
                                    </td>
                                    <td style={{ padding: '6px', textAlign: 'right', color: ppbDiffers ? '#f59e0b' : 'var(--color-text-muted)', fontWeight: ppbDiffers ? 600 : 400 }}>
                                        {ppbDiffers ? `${orderPpb || '—'} → ${pastePpb}` : (orderPpb || '—')}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>

                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 16, padding: 10, background: 'rgba(245,158,11,0.05)', borderRadius: 6 }}>
                    ℹ️ {t('paste_mismatch_hint')}
                </div>

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                    <button className="btn btn-secondary btn-sm" onClick={onCancel} disabled={submitting}>{t('price_resync_cancel')}</button>
                    <button className="btn btn-secondary btn-sm" onClick={onKeepOrder} disabled={submitting}>{t('paste_keep_order')}</button>
                    <button className="btn btn-primary btn-sm" onClick={onOverwrite} disabled={submitting}>
                        {submitting ? t('price_resync_applying') : t('paste_overwrite_order')}
                    </button>
                </div>
            </div>
        </div>,
        document.body
    );
}

// ─── Documents Tab ────────────────────────────────────────────────────────

function formatFileSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function DocumentsTab({ orderNo }: { orderNo: string }) {
    const { t } = useT();
    const DOC_TYPE_LABELS = getDocTypeLabels(t);
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
            setError(e instanceof Error ? e.message : t('msg_loading_error'));
        }
        setLoading(false);
    }, [orderNo, t]);

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
            alert(e instanceof Error ? e.message : t('vdetail_docs_upload_error'));
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
            alert(e instanceof Error ? e.message : t('msg_error'));
        }
    };

    const handleDelete = async (docId: number) => {
        if (!confirm(t('btn_delete') + '?')) return;
        setDeletingId(docId);
        try {
            await api.deleteVehicleDocument(orderNo, docId);
            loadDocs();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_delete_error'));
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
                    {t('vdetail_docs_upload')}
                </div>
                <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
                    <div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>{t('vdetail_docs_col_type')}</div>
                        <select value={docType} onChange={e => setDocType(e.target.value)} style={inputStyle}>
                            {Object.entries(DOC_TYPE_LABELS).map(([k, v]) => (
                                <option key={k} value={k}>{v}</option>
                            ))}
                        </select>
                    </div>
                    <div style={{ flex: 1, minWidth: 200 }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>{t('vdetail_docs_col_file')}</div>
                        <input type="file" onChange={e => setFile(e.target.files?.[0] || null)} style={{ fontSize: 13 }} />
                    </div>
                    <div style={{ flex: 1, minWidth: 150 }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>{t('col_note')}</div>
                        <input value={note} onChange={e => setNote(e.target.value)} placeholder="—" style={{ ...inputStyle, width: '100%' }} />
                    </div>
                    <button className="btn btn-primary btn-sm" onClick={handleUpload} disabled={!file || uploading}>
                        {uploading ? t('msg_saving') : t('vdetail_docs_upload')}
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
                    <div style={{ textAlign: 'center', padding: 32, color: 'var(--color-text-muted)' }}>{t('vdetail_docs_empty')}</div>
                ) : (
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                        <thead>
                            <tr style={{ borderBottom: '2px solid var(--color-border)' }}>
                                <th style={{ textAlign: 'left', padding: '8px 6px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>{t('vdetail_docs_col_file')}</th>
                                <th style={{ textAlign: 'left', padding: '8px 6px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>{t('vdetail_docs_col_type')}</th>
                                <th style={{ textAlign: 'right', padding: '8px 6px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>KB</th>
                                <th style={{ textAlign: 'left', padding: '8px 6px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>{t('col_note')}</th>
                                <th style={{ textAlign: 'left', padding: '8px 6px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>{t('vdetail_docs_col_uploaded')}</th>
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
                                                {t('vdetail_docs_download')}
                                            </button>
                                            <button className="btn btn-danger btn-sm" onClick={() => handleDelete(doc.id)} disabled={deletingId === doc.id}
                                                style={{ fontSize: 11, padding: '3px 8px', opacity: deletingId === doc.id ? 0.4 : 1 }}>
                                                {deletingId === doc.id ? '...' : t('btn_delete')}
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
    const { t } = useT();
    const STATUS_LABELS = getStatusLabels(t);
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
            setError(e instanceof Error ? e.message : t('msg_loading_error'));
        }
        setLoading(false);
    }, [orderNo, t]);

    useEffect(() => { loadHistory(); }, [loadHistory]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}><div className="spinner" style={{ margin: '0 auto' }} /></div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;
    if (entries.length === 0) return <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>{t('vdetail_history_empty')}</div>;

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
    const { t } = useT();
    const [loading, setLoading] = useState(false);
    const handleClick = async () => {
        setLoading(true);
        try {
            await api.recalcVehicle(orderNo);
            onDone();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('vehicle_items_recalc_error'));
        }
        setLoading(false);
    };
    return (
        <button className="btn btn-secondary btn-sm" onClick={handleClick} disabled={loading}>
            {loading ? t('vehicle_items_recalcing') : t('vehicle_items_recalc')}
        </button>
    );
}

function ShipButton({ orderNo, onDone, disabled = false, disabledTooltip }: {
    orderNo: string; onDone: () => void; disabled?: boolean; disabledTooltip?: string;
}) {
    const { t } = useT();
    const [loading, setLoading] = useState(false);
    const handleClick = async () => {
        if (!confirm(t('vdetail_ship_confirm'))) return;
        setLoading(true);
        try {
            await api.updateVehicleStatus(orderNo, { status: 'SHIPPED' });
            onDone();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_error'));
        }
        setLoading(false);
    };
    return (
        <button
            className="btn btn-primary btn-sm"
            onClick={handleClick}
            disabled={loading || disabled}
            title={disabled ? disabledTooltip : undefined}
        >
            {loading ? '...' : `🚛 ${t('btn_ship')}`}
        </button>
    );
}

function StatusTransitionButton({ orderNo, nextStatus, label, icon, onDone, disabled = false, disabledTooltip }: {
    orderNo: string; nextStatus: VehicleStatus; label: string; icon: string; onDone: () => void;
    disabled?: boolean; disabledTooltip?: string;
}) {
    const { t } = useT();
    const [loading, setLoading] = useState(false);
    const handleClick = async () => {
        let dt_number: string | undefined;
        if (nextStatus === 'CUSTOMS') {
            dt_number = prompt(`${t('vdetail_field_customs_no')}:`) || undefined;
        }
        setLoading(true);
        try {
            await api.updateVehicleStatus(orderNo, { status: nextStatus, dt_number });
            onDone();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_error'));
        }
        setLoading(false);
    };
    return (
        <button
            className="btn btn-secondary btn-sm"
            onClick={handleClick}
            disabled={loading || disabled}
            title={disabled ? disabledTooltip : undefined}
        >
            {loading ? '...' : `${icon} ${label}`}
        </button>
    );
}

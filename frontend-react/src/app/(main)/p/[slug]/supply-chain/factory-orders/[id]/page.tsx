'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate, formatDateTime, exportToExcel, calcTotalBoxesWithMix } from '@/lib/utils';
import PageHeader from '@/components/PageHeader';
import BoxDetailCell, { BoxDetailExpandRow } from '@/components/BoxDetailCell';
import type { FactoryOrder, FactoryOrderItem, FactoryOrderItemUpdate, FactoryOrderHistory, Nomenclature, Supplier } from '@/types/api';
import { LanguageProvider, useT, LanguageToggle, currencySymbol } from '../../i18n';

// ─── Helpers ──────────────────────────────────────────────────────────────

/** Mix-group color palette for visual grouping */
const MIX_COLORS = ['#0071e3', '#34c759', '#ff9f0a', '#af52de', '#ff375f', '#00c7be', '#ff6482', '#5e5ce6'];
function getMixColor(mixGroupId: string, allMixIds: string[]): string {
    const idx = allMixIds.indexOf(mixGroupId);
    return MIX_COLORS[idx % MIX_COLORS.length];
}

const parseNum = (s: string) => (s || '').trim().replace(/\s/g, '').replace(',', '.').replace(/[^\d.]/g, '');

/** Нормализация габаритов: "60,40,40" / "60/40/40" / "60*40*40" / "60×40×40" → "60x40x40" */
const normalizeBoxSize = (s: string): string => {
    const trimmed = s.trim();
    if (!trimmed) return '';
    return trimmed.replace(/[×*,/\\]/g, 'x');
};

/** Вычислить объём из box_size "60x40x40" (см → м³), qty, pcs_per_box */
const calcVolumeM3 = (boxSize: string, qty: number, pcsPerBox: number | null): number => {
    if (!boxSize || !qty) return 0;
    const parts = normalizeBoxSize(boxSize).split('x').map(Number);
    if (parts.length < 3 || parts.some(isNaN)) return 0;
    const volumePerBoxM3 = (parts[0] * parts[1] * parts[2]) / 1_000_000; // см³ → м³
    const boxes = pcsPerBox && pcsPerBox > 0 ? Math.ceil(qty / pcsPerBox) : 0;
    return boxes > 0 ? boxes * volumePerBoxM3 : 0;
};

// ─── Status constants (functions that accept t) ─────────────────────────

const getFoStatusLabels = (t: (k: string) => string): Record<string, string> => ({
    FORMING: t('factory_status_forming'),
    READY: t('factory_status_ready'),
    DISTRIBUTED: t('factory_status_distributed'),
});

const FO_STATUS_COLORS: Record<string, string> = {
    FORMING: 'var(--color-text-muted)',
    READY: 'var(--color-warning)',
    DISTRIBUTED: 'var(--color-success)',
};

const getFoEventLabels = (t: (k: string) => string): Record<string, string> => ({
    created: t('detail_event_created'),
    status_change: t('detail_event_status_change'),
    distributed: t('detail_event_distributed'),
    items_added: t('detail_event_items_added'),
    items_removed: t('detail_event_items_removed'),
});

const FO_EVENT_ICONS: Record<string, string> = {
    created: '+',
    status_change: '~',
    distributed: '>',
    items_added: '+',
    items_removed: '-',
};

function DistributionBadge({ totalQty, totalAssigned }: { totalQty: number; totalAssigned: number }) {
    const { t } = useT();
    const pct = totalQty > 0 ? Math.round((totalAssigned / totalQty) * 100) : 0;
    let label: string;
    let badgeClass: string;
    if (pct === 0) {
        label = t('detail_badge_undistributed');
        badgeClass = 'badge badge-secondary';
    } else if (pct >= 100) {
        label = t('detail_badge_distributed');
        badgeClass = 'badge badge-success';
    } else {
        label = `${pct}%`;
        badgeClass = 'badge badge-warning';
    }
    return <span className={badgeClass}>{label}</span>;
}

function MiniProgressBar({ qty, assignedQty }: { qty: number; assignedQty: number }) {
    const pct = qty > 0 ? Math.min(100, Math.round((assignedQty / qty) * 100)) : 0;
    const color = pct >= 100 ? 'var(--color-success)' : pct > 0 ? 'var(--color-warning)' : 'var(--color-border)';
    const remaining = qty - assignedQty;
    return (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{
                    fontSize: 12, fontWeight: 600,
                    color: pct >= 100 ? 'var(--color-success)' : pct > 0 ? 'var(--color-warning)' : 'var(--color-text-dim)',
                }}>
                    {formatNumber(assignedQty, 0)}<span style={{ fontWeight: 400, color: 'var(--color-text-dim)' }}>/{formatNumber(qty, 0)}</span>
                </span>
            </div>
            <div style={{
                width: 56, height: 4, borderRadius: 2,
                background: 'var(--color-bg-secondary)', overflow: 'hidden',
            }}>
                <div style={{
                    width: `${Math.max(pct, 2)}%`, height: '100%', borderRadius: 2,
                    background: color, transition: 'width 0.3s ease',
                    opacity: pct === 0 ? 0.3 : 1,
                }} />
            </div>
        </div>
    );
}

function InfoField({ label, value, editing, input }: {
    label: string; value?: string | null; editing?: boolean; input?: React.ReactNode;
}) {
    return (
        <div>
            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 4 }}>{label}</div>
            {editing && input ? input : (
                <div style={{ fontSize: 14, fontWeight: 600, color: value ? 'var(--color-text)' : 'var(--color-text-muted)' }}>
                    {value || '—'}
                </div>
            )}
        </div>
    );
}

function SummaryKpi({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
    return (
        <div style={{ textAlign: 'center', padding: '8px 4px', background: accent ? 'rgba(59,130,246,0.04)' : 'var(--color-bg-secondary)', borderRadius: 8 }}>
            <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.3px' }}>{label}</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: accent ? 'var(--color-primary)' : 'var(--color-text)' }}>{value}</div>
        </div>
    );
}

// ─── Order Info Card ──────────────────────────────────────────────────────

function OrderInfoCard({ order, onUpdated }: { order: FactoryOrder; onUpdated: () => void }) {
    const { t } = useT();
    const [editing, setEditing] = useState(false);
    const [saving, setSaving] = useState(false);
    const [suppliers, setSuppliers] = useState<Supplier[]>([]);
    const [form, setForm] = useState({
        order_number: order.order_number || '',
        factory_name: order.factory_name || '',
        supplier_id: order.supplier_id ?? undefined as number | undefined,
        order_date: order.order_date || '',
        expected_ready_date: order.expected_ready_date || '',
        total_cny: order.total_cny != null ? String(order.total_cny) : '',
        note: order.note || '',
    });

    useEffect(() => {
        api.getSuppliers().then(setSuppliers).catch(() => {});
    }, []);

    useEffect(() => {
        setForm({
            order_number: order.order_number || '',
            factory_name: order.factory_name || '',
            supplier_id: order.supplier_id ?? undefined,
            order_date: order.order_date || '',
            expected_ready_date: order.expected_ready_date || '',
            total_cny: order.total_cny != null ? String(order.total_cny) : '',
            note: order.note || '',
        });
    }, [order]);

    const handleSupplierChange = (supplierId: number | undefined) => {
        const supplier = suppliers.find(s => s.id === supplierId);
        setForm(f => ({
            ...f,
            supplier_id: supplierId,
            factory_name: supplier ? supplier.name : f.factory_name,
        }));
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            await api.updateFactoryOrder(order.id, {
                order_number: form.order_number || order.order_number,
                factory_name: form.factory_name || undefined,
                supplier_id: form.supplier_id,
                order_date: form.order_date || undefined,
                expected_ready_date: form.expected_ready_date || undefined,
                total_cny: form.total_cny ? Number(parseNum(form.total_cny)) : undefined,
                note: form.note || undefined,
            });
            setEditing(false);
            onUpdated();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_save_error'));
        }
        setSaving(false);
    };

    const supplierDisplayValue = order.supplier?.name || order.factory_name || undefined;

    const editInput: React.CSSProperties = {
        padding: '4px 8px', borderRadius: 6, border: '1px solid var(--color-border)',
        background: 'var(--color-bg)', color: 'var(--color-text)', fontSize: 13, width: '100%',
    };

    const items = order.items || [];
    const totalQty = items.reduce((s, i) => s + i.qty, 0);
    const totalAssigned = items.reduce((s, i) => s + i.assigned_qty, 0);
    const totalCny = items.reduce((s, i) => s + i.qty * Number(i.price_cny), 0);
    const distPct = totalQty > 0 ? Math.round((totalAssigned / totalQty) * 100) : 0;

    return (
        <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                    {t('detail_field_order_number')}
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    {!editing && (
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

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px 24px' }}>
                <InfoField label={t('detail_field_order_number')} value={order.order_number} editing={editing}
                    input={<input value={form.order_number} onChange={e => setForm(f => ({ ...f, order_number: e.target.value }))} style={editInput} />} />
                <InfoField label={t('detail_field_supplier')} value={supplierDisplayValue} editing={editing}
                    input={
                        <select
                            value={form.supplier_id ?? ''}
                            onChange={e => handleSupplierChange(e.target.value ? Number(e.target.value) : undefined)}
                            style={editInput}
                        >
                            <option value="">—</option>
                            {suppliers.map(s => (
                                <option key={s.id} value={s.id}>
                                    {s.name} ({s.country === 'CHINA' ? 'CN' : 'RU'})
                                </option>
                            ))}
                        </select>
                    } />
                <InfoField label={t('col_created_date')} value={order.order_date ? formatDate(order.order_date) : undefined} editing={editing}
                    input={<input type="date" value={form.order_date} onChange={e => setForm(f => ({ ...f, order_date: e.target.value }))} style={editInput} />} />
                <InfoField label={t('detail_field_ready_date')} value={order.expected_ready_date ? formatDate(order.expected_ready_date) : undefined} editing={editing}
                    input={<input type="date" value={form.expected_ready_date} onChange={e => setForm(f => ({ ...f, expected_ready_date: e.target.value }))} style={editInput} />} />
            </div>

            {/* Summary KPIs */}
            {(() => {
                const totalBoxes = calcTotalBoxesWithMix(items);
                const totalVolume = items.reduce((s, i) => s + calcVolumeM3(i.box_size || '', i.qty, i.pcs_per_box ?? null), 0);
                const progressColor = distPct >= 100 ? 'var(--color-success)' : distPct > 0 ? 'var(--color-warning)' : 'var(--color-border)';
                return (
                    <>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12, marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--color-border)' }}>
                            <SummaryKpi label={t('detail_kpi_items')} value={String(items.length)} />
                            <SummaryKpi label={t('detail_kpi_qty')} value={`${formatNumber(totalQty, 0)} ${t('unit_pcs')}`} />
                            <SummaryKpi label={t('detail_kpi_boxes')} value={totalBoxes > 0 ? formatNumber(totalBoxes, 0) : '—'} />
                            <SummaryKpi label={t('detail_kpi_volume')} value={totalVolume > 0 ? `${formatNumber(totalVolume, 1)} m³` : '—'} />
                            <SummaryKpi label={t('detail_kpi_amount')} value={totalCny > 0 ? `${formatNumber(totalCny, 0)} ${currencySymbol(order.supplier?.currency)}` : '—'} accent />
                        </div>
                        {/* Distribution progress */}
                        {totalQty > 0 && (
                            <div style={{ marginTop: 12 }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                                    <span style={{ fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>
                                        {t('detail_kpi_distribution')}: {formatNumber(totalAssigned, 0)} / {formatNumber(totalQty, 0)} {t('unit_pcs')}
                                    </span>
                                    <span style={{ fontSize: 11, fontWeight: 700, color: progressColor }}>
                                        {distPct}%
                                    </span>
                                </div>
                                <div style={{
                                    height: 8, borderRadius: 4,
                                    background: 'var(--color-bg-secondary)', overflow: 'hidden',
                                }}>
                                    <div style={{
                                        width: `${distPct}%`, height: '100%', borderRadius: 4,
                                        background: progressColor,
                                        transition: 'width 0.4s ease',
                                    }} />
                                </div>
                            </div>
                        )}
                    </>
                );
            })()}
        </div>
    );
}

// ─── Items Table with bulk editing (spreadsheet-style) ───────────────────

interface EditRow {
    id: number;
    barcode: string;
    subject: string;
    article_seller: string;
    qty: string;
    price_cny: string;
    box_size: string;
    pcs_per_box: string;
    weight_kg: string;
}

function itemToEditRow(item: FactoryOrderItem): EditRow {
    return {
        id: item.id,
        barcode: item.barcode,
        subject: item.subject || '',
        article_seller: item.article_seller || '',
        qty: String(item.qty),
        price_cny: String(item.price_cny),
        box_size: item.box_size || '',
        pcs_per_box: item.pcs_per_box ? String(item.pcs_per_box) : '',
        weight_kg: item.weight_kg ? String(item.weight_kg) : '',
    };
}

function ItemsTable({ items, orderId, nomMap, onChanged, currency }: {
    items: FactoryOrderItem[]; orderId: number; nomMap: Map<string, Nomenclature>; onChanged: () => void; currency?: string;
}) {
    const cs = currencySymbol(currency);
    const { t } = useT();
    const [editing, setEditing] = useState(false);
    const [editRows, setEditRows] = useState<EditRow[]>([]);
    const [saving, setSaving] = useState(false);
    const [deletingId, setDeletingId] = useState<number | null>(null);
    const [expandedId, setExpandedId] = useState<number | null>(null);
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [mixSaving, setMixSaving] = useState(false);
    const [mixFormOpen, setMixFormOpen] = useState(false);
    const [mixFormData, setMixFormData] = useState<{
        boxSize: string;
        items: { id: number; barcode: string; subject: string; qty: number; pcsPerBox: string }[];
    }>({ boxSize: '', items: [] });

    // Unique mix group IDs for color assignment
    const mixGroupIds = useMemo(() => {
        const ids = new Set<string>();
        items.forEach(i => { if (i.mix_group_id) ids.add(i.mix_group_id); });
        return Array.from(ids);
    }, [items]);

    const toggleSelect = (id: number) => {
        setSelected(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id); else next.add(id);
            return next;
        });
    };
    const toggleAll = () => {
        if (selected.size === items.length) setSelected(new Set());
        else setSelected(new Set(items.map(i => i.id)));
    };

    const handleOpenMixForm = () => {
        if (selected.size < 2) return;
        const selectedItems = items.filter(i => selected.has(i.id));
        setMixFormData({
            boxSize: '',
            items: selectedItems.map(i => ({
                id: i.id,
                barcode: i.barcode,
                subject: i.subject || '',
                qty: i.qty,
                pcsPerBox: i.pcs_per_box ? String(i.pcs_per_box) : '',
            })),
        });
        setMixFormOpen(true);
    };

    const handleSaveMix = async () => {
        setMixSaving(true);
        try {
            const mixItems = mixFormData.items.map(i => ({ id: i.id, pcs_per_box: parseInt(i.pcsPerBox) || 0 }));
            await api.setMixGroup(orderId, mixItems, mixFormData.boxSize);
            setMixFormOpen(false);
            setSelected(new Set());
            setMixFormData({ boxSize: '', items: [] });
            onChanged();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_save_error'));
        }
        setMixSaving(false);
    };

    const handleCancelMix = () => {
        setMixFormOpen(false);
        setMixFormData({ boxSize: '', items: [] });
    };

    const handleRemoveMix = async (mixGroupId: string) => {
        setMixSaving(true);
        try {
            await api.removeMixGroup(orderId, mixGroupId);
            onChanged();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_delete_error'));
        }
        setMixSaving(false);
    };

    const startBulkEdit = () => {
        setEditRows(items.map(itemToEditRow));
        setEditing(true);
    };

    const cancelBulkEdit = () => {
        setEditing(false);
        setEditRows([]);
    };

    const updateRow = (idx: number, field: keyof EditRow, value: string) => {
        setEditRows(rows => rows.map((r, i) => {
            if (i !== idx) return r;
            const updated = { ...r, [field]: field === 'box_size' ? normalizeBoxSize(value) : value };
            if (field === 'barcode') {
                const nom = nomMap.get(value.trim());
                updated.subject = nom?.subject || '';
                updated.article_seller = nom?.article_seller || '';
            }
            return updated;
        }));
    };

    const handleSaveAll = async () => {
        setSaving(true);
        try {
            const promises = editRows.map(row => {
                const orig = items.find(i => i.id === row.id);
                if (!orig) return null;
                const changed =
                    row.barcode !== orig.barcode ||
                    row.subject !== (orig.subject || '') ||
                    row.article_seller !== (orig.article_seller || '') ||
                    Number(row.qty) !== orig.qty ||
                    Number(row.price_cny) !== Number(orig.price_cny) ||
                    row.box_size !== (orig.box_size || '') ||
                    row.pcs_per_box !== (orig.pcs_per_box ? String(orig.pcs_per_box) : '') ||
                    row.weight_kg !== (orig.weight_kg ? String(orig.weight_kg) : '');
                if (!changed) return null;
                return api.updateFactoryOrderItem(orderId, row.id, {
                    barcode: row.barcode,
                    subject: row.subject || undefined,
                    article_seller: row.article_seller || undefined,
                    qty: Number(row.qty) || 0,
                    price_cny: Number(row.price_cny) || 0,
                    box_size: row.box_size || undefined,
                    pcs_per_box: row.pcs_per_box ? Number(row.pcs_per_box) : undefined,
                    weight_kg: row.weight_kg ? Number(row.weight_kg) : undefined,
                });
            }).filter(Boolean);
            await Promise.all(promises);
            setEditing(false);
            onChanged();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_save_error'));
        }
        setSaving(false);
    };

    const handleDelete = async (item: FactoryOrderItem) => {
        if (!confirm(`${t('detail_confirm_delete_item')} ${item.barcode}`)) return;
        setDeletingId(item.id);
        try {
            await api.deleteFactoryOrderItem(orderId, item.id);
            onChanged();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_delete_error'));
        }
        setDeletingId(null);
    };

    const handleExport = () => {
        const rows = items.map(i => ({
            [t('col_barcode')]: i.barcode,
            [t('detail_col_subject')]: i.subject || '',
            [t('col_article')]: i.article_seller || '',
            [t('col_qty')]: i.qty,
            [t('col_distributed')]: i.assigned_qty,
            [t('col_remaining')]: i.qty - i.assigned_qty,
            [`${t('col_price')} ${cs}`]: Number(i.price_cny),
            [`${t('col_sum')} ${cs}`]: i.qty * Number(i.price_cny),
            [t('col_box_spec')]: i.box_size || '',
            [t('col_pcs_per_box')]: i.pcs_per_box || '',
            [t('col_boxes')]: i.mix_group_id ? t('detail_col_mix') : (i.pcs_per_box && i.pcs_per_box > 0 ? Math.ceil(i.qty / i.pcs_per_box) : ''),
            [t('detail_col_mix')]: i.mix_group_id ? i.mix_group_id.slice(0, 8) : '',
            [t('col_weight_1pc')]: i.weight_kg ? Number(i.weight_kg) : '',
        }));
        exportToExcel(rows, 'factory-order-items');
    };

    const th: React.CSSProperties = { textAlign: 'left', padding: '8px 6px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500, whiteSpace: 'nowrap' };
    const thR: React.CSSProperties = { ...th, textAlign: 'right' };
    const td: React.CSSProperties = { padding: '4px 4px', fontSize: 13 };
    const tdR: React.CSSProperties = { ...td, textAlign: 'right' };
    const inp: React.CSSProperties = {
        padding: '4px 6px', borderRadius: 4, border: '1px solid var(--color-border)',
        background: 'var(--color-bg)', color: 'var(--color-text)', fontSize: 12, width: '100%',
    };
    const inpNum: React.CSSProperties = { ...inp, textAlign: 'right', width: 70 };

    const totalQty = items.reduce((s, i) => s + i.qty, 0);
    const totalAssigned = items.reduce((s, i) => s + i.assigned_qty, 0);
    const totalCny = items.reduce((s, i) => s + i.qty * Number(i.price_cny), 0);

    return (
        <>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>
                    {t('detail_items_title')} ({items.length})
                </h3>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    {!editing && selected.size >= 2 && !mixFormOpen && (
                        <button className="btn btn-primary btn-sm" onClick={handleOpenMixForm}
                            style={{ fontSize: 12 }}>
                            {t('detail_col_mix')} ({selected.size})
                        </button>
                    )}
                    {!editing ? (
                        <>
                            <button className="btn btn-secondary btn-sm" onClick={startBulkEdit}>{t('btn_edit')}</button>
                            <button className="btn btn-secondary btn-sm" onClick={handleExport}>Excel</button>
                        </>
                    ) : (
                        <>
                            <button className="btn btn-secondary btn-sm" onClick={cancelBulkEdit}>{t('btn_cancel')}</button>
                            <button className="btn btn-primary btn-sm" onClick={handleSaveAll} disabled={saving}>
                                {saving ? t('msg_saving') : t('btn_save')}
                            </button>
                        </>
                    )}
                </div>
            </div>

            {/* Mix form */}
            {mixFormOpen && (
                <div style={{
                    margin: '12px 0', padding: 16, borderRadius: 12,
                    background: 'var(--color-bg)', border: '1px solid var(--color-accent)',
                }}>
                    <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12, color: 'var(--color-accent)' }}>
                        {t('detail_col_mix')}
                    </div>
                    <div style={{ marginBottom: 12 }}>
                        <label style={{ fontSize: 12, color: 'var(--color-text-muted)', display: 'block', marginBottom: 4 }}>
                            {t('col_box_spec')}
                        </label>
                        <input
                            value={mixFormData.boxSize}
                            onChange={e => setMixFormData(f => ({ ...f, boxSize: e.target.value }))}
                            onBlur={e => setMixFormData(f => ({ ...f, boxSize: normalizeBoxSize(e.target.value) }))}
                            placeholder="60x40x40"
                            style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--color-border)', background: 'var(--color-bg)', color: 'var(--color-text)', fontSize: 13, width: 160 }}
                        />
                    </div>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginBottom: 12 }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                                <th style={{ textAlign: 'left', padding: '6px 4px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>{t('col_barcode')}</th>
                                <th style={{ textAlign: 'left', padding: '6px 4px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>{t('detail_col_subject')}</th>
                                <th style={{ textAlign: 'right', padding: '6px 4px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>{t('col_total')}</th>
                                <th style={{ textAlign: 'right', padding: '6px 4px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>{t('col_pcs_per_box')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {mixFormData.items.map((item, idx) => (
                                <tr key={item.id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                    <td style={{ padding: '6px 4px', fontFamily: 'monospace', fontSize: 12 }}>{item.barcode}</td>
                                    <td style={{ padding: '6px 4px', fontSize: 12 }}>{item.subject || '—'}</td>
                                    <td style={{ padding: '6px 4px', textAlign: 'right' }}>{item.qty}</td>
                                    <td style={{ padding: '6px 4px', textAlign: 'right' }}>
                                        <input
                                            type="number"
                                            min={1}
                                            value={item.pcsPerBox}
                                            onChange={e => setMixFormData(f => ({
                                                ...f,
                                                items: f.items.map((it, i) => i === idx ? { ...it, pcsPerBox: e.target.value } : it),
                                            }))}
                                            style={{ padding: '4px 6px', borderRadius: 6, border: '1px solid var(--color-border)', background: 'var(--color-bg)', color: 'var(--color-text)', fontSize: 12, width: 70, textAlign: 'right' }}
                                        />
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    {(() => {
                        const totalMixBoxes = mixFormData.items.every(i => parseInt(i.pcsPerBox) > 0)
                            ? Math.min(...mixFormData.items.map(i => Math.floor(i.qty / (parseInt(i.pcsPerBox) || 1))))
                            : 0;
                        return (
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                                {t('detail_total')}: <b style={{ color: 'var(--color-text)' }}>{totalMixBoxes}</b> {t('detail_col_mix')}
                            </div>
                        );
                    })()}
                    <div style={{ display: 'flex', gap: 8 }}>
                        <button className="btn btn-secondary btn-sm" onClick={handleCancelMix}>{t('btn_cancel')}</button>
                        <button className="btn btn-primary btn-sm" onClick={handleSaveMix} disabled={mixSaving || !mixFormData.boxSize || mixFormData.items.some(i => !parseInt(i.pcsPerBox))}>
                            {mixSaving ? '...' : t('btn_save')}
                        </button>
                    </div>
                </div>
            )}

            <div style={{ overflow: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                    <thead>
                        <tr style={{ borderBottom: '2px solid var(--color-border)' }}>
                            {!editing && (
                                <th style={{ width: 32, textAlign: 'center', padding: '8px 2px' }}>
                                    <input type="checkbox" checked={selected.size === items.length && items.length > 0}
                                        onChange={toggleAll} style={{ cursor: 'pointer' }} />
                                </th>
                            )}
                            <th style={th}>{t('col_barcode')}</th>
                            <th style={th}>{t('detail_col_subject')}</th>
                            <th style={th}>{t('col_article')}</th>
                            <th style={thR}>{t('col_qty')}</th>
                            <th style={thR}>{t('col_progress')}</th>
                            <th style={thR}>{t('col_price')} {cs}</th>
                            <th style={thR}>{t('col_sum')} {cs}</th>
                            <th style={th}>{t('col_box_spec')}</th>
                            <th style={thR}>{t('col_pcs_per_box')}</th>
                            <th style={thR}>{t('col_boxes')}</th>
                            <th style={thR}>{t('col_weight_1pc')}</th>
                            {!editing && <th style={{ width: 36 }} />}
                        </tr>
                    </thead>
                    <tbody>
                        {editing ? editRows.map((row, idx) => {
                            const orig = items.find(i => i.id === row.id);
                            const assignedQty = orig?.assigned_qty || 0;
                            const qty = Number(row.qty) || 0;
                            const priceCny = Number(row.price_cny) || 0;
                            const remaining = qty - assignedQty;
                            return (
                                <tr key={row.id} style={{ borderBottom: '1px solid var(--color-border)', background: 'rgba(59,130,246,0.02)' }}>
                                    <td style={td}><input value={row.barcode} onChange={e => updateRow(idx, 'barcode', e.target.value)} style={{ ...inp, width: 120, fontFamily: 'monospace', fontSize: 12 }} /></td>
                                    <td style={td}><span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{nomMap.get(row.barcode)?.subject || row.subject || '—'}</span></td>
                                    <td style={td}><span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{nomMap.get(row.barcode)?.article_seller || row.article_seller || '—'}</span></td>
                                    <td style={tdR}><input type="number" value={row.qty} onChange={e => updateRow(idx, 'qty', e.target.value)} style={inpNum} /></td>
                                    <td style={tdR}><MiniProgressBar qty={qty} assignedQty={assignedQty} /></td>
                                    <td style={tdR}><input type="number" step="0.01" value={row.price_cny} onChange={e => updateRow(idx, 'price_cny', e.target.value)} style={inpNum} /></td>
                                    <td style={tdR}>{formatNumber(qty * priceCny, 0)}</td>
                                    <td style={td}><input value={row.box_size} onChange={e => updateRow(idx, 'box_size', e.target.value)} style={{ ...inp, width: 80 }} placeholder="40x30x25" /></td>
                                    <td style={tdR}><input type="number" value={row.pcs_per_box} onChange={e => updateRow(idx, 'pcs_per_box', e.target.value)} style={{ ...inpNum, width: 50 }} /></td>
                                    <td style={tdR}>{(Number(row.pcs_per_box) || 0) > 0 ? Math.ceil(qty / Number(row.pcs_per_box)) : '—'}</td>
                                    <td style={tdR}><input type="number" step="0.01" value={row.weight_kg} onChange={e => updateRow(idx, 'weight_kg', e.target.value)} style={{ ...inpNum, width: 60 }} /></td>
                                </tr>
                            );
                        }) : items.map(item => {
                            const canDelete = item.assigned_qty === 0;
                            const ppb = item.pcs_per_box || 0;
                            const mixColor = item.mix_group_id ? getMixColor(item.mix_group_id, mixGroupIds) : undefined;
                            return (
                                <React.Fragment key={item.id}>
                                <tr style={{
                                    borderBottom: expandedId === item.id ? 'none' : '1px solid var(--color-border)',
                                    borderLeft: mixColor ? `3px solid ${mixColor}` : undefined,
                                }}>
                                    <td style={{ textAlign: 'center', padding: '4px 2px' }}>
                                        <input type="checkbox" checked={selected.has(item.id)}
                                            onChange={() => toggleSelect(item.id)} style={{ cursor: 'pointer' }} />
                                    </td>
                                    <td style={{ ...td, fontFamily: 'monospace', fontSize: 12 }}>{item.barcode}</td>
                                    <td style={td}>{item.subject || <span style={{ color: 'var(--color-text-dim)' }}>—</span>}</td>
                                    <td style={td}>{item.article_seller || <span style={{ color: 'var(--color-text-dim)' }}>—</span>}</td>
                                    <td style={tdR}>{formatNumber(item.qty, 0)}</td>
                                    <td style={tdR}><MiniProgressBar qty={item.qty} assignedQty={item.assigned_qty} /></td>
                                    <td style={tdR}>{formatNumber(Number(item.price_cny), 2)}</td>
                                    <td style={tdR}>{formatNumber(item.qty * Number(item.price_cny), 0)}</td>
                                    <td style={td}>
                                        {item.mix_group_id
                                            ? (item.mix_box_size || item.box_size || '—')
                                            : (item.box_size || '—')}
                                    </td>
                                    <td style={tdR}>
                                        {item.mix_group_id
                                            ? (item.mix_pcs_per_box || item.pcs_per_box || '—')
                                            : (item.pcs_per_box || '—')}
                                    </td>
                                    <td style={tdR}>
                                        {item.mix_group_id ? (() => {
                                            const effectivePpb = item.mix_pcs_per_box || item.pcs_per_box;
                                            const mixBoxCount = effectivePpb && effectivePpb > 0 ? Math.ceil(item.qty / effectivePpb) : null;
                                            return (
                                                <span title={`${t('detail_mix_group')}: ${item.mix_group_id.slice(0, 8)}`}>
                                                    {mixBoxCount !== null ? (
                                                        <>
                                                            <span style={{ fontWeight: 600, color: mixColor }}>{mixBoxCount}</span>
                                                            <span style={{ fontSize: 10, fontWeight: 600, color: mixColor, marginLeft: 3 }}>{t('detail_col_mix')}</span>
                                                        </>
                                                    ) : (
                                                        <span style={{ fontSize: 11, fontWeight: 600, color: mixColor }}>{t('detail_col_mix')}</span>
                                                    )}
                                                </span>
                                            );
                                        })() : (
                                            <BoxDetailCell
                                                qty={item.qty}
                                                pcsPerBox={item.pcs_per_box}
                                                boxDetail={item.box_detail}
                                                expanded={expandedId === item.id}
                                                onToggle={ppb > 0 ? () => setExpandedId(expandedId === item.id ? null : item.id) : undefined}
                                            />
                                        )}
                                    </td>
                                    <td style={tdR}>{item.weight_kg ? formatNumber(Number(item.weight_kg), 2) : '—'}</td>
                                    <td style={{ ...td, textAlign: 'center', whiteSpace: 'nowrap' }}>
                                        {item.mix_group_id && (
                                            <button onClick={() => handleRemoveMix(item.mix_group_id!)} disabled={mixSaving}
                                                style={{ background: 'none', border: 'none', cursor: 'pointer', color: mixColor, fontSize: 12, opacity: 0.7, padding: 2 }}
                                                title={t('detail_col_mix')}>⊘</button>
                                        )}
                                        {canDelete && (
                                            <button onClick={() => handleDelete(item)} disabled={deletingId === item.id}
                                                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-danger)', fontSize: 14, opacity: deletingId === item.id ? 0.3 : 0.6, padding: 2 }}
                                                title={t('btn_delete')}>✕</button>
                                        )}
                                    </td>
                                </tr>
                                {expandedId === item.id && ppb > 0 && (
                                    <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                                        <BoxDetailExpandRow
                                            qty={item.qty}
                                            pcsPerBox={ppb}
                                            boxDetail={item.box_detail}
                                            colSpan={13}
                                            editable
                                            orderId={orderId}
                                            itemId={item.id}
                                            onSaved={onChanged}
                                        />
                                    </tr>
                                )}
                                </React.Fragment>
                            );
                        })}
                    </tbody>
                    {items.length > 1 && (
                        <tfoot>
                            <tr style={{ borderTop: '2px solid var(--color-border)', fontWeight: 600 }}>
                                <td colSpan={editing ? 3 : 4} style={{ ...td, fontSize: 12, color: 'var(--color-text-muted)' }}>{t('detail_total')}</td>
                                <td style={tdR}>{formatNumber(totalQty, 0)}</td>
                                <td style={tdR}><MiniProgressBar qty={totalQty} assignedQty={totalAssigned} /></td>
                                <td style={tdR} />
                                <td style={tdR}>{formatNumber(totalCny, 0)}</td>
                                <td colSpan={editing ? 4 : 5} />
                            </tr>
                        </tfoot>
                    )}
                </table>
            </div>
        </>
    );
}

// ─── Add Items Section (table with paste from clipboard) ─────────────────

interface PasteRow {
    barcode: string; qty: string; price_cny: string;
    box_size: string; pcs_per_box: string; weight_kg: string;
}

const emptyPasteRow = (): PasteRow => ({
    barcode: '', qty: '', price_cny: '',
    box_size: '', pcs_per_box: '', weight_kg: '',
});

function AddItemsSection({ orderId, nomMap, onAdded, alwaysOpen, onClose, currency }: {
    orderId: number; nomMap: Map<string, Nomenclature>; onAdded: () => void;
    alwaysOpen?: boolean; onClose?: () => void; currency?: string;
}) {
    const { t } = useT();
    const cs = currencySymbol(currency);
    const [open, setOpen] = useState(false);
    const [rows, setRows] = useState<PasteRow[]>(() => Array.from({ length: 5 }, emptyPasteRow));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    const updatePasteRow = (idx: number, field: keyof PasteRow, value: string) => {
        setRows(prev => {
            const next = [...prev];
            next[idx] = { ...next[idx], [field]: field === 'box_size' ? normalizeBoxSize(value) : value };
            return next;
        });
        // Auto-expand: if editing last row, add empty row
        if (idx === rows.length - 1) {
            setRows(prev => [...prev, emptyPasteRow()]);
        }
    };

    const handlePaste = (e: React.ClipboardEvent) => {
        const text = e.clipboardData.getData('text/plain');
        if (!text.includes('\t') && !text.includes('\n')) return;
        e.preventDefault();
        const lines = text.trim().split('\n').map(l => l.split('\t'));
        const newRows: PasteRow[] = [];
        for (const cols of lines) {
            if (cols.length < 1) continue;
            const barcode = (cols[0] || '').trim();
            if (!barcode) continue;
            newRows.push({
                barcode,
                qty: parseNum(cols[1] || '') || '0',
                price_cny: parseNum(cols[2] || '') || '0',
                box_size: normalizeBoxSize(cols[3] || ''),
                pcs_per_box: parseNum(cols[4] || ''),
                weight_kg: parseNum(cols[5] || ''),
            });
        }
        if (newRows.length > 0) {
            setRows([...newRows, emptyPasteRow(), emptyPasteRow()]);
        }
    };

    const filledRows = rows.filter(r => r.barcode.trim());
    const validRows = filledRows.filter(r => (parseInt(r.qty) || 0) > 0);
    const canSave = validRows.length > 0;

    const handleSubmit = async () => {
        if (!canSave) return;
        setSaving(true);
        setError('');
        try {
            const items = validRows.map(r => {
                const nom = nomMap.get(r.barcode.trim());
                return {
                    barcode: r.barcode.trim(),
                    subject: nom?.subject || undefined,
                    article_seller: nom?.article_seller || undefined,
                    qty: parseInt(r.qty) || 0,
                    price_cny: parseFloat(r.price_cny) || 0,
                    box_size: r.box_size.trim() || undefined,
                    pcs_per_box: parseInt(r.pcs_per_box) || undefined,
                    weight_kg: parseFloat(r.weight_kg) || undefined,
                };
            });
            await api.addFactoryOrderItems(orderId, items as any);
            setRows(Array.from({ length: 5 }, emptyPasteRow));
            setOpen(false);
            setError('');
            onClose?.();
            onAdded();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : t('msg_save_error'));
        }
        setSaving(false);
    };

    const handleClear = () => {
        setRows(Array.from({ length: 5 }, emptyPasteRow));
        setError('');
    };

    const isOpen = alwaysOpen || open;

    if (!isOpen) {
        return (
            <button className="btn btn-secondary btn-sm" onClick={() => setOpen(true)} style={{ marginTop: 12 }}>
                {t('detail_add_items')}
            </button>
        );
    }

    const handleClose = () => {
        setOpen(false);
        handleClear();
        onClose?.();
    };

    const cellInp: React.CSSProperties = {
        width: '100%', background: 'var(--color-bg)',
        border: '1px solid var(--color-border)',
        borderRadius: 8, padding: '8px 10px', fontSize: 13,
        color: 'var(--color-text)',
    };
    const cellNum: React.CSSProperties = { ...cellInp, textAlign: 'right', width: 70 };
    const thStyle: React.CSSProperties = {
        textAlign: 'left', padding: '10px 6px', fontSize: 11, fontWeight: 600,
        color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.3px',
    };
    const thR: React.CSSProperties = { ...thStyle, textAlign: 'right' };

    return (
        <div className="glass-card" style={{ padding: 16, ...(alwaysOpen ? {} : { marginTop: 20, paddingTop: 20, borderTop: '1px solid var(--color-border)' }) }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                    {t('detail_paste_header')} <span style={{ fontSize: 12 }}>({t('detail_paste_sub')})</span>
                </div>
                <button className="btn btn-secondary btn-sm" onClick={handleClose}>
                    {t('btn_cancel')}
                </button>
            </div>

            {error && (
                <div style={{
                    background: 'rgba(239,68,68,0.08)', color: 'var(--color-danger)',
                    padding: '8px 12px', borderRadius: 8, fontSize: 12, marginBottom: 12,
                }}>
                    {error}
                </div>
            )}

            <div style={{ overflow: 'auto' }} onPaste={handlePaste}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead>
                        <tr>
                            <th style={{ ...thStyle, width: 36 }}>#</th>
                            <th style={{ ...thStyle, width: '100%' }}>{t('col_barcode')}</th>
                            <th style={thR}>{t('col_qty')}</th>
                            <th style={thR}>{t('col_price')} {cs}</th>
                            <th style={thStyle}>{t('col_box_spec')}</th>
                            <th style={thR}>{t('col_pcs_per_box')}</th>
                            <th style={thR}>{t('col_weight_1pc')}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row, i) => {
                            return (
                                <tr key={i} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                    <td style={{ padding: '6px', fontSize: 12, color: 'var(--color-text-dim)', textAlign: 'center' }}>
                                        {i + 1}
                                    </td>
                                    <td style={{ padding: '4px' }}>
                                        <input
                                            value={row.barcode}
                                            onChange={e => updatePasteRow(i, 'barcode', e.target.value)}
                                            placeholder={t('col_barcode')}
                                            autoComplete="off"
                                            style={{ ...cellInp, fontFamily: 'monospace' }}
                                        />
                                    </td>
                                    <td style={{ padding: '4px' }}>
                                        <input type="number" value={row.qty}
                                            onChange={e => updatePasteRow(i, 'qty', e.target.value)}
                                            min={0} autoComplete="off" style={cellNum} />
                                    </td>
                                    <td style={{ padding: '4px' }}>
                                        <input type="number" step="0.01" value={row.price_cny}
                                            onChange={e => updatePasteRow(i, 'price_cny', e.target.value)}
                                            min={0} autoComplete="off" style={cellNum} />
                                    </td>
                                    <td style={{ padding: '4px' }}>
                                        <input value={row.box_size}
                                            onChange={e => updatePasteRow(i, 'box_size', e.target.value)}
                                            autoComplete="off"
                                            style={{ ...cellInp, width: 110 }} />
                                    </td>
                                    <td style={{ padding: '4px' }}>
                                        <input type="number" value={row.pcs_per_box}
                                            onChange={e => updatePasteRow(i, 'pcs_per_box', e.target.value)}
                                            min={0} autoComplete="off" style={cellNum} />
                                    </td>
                                    <td style={{ padding: '4px' }}>
                                        <input type="number" step="0.01" value={row.weight_kg}
                                            onChange={e => updatePasteRow(i, 'weight_kg', e.target.value)}
                                            min={0} autoComplete="off" style={cellNum} />
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>

            <div style={{ display: 'flex', gap: 8, marginTop: 12, justifyContent: 'flex-end' }}>
                {filledRows.length > 0 && (
                    <button className="btn btn-secondary btn-sm" onClick={handleClear}>
                        {t('btn_cancel')}
                    </button>
                )}
                <button className="btn btn-primary btn-sm" onClick={handleSubmit}
                    disabled={!canSave || saving}>
                    {saving ? t('msg_saving') : `${t('btn_add')}${validRows.length > 0 ? ` (${validRows.length})` : ''}`}
                </button>
            </div>
        </div>
    );
}

// ─── Inner Content (needs LanguageProvider above) ────────────────────────

function FactoryOrderDetailContent() {
    const { t } = useT();
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const orderId = Number(params.id);

    const [order, setOrder] = useState<FactoryOrder | null>(null);
    const [nomenclature, setNomenclature] = useState<Nomenclature[]>([]);
    const [history, setHistory] = useState<FactoryOrderHistory[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [tab, setTab] = useState<'main' | 'history'>('main');
    const [itemFilter, setItemFilter] = useState<'all' | 'distributed' | 'undistributed'>('all');
    const [showAddItems, setShowAddItems] = useState(false);

    const nomMap = useMemo(() => {
        const m = new Map<string, Nomenclature>();
        nomenclature.forEach(n => { if (n.barcode) m.set(n.barcode, n); });
        return m;
    }, [nomenclature]);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [data, hist] = await Promise.all([
                api.getFactoryOrder(orderId),
                api.getFactoryOrderHistory(orderId),
            ]);
            setOrder(data);
            setHistory(hist);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : t('msg_loading_error'));
        }
        setLoading(false);
    }, [orderId, t]);

    useEffect(() => { load(); }, [load]);
    useEffect(() => { api.getNomenclature().then(setNomenclature).catch(() => {}); }, []);

    const goBack = () => router.push(`/p/${slug}/supply-chain`);

    const FO_EVENT_LABELS = getFoEventLabels(t);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}><div className="spinner" style={{ margin: '0 auto 12px' }} />{t('msg_loading')}</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error} <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={load}>{t('btn_retry')}</button></div>;
    if (!order) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>{t('detail_not_found')} <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={goBack}>{t('detail_back')}</button></div>;

    const items = order.items || [];

    const totalQtyAll = items.reduce((s, i) => s + i.qty, 0);
    const totalAssignedAll = items.reduce((s, i) => s + i.assigned_qty, 0);
    const distPctAll = totalQtyAll > 0 ? Math.round((totalAssignedAll / totalQtyAll) * 100) : 0;

    const filteredItems = itemFilter === 'distributed'
        ? items.filter(i => i.assigned_qty >= i.qty && i.qty > 0)
        : itemFilter === 'undistributed'
            ? items.filter(i => i.assigned_qty < i.qty)
            : items;

    const distributedCount = items.filter(i => i.assigned_qty >= i.qty && i.qty > 0).length;
    const undistributedCount = items.filter(i => i.assigned_qty < i.qty).length;

    return (
        <div className="animate-in">
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 20 }}>
                <button className="btn btn-secondary btn-sm" onClick={goBack} style={{ fontSize: 13 }}>
                    {t('detail_back')}
                </button>
                <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>
                            {t('detail_order')} #{order.order_number}
                        </h1>
                        <DistributionBadge totalQty={totalQtyAll} totalAssigned={totalAssignedAll} />
                    </div>
                    {order.factory_name && (
                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 2 }}>
                            {order.factory_name}
                        </div>
                    )}
                </div>
                <LanguageToggle />
                {order.created_at && (
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                        {formatDate(order.created_at)}
                    </div>
                )}
            </div>

            {/* Info card */}
            <OrderInfoCard order={order} onUpdated={load} />

            {/* Tabs */}
            <div style={{ display: 'flex', gap: 0, marginBottom: 16, borderBottom: '2px solid var(--color-border)' }}>
                {[
                    { key: 'main' as const, label: t('detail_tab_items') },
                    { key: 'history' as const, label: `${t('detail_tab_history')}${history.length > 0 ? ` (${history.length})` : ''}` },
                ].map(tb => (
                    <button
                        key={tb.key}
                        onClick={() => setTab(tb.key)}
                        style={{
                            padding: '10px 20px', fontSize: 13, fontWeight: 600,
                            border: 'none', background: 'none', cursor: 'pointer',
                            color: tab === tb.key ? 'var(--color-accent)' : 'var(--color-text-muted)',
                            borderBottom: tab === tb.key ? '2px solid var(--color-accent)' : '2px solid transparent',
                            marginBottom: -2, transition: 'all 0.2s ease',
                        }}
                    >
                        {tb.label}
                    </button>
                ))}
            </div>

            {/* Tab: Items */}
            {tab === 'main' && (
                <>
                    {/* Toolbar: filters + add button */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                        <div style={{ display: 'flex', gap: 6 }}>
                            {([
                                { key: 'all' as const, label: `${t('detail_filter_all')} (${items.length})` },
                                { key: 'undistributed' as const, label: `${t('detail_filter_undistributed')} (${undistributedCount})` },
                                { key: 'distributed' as const, label: `${t('detail_filter_distributed')} (${distributedCount})` },
                            ]).map(f => (
                                <button
                                    key={f.key}
                                    className={`btn btn-sm ${itemFilter === f.key ? 'btn-primary' : 'btn-secondary'}`}
                                    onClick={() => setItemFilter(f.key)}
                                >
                                    {f.label}
                                </button>
                            ))}
                        </div>
                        <button
                            className="btn btn-primary btn-sm"
                            onClick={() => setShowAddItems(v => !v)}
                        >
                            {showAddItems ? t('btn_close') : t('detail_add_items')}
                        </button>
                    </div>

                    {/* Add items form (toggled from toolbar button) */}
                    {showAddItems && (
                        <div style={{ marginBottom: 16 }}>
                            <AddItemsSection
                                orderId={order.id}
                                nomMap={nomMap}
                                onAdded={() => { load(); setShowAddItems(false); }}
                                alwaysOpen
                                onClose={() => setShowAddItems(false)}
                                currency={order.supplier?.currency}
                            />
                        </div>
                    )}

                    <div className="glass-card" style={{ padding: 16 }}>
                        {filteredItems.length === 0 ? (
                            <div className="empty-state">
                                <div className="empty-state-icon">📦</div>
                                <div className="empty-state-text">
                                    {items.length === 0
                                        ? t('detail_items_empty')
                                        : itemFilter === 'distributed'
                                            ? t('detail_filter_distributed')
                                            : t('detail_filter_undistributed')}
                                </div>
                            </div>
                        ) : (
                            <ItemsTable items={filteredItems} orderId={order.id} nomMap={nomMap} onChanged={load} currency={order.supplier?.currency} />
                        )}
                    </div>
                </>
            )}

            {/* Tab: History */}
            {tab === 'history' && (
                <div className="glass-card" style={{ padding: 20 }}>
                    {history.length === 0 ? (
                        <div style={{ textAlign: 'center', padding: '24px 0', color: 'var(--color-text-dim)', fontSize: 13 }}>
                            {t('detail_history_empty')}
                        </div>
                    ) : (
                        <div style={{ position: 'relative', paddingLeft: 28 }}>
                            {/* Vertical line */}
                            <div style={{
                                position: 'absolute', left: 7, top: 8, bottom: 8, width: 2,
                                background: 'var(--color-border)',
                            }} />

                            {history.map((entry, i) => {
                                const dotColor = entry.event_type === 'distributed' ? 'var(--color-success)'
                                    : entry.event_type === 'items_added' ? 'var(--color-accent)'
                                    : entry.event_type === 'created' ? 'var(--color-text-muted)'
                                    : 'var(--color-warning)';
                                return (
                                    <div key={entry.id} style={{ position: 'relative', marginBottom: i < history.length - 1 ? 20 : 0 }}>
                                        {/* Dot */}
                                        <div style={{
                                            position: 'absolute', left: -24, top: 4,
                                            width: 12, height: 12, borderRadius: '50%',
                                            background: dotColor, border: '2px solid var(--color-bg-card)',
                                            boxShadow: `0 0 0 2px ${dotColor}33`,
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
                                            <div style={{ fontSize: 13, fontWeight: 500 }}>
                                                {FO_EVENT_LABELS[entry.event_type] || entry.event_type}
                                            </div>
                                            {entry.details && (
                                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2 }}>
                                                    {entry.details}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

// ─── Main Page (wraps content in LanguageProvider) ───────────────────────

export default function FactoryOrderDetailPage() {
    return (
        <LanguageProvider>
            <FactoryOrderDetailContent />
        </LanguageProvider>
    );
}

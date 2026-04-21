'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel, calcTotalBoxesWithMix } from '@/lib/utils';
import { LanguageProvider, useT, LanguageToggle, currencySymbol, translateSubject } from './i18n';

/** Нормализация габаритов → "60x40x40" */
const normalizeBoxSize = (s: string): string => s.trim().replace(/[×*,/\\]/g, 'x');

/** Кол-во мест (коробок) для одной позиции */
const calcBoxes = (qty: number, pcsPerBox: number | null): number =>
    pcsPerBox && pcsPerBox > 0 ? Math.ceil(qty / pcsPerBox) : 0;

/** Объём одной позиции в м³ */
const calcVolumeM3 = (boxSize: string, qty: number, pcsPerBox: number | null): number => {
    if (!boxSize || !qty) return 0;
    const parts = normalizeBoxSize(boxSize).split('x').map(Number);
    if (parts.length < 3 || parts.some(isNaN)) return 0;
    const volPerBox = (parts[0] * parts[1] * parts[2]) / 1_000_000;
    const boxes = calcBoxes(qty, pcsPerBox);
    return boxes > 0 ? boxes * volPerBox : 0;
};
import PageHeader from '@/components/PageHeader';
import TabLayout from '@/components/TabLayout';
import DataTable, { Column } from '@/components/DataTable';
import FormModal from '@/components/FormModal';
import KpiCard from '@/components/KpiCard';
import type {
    FactoryOrder,
    FactoryOrderCreate,
    FactoryOrderItem,
    SupplyChainOverview,
    VehicleStatus,
    VehicleSchema,
    VehicleCreate,
    VehicleCostSummary,
    VehicleItemSchema,
    AvailableItemGroup,
    AvailableItem,
    SplitItem,
    Warehouse,
    Supplier,
    SupplierCatalogResponse,
    SupplierCatalogItem,
    SupplierCatalogSubjectGroup,
    SkuOrderHistoryEntry,
    ShipmentMatrixResponse,
    ShipmentMatrixItem,
} from '@/types/api';
import { CONTAINERS } from '@/app/(main)/p/[slug]/container-loader/lib/packer';

// ─── Status helpers ─────────────────────────────────────────────────────────

type TFn = (key: string) => string;

const getVehicleStatusLabels = (t: TFn): Record<VehicleStatus, string> => ({
    FORMING: t('status_forming'),
    SHIPPED: t('status_shipped'),
    CUSTOMS: t('status_customs'),
    DISPATCHED: t('status_dispatched'),
    DELIVERED: t('status_delivered'),
});

const VEHICLE_STATUS_COLORS: Record<VehicleStatus, string> = {
    FORMING: '#6b7280',
    SHIPPED: '#3b82f6',
    CUSTOMS: '#f59e0b',
    DISPATCHED: '#8b5cf6',
    DELIVERED: '#22c55e',
};

const VEHICLE_STATUSES: VehicleStatus[] = ['FORMING', 'SHIPPED', 'CUSTOMS', 'DISPATCHED', 'DELIVERED'];

const getContainerOptions = (t: TFn): { key: string; label: string; transport: string }[] => [
    { key: 'truck1', label: t('container_truck1'), transport: 'AUTO' },
    { key: 'truck2', label: t('container_truck2'), transport: 'AUTO' },
    { key: '20ft', label: t('container_20ft'), transport: 'CONTAINER' },
    { key: '40ft', label: t('container_40ft'), transport: 'CONTAINER' },
    { key: '40ft_hc', label: t('container_40ft_hc'), transport: 'CONTAINER' },
];

const getTransportTypes = (t: TFn): Record<string, { label: string; color: string }> => ({
    AUTO: { label: t('transport_auto'), color: '#3b82f6' },
    CONTAINER: { label: t('transport_container'), color: '#f59e0b' },
});

const getNextStatusMap = (t: TFn): Partial<Record<VehicleStatus, { status: VehicleStatus; label: string; icon: string }>> => ({
    SHIPPED: { status: 'CUSTOMS', label: t('transition_to_customs'), icon: '🏛' },
    CUSTOMS: { status: 'DISPATCHED', label: t('transition_dispatched'), icon: '🚛' },
    DISPATCHED: { status: 'DELIVERED', label: t('transition_deliver'), icon: '✓' },
});

function StatusBadge({ status }: { status: string }) {
    const { t } = useT();
    const s = status as VehicleStatus;
    const labels = getVehicleStatusLabels(t);
    const label = labels[s] || status;
    const color = VEHICLE_STATUS_COLORS[s] || '#6b7280';
    return (
        <span className="badge sc-badge-colored" style={{ background: color }}>
            {label}
        </span>
    );
}

const getFactoryOrderStatusLabels = (t: TFn): Record<string, string> => ({
    FORMING: t('factory_status_forming'),
    DISTRIBUTED: t('factory_status_distributed'),
    CLOSED: t('factory_status_closed'),
});

const FACTORY_ORDER_STATUS_COLORS: Record<string, string> = {
    FORMING: '#6b7280',
    DISTRIBUTED: '#3b82f6',
    CLOSED: '#22c55e',
};

function FactoryOrderStatusBadge({ status }: { status: string }) {
    const { t } = useT();
    const labels = getFactoryOrderStatusLabels(t);
    const label = labels[status] || status;
    const color = FACTORY_ORDER_STATUS_COLORS[status] || '#6b7280';
    return (
        <span className="badge sc-badge-colored" style={{ background: color }}>
            {label}
        </span>
    );
}

// ─── Main page ──────────────────────────────────────────────────────────────

export default function SupplyChainPage() {
    return (
        <LanguageProvider>
            <SupplyChainContent />
        </LanguageProvider>
    );
}

function SupplyChainContent() {
    const { t } = useT();
    const searchParams = useSearchParams();
    // Legacy 'overview' tab URL param redirects to merged 'suppliers' tab
    const rawInitialTab = searchParams.get('tab') || 'orders';
    const initialTab = rawInitialTab === 'overview' ? 'suppliers' : rawInitialTab;
    const [tab, setTab] = useState(initialTab);

    return (
        <div className="animate-in">
            <div className="sc-page-header">
                <PageHeader
                    title={t('page_title')}
                    subtitle={t('page_subtitle')}
                    icon="🚚"
                />
                <LanguageToggle />
            </div>
            <TabLayout
                tabs={[
                    { key: 'orders', label: t('tab_orders') },
                    { key: 'vehicles', label: t('tab_vehicles') },
                    { key: 'suppliers', label: t('tab_suppliers') },
                ]}
                active={tab}
                onChange={setTab}
            />
            {tab === 'orders' && <FactoryOrdersTab />}
            {tab === 'vehicles' && <VehiclesTab />}
            {tab === 'suppliers' && <SuppliersTab />}
        </div>
    );
}

// ─── Create Factory Order Inline Form ────────────────────────────────────

const getCreateOrderFields = (t: TFn) => [
    { key: 'order_number', label: t('create_order_number'), required: true },
    { key: 'factory_name', label: t('create_order_supplier_name') },
    { key: 'expected_ready_date', label: t('create_order_ready_date'), type: 'date' as const },
    { key: 'note', label: t('create_order_note') },
];

function CreateFactoryOrderForm({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
    const { t } = useT();
    const [form, setForm] = useState<FactoryOrderCreate>({
        order_number: '',
        factory_name: undefined,
        expected_ready_date: undefined,
        note: undefined,
        supplier_id: undefined,
    });
    const [suppliers, setSuppliers] = useState<Supplier[]>([]);
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        api.getSuppliers().then(setSuppliers).catch(() => {});
    }, []);

    const handleSupplierChange = (supplierId: number | undefined) => {
        const supplier = suppliers.find(s => s.id === supplierId);
        setForm(f => ({
            ...f,
            supplier_id: supplierId,
            factory_name: supplier ? supplier.name : f.factory_name,
        }));
    };

    const handleSubmit = async () => {
        if (!form.order_number.trim()) return;
        setSubmitting(true);
        try {
            await api.createFactoryOrder(form);
            onDone();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_error'));
        }
        setSubmitting(false);
    };

    return (
        <div className="glass-card sc-form-card">
            <div className="sc-form-header">
                <h3 className="sc-form-title">{t('create_order_title')}</h3>
                <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
            </div>
            <div className="sc-form-grid-5">
                <div>
                    <label className="sc-form-label">{t('create_order_number')}</label>
                    <input value={form.order_number} onChange={e => setForm(f => ({ ...f, order_number: e.target.value }))} placeholder={t('create_order_number_placeholder')} className="sc-form-input" autoFocus />
                </div>
                <div>
                    <label className="sc-form-label">{t('create_order_supplier_select')}</label>
                    <select
                        value={form.supplier_id || ''}
                        onChange={e => handleSupplierChange(e.target.value ? Number(e.target.value) : undefined)}
                        className="sc-form-input"
                    >
                        <option value="">{t('create_order_unselected')}</option>
                        {suppliers.map(s => (
                            <option key={s.id} value={s.id}>
                                {s.name} ({s.country === 'CHINA' ? 'CN' : 'RU'})
                            </option>
                        ))}
                    </select>
                </div>
                <div>
                    <label className="sc-form-label">{t('create_order_supplier_name')}</label>
                    <input value={form.factory_name || ''} onChange={e => setForm(f => ({ ...f, factory_name: e.target.value || undefined }))} placeholder={t('create_order_supplier_placeholder')} className="sc-form-input" />
                </div>
                <div>
                    <label className="sc-form-label">{t('create_order_ready_date')}</label>
                    <input type="date" value={form.expected_ready_date || ''} onChange={e => setForm(f => ({ ...f, expected_ready_date: e.target.value || undefined }))} className="sc-form-input" />
                </div>
                <div>
                    <label className="sc-form-label">{t('create_order_note')}</label>
                    <input value={form.note || ''} onChange={e => setForm(f => ({ ...f, note: e.target.value || undefined }))} placeholder={t('create_order_note_placeholder')} className="sc-form-input" />
                </div>
            </div>
            <div className="sc-form-footer">
                <button className="btn btn-secondary btn-sm" onClick={onClose}>{t('btn_cancel')}</button>
                <button className="btn btn-primary btn-sm" onClick={handleSubmit} disabled={!form.order_number.trim() || submitting}>
                    {submitting ? t('msg_creating') : t('btn_create')}
                </button>
            </div>
        </div>
    );
}

// ─── Tab 1: Factory Orders ──────────────────────────────────────────────────

function FactoryOrdersTab() {
    const { t } = useT();
    const [orders, setOrders] = useState<FactoryOrder[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [showCreate, setShowCreate] = useState(false);
    const [editOrder, setEditOrder] = useState<FactoryOrder | null>(null);
    const [splitOrderId, setSplitOrderId] = useState<number | null>(null);
    const [filterFactory, setFilterFactory] = useState<string>('');
    const router = useRouter();
    const { slug } = useParams() as { slug: string };

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const data = await api.getFactoryOrders();
            setOrders(data);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : t('msg_loading_error'));
        }
        setLoading(false);
    }, [t]);

    useEffect(() => { load(); }, [load]);

    const handleUpdate = async (form: Record<string, any>) => {
        if (!editOrder) return;
        await api.updateFactoryOrder(editOrder.id, {
            order_number: form.order_number,
            factory_name: form.factory_name || undefined,
            expected_ready_date: form.expected_ready_date || undefined,
            note: form.note || undefined,
        });
        setEditOrder(null);
        await load();
    };

    const handleDelete = async (id: number) => {
        if (!confirm(t('orders_confirm_delete'))) return;
        try {
            await api.deleteFactoryOrder(id);
            await load();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_error'));
        }
    };

    const handleRowClick = (row: FactoryOrder) => {
        router.push(`/p/${slug}/supply-chain/factory-orders/${row.id}`);
    };

    // Unique factory names for filter (with supplier info if available)
    const factoryNames = [...new Set(orders.map(o => o.factory_name).filter(Boolean))].sort();
    const filteredOrders = filterFactory ? orders.filter(o => o.factory_name === filterFactory) : orders;

    // Helper to get supplier label for filter dropdown
    const getFactoryLabel = (name: string) => {
        const order = orders.find(o => o.factory_name === name && o.supplier);
        if (!order?.supplier) return name;
        const countryFlag = order.supplier.country === 'CHINA' ? '\uD83C\uDDE8\uD83C\uDDF3' : '\uD83C\uDDF7\uD83C\uDDFA';
        return `${name} ${countryFlag} ${order.supplier.currency}`;
    };

    // KPI aggregates (from filtered orders), split cost by currency
    const kpi = filteredOrders.reduce((acc, o) => {
        const currency = o.supplier?.currency || 'CNY';
        for (const i of o.items || []) {
            acc.weight += (Number(i.weight_kg) || 0) * i.qty;
            acc.volume += calcVolumeM3(i.box_size || '', i.qty, i.pcs_per_box ?? null);
            const itemCost = (Number(i.price_cny) || 0) * i.qty;
            if (currency === 'RUB') acc.sumRub += itemCost;
            else acc.sumCny += itemCost;
            acc.qty += i.qty;
        }
        return acc;
    }, { weight: 0, volume: 0, sumCny: 0, sumRub: 0, qty: 0 });

    const columns: Column[] = [
        { key: 'order_number', label: t('col_number'), width: '120px' },
        { key: 'factory_name', label: t('col_supplier') },
        {
            key: 'items', label: t('col_items_count'), align: 'center',
            render: (_v: unknown, row: FactoryOrder) => {
                const items = row.items || [];
                return items.length > 0 ? String(items.length) : '\u2014';
            },
        },
        {
            key: 'total_qty', label: t('col_qty'), align: 'right',
            render: (_v: unknown, row: FactoryOrder) => {
                const items = row.items || [];
                const total = items.reduce((s, i) => s + i.qty, 0);
                return total > 0 ? formatNumber(total, 0) : '\u2014';
            },
        },
        {
            key: 'boxes', label: t('col_boxes'), align: 'right',
            render: (_v: unknown, row: FactoryOrder) => {
                const items = row.items || [];
                const total = calcTotalBoxesWithMix(items);
                return total > 0 ? formatNumber(total, 0) : '\u2014';
            },
        },
        {
            key: 'volume', label: t('col_volume_m3'), align: 'right',
            render: (_v: unknown, row: FactoryOrder) => {
                const items = row.items || [];
                const total = items.reduce((s, i) => s + calcVolumeM3(i.box_size || '', i.qty, i.pcs_per_box ?? null), 0);
                return total > 0 ? formatNumber(total, 1) : '\u2014';
            },
        },
        {
            key: 'weight', label: t('col_weight_kg'), align: 'right',
            render: (_v: unknown, row: FactoryOrder) => {
                const items = row.items || [];
                const total = items.reduce((s, i) => s + (Number(i.weight_kg) || 0) * i.qty, 0);
                return total > 0 ? formatNumber(total, 0) : '\u2014';
            },
        },
        {
            key: 'total_cny', label: t('col_amount'), align: 'right',
            render: (_v: unknown, row: FactoryOrder) => {
                // Use total_cny if set, otherwise calculate from items
                let cost = Number(row.total_cny) || 0;
                if (!cost && row.items?.length) {
                    cost = row.items.reduce((sum, i) => sum + (Number(i.price_cny) || 0) * i.qty, 0);
                }
                if (!cost) return '\u2014';
                const symbol = row.supplier?.currency === 'RUB' ? '\u20BD' : '\u00A5';
                return `${formatNumber(cost, 0)} ${symbol}`;
            },
        },
        {
            key: 'status', label: t('col_status'), align: 'center',
            render: (_v: unknown, row: FactoryOrder) => <FactoryOrderStatusBadge status={row.status} />,
        },
        {
            key: 'progress', label: t('col_progress'), align: 'center',
            render: (_v: unknown, row: FactoryOrder) => {
                const items = row.items || [];
                if (items.length === 0) return '\u2014';
                const totalQty = items.reduce((s, i) => s + i.qty, 0);
                const assignedQty = items.reduce((s, i) => s + i.assigned_qty, 0);
                const pct = totalQty > 0 ? Math.round((assignedQty / totalQty) * 100) : 0;
                return (
                    <div className="sc-progress-mini">
                        <div className="sc-progress-mini-track">
                            <div style={{ width: `${pct}%`, background: pct === 100 ? '#22c55e' : '#3b82f6', borderRadius: 4, height: '100%' }} />
                        </div>
                        <span className="sc-progress-mini-count">{assignedQty}/{totalQty}</span>
                    </div>
                );
            },
        },
        { key: 'expected_ready_date', label: t('col_ready_date'), format: 'date' },
        { key: 'created_at', label: t('col_created_date'), format: 'date' },
        {
            key: 'actions', label: '', width: '180px',
            render: (_v: unknown, row: FactoryOrder) => (
                <div className="sc-actions-cell" onClick={e => e.stopPropagation()}>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => setEditOrder(row)}
                    >
                        {t('btn_edit')}
                    </button>
                    <button
                        className="btn btn-secondary btn-sm sc-color-danger"
                        onClick={() => handleDelete(row.id)}
                    >
                        {t('btn_del')}
                    </button>
                </div>
            ),
        },
    ];

    const handleExport = () => {
        const rows = orders.map(o => {
            const items = o.items || [];
            const totalQty = items.reduce((s, i) => s + i.qty, 0);
            const assignedQty = items.reduce((s, i) => s + i.assigned_qty, 0);
            const totalBoxes = calcTotalBoxesWithMix(items);
            const totalVolume = items.reduce((s, i) => s + calcVolumeM3(i.box_size || '', i.qty, i.pcs_per_box ?? null), 0);
            const totalWeight = items.reduce((s, i) => s + (Number(i.weight_kg) || 0) * i.qty, 0);
            const totalCny = items.reduce((s, i) => s + (Number(i.price_cny) || 0) * i.qty, 0);
            return {
                [t('col_number')]: o.order_number,
                [t('col_supplier')]: o.factory_name || '',
                [t('col_items_count')]: items.length,
                [t('col_qty')]: totalQty,
                [t('col_boxes')]: totalBoxes || '',
                [t('col_volume_m3')]: totalVolume > 0 ? Number(totalVolume.toFixed(1)) : '',
                [t('col_weight_kg')]: totalWeight > 0 ? Math.round(totalWeight) : '',
                [t('col_amount') + ' \u00A5']: totalCny > 0 ? Number(totalCny.toFixed(2)) : '',
                [t('orders_col_distributed')]: `${assignedQty}/${totalQty}`,
                [t('col_ready_date')]: o.expected_ready_date || '',
                [t('col_created_date')]: o.created_at || '',
            };
        });
        exportToExcel(rows, t('orders_excel_filename'));
    };

    return (
        <>
            {error && (
                <div className="glass-card sc-error-card">
                    {error}
                    <button className="btn btn-secondary btn-sm sc-retry-btn" onClick={load}>{t('btn_retry')}</button>
                </div>
            )}

            {/* Create order inline form */}
            {showCreate && (
                <CreateFactoryOrderForm
                    onClose={() => setShowCreate(false)}
                    onDone={() => { setShowCreate(false); load(); }}
                />
            )}

            {/* KPI Summary */}
            {!loading && filteredOrders.length > 0 && (
                <div className="sc-kpi-grid-4">
                    <div className="glass-card sc-kpi-card">
                        <div className="sc-kpi-label">{t('kpi_orders')}</div>
                        <div className="sc-kpi-value sc-color-accent">{filteredOrders.length}</div>
                        <div className="sc-kpi-sub">{formatNumber(kpi.qty, 0)} {t('unit_pcs')}</div>
                    </div>
                    <div className="glass-card sc-kpi-card">
                        <div className="sc-kpi-label">{t('kpi_total_weight')}</div>
                        <div className="sc-kpi-value sc-color-warning">{formatNumber(kpi.weight, 0)}</div>
                        <div className="sc-kpi-sub">{t('unit_kg')}</div>
                    </div>
                    <div className="glass-card sc-kpi-card">
                        <div className="sc-kpi-label">{t('kpi_total_volume')}</div>
                        <div className="sc-kpi-value">{formatNumber(kpi.volume, 1)}</div>
                        <div className="sc-kpi-sub">{t('unit_m3')}</div>
                    </div>
                    <div className="glass-card sc-kpi-card">
                        <div className="sc-kpi-label">{t('kpi_total_amount')}</div>
                        {kpi.sumCny > 0 && (
                            <div className={`${kpi.sumRub > 0 ? 'sc-kpi-value-md' : 'sc-kpi-value'} sc-color-success sc-fw-700`}>
                                {formatNumber(kpi.sumCny, 0)} {'\u00A5'}
                            </div>
                        )}
                        {kpi.sumRub > 0 && (
                            <div className={`${kpi.sumCny > 0 ? 'sc-kpi-value-md' : 'sc-kpi-value'} sc-color-success sc-fw-700`}>
                                {formatNumber(kpi.sumRub, 0)} {'\u20BD'}
                            </div>
                        )}
                        {kpi.sumCny === 0 && kpi.sumRub === 0 && (
                            <div className="sc-kpi-value sc-color-text-muted">0</div>
                        )}
                    </div>
                </div>
            )}

            <DataTable
                columns={columns}
                data={filteredOrders}
                loading={loading}
                title={t('orders_title')}
                emptyText={t('orders_empty')}
                emptyIcon="📦"
                exportName="factory_orders"
                onRowClick={(row) => handleRowClick(row)}
                actions={
                    <div className="sc-filter-bar">
                        {factoryNames.length > 1 && (
                            <select
                                value={filterFactory}
                                onChange={e => setFilterFactory(e.target.value)}
                                className="sc-filter-select"
                            >
                                <option value="">{t('orders_filter_all')}</option>
                                {factoryNames.map(name => (
                                    <option key={name} value={name!}>{getFactoryLabel(name!)}</option>
                                ))}
                            </select>
                        )}
                        <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(v => !v)}>
                            {showCreate ? t('btn_close') : t('orders_btn_create')}
                        </button>
                    </div>
                }
            />

            {/* Edit Modal */}
            <FormModal
                open={!!editOrder}
                onClose={() => setEditOrder(null)}
                onSubmit={handleUpdate}
                title={t('orders_edit_title')}
                fields={getCreateOrderFields(t)}
                defaults={editOrder ? {
                    order_number: editOrder.order_number,
                    factory_name: editOrder.factory_name || '',
                    expected_ready_date: editOrder.expected_ready_date || '',
                    note: editOrder.note || '',
                } : {}}
            />

            {/* Split Modal */}
            {splitOrderId != null && (
                <SplitToVehiclesModal
                    orderId={splitOrderId}
                    orders={orders}
                    onClose={() => setSplitOrderId(null)}
                    onDone={() => { setSplitOrderId(null); load(); }}
                />
            )}
        </>
    );
}

// ─── Split to Vehicles Modal ────────────────────────────────────────────────

interface SplitModalProps {
    orderId: number;
    orders: FactoryOrder[];
    onClose: () => void;
    onDone: () => void;
}

function SplitToVehiclesModal({ orderId, orders, onClose, onDone }: SplitModalProps) {
    const { t } = useT();
    const order = orders.find(o => o.id === orderId);
    const items = order?.items || [];
    const [assignments, setAssignments] = useState<Record<number, { qty: string; vehicle: string }>>({});
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState('');
    const [detailLoaded, setDetailLoaded] = useState(false);
    const [detailItems, setDetailItems] = useState<FactoryOrderItem[]>(items);

    // Load detail if items not present
    useEffect(() => {
        if (items.length === 0 && !detailLoaded) {
            api.getFactoryOrder(orderId).then(detail => {
                setDetailItems(detail.items || []);
                setDetailLoaded(true);
            }).catch(() => setDetailLoaded(true));
        } else {
            setDetailItems(items);
        }
    }, [orderId, items, detailLoaded]);

    const updateAssignment = (itemId: number, field: 'qty' | 'vehicle', value: string) => {
        setAssignments(prev => ({
            ...prev,
            [itemId]: { ...prev[itemId], [field]: value },
        }));
    };

    const handleSubmit = async () => {
        const splitItems: SplitItem[] = [];
        for (const item of detailItems) {
            const a = assignments[item.id];
            if (!a?.qty || !a?.vehicle) continue;
            const qty = parseInt(a.qty);
            if (qty <= 0) continue;
            splitItems.push({
                factory_order_item_id: item.id,
                qty,
                vehicle_order_no: a.vehicle.trim(),
            });
        }
        if (splitItems.length === 0) {
            setError(t('split_validation'));
            return;
        }
        setSubmitting(true);
        setError('');
        try {
            await api.splitToVehicles(orderId, splitItems);
            onDone();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : t('msg_error'));
        }
        setSubmitting(false);
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card sc-modal-card-wide"
                onClick={e => e.stopPropagation()}>
                <div className="modal-title">{t('split_title')}: {order?.order_number || `#${orderId}`}</div>
                {error && <div className="auth-error sc-form-mb">{error}</div>}

                {detailItems.length === 0 ? (
                    <div className="sc-split-empty">
                        {t('split_empty')}
                    </div>
                ) : (
                    <table className="data-table sc-table-sm">
                        <thead>
                            <tr>
                                <th>{t('col_barcode')}</th>
                                <th>{t('col_article')}</th>
                                <th className="sc-th-right">{t('col_total')}</th>
                                <th className="sc-th-right">{t('col_distributed')}</th>
                                <th className="sc-th-right">{t('col_remaining')}</th>
                                <th style={{ width: 100 }}>{t('col_qty')}</th>
                                <th style={{ width: 160 }}>{t('col_vehicle_no')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {detailItems.map(item => {
                                const remaining = item.qty - item.assigned_qty;
                                const a = assignments[item.id] || { qty: '', vehicle: '' };
                                return (
                                    <tr key={item.id} style={{ opacity: remaining === 0 ? 0.4 : 1 }}>
                                        <td className="sc-barcode-td">{item.barcode}</td>
                                        <td>{item.article_seller || '\u2014'}</td>
                                        <td className="sc-td-right">{item.qty}</td>
                                        <td className="sc-td-right">{item.assigned_qty}</td>
                                        <td className="sc-remaining-danger" style={{ color: remaining > 0 ? 'var(--color-danger)' : 'var(--color-success)' }}>
                                            {remaining}
                                        </td>
                                        <td>
                                            <input
                                                type="number"
                                                min={0}
                                                max={remaining}
                                                value={a.qty}
                                                onChange={e => updateAssignment(item.id, 'qty', e.target.value)}
                                                disabled={remaining === 0}
                                                placeholder="0"
                                                className="sc-split-input"
                                            />
                                        </td>
                                        <td>
                                            <input
                                                type="text"
                                                value={a.vehicle}
                                                onChange={e => updateAssignment(item.id, 'vehicle', e.target.value)}
                                                disabled={remaining === 0}
                                                placeholder="AUTO-001"
                                                className="sc-split-input"
                                            />
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                )}

                <div className="sc-split-footer">
                    <button className="btn btn-secondary btn-sm" onClick={onClose} disabled={submitting}>
                        {t('btn_cancel')}
                    </button>
                    <button className="btn btn-primary btn-sm" onClick={handleSubmit} disabled={submitting || detailItems.length === 0}>
                        {submitting ? t('msg_saving') : t('split_btn')}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ─── Inline Items Section (existing items + spreadsheet for adding) ────────

interface ItemRow {
    barcode: string;
    qty: string;
    price_cny: string;
    box_size: string;
    pcs_per_box: string;
    weight_kg: string;
}

const emptyItemRow = (): ItemRow => ({
    barcode: '', qty: '', price_cny: '', box_size: '', pcs_per_box: '', weight_kg: '',
});

function InlineItemsSection({ order, onCollapse, onItemsAdded }: {
    order: FactoryOrder; onCollapse: () => void; onItemsAdded: () => void;
}) {
    const { t } = useT();
    const existingItems = order.items || [];
    const [showAddForm, setShowAddForm] = useState(false);
    const [rows, setRows] = useState<ItemRow[]>(Array.from({ length: 5 }, emptyItemRow));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    const updateRow = (idx: number, field: keyof ItemRow, value: string) => {
        setRows(prev => {
            const next = [...prev];
            next[idx] = { ...next[idx], [field]: value };
            return next;
        });
        if (idx === rows.length - 1) {
            setRows(prev => [...prev, emptyItemRow()]);
        }
    };

    const handlePaste = (e: React.ClipboardEvent) => {
        const text = e.clipboardData.getData('text/plain');
        if (!text.includes('\t') && !text.includes('\n')) return;
        e.preventDefault();

        const lines = text.trim().split('\n').map(l => l.split('\t'));
        const newRows: ItemRow[] = [];

        // Parse number: "1 088" → "1088", "51,84" → "51.84"
        const parseNum = (s: string) => (s || '').trim().replace(/\s/g, '').replace(',', '.').replace(/[^\d.]/g, '');
        // Normalize box size: "60*50*40", "60x50x40", "60,50,40", "60×50×40" → "60×50×40"
        const parseBox = (s: string) => (s || '').trim().replace(/[*xXхХ,]/g, '×');

        for (const cols of lines) {
            if (cols.length < 2) continue;

            const row: ItemRow = {
                barcode: cols[0]?.trim() || '',
                qty: parseNum(cols[1] || ''),
                price_cny: parseNum(cols[2] || ''),
                box_size: parseBox(cols[3] || ''),
                pcs_per_box: parseNum(cols[4] || ''),
                weight_kg: parseNum(cols[5] || ''),
            };
            if (row.barcode && row.qty) {
                newRows.push(row);
            }
        }

        if (newRows.length > 0) {
            setShowAddForm(true);
            setRows([...newRows, emptyItemRow(), emptyItemRow()]);
        }
    };

    const filledRows = rows.filter(r => r.barcode.trim() && r.qty.trim());
    // Normalize for parsing: "1 088" → "1088", "51,84" → "51.84"
    const toNum = (s: string) => parseFloat((s || '').replace(/\s/g, '').replace(',', '.')) || 0;
    const toInt = (s: string) => parseInt((s || '').replace(/\s/g, '').replace(',', '.')) || 0;

    const handleSave = async () => {
        if (!filledRows.length) return;
        setSaving(true);
        setError('');
        try {
            const items = filledRows.map(r => ({
                barcode: r.barcode.trim(),
                qty: toInt(r.qty),
                price_cny: toNum(r.price_cny),
                box_size: r.box_size.trim() || undefined,
                pcs_per_box: r.pcs_per_box?.trim() ? toInt(r.pcs_per_box) : undefined,
                weight_kg: r.weight_kg?.trim() ? toNum(r.weight_kg) : undefined,
            }));
            await api.addFactoryOrderItems(order.id, items as any);
            setRows(Array.from({ length: 5 }, emptyItemRow));
            setShowAddForm(false);
            onItemsAdded();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : t('msg_save_error'));
        }
        setSaving(false);
    };

    return (
        <div className="glass-card sc-items-section">
            <div className="sc-items-header">
                <h4 className="sc-items-title">
                    {t('items_title')} {order.order_number}
                </h4>
                <button className="btn btn-secondary btn-sm" onClick={onCollapse}>{t('items_collapse')}</button>
            </div>

            {/* Existing items */}
            {existingItems.length > 0 && (
                <DataTable
                    columns={[
                        { key: 'barcode', label: t('col_barcode') },
                        { key: 'article_seller', label: t('col_article'), render: (v: string | null) => v || '\u2014' },
                        { key: 'subject', label: t('col_category'), render: (v: string | null) => v || '\u2014' },
                        { key: 'qty', label: t('col_qty'), align: 'right', render: (v: number) => formatNumber(v, 0) },
                        {
                            key: 'price_cny', label: `${t('col_price')} ${currencySymbol(order.supplier?.currency)}`, align: 'right',
                            render: (v: unknown) => formatNumber(Number(v), 2) + ' ' + currencySymbol(order.supplier?.currency),
                        },
                        { key: 'box_size', label: t('col_box_spec') },
                        { key: 'pcs_per_box', label: t('col_pcs_per_box'), align: 'right' },
                        {
                            key: 'weight_kg', label: t('col_weight_1pc'), align: 'right',
                            render: (v: unknown) => v != null ? formatNumber(Number(v), 2) : '\u2014',
                        },
                        {
                            key: 'distribution', label: t('col_allocation'), align: 'center',
                            render: (_v: unknown, row: FactoryOrderItem) => {
                                const rem = row.qty - row.assigned_qty;
                                const pct = row.qty > 0 ? Math.round((row.assigned_qty / row.qty) * 100) : 0;
                                return (
                                    <div className="sc-allocation-cell">
                                        <span className="sc-allocation-count" style={{ color: pct === 100 ? 'var(--color-success)' : 'var(--color-primary)' }}>
                                            {row.assigned_qty} / {row.qty}
                                        </span>
                                        {pct === 100 ? (
                                            <span className="sc-allocation-done">{t('orders_all_allocated')}</span>
                                        ) : rem > 0 ? (
                                            <span className="sc-allocation-rem">{t('orders_remaining')} {rem}</span>
                                        ) : null}
                                    </div>
                                );
                            },
                        },
                    ]}
                    data={existingItems}
                    exportName={`order_${order.order_number}_items`}
                />
            )}

            {/* Add items section */}
            <div className={existingItems.length > 0 ? 'sc-items-add-divider' : ''} onPaste={handlePaste}>
                {!showAddForm ? (
                    <div className={existingItems.length > 0 ? 'sc-items-add-center-sm' : 'sc-items-add-center'}>
                        <button className="btn btn-primary btn-sm" onClick={() => setShowAddForm(true)}>
                            {t('items_add')}
                        </button>
                        {existingItems.length === 0 && (
                            <div className="sc-items-paste-hint">
                                {t('items_paste_hint')}
                            </div>
                        )}
                    </div>
                ) : (
                    <>
                        <div className="sc-items-add-header">
                            <div className="sc-items-add-hint">
                                {t('items_paste_header')} <span className="sc-items-add-hint-sub">({t('items_paste_sub')})</span>
                            </div>
                            <div className="sc-items-add-actions">
                                <button className="btn btn-secondary btn-sm" onClick={() => { setShowAddForm(false); setRows(Array.from({ length: 5 }, emptyItemRow)); setError(''); }}>
                                    {t('btn_cancel')}
                                </button>
                                {filledRows.length > 0 && (
                                    <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={saving}>
                                        {saving ? t('msg_saving') : `${t('items_save_count')} (${filledRows.length})`}
                                    </button>
                                )}
                            </div>
                        </div>

                        {error && <div className="sc-error-msg">{error}</div>}

                        <table className="data-table sc-table-xs">
                            <thead>
                                <tr>
                                    <th className="sc-th-center" style={{ width: 30 }}>#</th>
                                    <th style={{ minWidth: 130 }}>{t('col_barcode')}</th>
                                    <th style={{ width: 70 }}>{t('col_qty')}</th>
                                    <th style={{ width: 80 }}>{t('col_price')} {currencySymbol(order.supplier?.currency)}</th>
                                    <th style={{ width: 120 }}>{t('col_box_spec')}</th>
                                    <th style={{ width: 65 }}>{t('col_pcs_per_box')}</th>
                                    <th style={{ width: 70 }}>{t('col_weight_1pc')}</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((row, i) => (
                                    <tr key={i}>
                                        <td className="sc-item-row-num">{i + 1}</td>
                                        <td><input value={row.barcode} onChange={e => updateRow(i, 'barcode', e.target.value)} placeholder="2037142812010" className="sc-form-input-sm sc-form-input-mono" autoComplete="off" name={`b${i}`} /></td>
                                        <td><input inputMode="numeric" value={row.qty} onChange={e => updateRow(i, 'qty', e.target.value)} placeholder="0" className="sc-form-input-sm" autoComplete="off" name={`q${i}`} /></td>
                                        <td><input inputMode="decimal" value={row.price_cny} onChange={e => updateRow(i, 'price_cny', e.target.value)} placeholder="0" className="sc-form-input-sm" autoComplete="off" name={`p${i}`} /></td>
                                        <td><input value={row.box_size} onChange={e => updateRow(i, 'box_size', e.target.value)} placeholder="60*50*40" className="sc-form-input-sm" autoComplete="off" name={`bs${i}`} /></td>
                                        <td><input inputMode="numeric" value={row.pcs_per_box} onChange={e => updateRow(i, 'pcs_per_box', e.target.value)} placeholder="50" className="sc-form-input-sm" autoComplete="off" name={`pp${i}`} /></td>
                                        <td><input inputMode="decimal" value={row.weight_kg} onChange={e => updateRow(i, 'weight_kg', e.target.value)} placeholder="8,5" className="sc-form-input-sm" autoComplete="off" name={`w${i}`} /></td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </>
                )}
            </div>
        </div>
    );
}

// ─── Tab 2: Vehicles ────────────────────────────────────────────────────────

function TransportBadge({ type }: { type?: string }) {
    const { t } = useT();
    const transportTypes = getTransportTypes(t);
    const tt = transportTypes[type || 'AUTO'] || transportTypes.AUTO;
    return (
        <span className="badge sc-transport-badge" style={{ background: tt.color }}>
            {tt.label}
        </span>
    );
}

function VehicleTimeline({ status }: { status?: VehicleStatus }) {
    const { t } = useT();
    const statusLabels = getVehicleStatusLabels(t);
    const steps = VEHICLE_STATUSES;
    const currentIdx = status ? steps.indexOf(status) : -1;
    return (
        <div className="sc-timeline-row">
            {steps.map((s, i) => {
                const done = i <= currentIdx;
                const isCurrent = i === currentIdx;
                const color = done ? VEHICLE_STATUS_COLORS[s] : 'var(--color-border)';
                return (
                    <React.Fragment key={s}>
                        {i > 0 && (
                            <div className="sc-timeline-connector" style={{
                                background: done ? VEHICLE_STATUS_COLORS[steps[i]] : 'var(--color-border)',
                            }} />
                        )}
                        <div className="sc-timeline-step">
                            <div style={{
                                width: isCurrent ? 14 : 10, height: isCurrent ? 14 : 10,
                                borderRadius: '50%', background: color,
                                border: isCurrent ? `2px solid ${color}` : 'none',
                                boxShadow: isCurrent ? `0 0 6px ${color}66` : 'none',
                            }} />
                            <span className="sc-timeline-label" style={{ color: done ? 'var(--color-text)' : 'var(--color-text-muted)', fontWeight: isCurrent ? 600 : 400 }}>
                                {statusLabels[s]}
                            </span>
                        </div>
                    </React.Fragment>
                );
            })}
        </div>
    );
}

function CapacityBar({ vehicle }: { vehicle: VehicleSchema }) {
    // Simple volume/weight display based on items
    const containerKey = vehicle.container_type || 'truck1';
    const container = CONTAINERS[containerKey];
    if (!container) return null;

    const containerVol = container.l * container.w * container.h;

    // Estimate volume from box_size of items
    let usedVol = 0;
    for (const item of vehicle.items) {
        if (item.box_size && item.pcs_per_box && item.pcs_per_box > 0) {
            const parts = item.box_size.split(/[*xх×]/).map(Number);
            if (parts.length === 3 && parts.every(p => p > 0)) {
                const boxVol = (parts[0] * parts[1] * parts[2]) / 1e6; // cm³ → m³
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
        <div className="sc-capacity-bar-wrap">
            <div className="sc-capacity-label">
                {totalVol.toFixed(1)} / {containerVol.toFixed(1)} m{'\u00B3'} ({CONTAINERS[containerKey].name})
            </div>
            <div className="sc-capacity-track">
                <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 3, transition: 'width 0.3s' }} />
            </div>
            <div className="sc-capacity-pct" style={{ color }}>{pct.toFixed(1)}%</div>
        </div>
    );
}

function VehicleDetail({ vehicle, onRefresh }: { vehicle: VehicleSchema; onRefresh: () => void }) {
    const { t } = useT();
    const [removing, setRemoving] = useState<number | null>(null);
    const [recalcing, setRecalcing] = useState(false);
    const [costPreview, setCostPreview] = useState<VehicleCostSummary | null>(vehicle.cost_summary || null);

    const handleRemoveItem = async (itemId: number) => {
        if (!confirm(t('vehicle_items_confirm_delete'))) return;
        setRemoving(itemId);
        try {
            await api.removeItemFromVehicle(vehicle.order_no, itemId);
            onRefresh();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_error'));
        }
        setRemoving(null);
    };

    const handleRecalc = async () => {
        setRecalcing(true);
        try {
            const summary = await api.recalcVehicle(vehicle.order_no);
            setCostPreview(summary);
            onRefresh();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('vehicle_items_recalc_error'));
        }
        setRecalcing(false);
    };

    const containerOptions = getContainerOptions(t);
    const containerLabel = containerOptions.find(c => c.key === vehicle.container_type)?.label || vehicle.container_type || vehicle.transport_type;
    const cs = costPreview || vehicle.cost_summary;

    return (
        <div className="sc-vehicle-detail">
            {/* Timeline */}
            <div className="sc-vehicle-timeline">
                <VehicleTimeline status={vehicle.status as VehicleStatus} />
            </div>

            {/* Summary row */}
            <div className="sc-summary-row">
                <span>{t('cost_container')}: <b className="sc-color-text">{containerLabel}</b></span>
                {vehicle.delivery_cost_cny > 0 && (
                    <span>{t('cost_shipping')}: {formatNumber(vehicle.delivery_cost_cny)} {'\u00A5'} {'\u00D7'} {Number(vehicle.rate_cny).toFixed(2)} = {formatNumber(Number(vehicle.delivery_cost_cny) * Number(vehicle.rate_cny))} {'\u20BD'}</span>
                )}
                {vehicle.total_weight_kg && <span>{t('cost_weight')}: {formatNumber(vehicle.total_weight_kg)} kg</span>}
                {vehicle.estimated_arrival_date && (
                    <span>{t('cost_arrival')}: <b className="sc-color-text">{formatDate(vehicle.estimated_arrival_date)}</b></span>
                )}
                {vehicle.invoice_no && <span>{t('cost_invoice')}: {vehicle.invoice_no}</span>}
            </div>

            {/* Cost summary */}
            {cs && (
                <div className="sc-cost-grid">
                    <div className="sc-cost-item">
                        <div className="sc-cost-label">{t('cost_goods')}</div>
                        <div className="sc-cost-value">{formatNumber(cs.total_cost_rub)} {'\u20BD'}</div>
                    </div>
                    <div className="sc-cost-item">
                        <div className="sc-cost-label">{t('cost_delivery')}</div>
                        <div className="sc-cost-value">{formatNumber(cs.total_delivery_rub)} {'\u20BD'}</div>
                    </div>
                    <div className="sc-cost-item">
                        <div className="sc-cost-label">{t('cost_duty')}</div>
                        <div className="sc-cost-value">{formatNumber(cs.total_duty_rub)} {'\u20BD'}</div>
                    </div>
                    <div className="sc-cost-item">
                        <div className="sc-cost-label">{t('cost_vat')}</div>
                        <div className="sc-cost-value">{formatNumber(cs.total_vat_rub)} {'\u20BD'}</div>
                    </div>
                    <div className="sc-cost-item">
                        <div className="sc-cost-label">{t('cost_total')}</div>
                        <div className="sc-cost-total-value">{formatNumber(cs.total_rub)} {'\u20BD'}</div>
                    </div>
                </div>
            )}

            {/* Recalc button for FORMING */}
            {vehicle.status === 'FORMING' && vehicle.items.length > 0 && (
                <div className="sc-recalc-row">
                    <button className="btn btn-secondary btn-sm" onClick={handleRecalc} disabled={recalcing}>
                        {recalcing ? t('vehicle_items_recalcing') : t('vehicle_items_recalc')}
                    </button>
                </div>
            )}

            {/* Capacity bar */}
            <CapacityBar vehicle={vehicle} />

            {/* Items table */}
            {vehicle.items.length > 0 ? (
                <table className="sc-vehicle-items-table">
                    <thead>
                        <tr>
                            <th className="sc-vehicle-items-th">{t('col_from_order')}</th>
                            <th className="sc-vehicle-items-th">{t('col_barcode')}</th>
                            <th className="sc-vehicle-items-th">{t('col_article')}</th>
                            <th className="sc-vehicle-items-th-right">{t('col_qty')}</th>
                            <th className="sc-vehicle-items-th-right">{t('col_price_cny')}</th>
                            <th className="sc-vehicle-items-th-right">{t('col_sum_cny')}</th>
                            {vehicle.status === 'FORMING' && <th className="sc-vehicle-items-th" style={{ width: 40 }}></th>}
                        </tr>
                    </thead>
                    <tbody>
                        {vehicle.items.map(item => (
                            <tr key={item.id}>
                                <td className="sc-vehicle-items-td sc-text-primary">{item.factory_order_number || '\u2014'}</td>
                                <td className="sc-vehicle-items-td-mono">{item.barcode}</td>
                                <td className="sc-vehicle-items-td">{item.article_seller || item.subject || '\u2014'}</td>
                                <td className="sc-vehicle-items-td-fw">{formatNumber(item.qty)}</td>
                                <td className="sc-vehicle-items-td-right">{formatNumber(item.price_cny)}</td>
                                <td className="sc-vehicle-items-td-right">{formatNumber(item.qty * item.price_cny)}</td>
                                {vehicle.status === 'FORMING' && (
                                    <td className="sc-vehicle-items-td-center">
                                        <button
                                            onClick={() => handleRemoveItem(item.id)}
                                            disabled={removing === item.id}
                                            className="sc-vehicle-remove-btn"
                                            style={{ opacity: removing === item.id ? 0.3 : 0.6 }}
                                            title={t('vehicle_items_remove')}
                                        >
                                            {'\u2716'}
                                        </button>
                                    </td>
                                )}
                            </tr>
                        ))}
                    </tbody>
                </table>
            ) : (
                <div className="sc-vehicle-empty">{t('vehicle_items_empty')}</div>
            )}
        </div>
    );
}

// ─── Add Items Modal ───────────────────────────────────────────────────────

function AddItemsModal({ vehicle, onClose, onDone }: { vehicle: VehicleSchema; onClose: () => void; onDone: () => void }) {
    const { t } = useT();
    const [groups, setGroups] = useState<AvailableItemGroup[]>([]);
    const [loading, setLoading] = useState(true);
    const [submitting, setSubmitting] = useState(false);
    const [selected, setSelected] = useState<Record<number, number>>({});

    useEffect(() => {
        (async () => {
            try {
                const data = await api.getAvailableItems();
                setGroups(data);
            } catch {
                /* empty */
            }
            setLoading(false);
        })();
    }, []);

    const totalSelected = Object.values(selected).reduce((s, q) => s + q, 0);

    const handleSubmit = async () => {
        const items = Object.entries(selected)
            .filter(([, qty]) => qty > 0)
            .map(([id, qty]) => ({ factory_order_item_id: Number(id), qty }));
        if (items.length === 0) return;
        setSubmitting(true);
        try {
            await api.addItemsToVehicle(vehicle.order_no, items);
            onDone();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_error'));
        }
        setSubmitting(false);
    };

    return (
        <div className="sc-modal-overlay">
            <div className="sc-modal-backdrop" onClick={onClose} />
            <div className="sc-modal-dialog">
                {/* Header */}
                <div className="sc-modal-header">
                    <h3 className="sc-modal-header-title">{t('add_items_title')} {vehicle.order_no}</h3>
                    <p className="sc-modal-header-sub">{t('add_items_subtitle')}</p>
                </div>

                {/* Body */}
                <div className="sc-modal-body">
                    {loading ? (
                        <div className="sc-modal-loading"><div className="spinner sc-spinner-center" /></div>
                    ) : groups.length === 0 ? (
                        <div className="sc-modal-empty">{t('add_items_empty')}</div>
                    ) : (
                        groups.map(group => (
                            <div key={group.order_id} className="sc-add-group">
                                <div className="sc-add-group-header">
                                    <span>{'\uD83D\uDCE6'}</span>
                                    <span className="sc-add-group-name">{group.order_number}</span>
                                    {group.factory_name && <span className="sc-add-group-factory">{'\u2014'} {group.factory_name}</span>}
                                    <span className="sc-add-group-count">{group.items.length} {t('add_items_pos')}</span>
                                </div>
                                <table className="sc-add-items-table">
                                    <thead>
                                        <tr>
                                            <th className="sc-add-items-th">{t('col_barcode')}</th>
                                            <th className="sc-add-items-th">{t('col_article')}</th>
                                            <th className="sc-add-items-th-right">{t('col_available')}</th>
                                            <th className="sc-add-items-th-right" style={{ width: 100 }}>{t('btn_add')}</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {group.items.map(item => (
                                            <tr key={item.id}>
                                                <td className="sc-add-items-td-mono">{item.barcode}</td>
                                                <td className="sc-add-items-td">
                                                    {item.article_seller || '\u2014'}
                                                    {item.subject && <span className="sc-add-items-subject">{'\u2022'} {item.subject}</span>}
                                                </td>
                                                <td className="sc-add-items-td-right">{formatNumber(item.remaining_qty)}</td>
                                                <td className="sc-add-items-td-right">
                                                    <input
                                                        type="number"
                                                        min={0}
                                                        max={item.remaining_qty}
                                                        value={selected[item.id] || ''}
                                                        onChange={e => {
                                                            const v = Math.min(item.remaining_qty, Math.max(0, parseInt(e.target.value) || 0));
                                                            setSelected(prev => ({ ...prev, [item.id]: v }));
                                                        }}
                                                        placeholder="0"
                                                        className="sc-add-items-qty-input"
                                                    />
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        ))
                    )}
                </div>

                {/* Footer */}
                <div className="sc-modal-footer">
                    <button className="btn btn-secondary" onClick={onClose}>{t('btn_cancel')}</button>
                    <button
                        className="btn btn-primary"
                        onClick={handleSubmit}
                        disabled={totalSelected === 0 || submitting}
                    >
                        {submitting ? t('add_items_adding') : `${t('add_items_count')} (${formatNumber(totalSelected)} ${t('unit_pcs')})`}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ─── Create Vehicle Inline Form ──────────────────────────────────────────

function CreateVehicleForm({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
    const { t } = useT();
    const [form, setForm] = useState<VehicleCreate>({
        country: 'CHINA',
        container_type: 'truck1',
        delivery_cost_cny: 0,
        delivery_cost_usd: 0,
        delivery_cost_rub: 0,
        rate_cny: 12.5,
        rate_usd: 92,
        rate_eur: 98,
    });
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        api.getWarehouses().then(setWarehouses).catch(() => {});
    }, []);

    const handleSubmit = async () => {
        setSubmitting(true);
        try {
            await api.createVehicle(form);
            onDone();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_error'));
        }
        setSubmitting(false);
    };

    const containerOptions = getContainerOptions(t);
    const isRussia = form.country === 'RUSSIA';

    const setChina = () => setForm(f => ({
        ...f,
        country: 'CHINA',
        container_type: 'truck1',
        rate_cny: 12.5,
        rate_usd: 92,
        rate_eur: 98,
    }));

    const setRussia = () => setForm(f => ({
        ...f,
        country: 'RUSSIA',
        container_type: 'gazelle',
        delivery_cost_cny: 0,
        delivery_cost_usd: 0,
        rate_cny: 1,
        rate_usd: 1,
        rate_eur: 1,
        invoice_no: undefined,
        payment_ref: undefined,
    }));

    const countryBtnStyle = (active: boolean): React.CSSProperties => ({
        padding: '6px 12px', borderRadius: 8, fontSize: 12, fontWeight: 500,
        border: active ? '2px solid var(--color-primary)' : '1px solid var(--color-border)',
        background: active ? 'var(--color-primary-bg)' : 'var(--color-bg)',
        color: active ? 'var(--color-primary)' : 'var(--color-text)',
        cursor: 'pointer', transition: 'all 0.15s',
    });

    return (
        <div className="glass-card sc-form-card">
            <div className="sc-form-header">
                <h3 className="sc-form-title">{t('vehicle_form_title')}</h3>
                <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
            </div>
            <div className="sc-form-col">
                {/* Country selector */}
                <div>
                    <label className="sc-form-label">{t('vehicle_country')}</label>
                    <div className="sc-form-container-btns">
                        <button type="button" onClick={setChina} style={countryBtnStyle(!isRussia)}>
                            {t('country_china')}
                        </button>
                        <button type="button" onClick={setRussia} style={countryBtnStyle(isRussia)}>
                            {t('country_russia')}
                        </button>
                    </div>
                </div>

                <div className="sc-form-grid-2">
                    <div>
                        <label className="sc-form-label">{t('vehicle_form_vehicle_name')}</label>
                        <input value={form.vehicle_name || ''} onChange={e => setForm(f => ({ ...f, vehicle_name: e.target.value || undefined }))} placeholder={t('vehicle_form_vehicle_name_placeholder')} className="sc-form-input" autoFocus />
                    </div>
                    <div>
                        <label className="sc-form-label">{t('vehicle_form_container_type')}</label>
                        {isRussia ? (
                            <div className="sc-form-container-btns">
                                <button
                                    type="button"
                                    style={{
                                        padding: '6px 12px', borderRadius: 8, fontSize: 12, fontWeight: 500,
                                        border: '2px solid var(--color-primary)',
                                        background: 'var(--color-primary-bg)',
                                        color: 'var(--color-primary)',
                                        cursor: 'default',
                                    }}
                                >
                                    {t('container_gazelle')}
                                </button>
                            </div>
                        ) : (
                            <div className="sc-form-container-btns">
                                {containerOptions.map(opt => {
                                    const isSelected = form.container_type === opt.key;
                                    return (
                                        <button
                                            key={opt.key}
                                            type="button"
                                            onClick={() => setForm(f => ({ ...f, container_type: opt.key }))}
                                            style={{
                                                padding: '6px 12px', borderRadius: 8, fontSize: 12, fontWeight: 500,
                                                border: isSelected ? '2px solid var(--color-primary)' : '1px solid var(--color-border)',
                                                background: isSelected ? 'var(--color-primary-bg)' : 'var(--color-bg)',
                                                color: isSelected ? 'var(--color-primary)' : 'var(--color-text)',
                                                cursor: 'pointer', transition: 'all 0.15s',
                                            }}
                                        >
                                            {opt.label}
                                        </button>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                </div>

                {isRussia ? (
                    <div className="sc-form-grid-2">
                        <div>
                            <label className="sc-form-label">{t('vehicle_form_pickup_date')}</label>
                            <input type="date" value={form.ship_date || ''} onChange={e => setForm(f => ({ ...f, ship_date: e.target.value || undefined }))} className="sc-form-input" />
                        </div>
                        <div>
                            <label className="sc-form-label">{t('vehicle_form_delivery_rub')}</label>
                            <input
                                type="number"
                                min="0"
                                value={form.delivery_cost_rub ?? ''}
                                onChange={e => setForm(f => ({ ...f, delivery_cost_rub: parseFloat(e.target.value) || 0 }))}
                                className="sc-form-input"
                            />
                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 4 }}>
                                {t('vehicle_form_delivery_rub_hint')}
                            </div>
                        </div>
                    </div>
                ) : (
                    <>
                        <div className="sc-form-grid-3">
                            <div>
                                <label className="sc-form-label">{t('vehicle_form_pickup_date')}</label>
                                <input type="date" value={form.ship_date || ''} onChange={e => setForm(f => ({ ...f, ship_date: e.target.value || undefined }))} className="sc-form-input" />
                            </div>
                            <div>
                                <label className="sc-form-label">{t('vehicle_form_invoice')}</label>
                                <input value={form.invoice_no || ''} onChange={e => setForm(f => ({ ...f, invoice_no: e.target.value || undefined }))} placeholder="CC20260011" className="sc-form-input" />
                            </div>
                            <div>
                                <label className="sc-form-label">{t('vehicle_form_order_no')}</label>
                                <input value={form.payment_ref || ''} onChange={e => setForm(f => ({ ...f, payment_ref: e.target.value || undefined }))} placeholder="ENV-001" className="sc-form-input" />
                            </div>
                        </div>
                        <div className="sc-form-grid-5">
                            <div>
                                <label className="sc-form-label">{t('vehicle_form_delivery_cny')}</label>
                                <input type="number" value={form.delivery_cost_cny || ''} onChange={e => setForm(f => ({ ...f, delivery_cost_cny: parseFloat(e.target.value) || 0 }))} className="sc-form-input" />
                            </div>
                            <div>
                                <label className="sc-form-label">{t('vehicle_form_delivery_usd')}</label>
                                <input type="number" value={form.delivery_cost_usd || ''} onChange={e => setForm(f => ({ ...f, delivery_cost_usd: parseFloat(e.target.value) || 0 }))} className="sc-form-input" />
                            </div>
                            <div>
                                <label className="sc-form-label">{t('vehicle_form_rate_cny')}</label>
                                <input type="number" step="0.01" value={form.rate_cny || ''} onChange={e => setForm(f => ({ ...f, rate_cny: parseFloat(e.target.value) || 1 }))} className="sc-form-input" />
                            </div>
                            <div>
                                <label className="sc-form-label">{t('vehicle_form_rate_usd')}</label>
                                <input type="number" step="0.01" value={form.rate_usd || ''} onChange={e => setForm(f => ({ ...f, rate_usd: parseFloat(e.target.value) || 1 }))} className="sc-form-input" />
                            </div>
                            <div>
                                <label className="sc-form-label">{t('vehicle_form_rate_eur')}</label>
                                <input type="number" step="0.01" value={form.rate_eur || ''} onChange={e => setForm(f => ({ ...f, rate_eur: parseFloat(e.target.value) || 1 }))} className="sc-form-input" />
                            </div>
                        </div>
                    </>
                )}

                <div className="sc-form-grid-2">
                    <div>
                        <label className="sc-form-label">{t('vehicle_form_plate_number')}</label>
                        <input value={form.plate_number || ''} onChange={e => setForm(f => ({ ...f, plate_number: e.target.value || undefined }))} placeholder={t('vehicle_form_plate_placeholder')} className="sc-form-input" />
                    </div>
                    {warehouses.length > 0 && (
                        <div>
                            <label className="sc-form-label">{t('vehicle_form_warehouse')}</label>
                            <select value={form.target_warehouse_id || ''} onChange={e => setForm(f => ({ ...f, target_warehouse_id: e.target.value ? Number(e.target.value) : undefined }))} className="sc-form-input">
                                <option value="">{t('create_order_unselected')}</option>
                                {warehouses.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
                            </select>
                        </div>
                    )}
                </div>

                <div>
                    <label className="sc-form-label">{t('vehicle_form_note')}</label>
                    <input value={form.note || ''} onChange={e => setForm(f => ({ ...f, note: e.target.value }))} className="sc-form-input" />
                </div>
            </div>
            <div className="sc-form-footer">
                <button className="btn btn-secondary btn-sm" onClick={onClose}>{t('btn_cancel')}</button>
                <button className="btn btn-primary btn-sm" onClick={handleSubmit} disabled={submitting}>
                    {submitting ? t('msg_creating') : t('btn_create')}
                </button>
            </div>
        </div>
    );
}

// ─── Vehicles Tab Main ─────────────────────────────────────────────────────

function VehiclesTab() {
    const { t } = useT();
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const [vehicles, setVehicles] = useState<VehicleSchema[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [showCreate, setShowCreate] = useState(false);
    const [statusFilter, setStatusFilter] = useState<VehicleStatus[]>([]);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const data = await api.getVehicles();
            setVehicles(data);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : t('msg_loading_error'));
        }
        setLoading(false);
    }, [t]);

    useEffect(() => { load(); }, [load]);

    const statusCounts = useMemo(() => {
        const counts: Record<VehicleStatus, number> = {
            FORMING: 0, SHIPPED: 0, CUSTOMS: 0, DISPATCHED: 0, DELIVERED: 0,
        };
        for (const v of vehicles) {
            const s = v.status as VehicleStatus;
            if (s in counts) counts[s] += 1;
        }
        return counts;
    }, [vehicles]);

    const filteredVehicles = useMemo(() => {
        if (statusFilter.length === 0) return vehicles;
        const set = new Set(statusFilter);
        return vehicles.filter(v => set.has(v.status as VehicleStatus));
    }, [vehicles, statusFilter]);

    const toggleStatus = (status: VehicleStatus) => {
        setStatusFilter(prev =>
            prev.includes(status) ? prev.filter(s => s !== status) : [...prev, status]
        );
    };
    const clearStatusFilter = () => setStatusFilter([]);

    const openVehicle = (orderNo: string) => {
        // For order_no with slashes, encode each segment separately so Next.js catch-all route works
        const encodedPath = orderNo.split('/').map(encodeURIComponent).join('/');
        router.push(`/p/${slug}/supply-chain/vehicles/${encodedPath}`);
    };

    const NEXT_STATUS: Record<string, { status: string; label: string; color: string }> = {
        FORMING: { status: 'SHIPPED', label: t('btn_ship'), color: 'var(--color-primary)' },
        SHIPPED: { status: 'CUSTOMS', label: t('transition_to_customs'), color: '#f59e0b' },
        CUSTOMS: { status: 'DISPATCHED', label: t('transition_dispatched'), color: '#8b5cf6' },
        // DISPATCHED → DELIVERED: автоматически при приёмке на складе
    };

    const handleStatusChange = async (e: React.MouseEvent, orderNo: string, nextStatus: string) => {
        e.stopPropagation();
        if (!confirm(`${t('vehicles_confirm_status')} ${orderNo}?`)) return;
        try {
            await api.updateVehicleStatus(orderNo, { status: nextStatus as VehicleStatus });
            load();
        } catch (err: unknown) {
            alert(err instanceof Error ? err.message : t('msg_error'));
        }
    };

    if (loading) {
        return (
            <div className="glass-card sc-loading-card">
                <div className="spinner sc-spinner-center" />
                {t('msg_loading')}
            </div>
        );
    }

    return (
        <>
            {error && (
                <div className="glass-card sc-error-card">
                    {error}
                    <button className="btn btn-secondary btn-sm sc-retry-btn" onClick={load}>{t('btn_retry')}</button>
                </div>
            )}

            {/* Create vehicle inline form */}
            {showCreate && (
                <CreateVehicleForm
                    onClose={() => setShowCreate(false)}
                    onDone={() => { setShowCreate(false); load(); }}
                />
            )}

            <div className="glass-card sc-vehicles-card">
                {/* Header */}
                <div className="sc-vehicles-header">
                    <h3 className="sc-vehicles-header-title">
                        {t('vehicles_title')} ({statusFilter.length > 0 ? `${filteredVehicles.length} / ${vehicles.length}` : vehicles.length})
                    </h3>
                    <div className="sc-vehicles-header-actions">
                        <button className="btn btn-secondary btn-sm" onClick={load}>{t('btn_refresh')}</button>
                        <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(v => !v)}>
                            {showCreate ? t('btn_close') : t('vehicles_btn_create')}
                        </button>
                    </div>
                </div>

                {/* Status filter pills */}
                {vehicles.length > 0 && (
                    <div className="sc-vehicles-filter-row">
                        <button
                            type="button"
                            className={`sc-status-pill${statusFilter.length === 0 ? ' sc-status-pill-active' : ''}`}
                            onClick={clearStatusFilter}
                        >
                            {t('filter_all')} <span className="sc-status-pill-count">{vehicles.length}</span>
                        </button>
                        {VEHICLE_STATUSES.map(status => {
                            const count = statusCounts[status];
                            const active = statusFilter.includes(status);
                            const color = VEHICLE_STATUS_COLORS[status];
                            return (
                                <button
                                    key={status}
                                    type="button"
                                    onClick={() => toggleStatus(status)}
                                    className={`sc-status-pill${active ? ' sc-status-pill-active' : ''}`}
                                    style={active ? {
                                        borderColor: color,
                                        background: `${color}14`,
                                        color,
                                    } : undefined}
                                >
                                    {getVehicleStatusLabels(t)[status]} <span className="sc-status-pill-count">{count}</span>
                                </button>
                            );
                        })}
                    </div>
                )}

                {vehicles.length === 0 ? (
                    <div className="sc-vehicles-empty">
                        <div className="sc-vehicles-empty-icon">{'\uD83D\uDE9B'}</div>
                        <div>{t('vehicles_empty')}</div>
                    </div>
                ) : filteredVehicles.length === 0 ? (
                    <div className="sc-vehicles-empty">
                        <div className="sc-vehicles-empty-icon">🔍</div>
                        <div>{t('vehicles_filter_empty')}</div>
                    </div>
                ) : (
                    <table className="sc-vehicles-table">
                        <thead>
                            <tr>
                                <th className="sc-vehicles-th-first">{t('vehicles_title')}</th>
                                <th className="sc-vehicles-th">{t('col_plate_number')}</th>
                                <th className="sc-vehicles-th">{t('col_order')}</th>
                                <th className="sc-vehicles-th">{t('col_status')}</th>
                                <th className="sc-vehicles-th">{t('col_container')}</th>
                                <th className="sc-vehicles-th-right">{t('col_items')}</th>
                                <th className="sc-vehicles-th-right">{t('col_qty')}</th>
                                <th className="sc-vehicles-th">{t('col_invoice')}</th>
                                <th className="sc-vehicles-th-right">{t('col_weight')}</th>
                                <th className="sc-vehicles-th-right">{t('col_cost')}</th>
                                <th className="sc-vehicles-th">{t('col_shipped_date')}</th>
                                <th className="sc-vehicles-th">{t('col_arrival_date')}</th>
                                <th className="sc-vehicles-th-last">{t('col_actions')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {filteredVehicles.map(v => {
                                const status = v.status as VehicleStatus;
                                return (
                                    <tr
                                        key={v.order_no}
                                        onClick={() => openVehicle(v.order_no)}
                                        className="sc-tr-row"
                                        onMouseEnter={e => (e.currentTarget.style.background = 'var(--color-bg-secondary)')}
                                        onMouseLeave={e => (e.currentTarget.style.background = '')}
                                    >
                                        <td className="sc-vehicles-td-first">{v.vehicle_name || v.order_no}</td>
                                        <td className="sc-vehicles-td sc-text-muted-sm" style={{ color: v.plate_number ? 'var(--color-text)' : 'var(--color-text-muted)' }}>{v.plate_number || '\u2014'}</td>
                                        <td className="sc-vehicles-td sc-text-muted-sm" style={{ color: v.payment_ref ? 'var(--color-text)' : 'var(--color-text-muted)' }}>{v.payment_ref || '\u2014'}</td>
                                        <td className="sc-vehicles-td"><StatusBadge status={status} /></td>
                                        <td className="sc-vehicles-td sc-text-muted-sm">
                                            {getContainerOptions(t).find(c => c.key === v.container_type)?.label || v.transport_type || 'AUTO'}
                                        </td>
                                        <td className="sc-vehicles-td-right">{v.items_count}</td>
                                        <td className="sc-vehicles-td-right">{formatNumber(v.total_qty, 0)}</td>
                                        <td className="sc-vehicles-td sc-text-muted-sm" style={{ color: v.invoice_no ? 'var(--color-text)' : 'var(--color-text-muted)' }}>{v.invoice_no || '\u2014'}</td>
                                        <td className="sc-vehicles-td-right">{v.total_weight_kg ? formatNumber(Number(v.total_weight_kg), 0) + ' kg' : '\u2014'}</td>
                                        <td className="sc-vehicles-td-right">{Number(v.total_cny) > 0 ? formatNumber(Number(v.total_cny), 0) + ' \u00A5' : '\u2014'}</td>
                                        <td className="sc-vehicles-td">{v.ship_date ? formatDate(v.ship_date) : '\u2014'}</td>
                                        <td className="sc-vehicles-td">{v.estimated_arrival_date ? formatDate(v.estimated_arrival_date) : '\u2014'}</td>
                                        <td className="sc-vehicles-td-last" onClick={e => e.stopPropagation()}>
                                            <div className="sc-vehicles-td-actions">
                                                {NEXT_STATUS[status] && (
                                                    <button
                                                        onClick={e => handleStatusChange(e, v.order_no, NEXT_STATUS[status].status)}
                                                        style={{
                                                            padding: '3px 10px', borderRadius: 8, fontSize: 11, fontWeight: 600,
                                                            border: `1px solid ${NEXT_STATUS[status].color}`,
                                                            background: 'transparent', color: NEXT_STATUS[status].color,
                                                            cursor: 'pointer', transition: 'all 0.15s', whiteSpace: 'nowrap',
                                                        }}
                                                        onMouseEnter={e => { e.currentTarget.style.background = NEXT_STATUS[status].color; e.currentTarget.style.color = '#fff'; }}
                                                        onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = NEXT_STATUS[status].color; }}
                                                    >
                                                        {NEXT_STATUS[status].label}
                                                    </button>
                                                )}
                                                <span className="sc-open-link" onClick={() => openVehicle(v.order_no)}>{t('vehicles_open')}</span>
                                            </div>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                )}
            </div>

        </>
    );
}

// ─── Tab 3: Suppliers (merged with former Overview) ─────────────────────────

interface SupplierFormState {
    name: string;
    country: 'CHINA' | 'RUSSIA';
    currency: 'CNY' | 'RUB';
    delivery_days_min: string;
    delivery_days_max: string;
    note: string;
    inn: string;
    contract_number: string;
}

const emptySupplierForm = (): SupplierFormState => ({
    name: '',
    country: 'CHINA',
    currency: 'CNY',
    delivery_days_min: '',
    delivery_days_max: '',
    note: '',
    inn: '',
    contract_number: '',
});

// ─── Supplier Catalog View ──────────────────────────────────────────────────

const supplierFlag = (country: string): string =>
    country === 'CHINA' ? '\uD83C\uDDE8\uD83C\uDDF3' : '\uD83C\uDDF7\uD83C\uDDFA';

// ─── Shipment Matrix View ──────────────────────────────────────────────────

type MatrixSortKey = 'barcode' | 'article_seller' | 'subject' | 'brand' | 'total_qty' | 'total_boxes' | 'remaining_qty' | 'shipped_pct';

function ShipmentMatrixView({ supplierId }: { supplierId: number }) {
    const { t, lang } = useT();
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;

    const [matrix, setMatrix] = useState<ShipmentMatrixResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Filters
    const [search, setSearch] = useState('');
    const [onlyUnshipped, setOnlyUnshipped] = useState(false);
    const [selectedSubjects, setSelectedSubjects] = useState<string[]>([]);
    const [selectedBrands, setSelectedBrands] = useState<string[]>([]);
    const [subjectsOpen, setSubjectsOpen] = useState(false);
    const [brandsOpen, setBrandsOpen] = useState(false);

    // Sorting
    const [sortKey, setSortKey] = useState<MatrixSortKey>('remaining_qty');
    const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

    const loadMatrix = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const data = await api.getShipmentMatrix(supplierId);
            setMatrix(data);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : t('msg_loading_error'));
        }
        setLoading(false);
    }, [supplierId, t]);

    useEffect(() => { loadMatrix(); }, [loadMatrix]);

    // Flatten subjects into one list
    const allItems = useMemo((): ShipmentMatrixItem[] => {
        if (!matrix) return [];
        return matrix.subjects.flatMap(s => s.items);
    }, [matrix]);

    // Unique subjects and brands for dropdowns
    const allSubjectKeys = useMemo(() => {
        const set = new Set<string>();
        for (const it of allItems) if (it.subject) set.add(it.subject);
        return Array.from(set).sort();
    }, [allItems]);

    const allBrandsList = useMemo(() => {
        const set = new Set<string>();
        for (const it of allItems) if (it.brand) set.add(it.brand);
        return Array.from(set).sort();
    }, [allItems]);

    // Apply filters + sort
    const filteredItems = useMemo(() => {
        let list = allItems;

        if (search.trim()) {
            const q = search.trim().toLowerCase();
            list = list.filter(it =>
                it.barcode.toLowerCase().includes(q) ||
                (it.article_seller || '').toLowerCase().includes(q)
            );
        }
        if (selectedSubjects.length > 0) {
            const set = new Set(selectedSubjects);
            list = list.filter(it => it.subject && set.has(it.subject));
        }
        if (selectedBrands.length > 0) {
            const set = new Set(selectedBrands);
            list = list.filter(it => it.brand && set.has(it.brand));
        }
        if (onlyUnshipped) {
            list = list.filter(it => it.remaining_qty > 0);
        }

        const sorted = [...list].sort((a, b) => {
            const av = a[sortKey];
            const bv = b[sortKey];
            const an: number | string = sortKey === 'shipped_pct' ? Number(av ?? 0) : (av ?? '') as number | string;
            const bn: number | string = sortKey === 'shipped_pct' ? Number(bv ?? 0) : (bv ?? '') as number | string;
            if (typeof an === 'string' && typeof bn === 'string') {
                return sortDir === 'asc' ? an.localeCompare(bn) : bn.localeCompare(an);
            }
            return sortDir === 'asc' ? (an as number) - (bn as number) : (bn as number) - (an as number);
        });
        return sorted;
    }, [allItems, search, selectedSubjects, selectedBrands, onlyUnshipped, sortKey, sortDir]);

    // KPI recomputed for filtered set
    const filteredSummary = useMemo(() => {
        let totalQty = 0, inVehicles = 0, reallyShipped = 0, remaining = 0, boxes = 0;
        for (const it of filteredItems) {
            totalQty += it.total_qty;
            inVehicles += (it.total_qty - it.remaining_qty);
            reallyShipped += it.really_shipped_qty;
            remaining += it.remaining_qty;
            boxes += it.total_boxes;
        }
        return { totalQty, inVehicles, reallyShipped, remaining, boxes };
    }, [filteredItems]);

    const openVehicle = useCallback((orderNo: string) => {
        const encoded = orderNo.split('/').map(encodeURIComponent).join('/');
        router.push(`/p/${slug}/supply-chain/vehicles/${encoded}`);
    }, [router, slug]);

    const setSort = (key: MatrixSortKey) => {
        if (sortKey === key) {
            setSortDir(d => d === 'asc' ? 'desc' : 'asc');
        } else {
            setSortKey(key);
            setSortDir('desc');
        }
    };

    const toggleSubjectFilter = (s: string) => {
        setSelectedSubjects(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]);
    };
    const toggleBrandFilter = (b: string) => {
        setSelectedBrands(prev => prev.includes(b) ? prev.filter(x => x !== b) : [...prev, b]);
    };

    // Цветная полоска приоритета: красная = 0% + заказ старше 30 дней, жёлтая = частично, зелёная = 100%
    const getPriorityColor = (item: ShipmentMatrixItem): string => {
        const pct = Number(item.shipped_pct);
        if (pct >= 100) return 'var(--color-success)';
        if (pct > 0) return 'var(--color-warning)';
        if (item.latest_order_date) {
            const ageDays = (Date.now() - new Date(item.latest_order_date).getTime()) / 86400000;
            if (ageDays > 30) return 'var(--color-danger)';
        }
        return 'var(--color-text-dim)';
    };

    const handleExport = useCallback(() => {
        if (!matrix) return;
        const country = matrix.supplier.country;
        const rows = filteredItems.map(it => {
            const row: Record<string, unknown> = {
                [t('matrix_col_barcode')]: it.barcode,
                [t('matrix_col_article')]: it.article_seller || '',
                [t('matrix_col_subject')]: it.subject ? translateSubject(it.subject, country, lang) : '',
                [t('matrix_col_brand')]: it.brand || '',
                [t('matrix_col_total_qty')]: it.total_qty,
                [t('matrix_col_boxes')]: it.total_boxes,
                [t('matrix_col_remaining')]: it.remaining_qty,
                [t('matrix_col_pct')]: Number(it.shipped_pct),
            };
            matrix.vehicles.forEach(v => {
                row[v.order_no] = it.vehicle_allocations[v.order_no] || 0;
            });
            return row;
        });
        const filename = `${t('matrix_mode_shipment')}_${matrix.supplier.name}_${new Date().toISOString().slice(0, 10)}`;
        exportToExcel(rows, filename);
    }, [matrix, filteredItems, t, lang]);

    if (loading) {
        return (
            <div className="glass-card sc-loading-card">
                <div className="spinner sc-spinner-center" />
                {t('msg_loading')}
            </div>
        );
    }
    if (error) {
        return (
            <div className="glass-card sc-error-card">
                {error}
                <button className="btn btn-secondary btn-sm sc-retry-btn" onClick={loadMatrix}>
                    {t('btn_retry')}
                </button>
            </div>
        );
    }
    if (!matrix || allItems.length === 0) {
        return (
            <div className="empty-state">
                <div className="empty-state-icon">📦</div>
                <div>{t('matrix_empty')}</div>
            </div>
        );
    }

    const vehicles = matrix.vehicles;
    const totalAll = matrix.summary.total_qty;
    const isFiltered = filteredItems.length !== allItems.length;

    const statusBadge = (status: string | null) => {
        const cls = status === 'DELIVERED' ? 'badge-success'
            : status === 'DISPATCHED' ? 'badge-warning'
            : status === 'CUSTOMS' ? 'badge-info'
            : status === 'SHIPPED' ? 'badge-info'
            : 'badge-secondary';
        const label = status ? t(`status_${status.toLowerCase()}`) : '—';
        return <span className={`badge ${cls}`} style={{ fontSize: 10 }}>{label}</span>;
    };

    const sortIndicator = (key: MatrixSortKey) => {
        if (sortKey !== key) return <span className="sc-sort-indicator">⇅</span>;
        return <span className="sc-sort-indicator sc-sort-active">{sortDir === 'asc' ? '↑' : '↓'}</span>;
    };

    const reallyShippedPct = filteredSummary.totalQty > 0
        ? Math.round((filteredSummary.reallyShipped / filteredSummary.totalQty) * 100) : 0;
    const inVehiclesPct = filteredSummary.totalQty > 0
        ? Math.round((filteredSummary.inVehicles / filteredSummary.totalQty) * 100) : 0;

    return (
        <div>
            {/* KPI — recomputed for filtered set */}
            <div className="stats-grid">
                <div className="glass-card sc-catalog-kpi-card">
                    <div className="sc-catalog-kpi-label">{t('matrix_kpi_total')}</div>
                    <div className="sc-kpi-value-lg">{formatNumber(filteredSummary.totalQty, 0)}</div>
                    <div className="sc-catalog-kpi-sub">
                        {isFiltered ? t('matrix_of_total').replace('{total}', formatNumber(totalAll, 0)) : t('unit_pcs')}
                    </div>
                </div>
                <div className="glass-card sc-catalog-kpi-card">
                    <div className="sc-catalog-kpi-label">{t('matrix_kpi_in_vehicles')}</div>
                    <div className="sc-kpi-value-lg sc-color-accent">{formatNumber(filteredSummary.inVehicles, 0)}</div>
                    <div className="sc-catalog-kpi-sub">{inVehiclesPct}%</div>
                </div>
                <div className="glass-card sc-catalog-kpi-card">
                    <div className="sc-catalog-kpi-label">{t('matrix_kpi_really_shipped')}</div>
                    <div className="sc-kpi-value-lg sc-color-success">{formatNumber(filteredSummary.reallyShipped, 0)}</div>
                    <div className="sc-catalog-kpi-sub">{reallyShippedPct}%</div>
                </div>
                <div className="glass-card sc-catalog-kpi-card">
                    <div className="sc-catalog-kpi-label">{t('matrix_kpi_remaining')}</div>
                    <div className="sc-kpi-value-lg sc-color-warning">{formatNumber(filteredSummary.remaining, 0)}</div>
                    <div className="sc-catalog-kpi-sub">{t('unit_pcs')}</div>
                </div>
            </div>

            {/* Filter bar */}
            <div className="sc-matrix-filter-bar glass-card">
                <input
                    type="text"
                    className="sc-matrix-search-input"
                    placeholder={t('matrix_filter_search')}
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                />
                <div className="sc-matrix-dropdown-wrap">
                    <button
                        type="button"
                        className={`sc-matrix-dropdown-btn${selectedSubjects.length > 0 ? ' sc-matrix-dropdown-btn-active' : ''}`}
                        onClick={() => { setSubjectsOpen(v => !v); setBrandsOpen(false); }}
                    >
                        {selectedSubjects.length === 0
                            ? t('matrix_filter_subjects_all')
                            : t('matrix_filter_subjects_count').replace('{count}', String(selectedSubjects.length))}
                        <span className="sc-dropdown-chevron">▾</span>
                    </button>
                    {subjectsOpen && (
                        <div className="sc-matrix-dropdown-menu">
                            {allSubjectKeys.length === 0 ? (
                                <div className="sc-matrix-dropdown-empty">—</div>
                            ) : allSubjectKeys.map(s => (
                                <label key={s} className="sc-matrix-dropdown-item">
                                    <input
                                        type="checkbox"
                                        checked={selectedSubjects.includes(s)}
                                        onChange={() => toggleSubjectFilter(s)}
                                    />
                                    {translateSubject(s, matrix.supplier.country, lang)}
                                </label>
                            ))}
                            {selectedSubjects.length > 0 && (
                                <button
                                    type="button"
                                    className="sc-matrix-dropdown-clear"
                                    onClick={() => setSelectedSubjects([])}
                                >
                                    ✕ {t('filter_all')}
                                </button>
                            )}
                        </div>
                    )}
                </div>
                <div className="sc-matrix-dropdown-wrap">
                    <button
                        type="button"
                        className={`sc-matrix-dropdown-btn${selectedBrands.length > 0 ? ' sc-matrix-dropdown-btn-active' : ''}`}
                        onClick={() => { setBrandsOpen(v => !v); setSubjectsOpen(false); }}
                    >
                        {selectedBrands.length === 0
                            ? t('matrix_filter_brands_all')
                            : t('matrix_filter_brands_count').replace('{count}', String(selectedBrands.length))}
                        <span className="sc-dropdown-chevron">▾</span>
                    </button>
                    {brandsOpen && (
                        <div className="sc-matrix-dropdown-menu">
                            {allBrandsList.length === 0 ? (
                                <div className="sc-matrix-dropdown-empty">—</div>
                            ) : allBrandsList.map(b => (
                                <label key={b} className="sc-matrix-dropdown-item">
                                    <input
                                        type="checkbox"
                                        checked={selectedBrands.includes(b)}
                                        onChange={() => toggleBrandFilter(b)}
                                    />
                                    {b}
                                </label>
                            ))}
                            {selectedBrands.length > 0 && (
                                <button
                                    type="button"
                                    className="sc-matrix-dropdown-clear"
                                    onClick={() => setSelectedBrands([])}
                                >
                                    ✕ {t('filter_all')}
                                </button>
                            )}
                        </div>
                    )}
                </div>
                <label className="sc-matrix-unshipped-toggle">
                    <input
                        type="checkbox"
                        checked={onlyUnshipped}
                        onChange={e => setOnlyUnshipped(e.target.checked)}
                    />
                    {t('matrix_filter_unshipped')}
                </label>
                <button
                    className="btn btn-sm sc-matrix-excel-btn"
                    onClick={handleExport}
                    disabled={filteredItems.length === 0}
                >
                    📥 Excel
                </button>
            </div>

            {/* Table */}
            <div className="glass-card sc-matrix-card">
                {filteredItems.length === 0 ? (
                    <div className="empty-state">
                        <div className="empty-state-icon">🔍</div>
                        <div>{t('matrix_empty_filtered')}</div>
                    </div>
                ) : (
                    <div className="sc-matrix-wrap">
                        <table className="sc-matrix-table sc-matrix-table-sticky">
                            <thead>
                                <tr>
                                    <th className="sc-matrix-th-fixed sc-matrix-th-sortable" style={{ left: 0, minWidth: 150 }} onClick={() => setSort('barcode')}>
                                        {t('matrix_col_barcode')} {sortIndicator('barcode')}
                                    </th>
                                    <th className="sc-matrix-th-fixed sc-matrix-th-sortable" style={{ left: 150, minWidth: 110 }} onClick={() => setSort('article_seller')}>
                                        {t('matrix_col_article')} {sortIndicator('article_seller')}
                                    </th>
                                    <th className="sc-matrix-th-sortable" style={{ minWidth: 110 }} onClick={() => setSort('subject')}>
                                        {t('matrix_col_subject')} {sortIndicator('subject')}
                                    </th>
                                    <th className="sc-matrix-th-sortable" style={{ minWidth: 90 }} onClick={() => setSort('brand')}>
                                        {t('matrix_col_brand')} {sortIndicator('brand')}
                                    </th>
                                    <th className="sc-matrix-th-num sc-matrix-th-sortable" style={{ minWidth: 80 }} onClick={() => setSort('total_qty')}>
                                        {t('matrix_col_total_qty')} {sortIndicator('total_qty')}
                                    </th>
                                    <th className="sc-matrix-th-num sc-matrix-th-sortable" style={{ minWidth: 70 }} onClick={() => setSort('total_boxes')}>
                                        {t('matrix_col_boxes')} {sortIndicator('total_boxes')}
                                    </th>
                                    <th className="sc-matrix-th-num sc-matrix-th-sortable" style={{ minWidth: 80 }} onClick={() => setSort('remaining_qty')}>
                                        {t('matrix_col_remaining')} {sortIndicator('remaining_qty')}
                                    </th>
                                    <th className="sc-matrix-th-num sc-matrix-th-sortable" style={{ minWidth: 110 }} onClick={() => setSort('shipped_pct')}>
                                        {t('matrix_col_pct')} {sortIndicator('shipped_pct')}
                                    </th>
                                    {vehicles.map(v => (
                                        <th
                                            key={v.order_no}
                                            className="sc-matrix-th-vehicle sc-matrix-th-vehicle-clickable"
                                            onClick={() => openVehicle(v.order_no)}
                                            title={t('matrix_click_vehicle_hint')}
                                        >
                                            <div className="sc-matrix-vehicle-header">
                                                <div className="sc-matrix-vehicle-name">{v.order_no}</div>
                                                {statusBadge(v.status)}
                                                {v.ship_date && (
                                                    <div className="sc-matrix-vehicle-date">{formatDate(v.ship_date)}</div>
                                                )}
                                            </div>
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {filteredItems.map(item => {
                                    const pct = Number(item.shipped_pct);
                                    const priorityColor = getPriorityColor(item);
                                    return (
                                        <tr key={item.barcode} className="sc-tr-border">
                                            <td className="sc-matrix-td-fixed sc-catalog-td-mono sc-matrix-td-priority"
                                                style={{ left: 0, borderLeft: `3px solid ${priorityColor}` }}>
                                                {item.barcode}
                                            </td>
                                            <td className="sc-matrix-td-fixed" style={{ left: 150 }}>
                                                {item.article_seller || '—'}
                                            </td>
                                            <td className="sc-matrix-td">
                                                {item.subject
                                                    ? translateSubject(item.subject, matrix.supplier.country, lang)
                                                    : '—'}
                                            </td>
                                            <td className="sc-matrix-td">{item.brand || '—'}</td>
                                            <td className="sc-matrix-td-num">{formatNumber(item.total_qty, 0)}</td>
                                            <td className="sc-matrix-td-num">{item.total_boxes || '—'}</td>
                                            <td className="sc-matrix-td-num" style={{
                                                color: item.remaining_qty > 0 ? 'var(--color-warning)' : 'var(--color-success)',
                                                fontWeight: item.remaining_qty > 0 ? 600 : 400,
                                            }}>{formatNumber(item.remaining_qty, 0)}</td>
                                            <td className="sc-matrix-td-num">
                                                <div className="sc-progress-mini">
                                                    <div className="sc-progress-mini-track">
                                                        <div style={{
                                                            width: `${pct}%`,
                                                            height: '100%',
                                                            background: pct >= 100 ? 'var(--color-success)' : 'var(--color-accent)',
                                                            borderRadius: 4,
                                                        }} />
                                                    </div>
                                                    <span className="sc-progress-mini-count" style={{ fontSize: 11, minWidth: 32 }}>
                                                        {pct.toFixed(0)}%
                                                    </span>
                                                </div>
                                            </td>
                                            {vehicles.map(v => {
                                                const qty = item.vehicle_allocations[v.order_no] || 0;
                                                const cellClass = qty === 0
                                                    ? 'sc-matrix-cell-empty'
                                                    : qty >= item.total_qty
                                                    ? 'sc-matrix-cell-full'
                                                    : 'sc-matrix-cell-partial';
                                                return (
                                                    <td key={v.order_no} className={`sc-matrix-td-num ${cellClass}`}>
                                                        {qty > 0 ? formatNumber(qty, 0) : ''}
                                                    </td>
                                                );
                                            })}
                                        </tr>
                                    );
                                })}
                                {/* Grand total row */}
                                <tr className="sc-matrix-summary-row">
                                    <td className="sc-matrix-td-fixed" style={{ left: 0, fontWeight: 600 }} colSpan={4}>
                                        {t('matrix_total_row')}
                                    </td>
                                    <td className="sc-matrix-td-num" style={{ fontWeight: 600 }}>
                                        {formatNumber(filteredSummary.totalQty, 0)}
                                    </td>
                                    <td className="sc-matrix-td-num" style={{ fontWeight: 600 }}>
                                        {formatNumber(filteredSummary.boxes, 0)}
                                    </td>
                                    <td className="sc-matrix-td-num" style={{ fontWeight: 600, color: 'var(--color-warning)' }}>
                                        {formatNumber(filteredSummary.remaining, 0)}
                                    </td>
                                    <td className="sc-matrix-td-num" style={{ fontWeight: 600 }}>
                                        {filteredSummary.totalQty > 0
                                            ? Math.round((filteredSummary.inVehicles / filteredSummary.totalQty) * 100)
                                            : 0}%
                                    </td>
                                    {vehicles.map(v => {
                                        const colTotal = filteredItems.reduce(
                                            (s, it) => s + (it.vehicle_allocations[v.order_no] || 0), 0
                                        );
                                        return (
                                            <td key={v.order_no} className="sc-matrix-td-num" style={{ fontWeight: 600 }}>
                                                {colTotal > 0 ? formatNumber(colTotal, 0) : ''}
                                            </td>
                                        );
                                    })}
                                </tr>
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}

// ─── Catalog History Badge ──────────────────────────────────────────────────

function CatalogHistoryStatusBadge({ entry }: { entry: SkuOrderHistoryEntry }) {
    const { t } = useT();
    if (entry.is_delivered) {
        return <span className="badge badge-success">{t('catalog_status_delivered')}</span>;
    }
    if (entry.vehicle_order_no) {
        return <span className="badge badge-info">{t('catalog_status_in_transit')}</span>;
    }
    return <span className="badge badge-secondary">{t('catalog_status_at_factory')}</span>;
}

function SupplierCatalogView({
    supplierId,
    onBack,
}: {
    supplierId: number;
    onBack: () => void;
}) {
    const { t, lang } = useT();
    const [mode, setMode] = useState<'catalog' | 'matrix'>('matrix');
    const [catalog, setCatalog] = useState<SupplierCatalogResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [search, setSearch] = useState('');
    const [subjectFilter, setSubjectFilter] = useState<string>('all');
    const [brandFilter, setBrandFilter] = useState<string>('all');
    const [statusFilter, setStatusFilter] = useState<string>('all');
    const [expandedSku, setExpandedSku] = useState<Set<string>>(new Set());
    const [collapsedSubjects, setCollapsedSubjects] = useState<Set<string>>(new Set());

    const loadCatalog = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const data = await api.getSupplierCatalog(supplierId);
            setCatalog(data);
            // Default: first subject expanded, all others collapsed
            if (data.subjects.length > 1) {
                setCollapsedSubjects(new Set(data.subjects.slice(1).map(g => g.subject)));
            } else {
                setCollapsedSubjects(new Set());
            }
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : t('msg_loading_error'));
        }
        setLoading(false);
    }, [supplierId, t]);

    useEffect(() => { loadCatalog(); }, [loadCatalog]);

    // Unique brands for filter
    const allBrands = useMemo(() => {
        if (!catalog) return [];
        const brands = new Set<string>();
        catalog.subjects.forEach(g => g.items.forEach(it => { if (it.brand) brands.add(it.brand); }));
        return [...brands].sort();
    }, [catalog]);

    const filteredSubjects = useMemo((): SupplierCatalogSubjectGroup[] => {
        if (!catalog) return [];
        const q = search.trim().toLowerCase();
        return catalog.subjects
            .filter(g => subjectFilter === 'all' || g.subject === subjectFilter)
            .map(g => {
                const items = g.items.filter(it => {
                    if (statusFilter === 'not_distributed' && it.distributed_qty > 0) return false;
                    if (statusFilter === 'in_transit' && (it.distributed_qty === 0 || it.delivered_qty >= it.total_qty)) return false;
                    if (statusFilter === 'delivered' && it.delivered_qty < it.total_qty) return false;
                    if (brandFilter !== 'all' && it.brand !== brandFilter) return false;
                    if (!q) return true;
                    return (
                        it.barcode.toLowerCase().includes(q) ||
                        (it.article_seller || '').toLowerCase().includes(q) ||
                        (it.subject || '').toLowerCase().includes(q) ||
                        (it.brand || '').toLowerCase().includes(q)
                    );
                });
                return { ...g, items };
            })
            .filter(g => g.items.length > 0);
    }, [catalog, search, subjectFilter, brandFilter, statusFilter]);

    const buildExcelRows = useCallback((groups: SupplierCatalogSubjectGroup[]) => {
        if (!catalog) return [];
        const sym = currencySymbol(catalog.supplier.currency);
        const country = catalog.supplier.country;
        return groups.flatMap(g =>
            g.items.map(it => ({
                [t('catalog_subject_empty')]: translateSubject(g.subject, country, lang),
                [t('catalog_col_brand')]: it.brand || '',
                [t('catalog_col_barcode')]: it.barcode,
                [t('catalog_col_article')]: it.article_seller || '',
                [t('catalog_col_size')]: it.box_size || '',
                [t('catalog_col_per_box')]: it.pcs_per_box ?? '',
                [t('catalog_col_weight')]: it.weight_kg ? Number(it.weight_kg) : '',
                [t('catalog_col_total_qty')]: it.total_qty,
                [`${t('catalog_col_last_price')}, ${sym}`]: Number(it.last_price),
                [`${t('catalog_col_avg_price')}, ${sym}`]: Number(it.avg_price),
                [`${t('catalog_col_amount')}, ${sym}`]: Number(it.total_amount),
                [t('catalog_col_delivered')]: it.delivered_qty,
                [t('catalog_col_orders_count')]: it.orders_count,
            }))
        );
    }, [catalog, t]);

    const handleExport = useCallback(() => {
        if (!catalog) return;
        const rows = buildExcelRows(filteredSubjects);
        const filename = `${t('catalog_excel_filename')}_${catalog.supplier.name}_${new Date().toISOString().slice(0, 10)}`;
        exportToExcel(rows, filename);
    }, [catalog, filteredSubjects, buildExcelRows, t]);

    const handleExportSubject = useCallback((group: SupplierCatalogSubjectGroup) => {
        if (!catalog) return;
        const country = catalog.supplier.country;
        const rows = buildExcelRows([group]);
        const subjectName = translateSubject(group.subject, country);
        const filename = `${subjectName}_${catalog.supplier.name}_${new Date().toISOString().slice(0, 10)}`;
        exportToExcel(rows, filename);
    }, [catalog, buildExcelRows]);

    const toggleSubject = (subject: string) => {
        setCollapsedSubjects(prev => {
            const next = new Set(prev);
            if (next.has(subject)) next.delete(subject);
            else next.add(subject);
            return next;
        });
    };

    const toggleSku = (barcode: string) => {
        setExpandedSku(prev => {
            const next = new Set(prev);
            if (next.has(barcode)) next.delete(barcode);
            else next.add(barcode);
            return next;
        });
    };

    if (loading) {
        return (
            <div className="glass-card sc-loading-card">
                <div className="spinner sc-spinner-center" />
                {t('msg_loading')}
            </div>
        );
    }

    if (error) {
        return (
            <div className="glass-card sc-error-card">
                {error}
                <button className="btn btn-secondary btn-sm sc-retry-btn" onClick={loadCatalog}>
                    {t('btn_retry')}
                </button>
            </div>
        );
    }

    if (!catalog) return null;

    const sym = currencySymbol(catalog.supplier.currency);
    const flag = supplierFlag(catalog.supplier.country);

    return (
        <div>
            {/* Header */}
            <div className="sc-catalog-header">
                <div>
                    <button className="sc-catalog-back-btn" onClick={onBack}>
                        {t('catalog_back_to_list')}
                    </button>
                    <h1 className="sc-catalog-title">
                        <span className="sc-catalog-flag">{flag}</span>
                        {catalog.supplier.name}
                        <span className="sc-catalog-currency">
                            · {catalog.supplier.currency}
                        </span>
                    </h1>
                    <div className="sc-catalog-subtitle">
                        {t('catalog_subtitle')}
                    </div>
                </div>
                <div className="sc-matrix-mode-toggle">
                    <button
                        className={`sc-mode-btn ${mode === 'catalog' ? 'sc-mode-btn-active' : ''}`}
                        onClick={() => setMode('catalog')}
                    >
                        {t('matrix_mode_catalog')}
                    </button>
                    <button
                        className={`sc-mode-btn ${mode === 'matrix' ? 'sc-mode-btn-active' : ''}`}
                        onClick={() => setMode('matrix')}
                    >
                        {t('matrix_mode_shipment')}
                    </button>
                </div>
                {mode === 'catalog' && (
                    <button className="btn btn-sm" onClick={handleExport} disabled={filteredSubjects.length === 0}>
                        📥 Excel
                    </button>
                )}
            </div>

            {mode === 'matrix' ? (
                <ShipmentMatrixView supplierId={supplierId} />
            ) : (
            <>
            {/* KPI — computed from filtered data */}
            {(() => {
                const isFiltered = search || subjectFilter !== 'all' || brandFilter !== 'all' || statusFilter !== 'all';
                const fSkuCount = isFiltered ? filteredSubjects.reduce((s, g) => s + g.items.length, 0) : catalog.summary.sku_count;
                const fTotalQty = isFiltered ? filteredSubjects.reduce((s, g) => s + g.items.reduce((ss, it) => ss + it.total_qty, 0), 0) : catalog.summary.total_qty;
                const fTotalAmount = isFiltered ? filteredSubjects.reduce((s, g) => s + g.items.reduce((ss, it) => ss + Number(it.total_amount), 0), 0) : Number(catalog.summary.total_amount);
                const fOrdersCount = isFiltered ? new Set(filteredSubjects.flatMap(g => g.items.flatMap(it => it.order_history.map(h => h.order_number)))).size : catalog.summary.orders_count;
                return (
                    <div className="stats-grid">
                        <div className="glass-card sc-catalog-kpi-card">
                            <div className="sc-catalog-kpi-label">{t('catalog_kpi_orders')}</div>
                            <div className="sc-kpi-value-lg sc-color-accent">{fOrdersCount}</div>
                        </div>
                        <div className="glass-card sc-catalog-kpi-card">
                            <div className="sc-catalog-kpi-label">{t('catalog_kpi_sku')}</div>
                            <div className="sc-kpi-value-lg">{fSkuCount}</div>
                        </div>
                        <div className="glass-card sc-catalog-kpi-card">
                            <div className="sc-catalog-kpi-label">{t('catalog_kpi_qty')}</div>
                            <div className="sc-kpi-value-lg sc-color-warning">{formatNumber(fTotalQty, 0)}</div>
                            <div className="sc-catalog-kpi-sub">{t('unit_pcs')}</div>
                        </div>
                        <div className="glass-card sc-catalog-kpi-card">
                            <div className="sc-catalog-kpi-label">{t('catalog_kpi_amount')}</div>
                            <div className="sc-kpi-value-lg sc-color-success">{formatNumber(fTotalAmount, 0)}</div>
                            <div className="sc-catalog-kpi-sub">{sym}</div>
                        </div>
                    </div>
                );
            })()}

            {/* Toolbar */}
            <div className="sc-catalog-toolbar">
                <input
                    type="text"
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    placeholder={t('catalog_search_placeholder')}
                    className="sc-catalog-search"
                />
                <select
                    value={subjectFilter}
                    onChange={e => setSubjectFilter(e.target.value)}
                    className="sc-catalog-filter-select"
                >
                    <option value="all">{t('catalog_subject_all')}</option>
                    {catalog.subjects.map(g => (
                        <option key={g.subject} value={g.subject}>
                            {translateSubject(g.subject, catalog.supplier.country)}
                        </option>
                    ))}
                </select>
                {allBrands.length > 1 && (
                    <select
                        value={brandFilter}
                        onChange={e => setBrandFilter(e.target.value)}
                        className="sc-catalog-filter-select"
                    >
                        <option value="all">{t('catalog_brand_all')}</option>
                        {allBrands.map(b => (
                            <option key={b} value={b}>{b}</option>
                        ))}
                    </select>
                )}
                <select
                    value={statusFilter}
                    onChange={e => setStatusFilter(e.target.value)}
                    className="sc-catalog-filter-select"
                >
                    <option value="all">{t('catalog_status_filter_all')}</option>
                    <option value="not_distributed">{t('catalog_status_filter_not_distributed')}</option>
                    <option value="in_transit">{t('catalog_status_filter_in_transit')}</option>
                    <option value="delivered">{t('catalog_status_filter_delivered')}</option>
                </select>
            </div>

            {/* Empty state */}
            {filteredSubjects.length === 0 && (
                <div className="empty-state">
                    <div className="empty-state-icon">📦</div>
                    <div>{t('catalog_empty')}</div>
                </div>
            )}

            {/* Subject groups */}
            {filteredSubjects.map(group => {
                const collapsed = collapsedSubjects.has(group.subject);
                const groupTotalQty = group.items.reduce((s, it) => s + it.total_qty, 0);
                const groupTotalAmount = group.items.reduce((s, it) => s + Number(it.total_amount), 0);
                const groupDelivered = group.items.reduce((s, it) => s + it.delivered_qty, 0);
                const deliveredPct = groupTotalQty > 0 ? Math.round((groupDelivered / groupTotalQty) * 100) : 0;

                return (
                    <div key={group.subject} className="glass-card sc-subject-card">
                        <div
                            onClick={() => toggleSubject(group.subject)}
                            className="sc-subject-header"
                        >
                            <div className="sc-subject-header-left">
                                <span
                                    className="sc-subject-chevron"
                                    style={{ transform: collapsed ? 'rotate(-90deg)' : 'none' }}
                                >
                                    ▼
                                </span>
                                {translateSubject(group.subject, catalog.supplier.country, lang)}
                            </div>
                            <div className="sc-subject-stats">
                                <div><strong className="sc-color-text">{group.items.length}</strong> {t('catalog_sku_unit')}</div>
                                <div><strong className="sc-color-text">{formatNumber(groupTotalQty, 0)}</strong> {t('unit_pcs')}</div>
                                <div><strong className="sc-color-text">{formatNumber(groupTotalAmount, 0)} {sym}</strong></div>
                                <div>{t('catalog_delivered_label')}: <strong className="sc-color-text">{deliveredPct}%</strong></div>
                                <button
                                    className="btn btn-sm sc-subject-export-btn"
                                    onClick={e => { e.stopPropagation(); handleExportSubject(group); }}
                                    title="Excel"
                                >
                                    📥
                                </button>
                            </div>
                        </div>
                        {!collapsed && (
                            <div className="sc-subject-table-wrap">
                                <table className="sc-subject-table">
                                    <thead>
                                        <tr>
                                            <th className="sc-catalog-th">{t('catalog_col_barcode')}</th>
                                            <th className="sc-catalog-th">{t('catalog_col_article')}</th>
                                            <th className="sc-catalog-th">{t('catalog_col_size')}</th>
                                            <th className="sc-catalog-th-right">{t('catalog_col_per_box')}</th>
                                            <th className="sc-catalog-th-right">{t('catalog_col_weight')}</th>
                                            <th className="sc-catalog-th-right">{t('catalog_col_total_qty')}</th>
                                            <th className="sc-catalog-th-right">{t('catalog_col_last_price')}</th>
                                            <th className="sc-catalog-th-right">{t('catalog_col_avg_price')}</th>
                                            <th className="sc-catalog-th-right">{t('catalog_col_amount')}</th>
                                            <th className="sc-catalog-th">{t('catalog_col_delivered')}</th>
                                            <th className="sc-catalog-th-right">{t('catalog_col_orders_count')}</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {group.items.map(item => {
                                            const expanded = expandedSku.has(item.barcode);
                                            const deliveredPctItem = item.total_qty > 0
                                                ? Math.round((item.delivered_qty / item.total_qty) * 100)
                                                : 0;
                                            return (
                                                <React.Fragment key={item.barcode}>
                                                    <tr
                                                        onClick={() => toggleSku(item.barcode)}
                                                        className="sc-tr-row sc-tr-border"
                                                        style={{ background: expanded ? 'rgba(0, 113, 227, 0.04)' : undefined }}
                                                    >
                                                        <td className="sc-catalog-td-mono">{item.barcode}</td>
                                                        <td className="sc-catalog-td">{item.article_seller || '—'}</td>
                                                        <td className="sc-catalog-td">{item.box_size || '—'}</td>
                                                        <td className="sc-catalog-td-right">{item.pcs_per_box ?? '—'}</td>
                                                        <td className="sc-catalog-td-right">
                                                            {item.weight_kg ? formatNumber(Number(item.weight_kg), 2) : '—'}
                                                        </td>
                                                        <td className="sc-catalog-td-right">{formatNumber(item.total_qty, 0)}</td>
                                                        <td className="sc-catalog-td-right">
                                                            {formatNumber(Number(item.last_price), 2)} {sym}
                                                        </td>
                                                        <td className="sc-catalog-td-right">
                                                            {formatNumber(Number(item.avg_price), 2)} {sym}
                                                        </td>
                                                        <td className="sc-catalog-td-right">
                                                            {formatNumber(Number(item.total_amount), 0)} {sym}
                                                        </td>
                                                        <td className="sc-catalog-td">
                                                            <div className="sc-delivered-bar-wrap">
                                                                <div style={{
                                                                    width: `${deliveredPctItem}%`,
                                                                    height: '100%',
                                                                    background: 'var(--color-success)',
                                                                    borderRadius: 4,
                                                                }} />
                                                            </div>
                                                            <span className="sc-delivered-count">
                                                                {formatNumber(item.delivered_qty, 0)} / {formatNumber(item.total_qty, 0)}
                                                            </span>
                                                        </td>
                                                        <td className="sc-catalog-td-right">{item.orders_count}</td>
                                                    </tr>
                                                    {expanded && (
                                                        <tr>
                                                            <td colSpan={11} className="sc-sku-detail-td">
                                                                <div className="sc-sku-detail-inner">
                                                                    <div className="sc-sku-detail-label">
                                                                        {t('catalog_history_title')} ({item.article_seller || item.barcode})
                                                                    </div>
                                                                    <div className="sc-sku-detail-table-wrap">
                                                                        <table className="sc-subject-table">
                                                                            <thead>
                                                                                <tr>
                                                                                    <th className="sc-catalog-sub-th">{t('catalog_history_col_order')}</th>
                                                                                    <th className="sc-catalog-sub-th">{t('catalog_history_col_date')}</th>
                                                                                    <th className="sc-catalog-sub-th-right">{t('catalog_history_col_qty')}</th>
                                                                                    <th className="sc-catalog-sub-th-right">{t('catalog_history_col_price')}</th>
                                                                                    <th className="sc-catalog-sub-th-right">{t('catalog_history_col_amount')}</th>
                                                                                    <th className="sc-catalog-sub-th">{t('catalog_history_col_vehicle')}</th>
                                                                                    <th className="sc-catalog-sub-th">{t('catalog_history_col_status')}</th>
                                                                                </tr>
                                                                            </thead>
                                                                            <tbody>
                                                                                {item.order_history.map((h, idx) => (
                                                                                    <tr key={`${h.factory_order_id}-${idx}`} className="sc-tr-border">
                                                                                        <td className="sc-catalog-sub-td">{h.order_number}</td>
                                                                                        <td className="sc-catalog-sub-td">{formatDate(h.order_date)}</td>
                                                                                        <td className="sc-catalog-sub-td-right">{formatNumber(h.qty, 0)}</td>
                                                                                        <td className="sc-catalog-sub-td-right">{formatNumber(Number(h.price_cny), 2)} {sym}</td>
                                                                                        <td className="sc-catalog-sub-td-right">{formatNumber(Number(h.amount), 0)} {sym}</td>
                                                                                        <td className="sc-catalog-sub-td">{h.vehicle_order_no || '—'}</td>
                                                                                        <td className="sc-catalog-sub-td"><CatalogHistoryStatusBadge entry={h} /></td>
                                                                                    </tr>
                                                                                ))}
                                                                            </tbody>
                                                                        </table>
                                                                    </div>
                                                                </div>
                                                            </td>
                                                        </tr>
                                                    )}
                                                </React.Fragment>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                );
            })}
            </>
            )}
        </div>
    );
}


function SuppliersTab() {
    const { t } = useT();
    const router = useRouter();
    const searchParams = useSearchParams();
    const [suppliers, setSuppliers] = useState<Supplier[]>([]);
    const [orders, setOrders] = useState<FactoryOrder[]>([]);
    const [overview, setOverview] = useState<SupplyChainOverview | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [showForm, setShowForm] = useState(false);
    const [editSupplier, setEditSupplier] = useState<Supplier | null>(null);
    const [form, setForm] = useState<SupplierFormState>(emptySupplierForm());
    const [submitting, setSubmitting] = useState(false);

    // URL-sync for selected supplier (deeplink support)
    const supplierParam = searchParams.get('supplier');
    const selectedSupplierId = supplierParam ? Number(supplierParam) : null;

    const openSupplierCatalog = useCallback((id: number) => {
        const params = new URLSearchParams(searchParams.toString());
        params.set('tab', 'suppliers');
        params.set('supplier', String(id));
        router.replace(`?${params.toString()}`, { scroll: false });
    }, [router, searchParams]);

    const closeSupplierCatalog = useCallback(() => {
        const params = new URLSearchParams(searchParams.toString());
        params.delete('supplier');
        if (!params.get('tab')) params.set('tab', 'suppliers');
        router.replace(`?${params.toString()}`, { scroll: false });
    }, [router, searchParams]);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [suppliersData, ordersData, overviewData] = await Promise.all([
                api.getSuppliers(),
                api.getFactoryOrders(),
                api.getSupplyChainOverview(),
            ]);
            setSuppliers(suppliersData);
            setOrders(ordersData);
            setOverview(overviewData);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : t('msg_loading_error'));
        }
        setLoading(false);
    }, [t]);

    useEffect(() => { load(); }, [load]);

    // Aggregate per-supplier stats from factory orders
    // Match by supplier_id first, fallback to factory_name for orders without supplier_id
    const supplierStats = useMemo(() => {
        const nameToId: Record<string, number> = {};
        for (const s of suppliers) nameToId[s.name] = s.id;

        const resolveSupplier = (order: { supplier_id?: number; factory_name?: string }): number | null => {
            if (order.supplier_id) return order.supplier_id;
            if (order.factory_name && nameToId[order.factory_name] != null) return nameToId[order.factory_name];
            return null;
        };

        const stats: Record<number, {
            orders_count: number;
            sku_count: number;
            total_qty: number;
            total_amount: number;
        }> = {};
        for (const order of orders) {
            const sid = resolveSupplier(order);
            if (!sid) continue;
            const s = stats[sid] ?? {
                orders_count: 0,
                sku_count: 0,
                total_qty: 0,
                total_amount: 0,
            };
            s.orders_count += 1;
            for (const item of order.items || []) {
                s.total_qty += item.qty;
                s.total_amount += item.qty * Number(item.price_cny || 0);
            }
            stats[sid] = s;
        }
        // Second pass: compute unique barcodes per supplier
        const barcodeMap: Record<number, Set<string>> = {};
        for (const order of orders) {
            const sid = resolveSupplier(order);
            if (!sid) continue;
            const set = barcodeMap[sid] ?? new Set<string>();
            for (const item of order.items || []) set.add(item.barcode);
            barcodeMap[sid] = set;
        }
        for (const sid of Object.keys(barcodeMap)) {
            const idNum = Number(sid);
            if (stats[idNum]) stats[idNum].sku_count = barcodeMap[idNum].size;
        }
        return stats;
    }, [orders, suppliers]);

    const openCreate = () => {
        setEditSupplier(null);
        setForm(emptySupplierForm());
        setShowForm(true);
    };

    const openEdit = (s: Supplier) => {
        setEditSupplier(s);
        setForm({
            name: s.name,
            country: s.country,
            currency: s.currency,
            delivery_days_min: s.delivery_days_min != null ? String(s.delivery_days_min) : '',
            delivery_days_max: s.delivery_days_max != null ? String(s.delivery_days_max) : '',
            note: s.note || '',
            inn: s.inn ?? '',
            contract_number: s.contract_number ?? '',
        });
        setShowForm(true);
    };

    const handleCountryChange = (country: 'CHINA' | 'RUSSIA') => {
        setForm(f => ({ ...f, country, currency: country === 'CHINA' ? 'CNY' : 'RUB' }));
    };

    const handleSubmit = async () => {
        if (!form.name.trim()) return;
        setSubmitting(true);
        try {
            const payload: Partial<Supplier> = {
                name: form.name.trim(),
                country: form.country,
                currency: form.currency,
                delivery_days_min: form.delivery_days_min ? Number(form.delivery_days_min) : undefined,
                delivery_days_max: form.delivery_days_max ? Number(form.delivery_days_max) : undefined,
                note: form.note || undefined,
                inn: form.inn.trim() || null,
                contract_number: form.contract_number.trim() || null,
            };
            if (editSupplier) {
                await api.updateSupplier(editSupplier.id, payload);
            } else {
                await api.createSupplier(payload);
            }
            setShowForm(false);
            await load();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_error'));
        }
        setSubmitting(false);
    };

    const handleDelete = async (id: number, name: string) => {
        if (!confirm(`${t('suppliers_confirm_delete')} "${name}"?`)) return;
        try {
            await api.deleteSupplier(id);
            await load();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : t('msg_delete_error'));
        }
    };

    const chinaCount = suppliers.filter(s => s.country === 'CHINA').length;
    const russiaCount = suppliers.filter(s => s.country === 'RUSSIA').length;

    // Catalog view (deeplink supported)
    if (selectedSupplierId != null) {
        return <SupplierCatalogView supplierId={selectedSupplierId} onBack={closeSupplierCatalog} />;
    }

    if (loading) {
        return (
            <div className="glass-card sc-loading-card">
                <div className="spinner sc-spinner-center" />
                {t('msg_loading')}
            </div>
        );
    }

    return (
        <>
            {error && (
                <div className="glass-card sc-error-card">
                    {error}
                    <button className="btn btn-secondary btn-sm sc-retry-btn" onClick={load}>{t('btn_retry')}</button>
                </div>
            )}

            {/* Overview KPI (merged from former Overview tab) */}
            {overview && (
                <div className="sc-overview-kpi-grid">
                    <KpiCard
                        label={t('kpi_factory_orders')}
                        value={overview.total_factory_orders}
                        icon="📦"
                        color="var(--color-accent)"
                    />
                    <KpiCard
                        label={t('kpi_vehicles')}
                        value={overview.total_vehicles}
                        icon="🚛"
                        color="var(--color-info, #3b82f6)"
                    />
                    <KpiCard
                        label={t('kpi_items')}
                        value={overview.total_items}
                        icon="📋"
                        color="var(--color-warning)"
                    />
                    <KpiCard
                        label={t('kpi_sum_cny')}
                        value={formatNumber(overview.total_amount_cny) + ' \u00A5'}
                        icon="💰"
                        color="var(--color-success)"
                    />
                </div>
            )}

            {/* Vehicles by status (merged from former Overview tab) */}
            {overview && (
                <div className="glass-card sc-form-card">
                    <h3 className="sc-overview-status-title">{t('overview_status_title')}</h3>
                    <div className="sc-overview-status-grid">
                        {VEHICLE_STATUSES.map(status => {
                            const count = overview.vehicles_by_status[status] || 0;
                            const color = VEHICLE_STATUS_COLORS[status];
                            return (
                                <div
                                    key={status}
                                    className="sc-overview-status-item"
                                    style={{ border: `1px solid ${color}33`, background: `${color}0a` }}
                                >
                                    <div className="sc-overview-status-count" style={{ color }}>{count}</div>
                                    <div className="sc-overview-status-label">
                                        {getVehicleStatusLabels(t)[status]}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {/* Suppliers summary KPI */}
            <div className="sc-supplier-kpi-grid">
                <div className="glass-card sc-kpi-card">
                    <div className="sc-kpi-label">{t('kpi_suppliers_total')}</div>
                    <div className="sc-kpi-value-lg sc-color-accent">{suppliers.length}</div>
                </div>
                <div className="glass-card sc-kpi-card">
                    <div className="sc-kpi-label">{t('kpi_china')}</div>
                    <div className="sc-kpi-value-lg sc-color-warning">{chinaCount}</div>
                    <div className="sc-kpi-sub">CNY</div>
                </div>
                <div className="glass-card sc-kpi-card">
                    <div className="sc-kpi-label">{t('kpi_russia')}</div>
                    <div className="sc-kpi-value-lg sc-color-success">{russiaCount}</div>
                    <div className="sc-kpi-sub">RUB</div>
                </div>
            </div>

            {/* Create/Edit Form */}
            {showForm && (
                <div className="glass-card sc-form-card">
                    <div className="sc-form-header">
                        <h3 className="sc-form-title">
                            {editSupplier ? t('suppliers_edit_title') : t('suppliers_new_title')}
                        </h3>
                        <button className="btn btn-secondary btn-sm" onClick={() => setShowForm(false)}>✕</button>
                    </div>
                    <div className="sc-form-grid-2-1 sc-form-mb">
                        <div>
                            <label className="sc-form-label">{t('suppliers_form_name')}</label>
                            <input
                                value={form.name}
                                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                                placeholder={t('suppliers_form_name_placeholder')}
                                className="sc-form-input"
                                autoFocus
                            />
                        </div>
                        <div>
                            <label className="sc-form-label">{t('suppliers_form_country')}</label>
                            <select
                                value={form.country}
                                onChange={e => handleCountryChange(e.target.value as 'CHINA' | 'RUSSIA')}
                                className="sc-form-input"
                            >
                                <option value="CHINA">{t('suppliers_form_country_china')}</option>
                                <option value="RUSSIA">{t('suppliers_form_country_russia')}</option>
                            </select>
                        </div>
                        <div>
                            <label className="sc-form-label">{t('suppliers_form_currency')}</label>
                            <select
                                value={form.currency}
                                onChange={e => setForm(f => ({ ...f, currency: e.target.value as 'CNY' | 'RUB' }))}
                                className="sc-form-input"
                            >
                                <option value="CNY">CNY (¥)</option>
                                <option value="RUB">RUB (₽)</option>
                            </select>
                        </div>
                        <div>
                            <label className="sc-form-label">{t('suppliers_form_min_days')}</label>
                            <input
                                type="number"
                                value={form.delivery_days_min}
                                onChange={e => setForm(f => ({ ...f, delivery_days_min: e.target.value }))}
                                placeholder="30"
                                className="sc-form-input"
                            />
                        </div>
                        <div>
                            <label className="sc-form-label">{t('suppliers_form_max_days')}</label>
                            <input
                                type="number"
                                value={form.delivery_days_max}
                                onChange={e => setForm(f => ({ ...f, delivery_days_max: e.target.value }))}
                                placeholder="45"
                                className="sc-form-input"
                            />
                        </div>
                    </div>
                    <div className="sc-form-row" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                        <div>
                            <label className="sc-form-label">ИНН</label>
                            <input
                                value={form.inn}
                                onChange={e => setForm(f => ({ ...f, inn: e.target.value }))}
                                placeholder="10 или 12 цифр"
                                className="sc-form-input"
                            />
                        </div>
                        <div>
                            <label className="sc-form-label">Номер контракта</label>
                            <input
                                value={form.contract_number}
                                onChange={e => setForm(f => ({ ...f, contract_number: e.target.value }))}
                                placeholder="20250707"
                                className="sc-form-input"
                            />
                        </div>
                    </div>
                    <div className="sc-form-mb">
                        <label className="sc-form-label">{t('suppliers_form_note')}</label>
                        <input
                            value={form.note}
                            onChange={e => setForm(f => ({ ...f, note: e.target.value }))}
                            placeholder={t('suppliers_form_note_placeholder')}
                            className="sc-form-input"
                        />
                    </div>
                    <div className="sc-form-footer-plain">
                        <button className="btn btn-secondary btn-sm" onClick={() => setShowForm(false)}>{t('btn_cancel')}</button>
                        <button
                            className="btn btn-primary btn-sm"
                            onClick={handleSubmit}
                            disabled={!form.name.trim() || submitting}
                        >
                            {submitting ? t('msg_saving') : (editSupplier ? t('btn_save') : t('btn_create'))}
                        </button>
                    </div>
                </div>
            )}

            {/* Title + create button */}
            <div className="sc-title-row">
                <h3 className="sc-section-title">
                    {t('suppliers_title')} ({suppliers.length})
                </h3>
                <button className="btn btn-primary btn-sm" onClick={openCreate}>
                    {t('suppliers_btn_create')}
                </button>
            </div>

            {suppliers.length === 0 ? (
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-icon">📋</div>
                        <div>{t('suppliers_empty')}</div>
                    </div>
                </div>
            ) : (
                <div className="sc-supplier-grid">
                    {suppliers.map(s => {
                        const stats = supplierStats[s.id] ?? { orders_count: 0, sku_count: 0, total_qty: 0, total_amount: 0 };
                        const sym = currencySymbol(s.currency);
                        return (
                            <div
                                key={s.id}
                                className="glass-card sc-supplier-card"
                                onClick={() => openSupplierCatalog(s.id)}
                            >
                                {/* Head: flag + name + currency badge + actions */}
                                <div className="sc-supplier-head">
                                    <div className="sc-supplier-name">
                                        <span className="sc-supplier-flag">{supplierFlag(s.country)}</span>
                                        {s.name}
                                    </div>
                                    <div className="sc-supplier-head-right">
                                        <span className="sc-supplier-currency-badge">
                                            {s.currency}
                                        </span>
                                    </div>
                                </div>
                                {/* Metrics rows */}
                                <div className="sc-supplier-metric">
                                    <span>{t('catalog_kpi_orders')}</span>
                                    <strong className="sc-color-text sc-fw-600">{stats.orders_count}</strong>
                                </div>
                                <div className="sc-supplier-metric">
                                    <span>SKU</span>
                                    <strong className="sc-color-text sc-fw-600">{stats.sku_count}</strong>
                                </div>
                                <div className="sc-supplier-metric">
                                    <span>{t('col_qty') || t('catalog_kpi_qty')}</span>
                                    <strong className="sc-color-text sc-fw-600">
                                        {formatNumber(stats.total_qty, 0)} {t('unit_pcs')}
                                    </strong>
                                </div>
                                {/* Total amount */}
                                <div className="sc-supplier-amount">
                                    {formatNumber(stats.total_amount, 0)} {sym}
                                </div>
                                {/* Actions (edit/delete) — onClick stops card click */}
                                <div className="sc-supplier-actions">
                                    <button
                                        className="btn btn-secondary btn-sm"
                                        onClick={(e) => { e.stopPropagation(); openEdit(s); }}
                                    >
                                        {t('btn_edit')}
                                    </button>
                                    <button
                                        className="btn btn-danger btn-sm"
                                        onClick={(e) => { e.stopPropagation(); handleDelete(s.id, s.name); }}
                                    >
                                        {t('btn_delete')}
                                    </button>
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </>
    );
}

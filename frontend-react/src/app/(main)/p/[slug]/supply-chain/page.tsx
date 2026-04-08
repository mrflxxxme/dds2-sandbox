'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel, calcTotalBoxesWithMix } from '@/lib/utils';

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
} from '@/types/api';
import { CONTAINERS } from '@/app/(main)/p/[slug]/container-loader/lib/packer';

// ─── Status helpers ─────────────────────────────────────────────────────────

const VEHICLE_STATUS_LABELS: Record<VehicleStatus, string> = {
    FORMING: 'Формируется',
    SHIPPED: 'Отгружен',
    CUSTOMS: 'Таможня',
    DISPATCHED: 'Отправлена',
    DELIVERED: 'Принята',
};

const VEHICLE_STATUS_COLORS: Record<VehicleStatus, string> = {
    FORMING: '#6b7280',
    SHIPPED: '#3b82f6',
    CUSTOMS: '#f59e0b',
    DISPATCHED: '#8b5cf6',
    DELIVERED: '#22c55e',
};

const VEHICLE_STATUSES: VehicleStatus[] = ['FORMING', 'SHIPPED', 'CUSTOMS', 'DISPATCHED', 'DELIVERED'];

const CONTAINER_OPTIONS: { key: string; label: string; transport: string }[] = [
    { key: 'truck1', label: 'Авто 13.5м', transport: 'AUTO' },
    { key: 'truck2', label: 'Авто 13.6м', transport: 'AUTO' },
    { key: '20ft', label: '20 фут', transport: 'CONTAINER' },
    { key: '40ft', label: '40 фут', transport: 'CONTAINER' },
    { key: '40ft_hc', label: '40 фут HC', transport: 'CONTAINER' },
];

const TRANSPORT_TYPES: Record<string, { label: string; color: string }> = {
    AUTO: { label: 'АВТО', color: '#3b82f6' },
    CONTAINER: { label: 'КОНТЕЙНЕР', color: '#f59e0b' },
};

const NEXT_STATUS_MAP: Partial<Record<VehicleStatus, { status: VehicleStatus; label: string; icon: string }>> = {
    SHIPPED: { status: 'CUSTOMS', label: 'На таможню', icon: '🏛' },
    CUSTOMS: { status: 'DISPATCHED', label: 'Отправлена', icon: '🚛' },
    DISPATCHED: { status: 'DELIVERED', label: 'Принять', icon: '✓' },
};

function StatusBadge({ status }: { status: string }) {
    const s = status as VehicleStatus;
    const label = VEHICLE_STATUS_LABELS[s] || status;
    const color = VEHICLE_STATUS_COLORS[s] || '#6b7280';
    return (
        <span style={{
            display: 'inline-block',
            padding: '2px 10px',
            borderRadius: 12,
            fontSize: 12,
            fontWeight: 600,
            color: '#fff',
            background: color,
        }}>
            {label}
        </span>
    );
}

// ─── Main page ──────────────────────────────────────────────────────────────

export default function SupplyChainPage() {
    const searchParams = useSearchParams();
    const initialTab = searchParams.get('tab') || 'orders';
    const [tab, setTab] = useState(initialTab);

    return (
        <div className="animate-in">
            <PageHeader
                title="Цепочка поставок"
                subtitle="Фабричные заказы, машины, обзор"
                icon="🚚"
            />
            <TabLayout
                tabs={[
                    { key: 'orders', label: '📦 Фабричные заказы' },
                    { key: 'vehicles', label: '🚛 Машины' },
                    { key: 'overview', label: '📊 Обзор' },
                ]}
                active={tab}
                onChange={setTab}
            />
            {tab === 'orders' && <FactoryOrdersTab />}
            {tab === 'vehicles' && <VehiclesTab />}
            {tab === 'overview' && <OverviewTab />}
        </div>
    );
}

// ─── Create Factory Order Inline Form ────────────────────────────────────

const createOrderFields = [
    { key: 'order_number', label: 'Номер заказа', required: true },
    { key: 'factory_name', label: 'Поставщик / Фабрика' },
    { key: 'expected_ready_date', label: 'Примерная дата готовности', type: 'date' as const },
    { key: 'note', label: 'Заметка' },
];

function CreateFactoryOrderForm({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
    const [form, setForm] = useState<FactoryOrderCreate>({
        order_number: '',
        factory_name: undefined,
        expected_ready_date: undefined,
        note: undefined,
    });
    const [submitting, setSubmitting] = useState(false);

    const handleSubmit = async () => {
        if (!form.order_number.trim()) return;
        setSubmitting(true);
        try {
            await api.createFactoryOrder(form);
            onDone();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка');
        }
        setSubmitting(false);
    };

    const inputStyle: React.CSSProperties = {
        width: '100%', padding: '8px 12px', borderRadius: 8,
        border: '1px solid var(--color-border)', background: 'var(--color-bg)',
        color: 'var(--color-text)', fontSize: 13,
    };

    return (
        <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Новый фабричный заказ</h3>
                <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 12 }}>
                <div>
                    <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>Номер заказа *</label>
                    <input value={form.order_number} onChange={e => setForm(f => ({ ...f, order_number: e.target.value }))} placeholder="ЗАК-001" style={inputStyle} autoFocus />
                </div>
                <div>
                    <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>Поставщик / Фабрика</label>
                    <input value={form.factory_name || ''} onChange={e => setForm(f => ({ ...f, factory_name: e.target.value || undefined }))} placeholder="Название" style={inputStyle} />
                </div>
                <div>
                    <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>Дата готовности (план)</label>
                    <input type="date" value={form.expected_ready_date || ''} onChange={e => setForm(f => ({ ...f, expected_ready_date: e.target.value || undefined }))} style={inputStyle} />
                </div>
                <div>
                    <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>Заметка</label>
                    <input value={form.note || ''} onChange={e => setForm(f => ({ ...f, note: e.target.value || undefined }))} placeholder="Комментарий" style={inputStyle} />
                </div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16, paddingTop: 12, borderTop: '1px solid var(--color-border)' }}>
                <button className="btn btn-secondary btn-sm" onClick={onClose}>Отмена</button>
                <button className="btn btn-primary btn-sm" onClick={handleSubmit} disabled={!form.order_number.trim() || submitting}>
                    {submitting ? 'Создаём...' : 'Создать'}
                </button>
            </div>
        </div>
    );
}

// ─── Tab 1: Factory Orders ──────────────────────────────────────────────────

function FactoryOrdersTab() {
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
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, []);

    useEffect(() => { load(); }, [load]);

    const handleCreate = async (form: Record<string, any>) => {
        const data: FactoryOrderCreate = {
            order_number: form.order_number,
            factory_name: form.factory_name || undefined,
            expected_ready_date: form.expected_ready_date || undefined,
            note: form.note || undefined,
        };
        await api.createFactoryOrder(data);
        await load();
    };

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
        if (!confirm('Удалить заказ?')) return;
        try {
            await api.deleteFactoryOrder(id);
            await load();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка');
        }
    };

    const handleRowClick = (row: FactoryOrder) => {
        router.push(`/p/${slug}/supply-chain/factory-orders/${row.id}`);
    };

    // Unique factory names for filter
    const factoryNames = [...new Set(orders.map(o => o.factory_name).filter(Boolean))].sort();
    const filteredOrders = filterFactory ? orders.filter(o => o.factory_name === filterFactory) : orders;

    // KPI aggregates (from filtered orders)
    const kpi = filteredOrders.reduce((acc, o) => {
        for (const i of o.items || []) {
            acc.weight += (Number(i.weight_kg) || 0) * i.qty;
            acc.volume += calcVolumeM3(i.box_size || '', i.qty, i.pcs_per_box);
            acc.sumCny += (Number(i.price_cny) || 0) * i.qty;
            acc.qty += i.qty;
        }
        return acc;
    }, { weight: 0, volume: 0, sumCny: 0, qty: 0 });

    const columns: Column[] = [
        { key: 'order_number', label: 'Номер', width: '120px' },
        { key: 'factory_name', label: 'Поставщик' },
        {
            key: 'items', label: 'Позиций', align: 'center',
            render: (_v: unknown, row: FactoryOrder) => {
                const items = row.items || [];
                return items.length > 0 ? String(items.length) : '\u2014';
            },
        },
        {
            key: 'total_qty', label: 'Кол-во', align: 'right',
            render: (_v: unknown, row: FactoryOrder) => {
                const items = row.items || [];
                const total = items.reduce((s, i) => s + i.qty, 0);
                return total > 0 ? formatNumber(total, 0) : '\u2014';
            },
        },
        {
            key: 'boxes', label: 'Мест', align: 'right',
            render: (_v: unknown, row: FactoryOrder) => {
                const items = row.items || [];
                const total = calcTotalBoxesWithMix(items);
                return total > 0 ? formatNumber(total, 0) : '\u2014';
            },
        },
        {
            key: 'volume', label: 'Объём м³', align: 'right',
            render: (_v: unknown, row: FactoryOrder) => {
                const items = row.items || [];
                const total = items.reduce((s, i) => s + calcVolumeM3(i.box_size || '', i.qty, i.pcs_per_box), 0);
                return total > 0 ? formatNumber(total, 1) : '\u2014';
            },
        },
        {
            key: 'weight', label: 'Вес, кг', align: 'right',
            render: (_v: unknown, row: FactoryOrder) => {
                const items = row.items || [];
                const total = items.reduce((s, i) => s + (Number(i.weight_kg) || 0) * i.qty, 0);
                return total > 0 ? formatNumber(total, 0) : '\u2014';
            },
        },
        {
            key: 'progress', label: 'Прогресс', align: 'center',
            render: (_v: unknown, row: FactoryOrder) => {
                const items = row.items || [];
                if (items.length === 0) return '\u2014';
                const totalQty = items.reduce((s, i) => s + i.qty, 0);
                const assignedQty = items.reduce((s, i) => s + i.assigned_qty, 0);
                const pct = totalQty > 0 ? Math.round((assignedQty / totalQty) * 100) : 0;
                return (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <div style={{ flex: 1, background: 'var(--color-border)', borderRadius: 4, height: 6, minWidth: 60 }}>
                            <div style={{ width: `${pct}%`, background: pct === 100 ? '#22c55e' : '#3b82f6', borderRadius: 4, height: '100%' }} />
                        </div>
                        <span style={{ fontSize: 11, whiteSpace: 'nowrap' }}>{assignedQty}/{totalQty}</span>
                    </div>
                );
            },
        },
        { key: 'expected_ready_date', label: 'Готовность', format: 'date' },
        { key: 'created_at', label: 'Создан', format: 'date' },
        {
            key: 'actions', label: '', width: '180px',
            render: (_v: unknown, row: FactoryOrder) => (
                <div style={{ display: 'flex', gap: 4 }} onClick={e => e.stopPropagation()}>
                    <button
                        className="btn btn-secondary btn-sm"
                        style={{ fontSize: 11, padding: '2px 8px' }}
                        onClick={() => setEditOrder(row)}
                    >
                        Ред.
                    </button>
                    <button
                        className="btn btn-secondary btn-sm"
                        style={{ fontSize: 11, padding: '2px 8px', color: 'var(--color-danger)' }}
                        onClick={() => handleDelete(row.id)}
                    >
                        Уд.
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
            const totalVolume = items.reduce((s, i) => s + calcVolumeM3(i.box_size || '', i.qty, i.pcs_per_box), 0);
            const totalWeight = items.reduce((s, i) => s + (Number(i.weight_kg) || 0) * i.qty, 0);
            const totalCny = items.reduce((s, i) => s + (Number(i.price_cny) || 0) * i.qty, 0);
            return {
                'Номер': o.order_number,
                'Поставщик': o.factory_name || '',
                'Позиций': items.length,
                'Кол-во': totalQty,
                'Мест': totalBoxes || '',
                'Объём м³': totalVolume > 0 ? Number(totalVolume.toFixed(1)) : '',
                'Вес, кг': totalWeight > 0 ? Math.round(totalWeight) : '',
                'Сумма ¥': totalCny > 0 ? Number(totalCny.toFixed(2)) : '',
                'Распределено': `${assignedQty}/${totalQty}`,
                'Готовность': o.expected_ready_date || '',
                'Создан': o.created_at || '',
            };
        });
        exportToExcel(rows, 'Фабричные_заказы');
    };

    return (
        <>
            {error && (
                <div className="glass-card" style={{ padding: 16, color: 'var(--color-danger)', marginBottom: 16 }}>
                    {error}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={load}>Повторить</button>
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
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
                    <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Заказов</div>
                        <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--color-accent)' }}>{filteredOrders.length}</div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>{formatNumber(kpi.qty, 0)} шт</div>
                    </div>
                    <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Общий вес</div>
                        <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--color-warning)' }}>{formatNumber(kpi.weight, 0)}</div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>кг</div>
                    </div>
                    <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Общий объём</div>
                        <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--color-text)' }}>{formatNumber(kpi.volume, 1)}</div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>м³</div>
                    </div>
                    <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Сумма заказов</div>
                        <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--color-success)' }}>{formatNumber(kpi.sumCny, 0)}</div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>¥</div>
                    </div>
                </div>
            )}

            <DataTable
                columns={columns}
                data={filteredOrders}
                loading={loading}
                title="Фабричные заказы"
                emptyText="Нет фабричных заказов"
                emptyIcon="📦"
                exportName="factory_orders"
                onRowClick={(row) => handleRowClick(row)}
                actions={
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        {factoryNames.length > 1 && (
                            <select
                                value={filterFactory}
                                onChange={e => setFilterFactory(e.target.value)}
                                style={{ padding: '6px 12px', borderRadius: 8, border: '1px solid var(--color-border)', background: 'var(--color-bg)', color: 'var(--color-text)', fontSize: 13 }}
                            >
                                <option value="">Все поставщики</option>
                                {factoryNames.map(name => (
                                    <option key={name} value={name!}>{name}</option>
                                ))}
                            </select>
                        )}
                        <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(v => !v)}>
                            {showCreate ? '✕ Закрыть' : '+ Создать заказ'}
                        </button>
                    </div>
                }
            />

            {/* Edit Modal */}
            <FormModal
                open={!!editOrder}
                onClose={() => setEditOrder(null)}
                onSubmit={handleUpdate}
                title="Редактировать заказ"
                fields={createOrderFields}
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
            setError('Укажите хотя бы одну позицию для разбивки');
            return;
        }
        setSubmitting(true);
        setError('');
        try {
            await api.splitToVehicles(orderId, splitItems);
            onDone();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setSubmitting(false);
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card" style={{ width: 800, maxWidth: '95vw', maxHeight: '85vh', overflow: 'auto' }}
                onClick={e => e.stopPropagation()}>
                <div className="modal-title">Разбить на машины: {order?.order_number || `#${orderId}`}</div>
                {error && <div className="auth-error" style={{ marginBottom: 12 }}>{error}</div>}

                {detailItems.length === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                        Нет позиций для разбивки. Добавьте позиции в заказ.
                    </div>
                ) : (
                    <table className="data-table" style={{ fontSize: 13, marginBottom: 16 }}>
                        <thead>
                            <tr>
                                <th>Баркод</th>
                                <th>Артикул</th>
                                <th style={{ textAlign: 'right' }}>Всего</th>
                                <th style={{ textAlign: 'right' }}>Разбито</th>
                                <th style={{ textAlign: 'right' }}>Остаток</th>
                                <th style={{ width: 100 }}>Кол-во</th>
                                <th style={{ width: 160 }}>Машина (номер)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {detailItems.map(item => {
                                const remaining = item.qty - item.assigned_qty;
                                const a = assignments[item.id] || { qty: '', vehicle: '' };
                                return (
                                    <tr key={item.id} style={{ opacity: remaining === 0 ? 0.4 : 1 }}>
                                        <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{item.barcode}</td>
                                        <td>{item.article_seller || '\u2014'}</td>
                                        <td style={{ textAlign: 'right' }}>{item.qty}</td>
                                        <td style={{ textAlign: 'right' }}>{item.assigned_qty}</td>
                                        <td style={{ textAlign: 'right', fontWeight: 600, color: remaining > 0 ? 'var(--color-danger)' : 'var(--color-success)' }}>
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
                                                style={{
                                                    width: '100%', background: 'var(--color-bg)',
                                                    border: '1px solid var(--color-border)',
                                                    borderRadius: 6, padding: '4px 8px', fontSize: 13,
                                                    color: 'var(--color-text)',
                                                }}
                                            />
                                        </td>
                                        <td>
                                            <input
                                                type="text"
                                                value={a.vehicle}
                                                onChange={e => updateAssignment(item.id, 'vehicle', e.target.value)}
                                                disabled={remaining === 0}
                                                placeholder="AUTO-001"
                                                style={{
                                                    width: '100%', background: 'var(--color-bg)',
                                                    border: '1px solid var(--color-border)',
                                                    borderRadius: 6, padding: '4px 8px', fontSize: 13,
                                                    color: 'var(--color-text)',
                                                }}
                                            />
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                )}

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button className="btn btn-secondary btn-sm" onClick={onClose} disabled={submitting}>
                        Отмена
                    </button>
                    <button className="btn btn-primary btn-sm" onClick={handleSubmit} disabled={submitting || detailItems.length === 0}>
                        {submitting ? 'Сохранение...' : 'Разбить'}
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
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
        }
        setSaving(false);
    };

    const inputStyle: React.CSSProperties = {
        width: '100%', background: 'var(--color-bg)',
        border: '1px solid var(--color-border)',
        borderRadius: 6, padding: '4px 6px', fontSize: 12,
        color: 'var(--color-text)',
    };

    return (
        <div className="glass-card" style={{ marginTop: 8, padding: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <h4 style={{ margin: 0, fontSize: 14 }}>
                    Позиции заказа {order.order_number}
                </h4>
                <button className="btn btn-secondary btn-sm" onClick={onCollapse}>Свернуть</button>
            </div>

            {/* Existing items */}
            {existingItems.length > 0 && (
                <DataTable
                    columns={[
                        { key: 'barcode', label: 'Баркод' },
                        { key: 'article_seller', label: 'Артикул', render: (v: string | null) => v || '\u2014' },
                        { key: 'subject', label: 'Категория', render: (v: string | null) => v || '\u2014' },
                        { key: 'qty', label: 'Кол-во', align: 'right', render: (v: number) => formatNumber(v, 0) },
                        {
                            key: 'price_cny', label: 'Цена \u00A5', align: 'right',
                            render: (v: unknown) => formatNumber(Number(v), 2) + ' \u00A5',
                        },
                        { key: 'box_size', label: 'Коробка (см)' },
                        { key: 'pcs_per_box', label: 'Шт/кор', align: 'right' },
                        {
                            key: 'weight_kg', label: 'Вес (кг)', align: 'right',
                            render: (v: unknown) => v != null ? formatNumber(Number(v), 2) : '\u2014',
                        },
                        {
                            key: 'distribution', label: 'Распределение', align: 'center',
                            render: (_v: unknown, row: FactoryOrderItem) => {
                                const rem = row.qty - row.assigned_qty;
                                const pct = row.qty > 0 ? Math.round((row.assigned_qty / row.qty) * 100) : 0;
                                return (
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 110 }}>
                                        <span style={{ fontSize: 12, fontWeight: 600, color: pct === 100 ? 'var(--color-success)' : 'var(--color-primary)' }}>
                                            {row.assigned_qty} / {row.qty}
                                        </span>
                                        {pct === 100 ? (
                                            <span style={{ fontSize: 11, color: 'var(--color-success)' }}>{'\u2713'} все</span>
                                        ) : rem > 0 ? (
                                            <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{'\u2192'} ост. {rem}</span>
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
            <div style={{ marginTop: existingItems.length > 0 ? 16 : 0, borderTop: existingItems.length > 0 ? '1px solid var(--color-border)' : 'none', paddingTop: existingItems.length > 0 ? 16 : 0 }} onPaste={handlePaste}>
                {!showAddForm ? (
                    <div style={{ textAlign: 'center', padding: existingItems.length > 0 ? 8 : 24 }}>
                        <button className="btn btn-primary btn-sm" onClick={() => setShowAddForm(true)}>
                            + Добавить позиции
                        </button>
                        {existingItems.length === 0 && (
                            <div style={{ marginTop: 8, fontSize: 13, color: 'var(--color-text-muted)' }}>
                                или вставьте данные из Excel (Ctrl+V)
                            </div>
                        )}
                    </div>
                ) : (
                    <>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                Добавить позиции <span style={{ fontSize: 11 }}>(вставьте из Excel: Баркод, Кол-во, Цена, Коробка, Шт/кор, Вес)</span>
                            </div>
                            <div style={{ display: 'flex', gap: 8 }}>
                                <button className="btn btn-secondary btn-sm" onClick={() => { setShowAddForm(false); setRows(Array.from({ length: 5 }, emptyItemRow)); setError(''); }}>
                                    Отмена
                                </button>
                                {filledRows.length > 0 && (
                                    <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={saving}>
                                        {saving ? 'Сохранение...' : `Сохранить (${filledRows.length})`}
                                    </button>
                                )}
                            </div>
                        </div>

                        {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 8 }}>{error}</div>}

                        <table className="data-table" style={{ fontSize: 12, marginBottom: 0 }}>
                            <thead>
                                <tr>
                                    <th style={{ width: 30, textAlign: 'center' }}>#</th>
                                    <th style={{ minWidth: 130 }}>Баркод</th>
                                    <th style={{ width: 70 }}>Кол-во</th>
                                    <th style={{ width: 80 }}>Цена &#165;</th>
                                    <th style={{ width: 120 }}>Коробка (см)</th>
                                    <th style={{ width: 65 }}>Шт/кор</th>
                                    <th style={{ width: 70 }}>Вес (кг)</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((row, i) => (
                                    <tr key={i}>
                                        <td style={{ fontSize: 11, color: 'var(--color-text-muted)', textAlign: 'center' }}>{i + 1}</td>
                                        <td><input value={row.barcode} onChange={e => updateRow(i, 'barcode', e.target.value)} placeholder="2037142812010" style={{ ...inputStyle, fontFamily: 'monospace' }} autoComplete="off" name={`b${i}`} /></td>
                                        <td><input inputMode="numeric" value={row.qty} onChange={e => updateRow(i, 'qty', e.target.value)} placeholder="0" style={inputStyle} autoComplete="off" name={`q${i}`} /></td>
                                        <td><input inputMode="decimal" value={row.price_cny} onChange={e => updateRow(i, 'price_cny', e.target.value)} placeholder="0" style={inputStyle} autoComplete="off" name={`p${i}`} /></td>
                                        <td><input value={row.box_size} onChange={e => updateRow(i, 'box_size', e.target.value)} placeholder="60*50*40" style={inputStyle} autoComplete="off" name={`bs${i}`} /></td>
                                        <td><input inputMode="numeric" value={row.pcs_per_box} onChange={e => updateRow(i, 'pcs_per_box', e.target.value)} placeholder="50" style={inputStyle} autoComplete="off" name={`pp${i}`} /></td>
                                        <td><input inputMode="decimal" value={row.weight_kg} onChange={e => updateRow(i, 'weight_kg', e.target.value)} placeholder="8,5" style={inputStyle} autoComplete="off" name={`w${i}`} /></td>
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
    const t = TRANSPORT_TYPES[type || 'AUTO'] || TRANSPORT_TYPES.AUTO;
    return (
        <span style={{
            display: 'inline-block', padding: '2px 8px', borderRadius: 8,
            fontSize: 11, fontWeight: 600, color: '#fff', background: t.color,
        }}>
            {t.label}
        </span>
    );
}

function VehicleTimeline({ status }: { status?: VehicleStatus }) {
    const steps = VEHICLE_STATUSES;
    const currentIdx = status ? steps.indexOf(status) : -1;
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
            {steps.map((s, i) => {
                const done = i <= currentIdx;
                const isCurrent = i === currentIdx;
                const color = done ? VEHICLE_STATUS_COLORS[s] : 'var(--color-border)';
                return (
                    <React.Fragment key={s}>
                        {i > 0 && (
                            <div style={{
                                width: 32, height: 2,
                                background: done ? VEHICLE_STATUS_COLORS[steps[i]] : 'var(--color-border)',
                            }} />
                        )}
                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                            <div style={{
                                width: isCurrent ? 14 : 10, height: isCurrent ? 14 : 10,
                                borderRadius: '50%', background: color,
                                border: isCurrent ? `2px solid ${color}` : 'none',
                                boxShadow: isCurrent ? `0 0 6px ${color}66` : 'none',
                            }} />
                            <span style={{ fontSize: 10, color: done ? 'var(--color-text)' : 'var(--color-text-muted)', fontWeight: isCurrent ? 600 : 400 }}>
                                {VEHICLE_STATUS_LABELS[s]}
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
        <div style={{ marginTop: 8 }}>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>
                {totalVol.toFixed(1)} / {containerVol.toFixed(1)} m{'\u00B3'} ({CONTAINERS[containerKey].name})
            </div>
            <div style={{ height: 6, borderRadius: 3, background: 'var(--color-border)', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 3, transition: 'width 0.3s' }} />
            </div>
            <div style={{ fontSize: 11, color, fontWeight: 600, marginTop: 2 }}>{pct.toFixed(1)}%</div>
        </div>
    );
}

function VehicleDetail({ vehicle, onRefresh }: { vehicle: VehicleSchema; onRefresh: () => void }) {
    const [removing, setRemoving] = useState<number | null>(null);
    const [recalcing, setRecalcing] = useState(false);
    const [costPreview, setCostPreview] = useState<VehicleCostSummary | null>(vehicle.cost_summary || null);

    const handleRemoveItem = async (itemId: number) => {
        if (!confirm('Удалить позицию из машины?')) return;
        setRemoving(itemId);
        try {
            await api.removeItemFromVehicle(vehicle.order_no, itemId);
            onRefresh();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка');
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
            alert(e instanceof Error ? e.message : 'Ошибка пересчёта');
        }
        setRecalcing(false);
    };

    const containerLabel = CONTAINER_OPTIONS.find(c => c.key === vehicle.container_type)?.label || vehicle.container_type || vehicle.transport_type;
    const cs = costPreview || vehicle.cost_summary;

    return (
        <div style={{ padding: '16px 24px', background: 'var(--color-bg-secondary)', borderTop: '1px solid var(--color-border)' }}>
            {/* Timeline */}
            <div style={{ marginBottom: 16 }}>
                <VehicleTimeline status={vehicle.status as VehicleStatus} />
            </div>

            {/* Summary row */}
            <div style={{ display: 'flex', gap: 24, marginBottom: 16, fontSize: 13, color: 'var(--color-text-muted)', flexWrap: 'wrap' }}>
                <span>Контейнер: <b style={{ color: 'var(--color-text)' }}>{containerLabel}</b></span>
                {vehicle.delivery_cost_cny > 0 && (
                    <span>Перевозка: {formatNumber(vehicle.delivery_cost_cny)} {'\u00A5'} {'\u00D7'} {Number(vehicle.rate_cny).toFixed(2)} = {formatNumber(Number(vehicle.delivery_cost_cny) * Number(vehicle.rate_cny))} {'\u20BD'}</span>
                )}
                {vehicle.total_weight_kg && <span>Вес: {formatNumber(vehicle.total_weight_kg)} кг</span>}
                {vehicle.estimated_arrival_date && (
                    <span>Ожидаемое прибытие: <b style={{ color: 'var(--color-text)' }}>{formatDate(vehicle.estimated_arrival_date)}</b></span>
                )}
                {vehicle.invoice_no && <span>Инвойс: {vehicle.invoice_no}</span>}
            </div>

            {/* Cost summary */}
            {cs && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12, marginBottom: 16, padding: 12, background: 'var(--color-bg)', borderRadius: 10, border: '1px solid var(--color-border)' }}>
                    <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Товар</div>
                        <div style={{ fontSize: 14, fontWeight: 600 }}>{formatNumber(cs.total_cost_rub)} {'\u20BD'}</div>
                    </div>
                    <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Доставка</div>
                        <div style={{ fontSize: 14, fontWeight: 600 }}>{formatNumber(cs.total_delivery_rub)} {'\u20BD'}</div>
                    </div>
                    <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Пошлина</div>
                        <div style={{ fontSize: 14, fontWeight: 600 }}>{formatNumber(cs.total_duty_rub)} {'\u20BD'}</div>
                    </div>
                    <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>НДС</div>
                        <div style={{ fontSize: 14, fontWeight: 600 }}>{formatNumber(cs.total_vat_rub)} {'\u20BD'}</div>
                    </div>
                    <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Итого</div>
                        <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--color-primary)' }}>{formatNumber(cs.total_rub)} {'\u20BD'}</div>
                    </div>
                </div>
            )}

            {/* Recalc button for FORMING */}
            {vehicle.status === 'FORMING' && vehicle.items.length > 0 && (
                <div style={{ marginBottom: 12 }}>
                    <button className="btn btn-secondary btn-sm" onClick={handleRecalc} disabled={recalcing} style={{ fontSize: 12 }}>
                        {recalcing ? 'Пересчёт...' : '🔄 Пересчитать себестоимость'}
                    </button>
                </div>
            )}

            {/* Capacity bar */}
            <CapacityBar vehicle={vehicle} />

            {/* Items table */}
            {vehicle.items.length > 0 ? (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginTop: 12 }}>
                    <thead>
                        <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                            <th style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--color-text-muted)' }}>Из заказа</th>
                            <th style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--color-text-muted)' }}>Баркод</th>
                            <th style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--color-text-muted)' }}>Артикул</th>
                            <th style={{ textAlign: 'right', padding: '6px 8px', fontSize: 11, color: 'var(--color-text-muted)' }}>Кол-во</th>
                            <th style={{ textAlign: 'right', padding: '6px 8px', fontSize: 11, color: 'var(--color-text-muted)' }}>Цена {'\u00A5'}</th>
                            <th style={{ textAlign: 'right', padding: '6px 8px', fontSize: 11, color: 'var(--color-text-muted)' }}>Сумма {'\u00A5'}</th>
                            {vehicle.status === 'FORMING' && <th style={{ width: 40 }}></th>}
                        </tr>
                    </thead>
                    <tbody>
                        {vehicle.items.map(item => (
                            <tr key={item.id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                <td style={{ padding: '6px 8px', color: 'var(--color-primary)' }}>{item.factory_order_number || '\u2014'}</td>
                                <td style={{ padding: '6px 8px', fontFamily: 'monospace', fontSize: 12 }}>{item.barcode}</td>
                                <td style={{ padding: '6px 8px' }}>{item.article_seller || item.subject || '\u2014'}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right', fontWeight: 600 }}>{formatNumber(item.qty)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(item.price_cny)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(item.qty * item.price_cny)}</td>
                                {vehicle.status === 'FORMING' && (
                                    <td style={{ padding: '6px 8px', textAlign: 'center' }}>
                                        <button
                                            onClick={() => handleRemoveItem(item.id)}
                                            disabled={removing === item.id}
                                            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-danger)', fontSize: 14, opacity: removing === item.id ? 0.3 : 0.6, padding: 2 }}
                                            title="Удалить из машины"
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
                <div style={{ textAlign: 'center', padding: 20, opacity: 0.5, fontSize: 13 }}>Нет позиций в машине</div>
            )}
        </div>
    );
}

// ─── Add Items Modal ───────────────────────────────────────────────────────

function AddItemsModal({ vehicle, onClose, onDone }: { vehicle: VehicleSchema; onClose: () => void; onDone: () => void }) {
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
            alert(e instanceof Error ? e.message : 'Ошибка');
        }
        setSubmitting(false);
    };

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.5)' }} onClick={onClose} />
            <div style={{ position: 'relative', background: 'var(--color-bg)', borderRadius: 16, width: '90%', maxWidth: 720, maxHeight: '80vh', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}>
                {/* Header */}
                <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--color-border)' }}>
                    <h3 style={{ fontSize: 16, fontWeight: 600 }}>Добавить товар в {vehicle.order_no}</h3>
                    <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 4 }}>Выберите позиции из фабричных заказов</p>
                </div>

                {/* Body */}
                <div style={{ flex: 1, overflow: 'auto', padding: '16px 24px' }}>
                    {loading ? (
                        <div style={{ textAlign: 'center', padding: 32 }}><div className="spinner" style={{ margin: '0 auto' }} /></div>
                    ) : groups.length === 0 ? (
                        <div style={{ textAlign: 'center', padding: 32, opacity: 0.5 }}>Нет доступных позиций</div>
                    ) : (
                        groups.map(group => (
                            <div key={group.order_id} style={{ marginBottom: 20 }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                                    <span style={{ fontSize: 14 }}>{'\uD83D\uDCE6'}</span>
                                    <span style={{ fontWeight: 600, fontSize: 14 }}>{group.order_number}</span>
                                    {group.factory_name && <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>{'\u2014'} {group.factory_name}</span>}
                                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)', marginLeft: 'auto' }}>{group.items.length} поз.</span>
                                </div>
                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                                    <thead>
                                        <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                                            <th style={{ textAlign: 'left', padding: '4px 8px', fontSize: 11, color: 'var(--color-text-muted)' }}>Баркод</th>
                                            <th style={{ textAlign: 'left', padding: '4px 8px', fontSize: 11, color: 'var(--color-text-muted)' }}>Артикул</th>
                                            <th style={{ textAlign: 'right', padding: '4px 8px', fontSize: 11, color: 'var(--color-text-muted)' }}>Доступно</th>
                                            <th style={{ textAlign: 'right', padding: '4px 8px', fontSize: 11, color: 'var(--color-text-muted)', width: 100 }}>Добавить</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {group.items.map(item => (
                                            <tr key={item.id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                                <td style={{ padding: '6px 8px', fontFamily: 'monospace', fontSize: 12 }}>{item.barcode}</td>
                                                <td style={{ padding: '6px 8px' }}>
                                                    {item.article_seller || '\u2014'}
                                                    {item.subject && <span style={{ color: 'var(--color-text-muted)', fontSize: 11, marginLeft: 4 }}>{'\u2022'} {item.subject}</span>}
                                                </td>
                                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(item.remaining_qty)}</td>
                                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>
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
                                                        style={{
                                                            width: 80, padding: '4px 8px', borderRadius: 6, textAlign: 'right',
                                                            border: '1px solid var(--color-border)', background: 'var(--color-bg)', color: 'var(--color-text)', fontSize: 13,
                                                        }}
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
                <div style={{ padding: '16px 24px', borderTop: '1px solid var(--color-border)', display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                    <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
                    <button
                        className="btn btn-primary"
                        onClick={handleSubmit}
                        disabled={totalSelected === 0 || submitting}
                    >
                        {submitting ? 'Добавляем...' : `Добавить (${formatNumber(totalSelected)} шт)`}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ─── Create Vehicle Inline Form ──────────────────────────────────────────

function CreateVehicleForm({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
    const [form, setForm] = useState<VehicleCreate>({
        order_no: '',
        container_type: 'truck1',
        delivery_cost_cny: 0,
        delivery_cost_usd: 0,
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
        if (!form.order_no.trim()) return;
        setSubmitting(true);
        try {
            await api.createVehicle(form);
            onDone();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка');
        }
        setSubmitting(false);
    };

    const inputStyle: React.CSSProperties = {
        width: '100%', padding: '8px 12px', borderRadius: 8,
        border: '1px solid var(--color-border)', background: 'var(--color-bg)',
        color: 'var(--color-text)', fontSize: 13,
    };

    return (
        <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Новая машина</h3>
                <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div>
                        <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>Номер машины *</label>
                        <input value={form.order_no} onChange={e => setForm(f => ({ ...f, order_no: e.target.value }))} placeholder="М-001" style={inputStyle} autoFocus />
                    </div>
                    <div>
                        <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 6, display: 'block' }}>Тип контейнера</label>
                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                            {CONTAINER_OPTIONS.map(opt => {
                                const selected = form.container_type === opt.key;
                                return (
                                    <button
                                        key={opt.key}
                                        type="button"
                                        onClick={() => setForm(f => ({ ...f, container_type: opt.key }))}
                                        style={{
                                            padding: '6px 12px', borderRadius: 8, fontSize: 12, fontWeight: 500,
                                            border: selected ? '2px solid var(--color-primary)' : '1px solid var(--color-border)',
                                            background: selected ? 'var(--color-primary-bg)' : 'var(--color-bg)',
                                            color: selected ? 'var(--color-primary)' : 'var(--color-text)',
                                            cursor: 'pointer', transition: 'all 0.15s',
                                        }}
                                    >
                                        {opt.label}
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                    <div>
                        <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>Дата забора (план)</label>
                        <input type="date" value={form.ship_date || ''} onChange={e => setForm(f => ({ ...f, ship_date: e.target.value || undefined }))} style={inputStyle} />
                    </div>
                    <div>
                        <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>Инвойс</label>
                        <input value={form.invoice_no || ''} onChange={e => setForm(f => ({ ...f, invoice_no: e.target.value || undefined }))} placeholder="CC20260011" style={inputStyle} />
                    </div>
                    <div>
                        <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>Номер заказа</label>
                        <input value={form.payment_ref || ''} onChange={e => setForm(f => ({ ...f, payment_ref: e.target.value || undefined }))} placeholder="ENV-001" style={inputStyle} />
                    </div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr 1fr', gap: 12 }}>
                    <div>
                        <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>Перевозка ¥</label>
                        <input type="number" value={form.delivery_cost_cny || ''} onChange={e => setForm(f => ({ ...f, delivery_cost_cny: parseFloat(e.target.value) || 0 }))} style={inputStyle} />
                    </div>
                    <div>
                        <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>Перевозка $</label>
                        <input type="number" value={form.delivery_cost_usd || ''} onChange={e => setForm(f => ({ ...f, delivery_cost_usd: parseFloat(e.target.value) || 0 }))} style={inputStyle} />
                    </div>
                    <div>
                        <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>Курс ¥/₽</label>
                        <input type="number" step="0.01" value={form.rate_cny || ''} onChange={e => setForm(f => ({ ...f, rate_cny: parseFloat(e.target.value) || 1 }))} style={inputStyle} />
                    </div>
                    <div>
                        <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>Курс $/₽</label>
                        <input type="number" step="0.01" value={form.rate_usd || ''} onChange={e => setForm(f => ({ ...f, rate_usd: parseFloat(e.target.value) || 1 }))} style={inputStyle} />
                    </div>
                    <div>
                        <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>Курс €/₽</label>
                        <input type="number" step="0.01" value={form.rate_eur || ''} onChange={e => setForm(f => ({ ...f, rate_eur: parseFloat(e.target.value) || 1 }))} style={inputStyle} />
                    </div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    {warehouses.length > 0 && (
                        <div>
                            <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>Склад назначения</label>
                            <select value={form.target_warehouse_id || ''} onChange={e => setForm(f => ({ ...f, target_warehouse_id: e.target.value ? Number(e.target.value) : undefined }))} style={inputStyle}>
                                <option value="">-- Не выбран --</option>
                                {warehouses.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
                            </select>
                        </div>
                    )}
                    <div>
                        <label style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, display: 'block' }}>Заметка</label>
                        <input value={form.note || ''} onChange={e => setForm(f => ({ ...f, note: e.target.value }))} style={inputStyle} />
                    </div>
                </div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16, paddingTop: 12, borderTop: '1px solid var(--color-border)' }}>
                <button className="btn btn-secondary btn-sm" onClick={onClose}>Отмена</button>
                <button className="btn btn-primary btn-sm" onClick={handleSubmit} disabled={!form.order_no.trim() || submitting}>
                    {submitting ? 'Создаём...' : 'Создать'}
                </button>
            </div>
        </div>
    );
}

// ─── Vehicles Tab Main ─────────────────────────────────────────────────────

function VehiclesTab() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const [vehicles, setVehicles] = useState<VehicleSchema[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [showCreate, setShowCreate] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const data = await api.getVehicles();
            setVehicles(data);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, []);

    useEffect(() => { load(); }, [load]);

    const openVehicle = (orderNo: string) => {
        router.push(`/p/${slug}/supply-chain/vehicles/${encodeURIComponent(orderNo)}`);
    };

    const NEXT_STATUS: Record<string, { status: string; label: string; color: string }> = {
        FORMING: { status: 'SHIPPED', label: 'Отгрузить', color: 'var(--color-primary)' },
        SHIPPED: { status: 'CUSTOMS', label: 'На таможню', color: '#f59e0b' },
        CUSTOMS: { status: 'DISPATCHED', label: 'Отправлена', color: '#8b5cf6' },
        // DISPATCHED → DELIVERED: автоматически при приёмке на складе
    };

    const handleStatusChange = async (e: React.MouseEvent, orderNo: string, nextStatus: string) => {
        e.stopPropagation();
        if (!confirm(`Сменить статус машины ${orderNo}?`)) return;
        try {
            await api.updateVehicleStatus(orderNo, { status: nextStatus });
            load();
        } catch (err: unknown) {
            alert(err instanceof Error ? err.message : 'Ошибка');
        }
    };

    if (loading) {
        return (
            <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>
                <div className="spinner" style={{ margin: '0 auto 12px' }} />
                Загрузка...
            </div>
        );
    }

    return (
        <>
            {error && (
                <div className="glass-card" style={{ padding: 16, color: 'var(--color-danger)', marginBottom: 16 }}>
                    {error}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={load}>Повторить</button>
                </div>
            )}

            {/* Create vehicle inline form */}
            {showCreate && (
                <CreateVehicleForm
                    onClose={() => setShowCreate(false)}
                    onDone={() => { setShowCreate(false); load(); }}
                />
            )}

            <div className="glass-card" style={{ overflow: 'hidden' }}>
                {/* Header */}
                <div style={{ padding: '16px 20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--color-border)' }}>
                    <h3 style={{ fontSize: 15, fontWeight: 600 }}>Машины ({vehicles.length})</h3>
                    <div style={{ display: 'flex', gap: 8 }}>
                        <button className="btn btn-secondary btn-sm" onClick={load}>Обновить</button>
                        <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(v => !v)}>
                            {showCreate ? '✕ Закрыть' : '+ Новая машина'}
                        </button>
                    </div>
                </div>

                {vehicles.length === 0 ? (
                    <div style={{ padding: 40, textAlign: 'center', opacity: 0.5 }}>
                        <div style={{ fontSize: 32, marginBottom: 8 }}>{'\uD83D\uDE9B'}</div>
                        <div>Нет машин. Создайте первую!</div>
                    </div>
                ) : (
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                                <th style={{ textAlign: 'left', padding: '10px 16px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>Машина</th>
                                <th style={{ textAlign: 'left', padding: '10px 8px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>Заказ</th>
                                <th style={{ textAlign: 'left', padding: '10px 8px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>Статус</th>
                                <th style={{ textAlign: 'left', padding: '10px 8px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>Контейнер</th>
                                <th style={{ textAlign: 'right', padding: '10px 8px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>Позиции</th>
                                <th style={{ textAlign: 'right', padding: '10px 8px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>Кол-во</th>
                                <th style={{ textAlign: 'left', padding: '10px 8px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>Инвойс</th>
                                <th style={{ textAlign: 'right', padding: '10px 8px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>Вес</th>
                                <th style={{ textAlign: 'right', padding: '10px 8px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>Стоимость</th>
                                <th style={{ textAlign: 'left', padding: '10px 8px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>Отправлена</th>
                                <th style={{ textAlign: 'left', padding: '10px 8px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500 }}>Прибытие</th>
                                <th style={{ textAlign: 'right', padding: '10px 16px', fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 500, width: 180 }}>Действия</th>
                            </tr>
                        </thead>
                        <tbody>
                            {vehicles.map(v => {
                                const status = v.status as VehicleStatus;
                                return (
                                    <tr
                                        key={v.order_no}
                                        onClick={() => openVehicle(v.order_no)}
                                        style={{ borderBottom: '1px solid var(--color-border)', cursor: 'pointer', transition: 'background 0.15s' }}
                                        onMouseEnter={e => (e.currentTarget.style.background = 'var(--color-bg-secondary)')}
                                        onMouseLeave={e => (e.currentTarget.style.background = '')}
                                    >
                                        <td style={{ padding: '10px 16px', fontWeight: 600 }}>{v.order_no}</td>
                                        <td style={{ padding: '10px 8px', fontSize: 12, color: v.payment_ref ? 'var(--color-text)' : 'var(--color-text-muted)' }}>{v.payment_ref || '\u2014'}</td>
                                        <td style={{ padding: '10px 8px' }}><StatusBadge status={status} /></td>
                                        <td style={{ padding: '10px 8px' }}>
                                            <span style={{ fontSize: 12 }}>{CONTAINER_OPTIONS.find(c => c.key === v.container_type)?.label || v.transport_type || 'AUTO'}</span>
                                        </td>
                                        <td style={{ padding: '10px 8px', textAlign: 'right' }}>{v.items_count}</td>
                                        <td style={{ padding: '10px 8px', textAlign: 'right' }}>{formatNumber(v.total_qty, 0)}</td>
                                        <td style={{ padding: '10px 8px', fontSize: 12, color: v.invoice_no ? 'var(--color-text)' : 'var(--color-text-muted)' }}>{v.invoice_no || '\u2014'}</td>
                                        <td style={{ padding: '10px 8px', textAlign: 'right' }}>{v.total_weight_kg ? formatNumber(Number(v.total_weight_kg), 0) + ' кг' : '\u2014'}</td>
                                        <td style={{ padding: '10px 8px', textAlign: 'right' }}>{Number(v.total_cny) > 0 ? formatNumber(Number(v.total_cny), 0) + ' \u00A5' : '\u2014'}</td>
                                        <td style={{ padding: '10px 8px' }}>{v.ship_date ? formatDate(v.ship_date) : '\u2014'}</td>
                                        <td style={{ padding: '10px 8px' }}>{v.estimated_arrival_date ? formatDate(v.estimated_arrival_date) : '\u2014'}</td>
                                        <td style={{ padding: '10px 16px', textAlign: 'right' }} onClick={e => e.stopPropagation()}>
                                            <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end', alignItems: 'center' }}>
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
                                                <span style={{ fontSize: 12, color: 'var(--color-primary)', cursor: 'pointer' }} onClick={() => openVehicle(v.order_no)}>Открыть →</span>
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

// ─── Tab 3: Overview ────────────────────────────────────────────────────────

function OverviewTab() {
    const [overview, setOverview] = useState<SupplyChainOverview | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const data = await api.getSupplyChainOverview();
            setOverview(data);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, []);

    useEffect(() => { load(); }, [load]);

    if (loading) {
        return (
            <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>
                <div className="spinner" style={{ margin: '0 auto 12px' }} />
                Загрузка...
            </div>
        );
    }

    if (error) {
        return (
            <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>
                {error}
                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={load}>Повторить</button>
            </div>
        );
    }

    if (!overview) {
        return (
            <div className="glass-card" style={{ padding: 32, textAlign: 'center', opacity: 0.6 }}>
                Нет данных
            </div>
        );
    }

    const statusEntries = Object.entries(overview.vehicles_by_status);

    return (
        <div>
            {/* KPI Cards */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16, marginBottom: 24 }}>
                <KpiCard
                    label="Фабричные заказы"
                    value={overview.total_factory_orders}
                    icon="📦"
                    color="var(--color-accent)"
                />
                <KpiCard
                    label="Машины"
                    value={overview.total_vehicles}
                    icon="🚛"
                    color="var(--color-info, #3b82f6)"
                />
                <KpiCard
                    label="Позиции"
                    value={overview.total_items}
                    icon="📋"
                    color="var(--color-warning)"
                />
                <KpiCard
                    label="Сумма (CNY)"
                    value={formatNumber(overview.total_amount_cny) + ' \u00A5'}
                    icon="💰"
                    color="var(--color-success)"
                />
            </div>

            {/* Status breakdown */}
            <div className="glass-card" style={{ padding: 20 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>Машины по статусам</h3>
                {statusEntries.length === 0 ? (
                    <div style={{ opacity: 0.5, textAlign: 'center', padding: 20 }}>Нет данных по статусам</div>
                ) : (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
                        {VEHICLE_STATUSES.map(status => {
                            const count = overview.vehicles_by_status[status] || 0;
                            const color = VEHICLE_STATUS_COLORS[status];
                            return (
                                <div key={status} style={{
                                    padding: 16,
                                    borderRadius: 12,
                                    border: `1px solid ${color}33`,
                                    background: `${color}0a`,
                                    textAlign: 'center',
                                }}>
                                    <div style={{ fontSize: 28, fontWeight: 700, color }}>{count}</div>
                                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 4 }}>
                                        {VEHICLE_STATUS_LABELS[status]}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>
        </div>
    );
}

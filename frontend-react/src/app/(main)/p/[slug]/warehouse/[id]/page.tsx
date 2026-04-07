'use client';
import React, { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type {
    Warehouse, InboundReceipt, OutboundShipment,
    WarehouseStockRow, StockMovement, DeliveryTimesResponse,
} from '@/types/api';
import type { Column } from '@/components/DataTable';

/* ─── Main page ────────────────────────────────────────────────────────────── */

export default function WarehouseDetailPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const warehouseId = Number(params.id);
    const [tab, setTab] = useState<'all' | 'receipts' | 'shipments' | 'stock' | 'delivery'>('receipts');
    const [warehouse, setWarehouse] = useState<Warehouse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Counts for tab badges
    const [receiptCount, setReceiptCount] = useState(0);
    const [shipmentCount, setShipmentCount] = useState(0);

    // No modals — all create/detail views are separate pages

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const whs = await api.getWarehouses();
            const wh = whs.find(w => w.id === warehouseId);
            setWarehouse(wh || null);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    }, [warehouseId]);

    useEffect(() => { load(); }, [load]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;
    if (!warehouse) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Склад не найден</div>;

    const isFulfillment = warehouse.warehouse_type === 'FULFILLMENT';

    const tabs = [
        { key: 'receipts' as const, label: 'Приёмки', count: receiptCount },
        ...(isFulfillment ? [{ key: 'shipments' as const, label: 'Отгрузки', count: shipmentCount }] : []),
        { key: 'stock' as const, label: 'Остатки и статистика' },
        { key: 'delivery' as const, label: 'Время доставки' },
        { key: 'all' as const, label: 'История движений' },
    ];

    return (
        <div className="animate-in">
            {/* Header with action buttons */}
            <div className="page-header">
                <div>
                    <h1 className="page-title">{warehouse.name}</h1>
                    <p className="page-subtitle">
                        {isFulfillment ? 'Фулфилмент' : 'Внешний склад'}
                        {warehouse.country ? ` — ${warehouse.country}` : ''}
                    </p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-primary" onClick={() => router.push(`/p/${slug}/warehouse/${warehouseId}/receipt/new`)}>
                        + Приёмка
                    </button>
                    <button className="btn btn-secondary" onClick={() => router.push(`/p/${slug}/warehouse/${warehouseId}/transfer/new`)}>
                        + Перемещение
                    </button>
                </div>
            </div>

            {/* Expected vehicles */}
            <ExpectedVehicles warehouseId={warehouseId} slug={slug} />

            {/* Tabs with counts */}
            <div style={{ display: 'flex', gap: 4, marginBottom: 20, borderBottom: '1px solid var(--color-border)', paddingBottom: 0 }}>
                {tabs.map(t => (
                    <button
                        key={t.key}
                        onClick={() => setTab(t.key)}
                        style={{
                            padding: '10px 16px',
                            fontSize: 14,
                            fontWeight: tab === t.key ? 600 : 400,
                            color: tab === t.key ? 'var(--color-primary)' : 'var(--color-text-muted)',
                            background: 'none',
                            border: 'none',
                            borderBottom: tab === t.key ? '2px solid var(--color-primary)' : '2px solid transparent',
                            cursor: 'pointer',
                            marginBottom: -1,
                        }}
                    >
                        {t.label}
                        {'count' in t && t.count !== undefined && (
                            <span style={{
                                marginLeft: 6,
                                fontSize: 12,
                                background: tab === t.key ? 'var(--color-primary)' : 'var(--color-border)',
                                color: tab === t.key ? '#fff' : 'var(--color-text-muted)',
                                borderRadius: 10,
                                padding: '1px 7px',
                            }}>
                                {t.count}
                            </span>
                        )}
                    </button>
                ))}
            </div>

            {tab === 'all' && <AllTab warehouseId={warehouseId} />}
            {tab === 'receipts' && (
                <ReceiptsTab
                    warehouseId={warehouseId}
                    onCountChange={setReceiptCount}
                />
            )}
            {tab === 'shipments' && (
                <ShipmentsTab
                    warehouseId={warehouseId}
                    warehouseType={warehouse.warehouse_type}
                    onCountChange={setShipmentCount}
                />
            )}
            {tab === 'stock' && <StockTab warehouseId={warehouseId} />}
            {tab === 'delivery' && <DeliveryTab warehouseId={warehouseId} />}
        </div>
    );
}

/* ─── Expected Vehicles (Ожидаемые поставки) ──────────────────────────── */

const STATUS_LABELS_VEHICLE: Record<string, string> = {
    SHIPPED: 'Отгружен', CUSTOMS: 'Таможня', DISPATCHED: 'Отправлена',
};
const STATUS_COLORS_VEHICLE: Record<string, string> = {
    SHIPPED: '#3b82f6', CUSTOMS: '#f59e0b', DISPATCHED: '#8b5cf6',
};

const NEXT_VEHICLE_ACTION: Record<string, { status: string; label: string; color: string }> = {
    SHIPPED: { status: 'CUSTOMS', label: 'На таможню', color: '#f59e0b' },
    CUSTOMS: { status: 'DISPATCHED', label: 'Отправлена', color: '#8b5cf6' },
    // DISPATCHED: приёмка через InboundReceipt (таб "Приёмки")
};

function ExpectedVehicles({ warehouseId, slug }: { warehouseId: number; slug: string }) {
    const router = useRouter();
    const [vehicles, setVehicles] = useState<any[]>([]);

    const loadVehicles = useCallback(() => {
        api.getExpectedVehicles(warehouseId).then(setVehicles).catch(() => {});
    }, [warehouseId]);

    useEffect(() => { loadVehicles(); }, [loadVehicles]);

    const handleAction = async (e: React.MouseEvent, orderNo: string, nextStatus: string) => {
        e.stopPropagation();
        try {
            await api.updateVehicleStatus(orderNo, { status: nextStatus });
            loadVehicles();
        } catch (err: unknown) {
            alert(err instanceof Error ? err.message : 'Ошибка');
        }
    };

    if (vehicles.length === 0) return null;

    return (
        <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                <span>🚛</span> Ожидаемые поставки ({vehicles.length})
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
                {vehicles.map(v => {
                    const action = NEXT_VEHICLE_ACTION[v.status];
                    return (
                        <div
                            key={v.order_no}
                            onClick={() => router.push(`/p/${slug}/supply-chain/vehicles/${encodeURIComponent(v.order_no)}`)}
                            style={{
                                padding: '12px 14px', borderRadius: 12,
                                border: '1px solid var(--color-border)',
                                cursor: 'pointer', transition: 'all 0.15s',
                            }}
                            onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--color-primary)'; e.currentTarget.style.transform = 'translateY(-1px)'; }}
                            onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--color-border)'; e.currentTarget.style.transform = ''; }}
                        >
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                                <span style={{ fontWeight: 600, fontSize: 13 }}>{v.order_no}</span>
                                <span style={{
                                    padding: '2px 8px', borderRadius: 10, fontSize: 10, fontWeight: 600,
                                    color: '#fff', background: STATUS_COLORS_VEHICLE[v.status] || '#6b7280',
                                }}>
                                    {STATUS_LABELS_VEHICLE[v.status] || v.status}
                                </span>
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                <div style={{ display: 'flex', gap: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>
                                    <span>{v.items_count} поз. / {formatNumber(v.total_qty, 0)} шт</span>
                                    {v.estimated_arrival_date && (
                                        <span style={{ color: 'var(--color-text)' }}>📅 {formatDate(v.estimated_arrival_date)}</span>
                                    )}
                                </div>
                                {action ? (
                                    <button
                                        onClick={e => handleAction(e, v.order_no, action.status)}
                                        style={{
                                            padding: '3px 10px', borderRadius: 8, fontSize: 11, fontWeight: 600,
                                            border: `1px solid ${action.color}`, background: action.color,
                                            color: '#fff', cursor: 'pointer', whiteSpace: 'nowrap',
                                        }}
                                    >
                                        {action.label}
                                    </button>
                                ) : v.status === 'DISPATCHED' ? (
                                    <span style={{ fontSize: 11, color: 'var(--color-success)', fontWeight: 600 }}>→ Приёмки</span>
                                ) : null}
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

/* ─── Tab: Все (движения) ───────────────────────────────────────────────── */

function AllTab({ warehouseId }: { warehouseId: number }) {
    const [movements, setMovements] = useState<StockMovement[]>([]);
    const [loading, setLoading] = useState(true);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const r = await api.getStockMovements(warehouseId);
            setMovements(r);
        } catch { /* ignore */ }
        setLoading(false);
    }, [warehouseId]);

    useEffect(() => { load(); }, [load]);

    const movementTypeLabel: Record<string, string> = {
        INBOUND: 'Приёмка',
        INBOUND_EDIT: 'Корректировка приёмки',
        INBOUND_CANCEL: 'Отмена приёмки',
        OUTBOUND: 'Отгрузка',
        OUTBOUND_CANCEL: 'Отмена отгрузки',
        TRANSFER_IN: 'Перемещение (вход)',
        TRANSFER_OUT: 'Перемещение (выход)',
        ADJUSTMENT: 'Корректировка',
    };

    const cols: Column[] = [
        { key: 'created_at', label: 'Дата', format: 'date' },
        {
            key: 'movement_type', label: 'Тип',
            render: (v: string) => movementTypeLabel[v] || v,
        },
        { key: 'barcode', label: 'ШК' },
        {
            key: 'quantity', label: 'Кол-во', align: 'right',
            render: (v: number) => (
                <span style={{ color: v > 0 ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>
                    {v > 0 ? '+' : ''}{v}
                </span>
            ),
        },
        { key: 'reference_type', label: 'Документ' },
        { key: 'comment', label: 'Комментарий' },
    ];

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;

    return (
        <TanStackDataTable
            columns={cols}
            data={movements}
            emptyText="Нет движений"
            emptyIcon="📋"
            exportName="movements"
        />
    );
}

/* ─── Tab: Приёмки ──────────────────────────────────────────────────────── */

function ReceiptsTab({ warehouseId, onCountChange }: {
    warehouseId: number;
    onCountChange: (n: number) => void;
}) {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const [receipts, setReceipts] = useState<InboundReceipt[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const r = await api.getReceipts(warehouseId);
            setReceipts(r);
            onCountChange(r.length);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    }, [warehouseId, onCountChange]);

    useEffect(() => { load(); }, [load]);

    const statusBadge = (s: string) => {
        const map: Record<string, { label: string; bg: string; color: string }> = {
            DRAFT: { label: 'Черновик', bg: 'rgba(0,0,0,0.06)', color: 'var(--color-text-muted)' },
            EXPECTED: { label: 'Ожидается', bg: 'rgba(245,158,11,0.1)', color: '#b45309' },
            ACCEPTED: { label: 'Принята', bg: 'rgba(34,197,94,0.1)', color: '#16a34a' },
            CANCELLED: { label: 'Отменена', bg: 'rgba(239,68,68,0.1)', color: '#dc2626' },
        };
        const { label, bg, color } = map[s] || { label: s, bg: 'transparent', color: 'inherit' };
        return <span style={{ color, background: bg, padding: '2px 8px', borderRadius: 12, fontSize: 12, fontWeight: 600 }}>{label}</span>;
    };

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;

    const cols: Column[] = [
        { key: 'number', label: '№' },
        { key: 'status', label: 'Статус', render: (v: string) => statusBadge(v) },
        {
            key: 'items', label: 'Позиции',
            render: (_: unknown, row: InboundReceipt) => {
                const expected = row.items.reduce((s: number, it: any) => s + (it.expected_qty || 0), 0);
                const actual = row.items.reduce((s: number, it: any) => s + (it.actual_qty || 0), 0);
                const accepted = row.status === 'ACCEPTED';
                return (
                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        {row.items.length} поз., {formatNumber(expected, 0)} ожид.
                        {accepted && <span style={{ color: actual < expected ? '#b45309' : 'var(--color-success)', fontWeight: 600 }}> / {formatNumber(actual, 0)} факт</span>}
                    </span>
                );
            },
        },
        { key: 'planned_date', label: 'Плановая дата', format: 'date' },
        { key: 'comment', label: 'Комментарий' },
        { key: 'created_at', label: 'Создана', format: 'date' },
    ];

    return (
        <>
            {error && <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>}

            <TanStackDataTable
                columns={cols}
                data={receipts}
                emptyText="Нет приёмок"
                emptyIcon="📥"
                onRowClick={(row) => router.push(`/p/${slug}/warehouse/${warehouseId}/receipt/${row.id}`)}
            />
        </>
    );
}

/* ─── Tab: Отгрузки ─────────────────────────────────────────────────────── */

function ShipmentsTab({ warehouseId, warehouseType, onCountChange }: {
    warehouseId: number;
    warehouseType: string;
    onCountChange: (n: number) => void;
}) {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const [shipments, setShipments] = useState<OutboundShipment[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const r = await api.getShipments(warehouseId);
            setShipments(r);
            onCountChange(r.length);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    }, [warehouseId, onCountChange]);

    useEffect(() => { load(); }, [load]);

    const statusBadge = (s: string) => {
        const map: Record<string, { label: string; bg: string; color: string }> = {
            DRAFT: { label: 'Черновик', bg: 'rgba(0,0,0,0.06)', color: 'var(--color-text-muted)' },
            SHIPPED: { label: 'Отгружена', bg: 'rgba(245,158,11,0.1)', color: '#b45309' },
            DELIVERED: { label: 'Доставлена', bg: 'rgba(34,197,94,0.1)', color: '#16a34a' },
            CANCELLED: { label: 'Отменена', bg: 'rgba(239,68,68,0.1)', color: '#dc2626' },
        };
        const { label, bg, color } = map[s] || { label: s, bg: 'transparent', color: 'inherit' };
        return <span style={{ color, background: bg, padding: '2px 8px', borderRadius: 12, fontSize: 12, fontWeight: 600 }}>{label}</span>;
    };

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (warehouseType !== 'FULFILLMENT') {
        return <div className="glass-card" style={{ padding: 32, textAlign: 'center', opacity: 0.6 }}>Отгрузки доступны только для Фулфилмент</div>;
    }

    const cols: Column[] = [
        { key: 'number', label: '№' },
        { key: 'status', label: 'Статус', render: (v: string) => statusBadge(v) },
        {
            key: 'items', label: 'Позиции',
            render: (_: unknown, row: OutboundShipment) => {
                const qty = row.items.reduce((s: number, it: { quantity: number }) => s + it.quantity, 0);
                return <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>{row.items.length} поз., {formatNumber(qty)} шт.</span>;
            },
        },
        { key: 'destination', label: 'Назначение' },
        { key: 'shipped_date', label: 'Дата отгрузки', format: 'date' },
        { key: 'created_at', label: 'Создана', format: 'date' },
    ];

    return (
        <>
            {error && <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>}

            <TanStackDataTable columns={cols} data={shipments} emptyText="Нет отгрузок" emptyIcon="📤" onRowClick={(row) => router.push(`/p/${slug}/warehouse/${warehouseId}/shipment/${row.id}`)} />
        </>
    );
}

/* ─── Tab: Остатки и статистика ─────────────────────────────────────────── */

function StockTab({ warehouseId }: { warehouseId: number }) {
    const [stock, setStock] = useState<WarehouseStockRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const [showAdj, setShowAdj] = useState(false);
    const [adjBarcode, setAdjBarcode] = useState('');
    const [adjDelta, setAdjDelta] = useState('');
    const [adjReason, setAdjReason] = useState('');
    const [adjSaving, setAdjSaving] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try { const r = await api.getWarehouseStock(warehouseId); setStock(r); }
        catch (e: unknown) { setError(e instanceof Error ? e.message : 'Ошибка'); }
        setLoading(false);
    }, [warehouseId]);

    useEffect(() => { load(); }, [load]);

    const handleAdjustment = async () => {
        if (!adjBarcode.trim() || !adjDelta || !adjReason.trim()) return;
        setAdjSaving(true); setError('');
        try {
            await api.createAdjustment(warehouseId, { barcode: adjBarcode.trim(), delta: parseInt(adjDelta), reason: adjReason.trim() });
            setShowAdj(false); setAdjBarcode(''); setAdjDelta(''); setAdjReason(''); await load();
        } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Ошибка'); }
        setAdjSaving(false);
    };

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;

    const totalQty = stock.reduce((s, r) => s + r.quantity, 0);
    const totalCost = stock.reduce((s, r) => s + (r.cost_price || 0) * r.quantity, 0);
    const totalReserved = stock.reduce((s, r) => s + (r.reserved || 0), 0);
    const totalAvailable = stock.reduce((s, r) => s + (r.available || 0), 0);

    const cols: Column[] = [
        { key: 'barcode', label: 'ШК' },
        { key: 'quantity', label: 'Кол-во', align: 'right', format: 'number' },
        { key: 'reserved', label: 'Зарезерв.', align: 'right', format: 'number' },
        { key: 'available', label: 'Доступно', align: 'right', format: 'number' },
        { key: 'in_transit', label: 'В пути', align: 'right', format: 'number' },
        { key: 'cost_price', label: 'Себестоимость', align: 'right', render: (v: number | null) => v ? formatNumber(v) + ' \u20BD' : '—' },
        { key: 'updated_at', label: 'Обновлено', format: 'date' },
    ];

    return (
        <>
            {error && <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>}

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12, marginBottom: 20 }}>
                <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Позиций</div>
                    <div style={{ fontSize: 24, fontWeight: 700 }}>{stock.length}</div>
                </div>
                <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Всего штук</div>
                    <div style={{ fontSize: 24, fontWeight: 700 }}>{formatNumber(totalQty)}</div>
                </div>
                <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Зарезервировано</div>
                    <div style={{ fontSize: 24, fontWeight: 700, color: totalReserved > 0 ? 'var(--color-warning)' : undefined }}>{formatNumber(totalReserved)}</div>
                </div>
                <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Доступно</div>
                    <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--color-success)' }}>{formatNumber(totalAvailable)}</div>
                </div>
                <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Себестоимость</div>
                    <div style={{ fontSize: 24, fontWeight: 700 }}>{formatNumber(totalCost)} {'\u20BD'}</div>
                </div>
            </div>

            <div style={{ marginBottom: 12 }}>
                <button className="btn btn-secondary btn-sm" onClick={() => setShowAdj(true)}>Корректировка</button>
            </div>

            <TanStackDataTable columns={cols} data={stock} emptyText="Нет остатков" emptyIcon="📦" exportName="warehouse_stock" />

            {showAdj && (
                <div className="modal-overlay" onClick={() => setShowAdj(false)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 400 }}>
                        <h2 className="modal-title">Корректировка остатков</h2>
                        <div className="form-group">
                            <label className="form-label">Баркод *</label>
                            <input className="form-input" value={adjBarcode} onChange={e => setAdjBarcode(e.target.value)} autoFocus />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Дельта (+ излишек, - недостача) *</label>
                            <input className="form-input" type="number" value={adjDelta} onChange={e => setAdjDelta(e.target.value)} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Причина *</label>
                            <textarea className="form-input" value={adjReason} onChange={e => setAdjReason(e.target.value)} rows={2} />
                        </div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                            <button className="btn btn-secondary" onClick={() => setShowAdj(false)}>Отмена</button>
                            <button className="btn btn-primary" onClick={handleAdjustment} disabled={adjSaving}>
                                {adjSaving ? 'Сохранение...' : 'Применить'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </>
    );
}

/* ─── Tab: Время доставки ──────────────────────────────────────────────── */

function DeliveryTab({ warehouseId }: { warehouseId: number }) {
    const [data, setData] = useState<DeliveryTimesResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');

    // Editable state
    const [assemblyDays, setAssemblyDays] = useState(0);
    const [wbAcceptanceDays, setWbAcceptanceDays] = useState(2);
    const [deliveryMap, setDeliveryMap] = useState<Record<string, number>>({});

    // Inline editing for assembly_days / wb_acceptance_days
    const [editingAssembly, setEditingAssembly] = useState(false);
    const [editingAcceptance, setEditingAcceptance] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const r = await api.getDeliveryTimes(warehouseId);
            setData(r);
            setAssemblyDays(r.assembly_days);
            setWbAcceptanceDays(r.wb_acceptance_days);
            const map: Record<string, number> = {};
            r.wb_warehouses.forEach(w => { map[w.wb_warehouse_name] = w.delivery_days; });
            setDeliveryMap(map);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    }, [warehouseId]);

    useEffect(() => { load(); }, [load]);

    const handleSave = async () => {
        setSaving(true);
        setError('');
        setSuccess('');
        try {
            const items = Object.entries(deliveryMap).map(([name, days]) => ({
                wb_warehouse_name: name,
                delivery_days: days,
            }));
            const r = await api.updateDeliveryTimes(warehouseId, {
                assembly_days: assemblyDays,
                wb_acceptance_days: wbAcceptanceDays,
                items,
            });
            setData(r);
            setSuccess('Сохранено');
            setTimeout(() => setSuccess(''), 3000);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setSaving(false);
    };

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;

    if (!data || data.wb_warehouses.length === 0) {
        return (
            <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>
                <div style={{ fontSize: 40, marginBottom: 12 }}>📦</div>
                <div style={{ fontSize: 15, color: 'var(--color-text-muted)' }}>
                    Сначала синхронизируйте остатки WB, чтобы увидеть список складов
                </div>
            </div>
        );
    }

    const totalDays = (wbName: string) => assemblyDays + (deliveryMap[wbName] ?? 3) + wbAcceptanceDays;

    return (
        <div className="glass-card" style={{ padding: 24 }}>
            <p style={{ fontSize: 14, color: 'var(--color-text-muted)', marginBottom: 20 }}>
                Укажите сколько дней занимает доставка с этого склада до каждого склада WB
                (без учёта сборки)
            </p>

            {error && <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>}
            {success && <div style={{ color: 'var(--color-success)', marginBottom: 12 }}>{success}</div>}

            {/* Editable cards */}
            <div style={{ display: 'flex', gap: 12, marginBottom: 24 }}>
                <div
                    style={{
                        padding: '10px 16px',
                        border: '1px solid var(--color-border)',
                        borderRadius: 10,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        cursor: 'pointer',
                    }}
                    onClick={() => setEditingAssembly(true)}
                >
                    <span style={{ fontSize: 14 }}>Время сборки:</span>
                    {editingAssembly ? (
                        <input
                            type="number"
                            min={0}
                            value={assemblyDays}
                            onChange={e => setAssemblyDays(Math.max(0, parseInt(e.target.value) || 0))}
                            onBlur={() => setEditingAssembly(false)}
                            onKeyDown={e => e.key === 'Enter' && setEditingAssembly(false)}
                            autoFocus
                            style={{ width: 50, padding: '2px 6px', fontSize: 14, border: '1px solid var(--color-primary)', borderRadius: 6, textAlign: 'center' }}
                        />
                    ) : (
                        <span style={{ fontWeight: 600 }}>{assemblyDays} дн.</span>
                    )}
                    {!editingAssembly && <span style={{ fontSize: 14, opacity: 0.5 }}>✏️</span>}
                </div>

                <div
                    style={{
                        padding: '10px 16px',
                        border: '1px solid var(--color-border)',
                        borderRadius: 10,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        cursor: 'pointer',
                    }}
                    onClick={() => setEditingAcceptance(true)}
                >
                    <span style={{ fontSize: 14 }}>Приёмка WB:</span>
                    {editingAcceptance ? (
                        <input
                            type="number"
                            min={0}
                            value={wbAcceptanceDays}
                            onChange={e => setWbAcceptanceDays(Math.max(0, parseInt(e.target.value) || 0))}
                            onBlur={() => setEditingAcceptance(false)}
                            onKeyDown={e => e.key === 'Enter' && setEditingAcceptance(false)}
                            autoFocus
                            style={{ width: 50, padding: '2px 6px', fontSize: 14, border: '1px solid var(--color-primary)', borderRadius: 6, textAlign: 'center' }}
                        />
                    ) : (
                        <span style={{ fontWeight: 600 }}>{wbAcceptanceDays} дн.</span>
                    )}
                    {!editingAcceptance && <span style={{ fontSize: 14, opacity: 0.5 }}>✏️</span>}
                </div>
            </div>

            {/* Table */}
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                <thead>
                    <tr style={{ borderBottom: '2px solid var(--color-border)' }}>
                        <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, textTransform: 'uppercase', fontSize: 11, color: 'var(--color-text-muted)', letterSpacing: '0.5px' }}>Склад WB</th>
                        <th style={{ textAlign: 'center', padding: '8px 12px', fontWeight: 600, textTransform: 'uppercase', fontSize: 11, color: 'var(--color-text-muted)', letterSpacing: '0.5px' }}>Дней доставки</th>
                        <th style={{ textAlign: 'center', padding: '8px 12px', fontWeight: 600, textTransform: 'uppercase', fontSize: 11, color: 'var(--color-text-muted)', letterSpacing: '0.5px' }}>Итого до WB</th>
                    </tr>
                </thead>
                <tbody>
                    {data.wb_warehouses.map((wh, idx) => {
                        const days = deliveryMap[wh.wb_warehouse_name] ?? 3;
                        const total = totalDays(wh.wb_warehouse_name);
                        const isFirst = idx === 0;
                        return (
                            <tr key={wh.wb_warehouse_name} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                <td style={{ padding: '10px 12px' }}>{wh.wb_warehouse_name}</td>
                                <td style={{ padding: '10px 12px', textAlign: 'center' }}>
                                    <input
                                        type="number"
                                        min={0}
                                        value={days}
                                        onChange={e => {
                                            const v = Math.max(0, parseInt(e.target.value) || 0);
                                            setDeliveryMap(prev => ({ ...prev, [wh.wb_warehouse_name]: v }));
                                        }}
                                        style={{
                                            width: 60,
                                            padding: '4px 8px',
                                            fontSize: 14,
                                            border: '1px solid var(--color-border)',
                                            borderRadius: 6,
                                            textAlign: 'center',
                                        }}
                                    />
                                </td>
                                <td style={{ padding: '10px 12px', textAlign: 'center', fontWeight: 500 }}>
                                    {total} дн{isFirst ? ` (${assemblyDays}+${days}+${wbAcceptanceDays})` : ''}
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>

            {/* Save button */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 12, marginTop: 20 }}>
                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>По умолчанию: 3 дня, если не задано</span>
                <button
                    className="btn btn-primary"
                    onClick={handleSave}
                    disabled={saving}
                >
                    {saving ? 'Сохранение...' : 'Сохранить'}
                </button>
            </div>
        </div>
    );
}

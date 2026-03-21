'use client';
import React, { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import { DataTable } from '@/components';
import type {
    Warehouse, InboundReceipt, OutboundShipment,
    WarehouseStockRow, StockMovement,
} from '@/types/api';
import type { Column } from '@/components/DataTable';

/* ─── Main page ────────────────────────────────────────────────────────────── */

export default function WarehouseDetailPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const warehouseId = Number(params.id);
    const [tab, setTab] = useState<'all' | 'receipts' | 'shipments' | 'stock'>('all');
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
        { key: 'all' as const, label: 'Все' },
        { key: 'receipts' as const, label: 'Приёмки', count: receiptCount },
        ...(isFulfillment ? [{ key: 'shipments' as const, label: 'Отгрузки', count: shipmentCount }] : []),
        { key: 'stock' as const, label: 'Остатки и статистика' },
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
                    {isFulfillment && (
                        <button className="btn btn-secondary" onClick={() => router.push(`/p/${slug}/warehouse/${warehouseId}/shipment/new`)}>
                            + Отгрузка
                        </button>
                    )}
                </div>
            </div>

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
        <DataTable
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
                const qty = row.items.reduce((s: number, it: { expected_qty: number }) => s + it.expected_qty, 0);
                return <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>{row.items.length} поз., {formatNumber(qty)} шт.</span>;
            },
        },
        { key: 'planned_date', label: 'Плановая дата', format: 'date' },
        { key: 'comment', label: 'Комментарий' },
        { key: 'created_at', label: 'Создана', format: 'date' },
    ];

    return (
        <>
            {error && <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>}

            <DataTable
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

            <DataTable columns={cols} data={shipments} emptyText="Нет отгрузок" emptyIcon="📤" onRowClick={(row) => router.push(`/p/${slug}/warehouse/${warehouseId}/shipment/${row.id}`)} />
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

            <DataTable columns={cols} data={stock} emptyText="Нет остатков" emptyIcon="📦" exportName="warehouse_stock" />

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

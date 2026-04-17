'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { OutboundShipment, Nomenclature } from '@/types/api';

/* ─── Nomenclature lookup ─────────────────────────────────────────────────── */

function useNomLookup(nomenclature: Nomenclature[]) {
    return useMemo(() => {
        const byBarcode = new Map<string, Nomenclature>();
        nomenclature.forEach(n => { if (n.barcode) byBarcode.set(n.barcode, n); });
        const resolve = (barcode: string) => byBarcode.get(barcode);
        const label = (n: Nomenclature) => n.article_seller || n.subject || n.name || `nmId: ${n.article_wb}`;
        return { resolve, label };
    }, [nomenclature]);
}

/* ─── Status badge ────────────────────────────────────────────────────────── */

function statusBadge(s: string, isDefect = false) {
    const map: Record<string, { label: string; bg: string; color: string }> = {
        DRAFT: { label: 'Черновик', bg: 'rgba(0,0,0,0.06)', color: 'var(--color-text-muted)' },
        SHIPPED: { label: isDefect ? 'Списана' : 'Отгружена', bg: 'rgba(245,158,11,0.1)', color: '#b45309' },
        DELIVERED: { label: 'Доставлена', bg: 'rgba(34,197,94,0.1)', color: '#16a34a' },
        CANCELLED: { label: 'Отменена', bg: 'rgba(239,68,68,0.1)', color: '#dc2626' },
    };
    const { label, bg, color } = map[s] || { label: s, bg: 'transparent', color: 'inherit' };
    return <span style={{ color, background: bg, padding: '3px 10px', borderRadius: 12, fontSize: 13, fontWeight: 600 }}>{label}</span>;
}

/* ─── Page ────────────────────────────────────────────────────────────────── */

export default function ShipmentDetailPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const warehouseId = Number(params.id);
    const shipmentId = Number(params.shipmentId);

    const [shipment, setShipment] = useState<OutboundShipment | null>(null);
    const [nomenclature, setNomenclature] = useState<Nomenclature[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [actionLoading, setActionLoading] = useState(false);

    const nom = useNomLookup(nomenclature);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [sh, nomData] = await Promise.all([
                api.getShipment(shipmentId),
                api.getNomenclature(),
            ]);
            setShipment(sh);
            setNomenclature(nomData);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [shipmentId]);

    useEffect(() => { load(); }, [load]);

    const goBack = () => router.push(`/p/${slug}/warehouse/${warehouseId}`);

    const handleShip = async () => {
        setActionLoading(true);
        try { const r = await api.shipShipment(shipmentId); setShipment(r); }
        catch (e: unknown) { setError(e instanceof Error ? e.message : 'Ошибка'); }
        setActionLoading(false);
    };

    const handleDeliver = async () => {
        setActionLoading(true);
        try { const r = await api.deliverShipment(shipmentId); setShipment(r); }
        catch (e: unknown) { setError(e instanceof Error ? e.message : 'Ошибка'); }
        setActionLoading(false);
    };

    const handleCancel = async () => {
        if (!confirm('Отменить отгрузку? Товар вернётся на склад.')) return;
        setActionLoading(true);
        try { const r = await api.cancelShipment(shipmentId); setShipment(r); }
        catch (e: unknown) { setError(e instanceof Error ? e.message : 'Ошибка'); }
        setActionLoading(false);
    };

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error && !shipment) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;
    if (!shipment) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Отгрузка не найдена</div>;

    const totalQty = shipment.items.reduce((s, it) => s + it.quantity, 0);

    return (
        <div className="animate-in">
            {/* Header */}
            <div className="page-header">
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <button
                        onClick={goBack}
                        className="btn btn-secondary"
                        style={{ padding: '6px 12px', fontSize: 18, lineHeight: 1 }}
                    >&larr;</button>
                    <div>
                        <h1 className="page-title">{shipment.is_defect ? 'Списание брака' : 'Отгрузка'} {shipment.number}</h1>
                        <p className="page-subtitle" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            {statusBadge(shipment.status, shipment.is_defect)}
                            {shipment.is_defect && (
                                <span className="badge badge-warning" style={{ fontSize: 12 }}>Брак</span>
                            )}
                        </p>
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    {shipment.status === 'DRAFT' && (
                        <button className="btn btn-primary" onClick={handleShip} disabled={actionLoading}>
                            {actionLoading ? '...' : 'Отгрузить'}
                        </button>
                    )}
                    {shipment.status === 'SHIPPED' && (
                        <>
                            <button className="btn btn-success" onClick={handleDeliver} disabled={actionLoading}>
                                {actionLoading ? '...' : 'Доставлено'}
                            </button>
                            <button className="btn btn-danger" onClick={handleCancel} disabled={actionLoading}>
                                Отменить
                            </button>
                        </>
                    )}
                </div>
            </div>

            {error && (
                <div style={{ color: 'var(--color-danger)', background: 'rgba(239,68,68,0.06)', padding: '10px 16px', borderRadius: 8, marginBottom: 16, fontSize: 13 }}>
                    {error}
                </div>
            )}

            {/* Info card */}
            <div className="glass-card" style={{ padding: 20, marginBottom: 20 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 16 }}>
                    <div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Статус</div>
                        <div>{statusBadge(shipment.status, shipment.is_defect)}</div>
                    </div>
                    {shipment.destination && (
                        <div>
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Назначение</div>
                            <div style={{ fontWeight: 500 }}>{shipment.destination}</div>
                        </div>
                    )}
                    <div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Позиций</div>
                        <div style={{ fontWeight: 500 }}>{shipment.items.length} поз., {formatNumber(totalQty)} шт.</div>
                    </div>
                    {shipment.shipped_date && (
                        <div>
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Дата отгрузки</div>
                            <div style={{ fontWeight: 500 }}>{formatDate(shipment.shipped_date)}</div>
                        </div>
                    )}
                    {shipment.created_at && (
                        <div>
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Создана</div>
                            <div style={{ fontWeight: 500 }}>{formatDate(shipment.created_at)}</div>
                        </div>
                    )}
                    {shipment.comment && (
                        <div style={{ gridColumn: '1 / -1' }}>
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Комментарий</div>
                            <div>{shipment.comment}</div>
                        </div>
                    )}
                    {shipment.is_defect && shipment.defect_reason && (
                        <div style={{ gridColumn: '1 / -1' }}>
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Причина (брак)</div>
                            <div>{shipment.defect_reason}</div>
                        </div>
                    )}
                </div>
            </div>

            {/* Items */}
            {(() => {
                const shipmentItemsData = shipment.items.map((item, i) => ({ ...item, _index: i + 1 }));
                const totalRow = { _index: '', barcode: '', quantity: totalQty, _isTotal: true, id: '_total' };
                const shipmentCols: Column[] = [
                    { key: '_index', label: '#', align: 'center' as const, width: '40', render: (v: number, row: any) => row._isTotal ? '' : <span style={{ color: 'var(--color-text-muted)', fontSize: 12 }}>{v}</span> },
                    { key: '_product', label: 'ТОВАР', render: (_v: any, row: any) => {
                        if (row._isTotal) return <strong>Итого</strong>;
                        const n = nom.resolve(row.barcode);
                        return n ? (
                            <div>
                                <div style={{ fontWeight: 500 }}>{nom.label(n)}</div>
                                {n.article_seller && n.name && n.article_seller !== n.name && (
                                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{n.name}</div>
                                )}
                            </div>
                        ) : <span style={{ color: 'var(--color-text-muted)' }}>—</span>;
                    }},
                    { key: 'barcode', label: 'ШК', render: (v: string, row: any) => row._isTotal ? '' : <span style={{ fontSize: 13, fontFamily: 'monospace' }}>{v}</span> },
                    { key: 'quantity', label: 'КОЛ-ВО', align: 'right' as const, render: (v: number, row: any) => <span style={{ fontWeight: row._isTotal ? 700 : 500 }}>{formatNumber(v)}</span> },
                ];
                return (
                    <TanStackDataTable
                        columns={shipmentCols}
                        data={[...shipmentItemsData, totalRow]}
                        title="Позиции отгрузки"
                        enableSorting={false}
                        enablePagination={false}
                    />
                );
            })()}
        </div>
    );
}

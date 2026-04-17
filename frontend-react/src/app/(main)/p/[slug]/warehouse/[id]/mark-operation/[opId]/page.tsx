'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { DefectMarkOperation, DefectMarkOperationItem, Nomenclature } from '@/types/api';

function useNomLookup(nomenclature: Nomenclature[]) {
    return useMemo(() => {
        const byBarcode = new Map<string, Nomenclature>();
        nomenclature.forEach(n => { if (n.barcode) byBarcode.set(n.barcode, n); });
        const resolve = (barcode: string) => byBarcode.get(barcode);
        const label = (n: Nomenclature) => n.article_seller || n.subject || n.name || `nmId: ${n.article_wb}`;
        return { resolve, label };
    }, [nomenclature]);
}

function statusBadge(s: string) {
    const map: Record<string, { label: string; bg: string; color: string }> = {
        ACCEPTED: { label: 'Принята', bg: 'rgba(34,197,94,0.1)', color: '#16a34a' },
        CANCELLED: { label: 'Отменена', bg: 'rgba(239,68,68,0.1)', color: '#dc2626' },
    };
    const { label, bg, color } = map[s] || { label: s, bg: 'transparent', color: 'inherit' };
    return <span style={{ color, background: bg, padding: '3px 10px', borderRadius: 12, fontSize: 13, fontWeight: 600 }}>{label}</span>;
}

export default function DefectMarkOperationPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const warehouseId = Number(params.id);
    const opId = Number(params.opId);

    const [operation, setOperation] = useState<DefectMarkOperation | null>(null);
    const [nomenclature, setNomenclature] = useState<Nomenclature[]>([]);
    const [warehouseName, setWarehouseName] = useState('');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [cancelling, setCancelling] = useState(false);

    const nom = useNomLookup(nomenclature);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [op, nomData, whs] = await Promise.all([
                api.getDefectMarkOperation(opId),
                api.getNomenclature(),
                api.getWarehouses(),
            ]);
            setOperation(op);
            setNomenclature(nomData);
            const wh = whs.find(w => w.id === warehouseId);
            setWarehouseName(wh?.name || `Склад #${warehouseId}`);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [opId, warehouseId]);

    useEffect(() => { load(); }, [load]);

    const goBack = () => router.push(`/p/${slug}/warehouse/${warehouseId}?tab=receipts`);

    const handleCancel = async () => {
        if (!operation) return;
        if (!confirm(`Отменить операцию ${operation.number}? Товары вернутся из брака в годные.`)) return;
        setCancelling(true);
        try {
            await api.cancelDefectMarkOperation(opId);
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка при отмене');
        }
        setCancelling(false);
    };

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error && !operation) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;
    if (!operation) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Операция не найдена</div>;

    const totalQty = operation.items.reduce((s, it) => s + it.quantity, 0);
    const canCancel = operation.status === 'ACCEPTED';

    const cols: Column[] = [
        {
            key: 'barcode', label: 'Товар',
            render: (v: string, row: DefectMarkOperationItem) => {
                const n = nom.resolve(row.barcode);
                return <span>{n ? nom.label(n) : '—'}</span>;
            },
        },
        { key: 'barcode', label: 'ШК (баркод)' },
        { key: 'quantity', label: 'Количество', format: 'number' },
    ];

    return (
        <div className="animate-in">
            <div className="page-header">
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <button onClick={goBack} className="btn btn-secondary" style={{ padding: '6px 12px', fontSize: 18, lineHeight: 1 }} title="Назад">&larr;</button>
                    <div>
                        <h1 className="page-title" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            {operation.number}
                            <span className="badge badge-warning" style={{ fontSize: 12, padding: '4px 10px' }}>Пометка брака</span>
                            {statusBadge(operation.status)}
                        </h1>
                        <p className="page-subtitle">{warehouseName} — перевод годных остатков в брак</p>
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary" onClick={goBack}>Назад</button>
                    {canCancel && (
                        <button
                            className="btn btn-danger"
                            onClick={handleCancel}
                            disabled={cancelling}
                        >
                            {cancelling ? 'Отмена...' : 'Отменить'}
                        </button>
                    )}
                </div>
            </div>

            {error && (
                <div style={{ color: 'var(--color-danger)', background: 'rgba(239,68,68,0.06)', padding: '10px 16px', borderRadius: 8, marginBottom: 16, fontSize: 13 }}>
                    {error}
                </div>
            )}

            <div className="glass-card" style={{ padding: 20, marginBottom: 20 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 16 }}>
                    <div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Дата</div>
                        <div style={{ fontWeight: 600 }}>{operation.actual_date ? formatDate(operation.actual_date) : '—'}</div>
                    </div>
                    <div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Позиций</div>
                        <div style={{ fontWeight: 600 }}>{operation.items.length}</div>
                    </div>
                    <div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Всего штук</div>
                        <div style={{ fontWeight: 600 }}>{formatNumber(totalQty, 0)}</div>
                    </div>
                    <div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Создана</div>
                        <div style={{ fontWeight: 600 }}>{operation.created_at ? formatDate(operation.created_at) : '—'}</div>
                    </div>
                </div>
                {operation.reason && (
                    <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--color-border)' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Причина</div>
                        <div style={{ fontSize: 14, whiteSpace: 'pre-wrap' }}>{operation.reason}</div>
                    </div>
                )}
                {operation.status === 'CANCELLED' && (
                    <div style={{
                        marginTop: 16, padding: 12,
                        background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.2)',
                        borderRadius: 8, fontSize: 13, color: 'var(--color-danger)',
                    }}>
                        Операция отменена. Позиции возвращены из брака в годные остатки.
                    </div>
                )}
            </div>

            <TanStackDataTable
                columns={cols}
                data={operation.items}
                emptyText="Нет позиций"
                exportName={`${operation.number}_items`}
            />
        </div>
    );
}

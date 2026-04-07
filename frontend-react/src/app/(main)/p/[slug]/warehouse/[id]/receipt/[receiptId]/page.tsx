'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { InboundReceipt, Nomenclature } from '@/types/api';

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

function statusBadge(s: string) {
    const map: Record<string, { label: string; bg: string; color: string }> = {
        DRAFT: { label: 'Черновик', bg: 'rgba(0,0,0,0.06)', color: 'var(--color-text-muted)' },
        EXPECTED: { label: 'Ожидается', bg: 'rgba(245,158,11,0.1)', color: '#b45309' },
        ACCEPTED: { label: 'Принята', bg: 'rgba(34,197,94,0.1)', color: '#16a34a' },
        CANCELLED: { label: 'Отменена', bg: 'rgba(239,68,68,0.1)', color: '#dc2626' },
    };
    const { label, bg, color } = map[s] || { label: s, bg: 'transparent', color: 'inherit' };
    return <span style={{ color, background: bg, padding: '3px 10px', borderRadius: 12, fontSize: 13, fontWeight: 600 }}>{label}</span>;
}

/* ─── Page ────────────────────────────────────────────────────────────────── */

export default function ReceiptDetailPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const warehouseId = Number(params.id);
    const receiptId = Number(params.receiptId);

    const [receipt, setReceipt] = useState<InboundReceipt | null>(null);
    const [nomenclature, setNomenclature] = useState<Nomenclature[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [actionLoading, setActionLoading] = useState(false);
    const [actualQty, setActualQty] = useState<Record<number, string>>({});
    const [receiving, setReceiving] = useState(false);

    const nom = useNomLookup(nomenclature);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [r, nomData] = await Promise.all([
                api.getReceipt(receiptId),
                api.getNomenclature(),
            ]);
            setReceipt(r);
            setNomenclature(nomData);
            // Init actual_qty: use actual if set, otherwise expected
            const qtyInit: Record<number, string> = {};
            r.items.forEach((it: any) => { qtyInit[it.id] = String(it.actual_qty > 0 ? it.actual_qty : it.expected_qty); });
            setActualQty(qtyInit);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [receiptId]);

    useEffect(() => { load(); }, [load]);

    const goBack = () => router.push(`/p/${slug}/warehouse/${warehouseId}`);

    const handleAccept = async () => {
        setActionLoading(true);
        try {
            const quantities = Object.entries(actualQty).map(([itemId, qty]) => ({
                item_id: Number(itemId),
                actual_qty: parseInt(qty) || 0,
            }));
            const r = await api.acceptReceipt(receiptId, quantities);
            setReceipt(r);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
    };

    const handleFillAll = () => {
        if (!receipt) return;
        const filled: Record<number, string> = {};
        receipt.items.forEach(it => { filled[it.id] = String(it.expected_qty); });
        setActualQty(filled);
    };

    const handleCancel = async () => {
        if (!confirm('Отменить приёмку? Остатки будут откачены.')) return;
        setActionLoading(true);
        try {
            const r = await api.cancelReceipt(receiptId);
            setReceipt(r);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
    };

    const canReceive = receipt ? (receipt.status === 'DRAFT' || receipt.status === 'EXPECTED') : false;

    // Auto-exit receiving mode after accept
    useEffect(() => {
        if (receiving && !canReceive) setReceiving(false);
    }, [canReceive, receiving]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error && !receipt) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;
    if (!receipt) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Приёмка не найдена</div>;

    const isEditable = canReceive && receiving;
    const totalExpected = receipt.items.reduce((s, it) => s + it.expected_qty, 0);
    const totalActual = receipt.items.reduce((s, it) => s + (parseInt(actualQty[it.id]) || it.actual_qty || 0), 0);

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
                        <h1 className="page-title">Приёмка {receipt.number}</h1>
                        <p className="page-subtitle" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            {statusBadge(receipt.status)}
                        </p>
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    {canReceive && !receiving && (
                        <button className="btn btn-primary" onClick={() => setReceiving(true)}>
                            Начать приёмку
                        </button>
                    )}
                    {receipt.status === 'ACCEPTED' && (
                        <>
                            <button className="btn btn-danger" onClick={handleCancel} disabled={actionLoading}>
                                {actionLoading ? '...' : 'Отменить'}
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
                        <div>{statusBadge(receipt.status)}</div>
                    </div>
                    {receipt.planned_date && (
                        <div>
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Плановая дата</div>
                            <div style={{ fontWeight: 500 }}>{formatDate(receipt.planned_date)}</div>
                        </div>
                    )}
                    <div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Позиций</div>
                        <div style={{ fontWeight: 500 }}>{receipt.items.length} поз., {formatNumber(totalExpected)} шт.</div>
                    </div>
                    {receipt.created_at && (
                        <div>
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Создана</div>
                            <div style={{ fontWeight: 500 }}>{formatDate(receipt.created_at)}</div>
                        </div>
                    )}
                    {receipt.comment && (
                        <div style={{ gridColumn: '1 / -1' }}>
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Комментарий</div>
                            <div>{receipt.comment}</div>
                        </div>
                    )}
                </div>
            </div>

            {/* Items */}
            <div className="glass-card" style={{ padding: 16, overflow: 'auto' }}>
                <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>Позиции приёмки ({receipt.items.length})</h3>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                    <thead>
                        <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                            <th style={{ textAlign: 'center', padding: '8px 4px', fontSize: 11, color: 'var(--color-text-muted)', width: 40 }}>#</th>
                            <th style={{ textAlign: 'left', padding: '8px', fontSize: 11, color: 'var(--color-text-muted)' }}>ТОВАР</th>
                            <th style={{ textAlign: 'left', padding: '8px', fontSize: 11, color: 'var(--color-text-muted)' }}>ШК</th>
                            <th style={{ textAlign: 'right', padding: '8px', fontSize: 11, color: 'var(--color-text-muted)' }}>ОЖИД.</th>
                            <th style={{ textAlign: 'right', padding: '8px', fontSize: 11, color: 'var(--color-text-muted)', width: 120 }}>ФАКТ</th>
                        </tr>
                    </thead>
                    <tbody>
                        {receipt.items.map((item, i) => {
                            const n = nom.resolve(item.barcode);
                            const rawVal = actualQty[item.id] ?? String(item.actual_qty);
                            const numVal = parseInt(rawVal) || 0;
                            const mismatch = numVal !== item.expected_qty;
                            return (
                                <tr key={item.id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                    <td style={{ textAlign: 'center', padding: '8px 4px', color: 'var(--color-text-muted)', fontSize: 12 }}>{i + 1}</td>
                                    <td style={{ padding: '8px', fontWeight: 500 }}>{n ? nom.label(n) : '—'}</td>
                                    <td style={{ padding: '8px', fontFamily: 'monospace', fontSize: 12 }}>{item.barcode}</td>
                                    <td style={{ padding: '8px', textAlign: 'right', fontWeight: 500 }}>{formatNumber(item.expected_qty, 0)}</td>
                                    <td style={{ padding: '8px', textAlign: 'right' }}>
                                        {isEditable ? (
                                            <input
                                                type="number"
                                                defaultValue={numVal}
                                                onBlur={e => setActualQty(prev => ({ ...prev, [item.id]: e.target.value }))}
                                                style={{
                                                    width: 80, padding: '4px 8px', borderRadius: 6, textAlign: 'right',
                                                    border: `1px solid ${mismatch ? '#f59e0b' : 'var(--color-border)'}`,
                                                    background: mismatch ? 'rgba(245,158,11,0.05)' : 'var(--color-bg)',
                                                    color: 'var(--color-text)', fontSize: 13, fontWeight: 600,
                                                }}
                                            />
                                        ) : (
                                            <span style={{ fontWeight: 500, color: mismatch ? '#b45309' : 'var(--color-text)' }}>{formatNumber(numVal, 0)}</span>
                                        )}
                                    </td>
                                </tr>
                            );
                        })}
                        <tr style={{ background: 'var(--color-bg-secondary)' }}>
                            <td />
                            <td style={{ padding: '8px', fontWeight: 700 }}>Итого</td>
                            <td />
                            <td style={{ padding: '8px', textAlign: 'right', fontWeight: 700 }}>{formatNumber(totalExpected, 0)}</td>
                            <td style={{ padding: '8px', textAlign: 'right', fontWeight: 700 }}>{formatNumber(totalActual, 0)}</td>
                        </tr>
                    </tbody>
                </table>
            </div>

            {/* Action bar — below table when receiving */}
            {receiving && (
                <div className="glass-card" style={{ padding: '16px 20px', marginTop: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        Итого факт: <strong style={{ color: 'var(--color-text)' }}>{formatNumber(totalActual, 0)} шт</strong>
                        {totalActual !== totalExpected && (
                            <span style={{ color: '#b45309', marginLeft: 8 }}>
                                (ожид. {formatNumber(totalExpected, 0)}, разница: {formatNumber(totalActual - totalExpected, 0)})
                            </span>
                        )}
                    </div>
                    <div style={{ display: 'flex', gap: 8 }}>
                        <button className="btn btn-secondary" onClick={() => setReceiving(false)}>Отмена</button>
                        <button className="btn btn-secondary" onClick={handleFillAll}>Принять всё</button>
                        <button className="btn btn-success" onClick={handleAccept} disabled={actionLoading}>
                            {actionLoading ? '...' : 'Подтвердить приёмку'}
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}

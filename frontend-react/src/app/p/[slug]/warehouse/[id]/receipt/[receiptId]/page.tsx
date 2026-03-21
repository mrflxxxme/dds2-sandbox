'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
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
            const r = await api.acceptReceipt(receiptId);
            setReceipt(r);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
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

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error && !receipt) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;
    if (!receipt) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Приёмка не найдена</div>;

    const totalExpected = receipt.items.reduce((s, it) => s + it.expected_qty, 0);
    const totalActual = receipt.items.reduce((s, it) => s + it.actual_qty, 0);

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
                    {(receipt.status === 'DRAFT' || receipt.status === 'EXPECTED') && (
                        <>
                            <button
                                className="btn btn-secondary"
                                onClick={() => router.push(`/p/${slug}/warehouse/${warehouseId}/receipt/${receiptId}/edit`)}
                            >
                                Редактировать
                            </button>
                            <button className="btn btn-success" onClick={handleAccept} disabled={actionLoading}>
                                {actionLoading ? '...' : 'Принять'}
                            </button>
                        </>
                    )}
                    {receipt.status === 'ACCEPTED' && (
                        <>
                            <button
                                className="btn btn-secondary"
                                onClick={() => router.push(`/p/${slug}/warehouse/${warehouseId}/receipt/${receiptId}/edit`)}
                            >
                                Редактировать
                            </button>
                            <button
                                className="btn btn-secondary"
                                onClick={() => router.push(`/p/${slug}/warehouse/${warehouseId}/shipment/new`)}
                            >
                                Создать отгрузку
                            </button>
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
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Позиции приёмки</h2>
                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                    {receipt.items.length} поз., {formatNumber(totalExpected)} шт.
                </span>
            </div>

            <div className="glass-card" style={{ padding: 0, overflow: 'auto' }}>
                <table className="data-table" style={{ marginBottom: 0 }}>
                    <thead>
                        <tr>
                            <th style={{ width: 40, textAlign: 'center' }}>#</th>
                            <th style={{ minWidth: 220 }}>ТОВАР</th>
                            <th style={{ minWidth: 150 }}>ШК</th>
                            <th style={{ width: 100, textAlign: 'right' }}>ОЖИД.</th>
                            <th style={{ width: 100, textAlign: 'right' }}>ФАКТ</th>
                        </tr>
                    </thead>
                    <tbody>
                        {receipt.items.map((item, i) => {
                            const n = nom.resolve(item.barcode);
                            const mismatch = item.expected_qty !== item.actual_qty;
                            return (
                                <tr key={item.id} style={{ background: mismatch ? 'rgba(245,158,11,0.04)' : undefined }}>
                                    <td style={{ textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 12 }}>{i + 1}</td>
                                    <td style={{ fontSize: 13 }}>
                                        {n ? (
                                            <div>
                                                <div style={{ fontWeight: 500 }}>{nom.label(n)}</div>
                                                {n.article_seller && n.name && n.article_seller !== n.name && (
                                                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{n.name}</div>
                                                )}
                                            </div>
                                        ) : (
                                            <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                                        )}
                                    </td>
                                    <td style={{ fontSize: 13, fontFamily: 'monospace' }}>{item.barcode}</td>
                                    <td style={{ textAlign: 'right', fontWeight: 500 }}>{formatNumber(item.expected_qty)}</td>
                                    <td style={{
                                        textAlign: 'right', fontWeight: 500,
                                        color: mismatch ? '#b45309' : 'var(--color-text)',
                                    }}>
                                        {formatNumber(item.actual_qty)}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                    <tfoot>
                        <tr style={{ fontWeight: 700, borderTop: '2px solid var(--color-border)' }}>
                            <td></td>
                            <td>Итого</td>
                            <td></td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(totalExpected)}</td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(totalActual)}</td>
                        </tr>
                    </tfoot>
                </table>
            </div>
        </div>
    );
}

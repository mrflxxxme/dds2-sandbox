'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type { Nomenclature } from '@/types/api';

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

/* ─── Types ───────────────────────────────────────────────────────────────── */

interface ShipmentRow {
    barcode: string;
    quantity: string;
}

const emptyRow = (): ShipmentRow => ({ barcode: '', quantity: '' });

/* ─── Page ────────────────────────────────────────────────────────────────── */

export default function NewShipmentPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const warehouseId = Number(params.id);

    const [nomenclature, setNomenclature] = useState<Nomenclature[]>([]);
    const [warehouseName, setWarehouseName] = useState('');
    const [loading, setLoading] = useState(true);

    const [formDest, setFormDest] = useState('');
    const [formComment, setFormComment] = useState('');
    const [rows, setRows] = useState<ShipmentRow[]>(() => Array.from({ length: 8 }, emptyRow));
    const [search, setSearch] = useState('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    const nom = useNomLookup(nomenclature);

    const loadData = useCallback(async () => {
        setLoading(true);
        try {
            const [whs, nomData] = await Promise.all([
                api.getWarehouses(),
                api.getNomenclature(),
            ]);
            const wh = whs.find(w => w.id === warehouseId);
            setWarehouseName(wh?.name || `Склад #${warehouseId}`);
            setNomenclature(nomData);
        } catch { /* ignore */ }
        setLoading(false);
    }, [warehouseId]);

    useEffect(() => { loadData(); }, [loadData]);

    /* ─── Row operations ─────────────────────────────────────────────────── */

    const updateRow = (idx: number, field: keyof ShipmentRow, value: string) => {
        setRows(prev => {
            const next = [...prev];
            next[idx] = { ...next[idx], [field]: value };
            return next;
        });
        if (idx === rows.length - 1 && value.trim()) {
            setRows(prev => [...prev, emptyRow()]);
        }
    };

    const removeRow = (idx: number) => {
        setRows(prev => prev.filter((_, i) => i !== idx));
    };

    const handlePaste = (e: React.ClipboardEvent) => {
        const text = e.clipboardData.getData('text/plain');
        if (!text.includes('\t') && !text.includes('\n')) return;
        e.preventDefault();
        const lines = text.trim().split('\n').map(l => l.split('\t'));
        const newRows: ShipmentRow[] = [];
        for (const cols of lines) {
            if (cols.length < 2) continue;
            const barcode = cols[0].trim();
            const qty = cols[1].trim().replace(',', '.').replace(/[^\d]/g, '');
            if (barcode && qty) newRows.push({ barcode, quantity: qty });
        }
        if (newRows.length > 0) setRows([...newRows, emptyRow(), emptyRow()]);
    };

    const addFromSearch = (n: Nomenclature) => {
        if (!n.barcode) return;
        const firstEmpty = rows.findIndex(r => !r.barcode.trim());
        if (firstEmpty >= 0) {
            setRows(prev => {
                const next = [...prev];
                next[firstEmpty] = { barcode: n.barcode!, quantity: '' };
                return next;
            });
        } else {
            setRows(prev => [...prev, { barcode: n.barcode!, quantity: '' }]);
        }
        setSearch('');
    };

    /* ─── Computed ────────────────────────────────────────────────────────── */

    const filledRows = rows.filter(r => r.barcode.trim() && r.quantity.trim());
    const totalQty = filledRows.reduce((s, r) => s + (parseInt(r.quantity) || 0), 0);

    const filteredNom = search.trim()
        ? nomenclature.filter(n => {
            const q = search.toLowerCase();
            return (n.barcode && n.barcode.includes(q)) ||
                (n.article_seller && n.article_seller.toLowerCase().includes(q)) ||
                (n.name && n.name.toLowerCase().includes(q));
        }).slice(0, 10)
        : [];

    /* ─── Submit ──────────────────────────────────────────────────────────── */

    const handleCreate = async () => {
        if (filledRows.length === 0) { setError('Добавьте хотя бы одну позицию'); return; }
        setSaving(true);
        setError('');
        try {
            const items = filledRows.map(r => ({
                barcode: r.barcode.trim(),
                quantity: parseInt(r.quantity) || 0,
            }));
            await api.createShipment(warehouseId, {
                destination: formDest.trim() || undefined,
                comment: formComment.trim() || undefined,
                items,
            });
            router.push(`/p/${slug}/warehouse/${warehouseId}`);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setSaving(false);
    };

    const goBack = () => router.push(`/p/${slug}/warehouse/${warehouseId}`);

    /* ─── Render ──────────────────────────────────────────────────────────── */

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;

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
                        <h1 className="page-title">Новая отгрузка</h1>
                        <p className="page-subtitle">{warehouseName}</p>
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary" onClick={goBack}>Отмена</button>
                    <button
                        className="btn btn-primary"
                        onClick={handleCreate}
                        disabled={saving || filledRows.length === 0}
                    >
                        {saving ? 'Сохранение...' : 'Создать отгрузку'}
                    </button>
                </div>
            </div>

            {error && (
                <div style={{
                    color: 'var(--color-danger)', background: 'rgba(239,68,68,0.06)',
                    padding: '10px 16px', borderRadius: 8, marginBottom: 16, fontSize: 13,
                }}>
                    {error}
                </div>
            )}

            {/* Parameters */}
            <div className="glass-card" style={{ padding: 20, marginBottom: 20 }}>
                <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Параметры отгрузки</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Назначение</label>
                        <input className="form-input" value={formDest} onChange={e => setFormDest(e.target.value)} placeholder="Склад WB..." />
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Комментарий</label>
                        <input className="form-input" value={formComment} onChange={e => setFormComment(e.target.value)} placeholder="Примечание..." />
                    </div>
                </div>
            </div>

            {/* Toolbar: search + counter */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
                <div style={{ flex: 1, position: 'relative', minWidth: 200 }}>
                    <input
                        className="form-input"
                        placeholder="Поиск по товарам... (артикул, баркод, название)"
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        style={{ width: '100%' }}
                    />
                    {filteredNom.length > 0 && (
                        <div style={{
                            position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 100,
                            background: 'var(--color-bg)', border: '1px solid var(--color-border)',
                            borderRadius: 8, boxShadow: '0 4px 16px rgba(0,0,0,0.1)', maxHeight: 300, overflow: 'auto',
                        }}>
                            {filteredNom.map(n => (
                                <div
                                    key={n.id}
                                    onClick={() => addFromSearch(n)}
                                    style={{
                                        padding: '10px 14px', cursor: 'pointer', fontSize: 13,
                                        borderBottom: '1px solid var(--color-border)',
                                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                                    }}
                                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--color-bg-hover)')}
                                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                                >
                                    <span style={{ fontWeight: 500 }}>{nom.label(n)}</span>
                                    <span style={{ color: 'var(--color-text-muted)', fontSize: 12, marginLeft: 12 }}>{n.barcode}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                <span style={{ fontSize: 13, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
                    {filledRows.length} позиций, {formatNumber(totalQty)} шт.
                </span>
            </div>

            {/* Items table */}
            <div className="glass-card" style={{ overflow: 'auto', padding: 0 }} onPaste={handlePaste}>
                {/* TODO: migrate to TanStackDataTable — has inline form inputs (barcode input, quantity input, paste handler) */}
                <table className="data-table" style={{ marginBottom: 0 }}>
                    <thead>
                        <tr>
                            <th style={{ width: 40, textAlign: 'center' }}>#</th>
                            <th style={{ minWidth: 220 }}>ТОВАР</th>
                            <th style={{ minWidth: 160 }}>ШК (БАРКОД)</th>
                            <th style={{ width: 120, textAlign: 'right' }}>КОЛИЧЕСТВО</th>
                            <th style={{ width: 50 }}></th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row, i) => {
                            const n = row.barcode.trim() ? nom.resolve(row.barcode.trim()) : undefined;
                            const unknown = row.barcode.trim() && !n;
                            return (
                                <tr key={i} style={{ background: unknown ? 'rgba(239,68,68,0.04)' : undefined }}>
                                    <td style={{ textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 12 }}>
                                        {row.barcode.trim() ? i + 1 : ''}
                                    </td>
                                    <td style={{ fontSize: 13, color: n ? 'var(--color-text)' : unknown ? '#ef4444' : 'var(--color-text-muted)' }}>
                                        {n ? nom.label(n) : (unknown ? '(не найден)' : '—')}
                                    </td>
                                    <td>
                                        <input
                                            value={row.barcode}
                                            onChange={e => updateRow(i, 'barcode', e.target.value)}
                                            placeholder="Введите баркод..."
                                            style={{
                                                width: '100%', background: 'transparent',
                                                border: 'none', padding: '8px 4px', fontSize: 13,
                                                color: unknown ? '#ef4444' : 'var(--color-text)',
                                                outline: 'none',
                                            }}
                                        />
                                    </td>
                                    <td>
                                        <input
                                            type="number"
                                            value={row.quantity}
                                            onChange={e => updateRow(i, 'quantity', e.target.value)}
                                            placeholder="0"
                                            style={{
                                                width: '100%', background: 'transparent',
                                                border: 'none', padding: '8px 4px', fontSize: 13,
                                                textAlign: 'right', color: 'var(--color-text)',
                                                outline: 'none',
                                            }}
                                        />
                                    </td>
                                    <td>
                                        {row.barcode.trim() && (
                                            <button
                                                onClick={() => removeRow(i)}
                                                style={{
                                                    background: 'none', border: 'none', cursor: 'pointer',
                                                    color: 'var(--color-text-muted)', fontSize: 16, padding: '4px 6px',
                                                }}
                                            >×</button>
                                        )}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>

            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 8 }}>
                Вставьте данные из Excel/Google Sheets (Ctrl+V): Баркод &#x21B9; Кол-во
            </div>

            {/* Bottom action bar */}
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20, paddingBottom: 20 }}>
                <button className="btn btn-secondary" onClick={goBack}>Отмена</button>
                <button
                    className="btn btn-primary"
                    onClick={handleCreate}
                    disabled={saving || filledRows.length === 0}
                >
                    {saving ? 'Сохранение...' : `Создать отгрузку (${filledRows.length} поз.)`}
                </button>
            </div>
        </div>
    );
}

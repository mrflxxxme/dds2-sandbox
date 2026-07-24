'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { mergeRowsByBarcode } from '@/lib/utils/transferRows';
import type { Nomenclature, Warehouse } from '@/types/api';

/* ─── Nomenclature lookup helper ──────────────────────────────────────────── */

function useNomLookup(nomenclature: Nomenclature[]) {
    return useMemo(() => {
        const byBarcode = new Map<string, Nomenclature>();
        nomenclature.forEach(n => {
            if (n.barcode) byBarcode.set(n.barcode, n);
        });
        const resolve = (barcode: string): Nomenclature | undefined => byBarcode.get(barcode);
        const label = (n: Nomenclature): string => n.article_seller || n.subject || n.name || `nmId: ${n.article_wb}`;
        return { resolve, label };
    }, [nomenclature]);
}

/* ─── Types ───────────────────────────────────────────────────────────────── */

interface ItemRow {
    barcode: string;
    quantity: string;
}

const emptyItemRow = (): ItemRow => ({ barcode: '', quantity: '' });

/* ─── Page ────────────────────────────────────────────────────────────────── */

export default function NewTransferPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const fromWarehouseId = Number(params.id);

    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [nomenclature, setNomenclature] = useState<Nomenclature[]>([]);
    const [fromName, setFromName] = useState('');
    const [loading, setLoading] = useState(true);

    const [toWarehouseId, setToWarehouseId] = useState<number | ''>('');
    const [formComment, setFormComment] = useState('');
    const [isDefect, setIsDefect] = useState(false);
    const [defectReason, setDefectReason] = useState('');
    const [rows, setRows] = useState<ItemRow[]>(() => Array.from({ length: 8 }, emptyItemRow));
    const [search, setSearch] = useState('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [stockMap, setStockMap] = useState<Record<string, number>>({});
    const [defectMap, setDefectMap] = useState<Record<string, number>>({});

    const nom = useNomLookup(nomenclature);

    const loadData = useCallback(async () => {
        setLoading(true);
        try {
            const [whs, nomData, whStock, defectStock] = await Promise.all([
                api.getWarehouses(),
                api.getNomenclature(),
                api.getWarehouseStock(fromWarehouseId),
                api.getDefectStock(fromWarehouseId),
            ]);
            setWarehouses(whs);
            const wh = whs.find(w => w.id === fromWarehouseId);
            setFromName(wh?.name || `Склад #${fromWarehouseId}`);
            setNomenclature(nomData);
            const sm: Record<string, number> = {};
            whStock.forEach((r: { barcode: string; quantity: number }) => { sm[r.barcode] = r.quantity; });
            setStockMap(sm);
            const dm: Record<string, number> = {};
            defectStock.forEach((r: { barcode: string; defect_quantity: number }) => { dm[r.barcode] = r.defect_quantity; });
            setDefectMap(dm);
        } catch { /* ignore */ }
        setLoading(false);
    }, [fromWarehouseId]);

    useEffect(() => { loadData(); }, [loadData]);

    /* ─── Row operations ─────────────────────────────────────────────────── */

    const updateRow = (idx: number, field: keyof ItemRow, value: string) => {
        setRows(prev => {
            const next = [...prev];
            next[idx] = { ...next[idx], [field]: value };
            return next;
        });
        if (idx === rows.length - 1 && value.trim()) {
            setRows(prev => [...prev, emptyItemRow()]);
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
        const parsed: ItemRow[] = [];
        for (const cols of lines) {
            if (cols.length < 2) continue;
            const barcode = cols[0].trim();
            const qty = cols[1].trim().replace(',', '.').replace(/[^\d]/g, '');
            if (barcode && qty) parsed.push({ barcode, quantity: qty });
        }
        // Схлопываем дубли ШК из склейки нескольких источников: одна строка на ШК
        // с суммарным количеством — иначе остаток списывается многократно и send падает.
        const newRows = mergeRowsByBarcode(parsed).map(m => ({ barcode: m.barcode, quantity: String(m.quantity) }));
        if (newRows.length > 0) setRows([...newRows, emptyItemRow(), emptyItemRow()]);
    };

    const addFromSearch = (n: Nomenclature) => {
        if (!n.barcode) return;
        const existing = rows.findIndex(r => r.barcode === n.barcode);
        if (existing >= 0) return;
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
    const otherWarehouses = warehouses.filter(w => w.id !== fromWarehouseId && w.is_active);

    const filteredNom = search.trim()
        ? nomenclature.filter(n => {
            const q = search.toLowerCase();
            return (n.barcode && n.barcode.includes(q)) ||
                (n.article_seller && n.article_seller.toLowerCase().includes(q)) ||
                (n.name && n.name.toLowerCase().includes(q)) ||
                (n.subject && n.subject.toLowerCase().includes(q));
        }).slice(0, 10)
        : [];

    /* ─── Submit ──────────────────────────────────────────────────────────── */

    const handleCreate = async () => {
        if (!toWarehouseId) { setError('Выберите склад назначения'); return; }
        if (filledRows.length === 0) { setError('Добавьте хотя бы одну позицию'); return; }
        const zero = filledRows.filter(r => (parseInt(r.quantity, 10) || 0) <= 0);
        if (zero.length > 0) {
            setError(`Количество должно быть больше нуля: ${zero.map(r => r.barcode.trim()).join(', ')}`);
            return;
        }
        // Схлопываем дубли ШК и валидируем по СУММЕ на штрихкод, а не построчно:
        // одна строка «52 ≤ остатка» проходит, а сумма трёх таких строк — нет,
        // и тогда backend-send падает построчным «have 0».
        const items = mergeRowsByBarcode(filledRows);
        const availMap = isDefect ? defectMap : stockMap;
        const over = items.filter(it => it.quantity > (availMap[it.barcode] || 0));
        if (over.length > 0) {
            setError(`Недостаточно остатка: ${over.map(it => `${it.barcode} (нужно ${formatNumber(it.quantity)}, есть ${formatNumber(availMap[it.barcode] || 0)})`).join(', ')}`);
            return;
        }
        setSaving(true);
        setError('');
        try {
            const transfer = await api.createTransfer({
                from_warehouse_id: fromWarehouseId,
                to_warehouse_id: Number(toWarehouseId),
                comment: formComment.trim() || undefined,
                is_defect: isDefect || undefined,
                defect_reason: isDefect && defectReason.trim() ? defectReason.trim() : undefined,
                items,
            });
            if (transfer?.id) {
                // Auto-send (source deducted immediately, destination gets in_transit).
                // Destination must accept via "Принять": брак — вкладка «Брак»,
                // обычные — вкладка «Перемещения»
                try {
                    await api.sendTransfer(transfer.id);
                } catch (sendErr: unknown) {
                    // Откат черновика, чтобы не оставлять сироту (ретрай создал бы дубль)
                    try {
                        await api.cancelTransfer(transfer.id);
                    } catch {
                        setError(`Отправка не удалась, черновик ${transfer.number} остался — управляйте им на вкладке «Перемещения». ` +
                            (sendErr instanceof Error ? sendErr.message : ''));
                        setSaving(false);
                        return;
                    }
                    throw sendErr;
                }
            }
            router.push(`/p/${slug}/warehouse/${fromWarehouseId}`);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setSaving(false);
    };

    const goBack = () => router.push(`/p/${slug}/warehouse/${fromWarehouseId}`);

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
                        title="Назад"
                    >&larr;</button>
                    <div>
                        <h1 className="page-title">Новое перемещение</h1>
                        <p className="page-subtitle">Со склада: {fromName}</p>
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary" onClick={goBack}>Отмена</button>
                    <button
                        className="btn btn-primary"
                        onClick={handleCreate}
                        disabled={saving || filledRows.length === 0 || !toWarehouseId}
                    >
                        {saving ? 'Сохранение...' : 'Создать перемещение'}
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
                <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Параметры перемещения</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Откуда</label>
                        <input className="form-input" value={fromName} disabled style={{ background: 'var(--color-hover)' }} />
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Куда *</label>
                        <select
                            className="form-input"
                            value={toWarehouseId}
                            onChange={e => setToWarehouseId(e.target.value ? Number(e.target.value) : '')}
                        >
                            <option value="">Выберите склад...</option>
                            {otherWarehouses.map(w => (
                                <option key={w.id} value={w.id}>{w.name}</option>
                            ))}
                        </select>
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Комментарий</label>
                        <input className="form-input" value={formComment} onChange={e => setFormComment(e.target.value)} placeholder="Примечание..." />
                    </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginTop: 16 }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 14 }}>
                        <input
                            type="checkbox"
                            checked={isDefect}
                            onChange={e => setIsDefect(e.target.checked)}
                            style={{ width: 16, height: 16, cursor: 'pointer' }}
                        />
                        Перемещение брака
                    </label>
                    {isDefect && (
                        <div className="form-group" style={{ margin: 0, flex: 1 }}>
                            <input
                                className="form-input"
                                value={defectReason}
                                onChange={e => setDefectReason(e.target.value)}
                                placeholder="Причина брака..."
                            />
                        </div>
                    )}
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
                                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--color-hover)')}
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
            <div
                className="glass-card"
                style={{ overflow: 'auto', padding: 0 }}
                onPaste={handlePaste}
            >
                {/* TODO: migrate to TanStackDataTable — has inline form inputs (barcode input, quantity input, paste handler) */}
                <table className="data-table" style={{ marginBottom: 0 }}>
                    <thead>
                        <tr>
                            <th style={{ width: 40, textAlign: 'center' }}>#</th>
                            <th style={{ minWidth: 220 }}>ТОВАР</th>
                            <th style={{ minWidth: 160 }}>ШК (БАРКОД)</th>
                            <th style={{ width: 100, textAlign: 'right' }}>{isDefect ? 'В БРАКЕ' : 'НА СКЛАДЕ'}</th>
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
                                        {n ? nom.label(n) : (unknown ? '(не найден)' : '\u2014')}
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
                                    <td style={{ textAlign: 'right', fontSize: 13, fontWeight: 600, color: isDefect ? 'var(--color-warning)' : 'var(--color-text)' }}>
                                        {row.barcode.trim() ? formatNumber((isDefect ? defectMap : stockMap)[row.barcode.trim()] || 0) : ''}
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
                                                title="Удалить строку"
                                            >{'\u00D7'}</button>
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
                    disabled={saving || filledRows.length === 0 || !toWarehouseId}
                >
                    {saving ? 'Сохранение...' : `Создать перемещение (${filledRows.length} поз.)`}
                </button>
            </div>
        </div>
    );
}

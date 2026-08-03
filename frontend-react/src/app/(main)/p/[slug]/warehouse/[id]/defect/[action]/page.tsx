'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type { DefectBulkOperation, DefectBulkResponse, Nomenclature } from '@/types/api';

/* ─── Action config ──────────────────────────────────────────────────────── */

type BulkMethodName = 'markDefectBulk' | 'receiveDefectBulk' | 'writeoffDefectBulk' | 'recoverDefectBulk';

const ACTION_CONFIG: Record<string, { title: string; subtitle: string; btnLabel: string; apiMethod: BulkMethodName }> = {
    mark: {
        title: 'Отметить брак',
        subtitle: 'Перевести годный товар в статус бракованного',
        btnLabel: 'Отметить как брак',
        apiMethod: 'markDefectBulk',
    },
    receive: {
        title: 'Принять брак',
        subtitle: 'Оприходовать бракованный товар извне (возвраты WB, от покупателей)',
        btnLabel: 'Принять брак',
        apiMethod: 'receiveDefectBulk',
    },
    writeoff: {
        title: 'Списать брак',
        subtitle: 'Списать бракованный товар (уничтожение, утилизация)',
        btnLabel: 'Списать',
        apiMethod: 'writeoffDefectBulk',
    },
    recover: {
        title: 'Восстановить из брака',
        subtitle: 'Вернуть товар в годный (после ремонта, пересортировки)',
        btnLabel: 'Восстановить',
        apiMethod: 'recoverDefectBulk',
    },
};

/* ─── Nomenclature lookup ────────────────────────────────────────────────── */

function useNomLookup(nomenclature: Nomenclature[]) {
    return useMemo(() => {
        const byBarcode = new Map<string, Nomenclature>();
        nomenclature.forEach(n => { if (n.barcode) byBarcode.set(n.barcode, n); });
        const resolve = (barcode: string): Nomenclature | undefined => byBarcode.get(barcode);
        const label = (n: Nomenclature): string => n.article_seller || n.subject || n.name || `nmId: ${n.article_wb}`;
        return { resolve, label, byBarcode };
    }, [nomenclature]);
}

/* ─── Types ───────────────────────────────────────────────────────────────── */

interface ItemRow { barcode: string; quantity: string }
const emptyRow = (): ItemRow => ({ barcode: '', quantity: '' });

/* ─── Page ────────────────────────────────────────────────────────────────── */

export default function DefectActionPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const warehouseId = Number(params.id);
    const action = params.action as string;

    const config = ACTION_CONFIG[action];

    const [nomenclature, setNomenclature] = useState<Nomenclature[]>([]);
    const [warehouseName, setWarehouseName] = useState('');
    const [loading, setLoading] = useState(true);
    const [defectMap, setDefectMap] = useState<Record<string, number>>({});
    const [stockMap, setStockMap] = useState<Record<string, number>>({});

    const [reason, setReason] = useState('');
    const [rows, setRows] = useState<ItemRow[]>(() => Array.from({ length: 8 }, emptyRow));
    const [search, setSearch] = useState('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [missingItems, setMissingItems] = useState<{ barcode: string; quantity: number }[]>([]);
    const [receiving, setReceiving] = useState(false);

    const nom = useNomLookup(nomenclature);

    const loadData = useCallback(async () => {
        setLoading(true);
        try {
            const [whs, nomData, defectStock, fullStock] = await Promise.all([
                api.getWarehouses(),
                api.getNomenclature(),
                api.getDefectStock(warehouseId),
                api.getWarehouseStock(warehouseId),
            ]);
            const wh = whs.find(w => w.id === warehouseId);
            setWarehouseName(wh?.name || `Склад #${warehouseId}`);
            setNomenclature(nomData);
            const dm: Record<string, number> = {};
            defectStock.forEach((r: { barcode: string; defect_quantity: number }) => {
                dm[r.barcode] = r.defect_quantity;
            });
            setDefectMap(dm);
            const sm: Record<string, number> = {};
            fullStock.forEach((r) => { sm[r.barcode] = r.quantity; });
            setStockMap(sm);
        } catch { /* ignore */ }
        setLoading(false);
    }, [warehouseId]);

    useEffect(() => { loadData(); }, [loadData]);

    /* ─── Row operations ─────────────────────────────────────────────────── */

    const updateRow = (idx: number, field: keyof ItemRow, value: string) => {
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
        const newRows: ItemRow[] = [];
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

    const handleSubmit = async () => {
        if (filledRows.length === 0) { setError('Добавьте хотя бы одну позицию'); return; }
        if (!reason.trim()) { setError('Укажите причину'); return; }
        setSaving(true);
        setError('');
        setMissingItems([]);

        const payload: DefectBulkOperation = {
            reason: reason.trim(),
            items: filledRows.map(r => ({
                barcode: r.barcode.trim(),
                quantity: parseInt(r.quantity) || 0,
            })),
        };

        const apiMethod = api[config.apiMethod].bind(api) as (
            warehouseId: number,
            data: DefectBulkOperation,
        ) => Promise<DefectBulkResponse>;

        try {
            const response = await apiMethod(warehouseId, payload);
            setSaving(false);

            if (response.status === 'ok') {
                router.push(`/p/${slug}/warehouse/${warehouseId}?tab=defects`);
                return;
            }

            const total = filledRows.length;
            const errorLines = response.errors.map(e => `${e.barcode}: ${e.error}`).join('\n');
            setError(`Обработано ${response.processed}/${total}. Ошибки:\n${errorLines}`);

            if (action === 'mark') {
                const errorBarcodes = new Set(response.errors.map(e => e.barcode));
                const missing = filledRows
                    .filter(r => errorBarcodes.has(r.barcode.trim()))
                    .map(r => ({ barcode: r.barcode.trim(), quantity: parseInt(r.quantity) || 0 }));
                setMissingItems(missing);
            }
        } catch (e: unknown) {
            setSaving(false);
            setError(e instanceof Error ? e.message : 'Ошибка при сохранении');
        }
    };

    const handleReceiveMissing = async () => {
        if (missingItems.length === 0) return;
        if (!reason.trim()) { setError('Укажите причину'); return; }
        setReceiving(true);
        try {
            const response = await api.receiveDefectBulk(warehouseId, {
                reason: reason.trim(),
                items: missingItems,
            });
            setReceiving(false);

            if (response.status === 'ok') {
                router.push(`/p/${slug}/warehouse/${warehouseId}?tab=defects`);
                return;
            }
            const errorLines = response.errors.map(e => `${e.barcode}: ${e.error}`).join('\n');
            setError(`Принято ${response.processed}/${missingItems.length}. Ошибки:\n${errorLines}`);
        } catch (e: unknown) {
            setReceiving(false);
            setError(e instanceof Error ? e.message : 'Ошибка при приёмке');
        }
    };

    const goBack = () => router.push(`/p/${slug}/warehouse/${warehouseId}?tab=defects`);

    /* ─── Render ──────────────────────────────────────────────────────────── */

    if (!config) return <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-danger)' }}>Неизвестная операция: {action}</div>;
    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;

    return (
        <div className="animate-in">
            {/* Header */}
            <div className="page-header">
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <button onClick={goBack} className="btn btn-secondary" style={{ padding: '6px 12px', fontSize: 18, lineHeight: 1 }} title="Назад">&larr;</button>
                    <div>
                        <h1 className="page-title">{config.title}</h1>
                        <p className="page-subtitle">{warehouseName} — {config.subtitle}</p>
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary" onClick={goBack}>Отмена</button>
                    <button className="btn btn-primary" onClick={handleSubmit} disabled={saving || filledRows.length === 0}>
                        {saving ? 'Обработка...' : `${config.btnLabel} (${filledRows.length} поз.)`}
                    </button>
                </div>
            </div>

            {error && (
                <div style={{ color: 'var(--color-danger)', background: 'rgba(239,68,68,0.06)', padding: '10px 16px', borderRadius: 8, marginBottom: 16, fontSize: 13, whiteSpace: 'pre-line' }}>
                    {error}
                </div>
            )}

            {action === 'mark' && missingItems.length > 0 && (
                <div style={{
                    background: 'rgba(255,159,10,0.08)',
                    border: '1px solid rgba(255,159,10,0.3)',
                    padding: '12px 16px', borderRadius: 8, marginBottom: 16,
                    display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                }}>
                    <div style={{ flex: 1, fontSize: 13 }}>
                        <div style={{ fontWeight: 600, color: 'var(--color-text)' }}>
                            {missingItems.length} позиций не нашлось на складе
                        </div>
                        <div style={{ color: 'var(--color-text-muted)', marginTop: 4 }}>
                            Принять их на склад сразу как брак (создаст приход с отметкой брака)?
                        </div>
                    </div>
                    <button
                        className="btn btn-primary"
                        onClick={handleReceiveMissing}
                        disabled={receiving}
                    >
                        {receiving ? 'Приёмка...' : `Принять как брак (${missingItems.length} поз.)`}
                    </button>
                </div>
            )}

            {/* Reason */}
            <div className="glass-card" style={{ padding: 20, marginBottom: 20 }}>
                <div className="form-group" style={{ margin: 0 }}>
                    <label className="form-label">Причина *</label>
                    <textarea
                        className="form-input"
                        value={reason}
                        onChange={e => setReason(e.target.value)}
                        placeholder="Укажите причину (обязательно)..."
                        rows={2}
                        style={{ resize: 'vertical' }}
                    />
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
                <table className="data-table" style={{ marginBottom: 0 }}>
                    <thead>
                        <tr>
                            <th style={{ width: 40, textAlign: 'center' }}>#</th>
                            <th style={{ minWidth: 220 }}>ТОВАР</th>
                            <th style={{ minWidth: 160 }}>ШК (БАРКОД)</th>
                            <th style={{ width: 100, textAlign: 'right' }}>НА СКЛАДЕ</th>
                            <th style={{ width: 100, textAlign: 'right' }}>В БРАКЕ</th>
                            <th style={{ width: 120, textAlign: 'right' }}>КОЛИЧЕСТВО</th>
                            <th style={{ width: 50 }}></th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row, i) => {
                            const barcode = row.barcode.trim();
                            const qty = parseInt(row.quantity) || 0;
                            const n = barcode ? nom.resolve(barcode) : undefined;
                            const unknown = barcode && !n;
                            const stockQty = barcode ? (stockMap[barcode] || 0) : 0;
                            const notEnough = action === 'mark' && barcode && qty > 0 && qty > stockQty;
                            const rowBg = unknown ? 'rgba(239,68,68,0.04)' : notEnough ? 'rgba(255,159,10,0.06)' : undefined;
                            return (
                                <tr key={i} style={{ background: rowBg }}>
                                    <td style={{ textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 12 }}>
                                        {barcode ? i + 1 : ''}
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
                                                width: '100%', background: 'transparent', border: 'none',
                                                padding: '8px 4px', fontSize: 13, outline: 'none',
                                                color: unknown ? '#ef4444' : 'var(--color-text)',
                                            }}
                                        />
                                    </td>
                                    <td style={{ textAlign: 'right', fontSize: 13, color: notEnough ? 'var(--color-danger)' : 'var(--color-text-muted)', fontWeight: notEnough ? 600 : 400 }}>
                                        {barcode ? formatNumber(stockQty) : ''}
                                    </td>
                                    <td style={{ textAlign: 'right', fontSize: 13, color: 'var(--color-warning)', fontWeight: 600 }}>
                                        {barcode ? formatNumber(defectMap[barcode] || 0) : ''}
                                    </td>
                                    <td>
                                        <input
                                            type="number"
                                            value={row.quantity}
                                            onChange={e => updateRow(i, 'quantity', e.target.value)}
                                            placeholder="0"
                                            min="1"
                                            style={{
                                                width: '100%', background: 'transparent', border: 'none',
                                                padding: '8px 4px', fontSize: 13, textAlign: 'right',
                                                color: notEnough ? 'var(--color-danger)' : 'var(--color-text)', outline: 'none',
                                                fontWeight: notEnough ? 600 : 400,
                                            }}
                                        />
                                    </td>
                                    <td>
                                        {barcode && (
                                            <button
                                                onClick={() => removeRow(i)}
                                                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-muted)', fontSize: 16, padding: '4px 6px' }}
                                                title="Удалить строку"
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
                <button className="btn btn-primary" onClick={handleSubmit} disabled={saving || filledRows.length === 0}>
                    {saving ? 'Обработка...' : `${config.btnLabel} (${filledRows.length} поз.)`}
                </button>
            </div>
        </div>
    );
}

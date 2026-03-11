'use client';
import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';

const fmt = (n: number) => n?.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) ?? '0';

export default function CostPage() {
    const [tab, setTab] = useState<'history' | 'bulk'>('history');

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">💰 Себестоимость</h1>
                    <p className="page-subtitle">Управление себестоимостью товаров — ручной ввод и массовая загрузка</p>
                </div>
            </div>
            <div style={{ display: 'flex', gap: 4, marginBottom: 20 }}>
                <button className={`btn ${tab === 'history' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                    onClick={() => setTab('history')}>📋 История себестоимости</button>
                <button className={`btn ${tab === 'bulk' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                    onClick={() => setTab('bulk')}>📥 Массовая себестоимость</button>
            </div>
            {tab === 'history' && <CostHistory />}
            {tab === 'bulk' && <BulkCost />}
        </div>
    );
}

/* ─── Tab 1: История себестоимости (Cost History / Overrides) ──── */

function CostHistory() {
    const [costs, setCosts] = useState<any>({ overrides: [], missing: [] });
    const [editCost, setEditCost] = useState<{ nm_id: number; cost_price: string } | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => { loadCosts(); }, []);
    const loadCosts = async () => {
        try { setCosts(await api.getFunnelCosts()); } catch { }
        setLoading(false);
    };

    const handleSaveCost = async () => {
        if (!editCost) return;
        try {
            await api.setFunnelCost(editCost.nm_id, parseFloat(editCost.cost_price));
            setEditCost(null);
            loadCosts();
        } catch (e: any) { alert(e.message); }
    };

    if (loading) return <div style={{ padding: 40, color: 'var(--color-text-muted)' }}>Загрузка...</div>;

    return (
        <div>
            {/* Toolbar */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <div style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>
                    Без себестоимости: <b style={{ color: 'var(--color-warning)' }}>{costs.missing?.length || 0}</b> · Установлено: <b style={{ color: 'var(--color-success)' }}>{costs.overrides?.length || 0}</b>
                </div>
                {costs.overrides?.length > 0 && (
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(costs.overrides, 'cost_history')}>📥 Excel</button>
                )}
            </div>

            {/* Missing costs */}
            {costs.missing?.length > 0 && (
                <div className="glass-card" style={{ marginBottom: 16 }}>
                    <h3 style={{ fontSize: 14, fontWeight: 500, marginBottom: 8, padding: '12px 16px 0' }}>
                        ⚠️ Без себестоимости ({costs.missing.length})
                    </h3>
                    <div style={{ overflowX: 'auto' }}>
                        <table className="data-table">
                            <thead><tr><th>nmId</th><th>Артикул</th><th>Предмет</th><th>Бренд</th><th>Себестоимость ₽</th><th></th></tr></thead>
                            <tbody>
                                {costs.missing.map((m: any) => (
                                    <tr key={m.nm_id}>
                                        <td><a href={`https://www.wildberries.ru/catalog/${m.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>{m.nm_id}</a></td>
                                        <td>{m.vendor_code}</td><td>{m.subject}</td><td>{m.brand}</td>
                                        <td>{editCost?.nm_id === m.nm_id ? (
                                            <input type="number" value={editCost.cost_price} autoFocus
                                                onChange={e => setEditCost({ ...editCost, cost_price: e.target.value })}
                                                onKeyDown={e => e.key === 'Enter' && handleSaveCost()}
                                                style={{ width: 100, background: 'var(--color-bg)', border: '1px solid var(--color-accent)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)' }} />
                                        ) : '—'}</td>
                                        <td>{editCost?.nm_id === m.nm_id ? (
                                            <div style={{ display: 'flex', gap: 4 }}>
                                                <button className="btn-primary" onClick={handleSaveCost} style={{ padding: '2px 8px', fontSize: 12 }}>✓</button>
                                                <button className="btn-secondary" onClick={() => setEditCost(null)} style={{ padding: '2px 8px', fontSize: 12 }}>✕</button>
                                            </div>
                                        ) : (
                                            <button className="btn-secondary" onClick={() => setEditCost({ nm_id: m.nm_id, cost_price: '' })} style={{ padding: '2px 8px', fontSize: 12 }}>✏️</button>
                                        )}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {/* Existing overrides */}
            {costs.overrides?.length > 0 && (
                <div className="glass-card">
                    <h3 style={{ fontSize: 14, fontWeight: 500, marginBottom: 8, padding: '12px 16px 0' }}>
                        ✅ Установленные ({costs.overrides.length})
                    </h3>
                    <div style={{ overflowX: 'auto' }}>
                        <table className="data-table">
                            <thead><tr><th>nmId</th><th>Себестоимость ₽</th><th></th></tr></thead>
                            <tbody>
                                {costs.overrides.map((o: any) => (
                                    <tr key={o.nm_id}>
                                        <td><a href={`https://www.wildberries.ru/catalog/${o.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>{o.nm_id}</a></td>
                                        <td>{editCost?.nm_id === o.nm_id ? (
                                            <input type="number" value={editCost.cost_price} autoFocus
                                                onChange={e => setEditCost({ ...editCost, cost_price: e.target.value })}
                                                onKeyDown={e => e.key === 'Enter' && handleSaveCost()}
                                                style={{ width: 100, background: 'var(--color-bg)', border: '1px solid var(--color-accent)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)' }} />
                                        ) : fmt(o.cost_price)}</td>
                                        <td>
                                            <div style={{ display: 'flex', gap: 4 }}>
                                                {editCost?.nm_id === o.nm_id ? (
                                                    <>
                                                        <button className="btn-primary" onClick={handleSaveCost} style={{ padding: '2px 8px', fontSize: 12 }}>✓</button>
                                                        <button className="btn-secondary" onClick={() => setEditCost(null)} style={{ padding: '2px 8px', fontSize: 12 }}>✕</button>
                                                    </>
                                                ) : (
                                                    <button className="btn-secondary" onClick={() => setEditCost({ nm_id: o.nm_id, cost_price: String(o.cost_price) })} style={{ padding: '2px 8px', fontSize: 12 }}>✏️</button>
                                                )}
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {costs.missing?.length === 0 && costs.overrides?.length === 0 && (
                <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                    Нет данных. Дождитесь автоматической синхронизации воронки.
                </div>
            )}
        </div>
    );
}

/* ─── Tab 2: Массовая себестоимость (Bulk Cost) ────────────────── */

interface BulkRow {
    barcode: string;
    cost_price: string;
    currency: string;
    name: string;
}

const emptyRow = (): BulkRow => ({ barcode: '', cost_price: '', currency: 'RUB', name: '' });

function BulkCost() {
    const [rows, setRows] = useState<BulkRow[]>(Array.from({ length: 10 }, emptyRow));
    const [saving, setSaving] = useState(false);
    const [result, setResult] = useState<{ saved: number; not_found: string[] } | null>(null);
    const [nomenclature, setNomenclature] = useState<any[]>([]);
    const [missing, setMissing] = useState<any[]>([]);

    useEffect(() => {
        api.getNomenclature().then(setNomenclature).catch(() => {});
        api.getFunnelCosts().then((data: any) => setMissing(data.missing || [])).catch(() => {});
    }, []);

    const barcodeMap = new Map<string, string>();
    const nmIdMap = new Map<string, string>();
    nomenclature.forEach((n: any) => {
        const label = n.article_seller || n.subject || `nmId: ${n.article_wb}`;
        if (n.barcode) barcodeMap.set(n.barcode, label);
        if (n.article_wb) nmIdMap.set(String(n.article_wb), label);
    });

    const resolveName = (code: string): string => barcodeMap.get(code) || nmIdMap.get(code) || '';
    const isKnown = (code: string): boolean => {
        if (!code.trim()) return true;
        return barcodeMap.has(code) || nmIdMap.has(code);
    };

    const updateRow = (idx: number, field: keyof BulkRow, value: string) => {
        setRows(prev => {
            const next = [...prev];
            next[idx] = { ...next[idx], [field]: value };
            if (field === 'barcode' && value.trim()) {
                next[idx].name = resolveName(value.trim());
            }
            return next;
        });
        if (idx === rows.length - 1) {
            setRows(prev => [...prev, emptyRow()]);
        }
    };

    const filledRows = rows.filter(r => r.barcode.trim() && r.cost_price.trim());
    const invalidRows = filledRows.filter(r => !isKnown(r.barcode.trim()));
    const hasInvalid = invalidRows.length > 0;
    const canSave = filledRows.length > 0 && !hasInvalid;

    const handlePaste = (e: React.ClipboardEvent) => {
        const text = e.clipboardData.getData('text/plain');
        if (!text.includes('\t') && !text.includes('\n')) return;
        e.preventDefault();

        const lines = text.trim().split('\n').map(l => l.split('\t'));
        const newRows: BulkRow[] = [];

        for (const cols of lines) {
            if (cols.length < 2) continue;
            let barcode = '', cost = '', currency = 'RUB', name = '';

            if (cols.length >= 4) {
                name = cols[0].trim();
                barcode = cols[1].trim();
                cost = cols[2].trim().replace(',', '.').replace(/[^\d.]/g, '');
                currency = cols[3].trim().toUpperCase() || 'RUB';
            } else if (cols.length === 3) {
                const second = cols[1].trim();
                const third = cols[2].trim();
                if (['RUB', 'CNY', 'USD', 'EUR'].includes(third.toUpperCase())) {
                    barcode = cols[0].trim();
                    cost = second.replace(',', '.').replace(/[^\d.]/g, '');
                    currency = third.toUpperCase();
                } else {
                    name = cols[0].trim();
                    barcode = second;
                    cost = third.replace(',', '.').replace(/[^\d.]/g, '');
                }
            } else {
                barcode = cols[0].trim();
                cost = cols[1].trim().replace(',', '.').replace(/[^\d.]/g, '');
            }

            if (barcode && cost) {
                if (!name) name = resolveName(barcode);
                newRows.push({ barcode, cost_price: cost, currency, name });
            }
        }

        if (newRows.length > 0) {
            setRows([...newRows, emptyRow(), emptyRow()]);
        }
    };

    const handleFillMissing = () => {
        const newRows: BulkRow[] = missing.map((m: any) => ({
            barcode: String(m.nm_id),
            cost_price: '',
            currency: 'RUB',
            name: m.vendor_code || m.subject || '',
        }));
        setRows([...newRows, emptyRow()]);
    };

    const handleClear = () => {
        setRows(Array.from({ length: 10 }, emptyRow));
        setResult(null);
    };

    const handleSave = async () => {
        if (!filledRows.length) return;
        setSaving(true);
        try {
            const items = filledRows.map(r => ({
                barcode: r.barcode.trim(),
                cost_price: parseFloat(r.cost_price),
                currency: r.currency || 'RUB',
            }));
            const res = await api.bulkSetFunnelCosts(items);
            setResult(res);
        } catch (e: any) { alert(e.message); }
        setSaving(false);
    };

    return (
        <div>
            {/* Instructions */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 20 }}>
                <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 14 }}>💡 Инструкция</div>
                <div style={{ fontSize: 13, lineHeight: 1.8, color: 'var(--color-text-dim)' }}>
                    Скопируйте данные из Google Sheets и вставьте прямо в таблицу ниже (Ctrl+V / Cmd+V).
                    Поддерживаемые форматы:
                    <ul style={{ margin: '4px 0 0', paddingLeft: 20 }}>
                        <li><b>Название, Баркод, Себестоимость</b> — 3 колонки</li>
                        <li><b>Название, Баркод, Себестоимость, Валюта</b> — 4 колонки</li>
                        <li><b>Баркод, Себестоимость</b> — 2 колонки (минимум)</li>
                    </ul>
                </div>
            </div>

            {/* Alerts */}
            {hasInvalid && (
                <div style={{
                    background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)',
                    borderRadius: 8, padding: 12, marginBottom: 12, fontSize: 13, color: '#ef4444',
                }}>
                    ❌ Не найдено в базе: <b>{invalidRows.length}</b> шт. Исправьте или удалите баркоды, чтобы сохранить.
                </div>
            )}
            {result && result.saved > 0 && (
                <div style={{
                    background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.3)',
                    borderRadius: 8, padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-success)',
                }}>
                    ✅ Сохранено: <b>{result.saved}</b> шт.
                </div>
            )}

            {/* Toolbar */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <div style={{ fontSize: 15, fontWeight: 600 }}>
                    Данные
                    <span style={{ fontWeight: 400, fontSize: 13, color: 'var(--color-text-dim)', marginLeft: 10 }}>
                        Заполнено строк: {filledRows.length}
                    </span>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    {missing.length > 0 && (
                        <button className="btn btn-secondary btn-sm" onClick={handleFillMissing}>
                            ⚠️ Без себестоимости ({missing.length})
                        </button>
                    )}
                    <button className="btn btn-secondary btn-sm" onClick={handleClear}>
                        🗑 Очистить всё
                    </button>
                </div>
            </div>

            {/* Spreadsheet Table */}
            <div
                className="glass-card"
                style={{ overflow: 'auto', maxHeight: 'calc(100vh - 420px)', padding: 0 }}
                onPaste={handlePaste}
            >
                <table className="data-table" style={{ marginBottom: 0 }}>
                    <thead>
                        <tr>
                            <th style={{ width: 45, textAlign: 'center' }}>#</th>
                            <th style={{ minWidth: 250 }}>Название товара</th>
                            <th style={{ minWidth: 180 }}>Код товара / Баркод</th>
                            <th style={{ width: 140 }}>Себестоимость</th>
                            <th style={{ width: 90 }}>Валюта</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row, i) => {
                            const bc = row.barcode.trim();
                            const unknown = bc && !isKnown(bc);
                            const rowBg = unknown ? 'rgba(239,68,68,0.06)' : undefined;
                            return (
                                <tr key={i} style={{ background: rowBg }}>
                                    <td style={{ fontSize: 12, color: 'var(--color-text-dim)', textAlign: 'center' }}>{i + 1}</td>
                                    <td>
                                        <input
                                            value={row.name}
                                            readOnly
                                            tabIndex={-1}
                                            placeholder="—"
                                            style={{
                                                width: '100%', background: 'transparent', border: 'none',
                                                color: row.name ? 'var(--color-text-dim)' : 'var(--color-text-muted)',
                                                fontSize: 13, padding: '6px 4px', outline: 'none',
                                            }}
                                        />
                                    </td>
                                    <td>
                                        <input
                                            value={row.barcode}
                                            onChange={e => updateRow(i, 'barcode', e.target.value)}
                                            placeholder="Баркод или nmId"
                                            style={{
                                                width: '100%', background: 'var(--color-bg)',
                                                border: `1px solid ${unknown ? '#ef4444' : 'var(--color-border)'}`,
                                                borderRadius: 6, padding: '6px 8px', fontSize: 13,
                                                color: unknown ? '#ef4444' : 'var(--color-text)',
                                            }}
                                        />
                                    </td>
                                    <td>
                                        <input
                                            type="number"
                                            value={row.cost_price}
                                            onChange={e => updateRow(i, 'cost_price', e.target.value)}
                                            placeholder="0"
                                            style={{
                                                width: '100%', background: 'var(--color-bg)',
                                                border: '1px solid var(--color-border)',
                                                borderRadius: 6, padding: '6px 8px', fontSize: 13,
                                                color: 'var(--color-text)',
                                            }}
                                        />
                                    </td>
                                    <td>
                                        <select
                                            value={row.currency}
                                            onChange={e => updateRow(i, 'currency', e.target.value)}
                                            style={{
                                                width: '100%', background: 'var(--color-bg)',
                                                border: '1px solid var(--color-border)',
                                                borderRadius: 6, padding: '6px 4px', fontSize: 12,
                                                color: 'var(--color-text)',
                                            }}
                                        >
                                            <option>RUB</option>
                                            <option>CNY</option>
                                            <option>USD</option>
                                        </select>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>

            {/* Footer */}
            <div style={{
                position: 'sticky', bottom: 0, background: 'var(--color-bg)',
                padding: '12px 0', display: 'flex', justifyContent: 'flex-end', gap: 10,
                borderTop: '1px solid var(--color-border)', marginTop: 8,
            }}>
                <button
                    className="btn btn-primary"
                    onClick={handleSave}
                    disabled={saving || !canSave}
                    style={{ minWidth: 160, padding: '10px 20px', fontSize: 14, opacity: canSave ? 1 : 0.5 }}
                >
                    {saving ? '⏳ Сохранение...' : hasInvalid ? `❌ Есть ошибки (${invalidRows.length})` : `💾 Сохранить (${filledRows.length})`}
                </button>
            </div>
        </div>
    );
}

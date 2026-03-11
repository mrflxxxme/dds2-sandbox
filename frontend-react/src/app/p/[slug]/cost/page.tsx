'use client';
import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';

export default function CostPage() {
    const [tab, setTab] = useState<'history' | 'bulk'>('history');

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">💰 Себестоимость</h1>
                    <p className="page-subtitle">История себестоимости по заказам и массовая загрузка</p>
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

/* ─── Tab 1: История себестоимости (from Reports) ──── */

function CostHistory() {
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [search, setSearch] = useState('');
    const [brand, setBrand] = useState('');

    const load = React.useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const r = await api.getCostHistory(search || undefined, brand || undefined);
            setData(r);
        } catch (e: any) { setError(e.message || 'Ошибка'); }
        setLoading(false);
    }, [search, brand]);

    useEffect(() => { load(); }, [load]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>⏳ Загрузка...</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--danger)' }}>❌ {error}</div>;
    if (!data || !data.articles?.length) return <div className="glass-card" style={{ padding: 32, textAlign: 'center', opacity: 0.6 }}>Нет данных о себестоимости</div>;

    const orders: Array<{ order_no: string; ship_date: string }> = data.orders || [];
    const articles: any[] = data.articles || [];
    const brands: string[] = data.brands || [];

    const handleExport = () => {
        const rows = articles.map((a: any) => {
            const row: Record<string, any> = {
                'Артикул': a.article_seller,
                'Артикул WB': a.article_wb || '',
                'Баркод': a.barcode,
                'Бренд': a.brand || '',
                'Категория': a.subject,
            };
            orders.forEach((o: any) => {
                const c = a.costs?.[o.order_no];
                row[`Заказ ${o.order_no}`] = c ? c.cost : '';
                row[`Кол-во ${o.order_no}`] = c ? c.qty : '';
            });
            row['Средняя'] = a.avg_cost;
            row['Последняя'] = a.latest_cost;
            return row;
        });
        exportToExcel(rows, `Себестоимость`);
    };

    const selectStyle: React.CSSProperties = {
        padding: '8px 12px', borderRadius: 8,
        border: '1px solid var(--border)',
        background: 'var(--bg-secondary)', color: 'var(--text-primary)',
    };

    return (
        <div>
            <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                <input
                    type="text" placeholder="🔍 Поиск по артикулу / WB артикулу"
                    value={search}
                    onChange={(e: any) => setSearch(e.target.value)}
                    onKeyDown={(e: any) => e.key === 'Enter' && load()}
                    style={{ ...selectStyle, width: 280 }}
                />
                <select value={brand} onChange={(e: any) => setBrand(e.target.value)} style={selectStyle}>
                    <option value="">Все бренды</option>
                    {brands.map((b: string) => <option key={b} value={b}>{b}</option>)}
                </select>
                <button className="btn btn-secondary btn-sm" onClick={load}>🔄</button>
                <button className="btn btn-secondary btn-sm" onClick={handleExport}>📥 Excel</button>
                <span style={{ opacity: 0.5, fontSize: 13 }}>{articles.length} артикулов × {orders.length} заказов</span>
            </div>

            <div className="glass-card" style={{ overflow: 'auto', maxHeight: 'calc(100vh - 280px)' }}>
                <table className="data-table" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>
                    <thead>
                        <tr>
                            <th style={{ position: 'sticky', left: 0, background: 'var(--bg-secondary)', zIndex: 2, minWidth: 180 }}>Артикул</th>
                            <th style={{ minWidth: 100 }}>WB Артикул</th>
                            <th style={{ minWidth: 80 }}>Бренд</th>
                            <th style={{ minWidth: 100 }}>Категория</th>
                            <th style={{ textAlign: 'right', fontWeight: 700, color: 'var(--primary)' }}>Средняя ₽</th>
                            <th style={{ textAlign: 'right', fontWeight: 700, color: 'var(--success)' }}>Последняя ₽</th>
                            {orders.map((o: any) => (
                                <th key={o.order_no} style={{ textAlign: 'right', minWidth: 100 }}>
                                    <div>{o.order_no}</div>
                                    <div style={{ fontSize: 10, opacity: 0.5 }}>{o.ship_date ? formatDate(o.ship_date) : ''}</div>
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {articles.map((a: any, i: number) => {
                            return (
                                <tr key={i}>
                                    <td style={{ position: 'sticky', left: 0, background: 'var(--bg-primary)', zIndex: 1, fontWeight: 600 }}>{a.article_seller}</td>
                                    <td style={{ opacity: 0.7, fontSize: 12 }}>{a.article_wb || '—'}</td>
                                    <td><span className="badge badge-info" style={{ fontSize: 11 }}>{a.brand || '—'}</span></td>
                                    <td style={{ opacity: 0.7 }}>{a.subject || '—'}</td>
                                    <td style={{ textAlign: 'right', fontWeight: 600, color: 'var(--primary)' }}>{a.avg_cost ? formatNumber(a.avg_cost) : '—'}</td>
                                    <td style={{ textAlign: 'right', fontWeight: 600, color: 'var(--success)' }}>{a.latest_cost ? formatNumber(a.latest_cost) : '—'}</td>
                                    {orders.map((o: any, j: number) => {
                                        const c = a.costs?.[o.order_no];
                                        if (!c) return <td key={j} style={{ textAlign: 'right', opacity: 0.2 }}>—</td>;
                                        const prev = j < orders.length - 1 ? (a.costs?.[orders[j + 1]?.order_no]?.cost || 0) : 0;
                                        const diff = prev > 0 ? ((c.cost - prev) / prev * 100) : 0;
                                        const color = diff > 5 ? 'var(--danger)' : diff < -5 ? 'var(--success)' : 'var(--text-primary)';
                                        return (
                                            <td key={j} style={{ textAlign: 'right', color }}>
                                                <div>{formatNumber(c.cost)}</div>
                                                {diff !== 0 && <div style={{ fontSize: 10, opacity: 0.6 }}>{diff > 0 ? '↑' : '↓'}{Math.abs(diff).toFixed(1)}%</div>}
                                                <div style={{ fontSize: 10, opacity: 0.4 }}>{c.qty} шт</div>
                                            </td>
                                        );
                                    })}
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
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
        // Load missing from BDR (same source as "⚠️ Товары без себестоимости" in БДР report)
        const fmt = (d: Date) => d.toISOString().split('T')[0];
        const now = new Date();
        const day = now.getDay();
        const diffToLastSun = day === 0 ? 7 : day;
        const lastSun = new Date(now); lastSun.setDate(now.getDate() - diffToLastSun);
        const lastMon = new Date(lastSun); lastMon.setDate(lastSun.getDate() - 6);
        api.getWbBdr(fmt(lastMon), fmt(lastSun)).then((data: any) => {
            const articles = data?.articles || [];
            const noCost = articles.filter((a: any) => (!a.cost_price || a.cost_price === 0) && a.nm_id && a.nm_id !== 0);
            setMissing(noCost.map((a: any) => ({
                nm_id: a.nm_id,
                vendor_code: a.sa_name || '',
                subject: a.subject || '',
                brand: a.brand || '',
            })));
        }).catch(() => {});
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

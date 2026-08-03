'use client';
import React, { useCallback, useEffect, useState, ChangeEvent, KeyboardEvent } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { usePermissions } from '@/lib/hooks/usePermissions';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import type {
    CostHistoryResponse, CostHistoryArticle, CostHistoryOrder,
    Nomenclature, SkuValuation, MonthlyCogs, CostLayer, ValuationMethod, ValuationSummaryRow,
} from '@/types/api';

export default function CostPage() {
    const [tab, setTab] = useState<'history' | 'bulk' | 'analytics'>('history');

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">💰 Себестоимость</h1>
                    <p className="page-subtitle">История себестоимости по заказам, массовая загрузка и аналитика SKU</p>
                </div>
            </div>
            <div style={{ display: 'flex', gap: 4, marginBottom: 20 }}>
                <button className={`btn ${tab === 'history' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                    onClick={() => setTab('history')}>📋 История себестоимости</button>
                <button className={`btn ${tab === 'bulk' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                    onClick={() => setTab('bulk')}>📥 Массовая себестоимость</button>
                <button className={`btn ${tab === 'analytics' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                    onClick={() => setTab('analytics')}>📊 Аналитика SKU</button>
            </div>
            {tab === 'history' && <CostHistory />}
            {tab === 'bulk' && <BulkCost />}
            {tab === 'analytics' && <SkuAnalytics />}
        </div>
    );
}

/* ─── Tab 1: История себестоимости (from Reports) ──── */

function CostHistory() {
    const [data, setData] = useState<CostHistoryResponse|null>(null);
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
        } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Ошибка'); }
        setLoading(false);
    }, [search, brand]);

    useEffect(() => { load(); }, [load]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>⏳ Загрузка...</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>❌ {error}</div>;
    if (!data || !data.articles?.length) return <div className="glass-card" style={{ padding: 32, textAlign: 'center', opacity: 0.6 }}>Нет данных о себестоимости</div>;

    const orders: CostHistoryOrder[] = data.orders || [];
    const articles: CostHistoryArticle[] = data.articles || [];
    const brands: string[] = data.brands || [];

    const handleExport = () => {
        const rows = articles.map((a: CostHistoryArticle) => {
            const row: Record<string, string | number | null> = {
                'Артикул': a.article_seller,
                'Артикул WB': a.article_wb || '',
                'Баркод': a.barcode,
                'Бренд': a.brand || '',
                'Категория': a.subject,
            };
            orders.forEach((o: CostHistoryOrder) => {
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
        border: '1px solid var(--color-border)',
        background: 'var(--color-bg-input)', color: 'var(--color-text)',
    };

    return (
        <div>
            <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                <input
                    type="text" placeholder="🔍 Поиск по артикулу / WB артикулу"
                    value={search}
                    onChange={(e: ChangeEvent<HTMLInputElement>) => setSearch(e.target.value)}
                    onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => e.key === 'Enter' && load()}
                    style={{ ...selectStyle, width: 280 }}
                />
                <select value={brand} onChange={(e: ChangeEvent<HTMLSelectElement>) => setBrand(e.target.value)} style={selectStyle}>
                    <option value="">Все бренды</option>
                    {brands.map((b: string) => <option key={b} value={b}>{b}</option>)}
                </select>
                <button className="btn btn-secondary btn-sm" onClick={load}>🔄</button>
                <button className="btn btn-secondary btn-sm" onClick={handleExport}>📥 Excel</button>
                <span style={{ opacity: 0.5, fontSize: 13 }}>{articles.length} артикулов × {orders.length} заказов</span>
            </div>

            {/* TODO: migrate to TanStackDataTable — complex table with dynamic order columns and sticky first column */}
            <div className="glass-card" style={{ overflow: 'auto', maxHeight: 'calc(100vh - 280px)' }}>
                <table className="data-table" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>
                    <thead>
                        <tr>
                            {/* Фон не задаём: .data-table thead th уже даёт frost-подложку */}
                            <th style={{ position: 'sticky', left: 0, zIndex: 2, minWidth: 180 }}>Артикул</th>
                            <th style={{ minWidth: 100 }}>WB Артикул</th>
                            <th style={{ minWidth: 80 }}>Бренд</th>
                            <th style={{ minWidth: 100 }}>Категория</th>
                            <th style={{ textAlign: 'right', fontWeight: 700, color: 'var(--color-accent)' }}>Средняя ₽</th>
                            <th style={{ textAlign: 'right', fontWeight: 700, color: 'var(--color-success)' }}>Последняя ₽</th>
                            {orders.map((o: CostHistoryOrder) => (
                                <th key={o.order_no} style={{ textAlign: 'right', minWidth: 100 }}>
                                    <div>{o.order_no}</div>
                                    <div style={{ fontSize: 10, opacity: 0.5 }}>{o.ship_date ? formatDate(o.ship_date) : ''}</div>
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {articles.map((a: CostHistoryArticle, i: number) => {
                            return (
                                <tr key={i}>
                                    <td style={{ position: 'sticky', left: 0, background: 'var(--color-bg)', zIndex: 1, fontWeight: 600 }}>{a.article_seller}</td>
                                    <td style={{ opacity: 0.7, fontSize: 12 }}>{a.article_wb || '—'}</td>
                                    <td><span className="badge badge-info" style={{ fontSize: 11 }}>{a.brand || '—'}</span></td>
                                    <td style={{ opacity: 0.7 }}>{a.subject || '—'}</td>
                                    <td style={{ textAlign: 'right', fontWeight: 600, color: 'var(--color-accent)' }}>{a.avg_cost ? formatNumber(a.avg_cost) : '—'}</td>
                                    <td style={{ textAlign: 'right', fontWeight: 600, color: 'var(--color-success)' }}>{a.latest_cost ? formatNumber(a.latest_cost) : '—'}</td>
                                    {orders.map((o: CostHistoryOrder, j: number) => {
                                        const c = a.costs?.[o.order_no];
                                        if (!c) return <td key={j} style={{ textAlign: 'right', opacity: 0.2 }}>—</td>;
                                        const prev = j < orders.length - 1 ? (a.costs?.[orders[j + 1]?.order_no]?.cost || 0) : 0;
                                        const diff = prev > 0 ? ((c.cost - prev) / prev * 100) : 0;
                                        const color = diff > 5 ? 'var(--color-danger)' : diff < -5 ? 'var(--color-success)' : 'var(--color-text)';
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

import type { MissingCostItem } from '@/types/api';

interface PasteRow {
    barcode: string;
    cost_price: string;
    currency: string;
    name: string;
}

const emptyRow = (): PasteRow => ({ barcode: '', cost_price: '', currency: 'RUB', name: '' });

function BulkCost() {
    const [missing, setMissing] = useState<MissingCostItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [result, setResult] = useState<{ saved: number; not_found: string[] } | null>(null);
    const [costs, setCosts] = useState<Record<number, string>>({});
    const [search, setSearch] = useState('');
    // Paste-from-spreadsheet state
    const [pasteRows, setPasteRows] = useState<PasteRow[]>(Array.from({ length: 5 }, emptyRow));
    const [pasteResult, setPasteResult] = useState<{ saved: number; not_found: string[] } | null>(null);
    const [pasteSaving, setPasteSaving] = useState(false);
    const [nomenclature, setNomenclature] = useState<Nomenclature[]>([]);

    const loadMissing = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const data = await api.getMissingCosts();
            setMissing(data);
        } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Ошибка'); }
        setLoading(false);
    }, []);

    useEffect(() => {
        loadMissing();
        api.getNomenclature().then(setNomenclature).catch(() => {});
    }, [loadMissing]);

    // Nomenclature lookup for paste section
    const barcodeMap = new Map<string, string>();
    const nmIdMap = new Map<string, string>();
    nomenclature.forEach((n: Nomenclature) => {
        const label = n.article_seller || n.subject || `nmId: ${n.article_wb}`;
        if (n.barcode) barcodeMap.set(n.barcode, label);
        if (n.article_wb) nmIdMap.set(String(n.article_wb), label);
    });
    const resolveName = (code: string): string => barcodeMap.get(code) || nmIdMap.get(code) || '';
    const isKnown = (code: string): boolean => !code.trim() || barcodeMap.has(code) || nmIdMap.has(code);

    // ─── Missing costs table handlers ───
    const setCost = (nmId: number, value: string) => {
        setCosts(prev => ({ ...prev, [nmId]: value }));
    };

    const filledCount = Object.values(costs).filter(v => v.trim() && parseFloat(v) > 0).length;

    const handleSave = async () => {
        const items = Object.entries(costs)
            .filter(([, v]) => v.trim() && parseFloat(v) > 0)
            .map(([nmId, v]) => ({
                barcode: nmId,
                cost_price: parseFloat(v),
                currency: 'RUB',
            }));
        if (!items.length) return;
        setSaving(true);
        setResult(null);
        try {
            const res = await api.bulkSetFunnelCosts(items);
            setResult(res);
            setCosts({});
            await loadMissing();
        } catch (e: unknown) { alert(e instanceof Error ? e.message : 'Ошибка'); }
        setSaving(false);
    };

    const filtered = missing.filter(m => {
        if (!search.trim()) return true;
        const s = search.toLowerCase();
        return (
            String(m.nm_id).includes(s) ||
            m.barcode.toLowerCase().includes(s) ||
            m.vendor_code.toLowerCase().includes(s) ||
            m.subject.toLowerCase().includes(s) ||
            m.brand.toLowerCase().includes(s)
        );
    });

    const totalOrdersSum = missing.reduce((s, m) => s + m.total_orders, 0);

    const handleExport = () => {
        const data = missing.map((m, i) => ({
            '#': i + 1,
            'nm_id': m.nm_id,
            'Баркод': m.barcode || '',
            'Артикул': m.vendor_code,
            'Категория': m.subject,
            'Бренд': m.brand,
            'Заказы (руб)': m.total_orders,
            'Кол-во заказов': m.total_qty,
            'Дней в воронке': m.days_count,
            'Текущая с/с': m.current_cost || 0,
            'Средняя цена': m.avg_price || 0,
            'Подозрительно': m.is_suspicious ? 'да' : '',
        }));
        exportToExcel(data, 'Товары_без_себестоимости');
    };

    // ─── Paste-from-spreadsheet handlers ───
    const updatePasteRow = (idx: number, field: keyof PasteRow, value: string) => {
        setPasteRows(prev => {
            const next = [...prev];
            next[idx] = { ...next[idx], [field]: value };
            if (field === 'barcode' && value.trim()) next[idx].name = resolveName(value.trim());
            return next;
        });
        if (idx === pasteRows.length - 1) setPasteRows(prev => [...prev, emptyRow()]);
    };

    const pasteFilledRows = pasteRows.filter(r => r.barcode.trim() && r.cost_price.trim());
    const pasteInvalidRows = pasteFilledRows.filter(r => !isKnown(r.barcode.trim()));
    const pasteCanSave = pasteFilledRows.length > 0 && pasteInvalidRows.length === 0;

    const handlePaste = (e: React.ClipboardEvent) => {
        const text = e.clipboardData.getData('text/plain');
        if (!text.includes('\t') && !text.includes('\n')) return;
        e.preventDefault();
        const lines = text.trim().split('\n').map(l => l.split('\t'));
        const newRows: PasteRow[] = [];
        for (const cols of lines) {
            if (cols.length < 2) continue;
            let barcode = '', cost = '', currency = 'RUB', name = '';
            if (cols.length >= 4) {
                name = cols[0].trim(); barcode = cols[1].trim();
                cost = cols[2].trim().replace(',', '.').replace(/[^\d.]/g, '');
                currency = cols[3].trim().toUpperCase() || 'RUB';
            } else if (cols.length === 3) {
                const third = cols[2].trim();
                if (['RUB', 'CNY', 'USD', 'EUR'].includes(third.toUpperCase())) {
                    barcode = cols[0].trim(); cost = cols[1].trim().replace(',', '.').replace(/[^\d.]/g, '');
                    currency = third.toUpperCase();
                } else {
                    name = cols[0].trim(); barcode = cols[1].trim();
                    cost = third.replace(',', '.').replace(/[^\d.]/g, '');
                }
            } else {
                barcode = cols[0].trim(); cost = cols[1].trim().replace(',', '.').replace(/[^\d.]/g, '');
            }
            if (barcode && cost) {
                if (!name) name = resolveName(barcode);
                newRows.push({ barcode, cost_price: cost, currency, name });
            }
        }
        if (newRows.length > 0) setPasteRows([...newRows, emptyRow(), emptyRow()]);
    };

    const handlePasteSave = async () => {
        if (!pasteFilledRows.length) return;
        setPasteSaving(true);
        setPasteResult(null);
        try {
            const items = pasteFilledRows.map(r => ({
                barcode: r.barcode.trim(), cost_price: parseFloat(r.cost_price), currency: r.currency || 'RUB',
            }));
            const res = await api.bulkSetFunnelCosts(items);
            setPasteResult(res);
            setPasteRows(Array.from({ length: 5 }, emptyRow));
            await loadMissing();
        } catch (e: unknown) { alert(e instanceof Error ? e.message : 'Ошибка'); }
        setPasteSaving(false);
    };

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;

    return (
        <div>
            {/* ─── Section 1: Missing costs table ─── */}
            {missing.length > 0 ? (
                <div style={{
                    background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)',
                    borderRadius: 8, padding: 14, marginBottom: 16, fontSize: 13,
                }}>
                    <b>{missing.length}</b> товаров без или с подозрительно низкой себестоимостью участвуют в расчётах.
                    Суммарные заказы: <b>{formatNumber(totalOrdersSum)}</b>.
                    Прибыль и маржинальность завышены для этих товаров.
                    {missing.some(m => m.is_suspicious) && (
                        <div style={{ marginTop: 6, fontSize: 12, color: 'var(--color-text-dim)' }}>
                            Подозрительно низкая = с/с меньше 5% от средней цены продажи.
                        </div>
                    )}
                </div>
            ) : (
                <div style={{
                    background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.3)',
                    borderRadius: 8, padding: 14, marginBottom: 16, fontSize: 13, color: 'var(--color-success)',
                }}>
                    Все товары в расчётах имеют себестоимость.
                </div>
            )}

            {result && result.saved > 0 && (
                <div style={{
                    background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.3)',
                    borderRadius: 8, padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-success)',
                }}>
                    Сохранено: <b>{result.saved}</b> шт.
                </div>
            )}

            {missing.length > 0 && (
                <>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
                        <input
                            type="text" placeholder="Поиск по nm_id, баркоду, артикулу, категории..."
                            value={search} onChange={e => setSearch(e.target.value)}
                            style={{
                                padding: '8px 12px', borderRadius: 8, border: '1px solid var(--color-border)',
                                background: 'var(--color-bg-input)', color: 'var(--color-text)', width: 320,
                            }}
                        />
                        <div style={{ display: 'flex', gap: 8 }}>
                            <button className="btn btn-secondary btn-sm" onClick={handleExport}>
                                Excel ({missing.length})
                            </button>
                            <button className="btn btn-secondary btn-sm" onClick={loadMissing}>
                                Обновить
                            </button>
                        </div>
                    </div>

                    {/* TODO: migrate to TanStackDataTable — table with inline <input> for cost entry */}
                    <div className="glass-card" style={{ overflow: 'auto', maxHeight: 'calc(100vh - 520px)', padding: 0 }}>
                        <table className="data-table" style={{ marginBottom: 0, fontSize: 13 }}>
                            <thead>
                                <tr>
                                    <th style={{ width: 40, textAlign: 'center' }}>#</th>
                                    <th style={{ minWidth: 110 }}>nm_id</th>
                                    <th style={{ minWidth: 130 }}>Баркод</th>
                                    <th style={{ minWidth: 160 }}>Артикул</th>
                                    <th style={{ minWidth: 120 }}>Категория</th>
                                    <th style={{ minWidth: 100 }}>Бренд</th>
                                    <th style={{ textAlign: 'right', minWidth: 130 }}>Заказы</th>
                                    <th style={{ textAlign: 'right', minWidth: 70 }}>Шт</th>
                                    <th style={{ textAlign: 'right', minWidth: 120 }}>Текущая с/с</th>
                                    <th style={{ width: 130 }}>Новая с/с</th>
                                </tr>
                            </thead>
                            <tbody>
                                {filtered.map((m, i) => (
                                    <tr key={m.nm_id}>
                                        <td style={{ fontSize: 11, color: 'var(--color-text-dim)', textAlign: 'center' }}>{i + 1}</td>
                                        <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{m.nm_id}</td>
                                        <td style={{ fontFamily: 'monospace', fontSize: 12, opacity: m.barcode ? 1 : 0.3 }}>{m.barcode || '—'}</td>
                                        <td style={{ fontWeight: 500 }}>{m.vendor_code || '—'}</td>
                                        <td><span className="badge badge-info" style={{ fontSize: 11 }}>{m.subject}</span></td>
                                        <td style={{ opacity: 0.7 }}>{m.brand}</td>
                                        <td style={{ textAlign: 'right', fontWeight: 600 }}>{formatNumber(m.total_orders)}</td>
                                        <td style={{ textAlign: 'right', opacity: 0.7 }}>{m.total_qty}</td>
                                        <td style={{ textAlign: 'right' }}>
                                            {m.is_suspicious ? (
                                                <span
                                                    className="badge badge-warning"
                                                    style={{ fontSize: 11 }}
                                                    title={m.avg_price > 0 ? `Средняя цена: ${formatNumber(m.avg_price)}` : undefined}
                                                >
                                                    ⚠ {formatNumber(m.current_cost)}
                                                </span>
                                            ) : (
                                                <span style={{ opacity: 0.4 }}>—</span>
                                            )}
                                        </td>
                                        <td>
                                            <input
                                                type="number"
                                                value={costs[m.nm_id] || ''}
                                                onChange={e => setCost(m.nm_id, e.target.value)}
                                                placeholder="0"
                                                style={{
                                                    width: '100%', background: 'var(--color-bg)',
                                                    border: '1px solid var(--color-border)',
                                                    borderRadius: 6, padding: '5px 8px', fontSize: 13,
                                                    color: 'var(--color-text)',
                                                }}
                                            />
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    <div style={{
                        padding: '12px 0', display: 'flex', justifyContent: 'flex-end', gap: 10,
                        borderBottom: '1px solid var(--color-border)', marginBottom: 24,
                    }}>
                        <button
                            className="btn btn-primary"
                            onClick={handleSave}
                            disabled={saving || filledCount === 0}
                            style={{ minWidth: 160, padding: '10px 20px', fontSize: 14, opacity: filledCount > 0 ? 1 : 0.5 }}
                        >
                            {saving ? 'Сохранение...' : `Сохранить (${filledCount})`}
                        </button>
                    </div>
                </>
            )}

            {/* ─── Section 2: Paste from spreadsheet ─── */}
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>
                Загрузить себестоимость вручную
            </div>
            <div className="glass-card" style={{ padding: 14, marginBottom: 16, fontSize: 13, lineHeight: 1.8, color: 'var(--color-text-dim)' }}>
                Скопируйте данные из Google Sheets / Excel и вставьте в таблицу ниже (Ctrl+V / Cmd+V).
                Форматы: <b>Баркод, Себестоимость</b> | <b>Название, Баркод, Себестоимость</b> | <b>+ Валюта</b> (4 колонки).
            </div>

            {pasteInvalidRows.length > 0 && (
                <div style={{
                    background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)',
                    borderRadius: 8, padding: 12, marginBottom: 12, fontSize: 13, color: '#ef4444',
                }}>
                    Не найдено в базе: <b>{pasteInvalidRows.length}</b> шт.
                </div>
            )}
            {pasteResult && pasteResult.saved > 0 && (
                <div style={{
                    background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.3)',
                    borderRadius: 8, padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-success)',
                }}>
                    Сохранено: <b>{pasteResult.saved}</b> шт.
                </div>
            )}

            {/* TODO: migrate to TanStackDataTable — paste-from-spreadsheet table with inline <input>/<select> */}
            <div
                className="glass-card"
                style={{ overflow: 'auto', maxHeight: 320, padding: 0 }}
                onPaste={handlePaste}
            >
                <table className="data-table" style={{ marginBottom: 0 }}>
                    <thead>
                        <tr>
                            <th style={{ width: 45, textAlign: 'center' }}>#</th>
                            <th style={{ minWidth: 200 }}>Название</th>
                            <th style={{ minWidth: 180 }}>Баркод / nmId</th>
                            <th style={{ width: 140 }}>Себестоимость</th>
                            <th style={{ width: 90 }}>Валюта</th>
                        </tr>
                    </thead>
                    <tbody>
                        {pasteRows.map((row, i) => {
                            const bc = row.barcode.trim();
                            const unknown = bc && !isKnown(bc);
                            return (
                                <tr key={i} style={{ background: unknown ? 'rgba(239,68,68,0.06)' : undefined }}>
                                    <td style={{ fontSize: 12, color: 'var(--color-text-dim)', textAlign: 'center' }}>{i + 1}</td>
                                    <td>
                                        <input value={row.name} readOnly tabIndex={-1} placeholder="—"
                                            style={{ width: '100%', background: 'transparent', border: 'none',
                                                color: row.name ? 'var(--color-text-dim)' : 'var(--color-text-muted)',
                                                fontSize: 13, padding: '6px 4px', outline: 'none' }} />
                                    </td>
                                    <td>
                                        <input value={row.barcode} onChange={e => updatePasteRow(i, 'barcode', e.target.value)}
                                            placeholder="Баркод или nmId"
                                            style={{ width: '100%', background: 'var(--color-bg)',
                                                border: `1px solid ${unknown ? '#ef4444' : 'var(--color-border)'}`,
                                                borderRadius: 6, padding: '6px 8px', fontSize: 13,
                                                color: unknown ? '#ef4444' : 'var(--color-text)' }} />
                                    </td>
                                    <td>
                                        <input type="number" value={row.cost_price}
                                            onChange={e => updatePasteRow(i, 'cost_price', e.target.value)}
                                            placeholder="0"
                                            style={{ width: '100%', background: 'var(--color-bg)',
                                                border: '1px solid var(--color-border)',
                                                borderRadius: 6, padding: '6px 8px', fontSize: 13,
                                                color: 'var(--color-text)' }} />
                                    </td>
                                    <td>
                                        <select value={row.currency} onChange={e => updatePasteRow(i, 'currency', e.target.value)}
                                            style={{ width: '100%', background: 'var(--color-bg)',
                                                border: '1px solid var(--color-border)',
                                                borderRadius: 6, padding: '6px 4px', fontSize: 12,
                                                color: 'var(--color-text)' }}>
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

            <div style={{
                position: 'sticky', bottom: 0, background: 'var(--color-bg)',
                padding: '12px 0', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                borderTop: '1px solid var(--color-border)', marginTop: 8,
            }}>
                <button className="btn btn-secondary btn-sm"
                    onClick={() => { setPasteRows(Array.from({ length: 5 }, emptyRow)); setPasteResult(null); }}>
                    Очистить
                </button>
                <button
                    className="btn btn-primary"
                    onClick={handlePasteSave}
                    disabled={pasteSaving || !pasteCanSave}
                    style={{ minWidth: 160, padding: '10px 20px', fontSize: 14, opacity: pasteCanSave ? 1 : 0.5 }}
                >
                    {pasteSaving ? 'Сохранение...' : pasteInvalidRows.length > 0
                        ? `Есть ошибки (${pasteInvalidRows.length})`
                        : `Сохранить (${pasteFilledRows.length})`}
                </button>
            </div>
        </div>
    );
}

/* ─── Tab 3: Аналитика SKU (FIFO / Скользящая / Пожизненная) ──────────────── */

const METHOD_LABELS: Record<ValuationMethod, string> = {
    fifo: 'FIFO',
    moving_avg: 'Скользящая',
    lifetime_avg: 'Пожизненная',
};
const METHOD_ORDER: ValuationMethod[] = ['fifo', 'moving_avg', 'lifetime_avg'];

/** monthly COGS field for a given preview method. */
function monthlyCogs(m: MonthlyCogs, method: ValuationMethod): number {
    if (method === 'fifo') return m.cogs_fifo;
    if (method === 'moving_avg') return m.cogs_moving;
    return m.cogs_avg; // lifetime_avg
}

function SkuAnalytics() {
    // Nomenclature for the SKU picker (barcode / article search)
    const [nomenclature, setNomenclature] = useState<Nomenclature[]>([]);
    const [query, setQuery] = useState('');
    const [barcode, setBarcode] = useState<string>('');

    // Drill-down data
    const [val, setVal] = useState<SkuValuation | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    // Preview method (client-side only — does NOT call set_valuation_method)
    const [previewMethod, setPreviewMethod] = useState<ValuationMethod>('lifetime_avg');

    // Saved (applied-to-all-reports) method
    const [savedMethod, setSavedMethod] = useState<ValuationMethod | null>(null);
    const [applying, setApplying] = useState(false);

    // Project-wide data-start cutoff — sales before this date are ignored
    // (incomplete early periods, e.g. Dec 2025).
    const [cutoff, setCutoff] = useState<string>('');
    const [savingCutoff, setSavingCutoff] = useState(false);

    // Bump to refetch the current SKU after an edit (cutoff / arrival / opening).
    const [reloadKey, setReloadKey] = useState(0);
    const reload = () => setReloadKey(k => k + 1);

    const saveCutoff = async (value: string | null) => {
        setSavingCutoff(true);
        try {
            const r = await api.setValuationStart(value);
            setCutoff(r.start_date || '');
            reload();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка');
        }
        setSavingCutoff(false);
    };

    // Load nomenclature + current saved method once
    useEffect(() => {
        const controller = new AbortController();
        api.getNomenclature().then(rows => {
            if (controller.signal.aborted) return;
            setNomenclature(rows);
        }).catch(() => {});
        api.getValuationMethod().then(r => {
            if (controller.signal.aborted) return;
            setSavedMethod(r.method);
            setPreviewMethod(r.method);
        }).catch(() => {});
        api.getValuationStart().then(r => {
            if (controller.signal.aborted) return;
            setCutoff(r.start_date || '');
        }).catch(() => {});
        return () => controller.abort();
    }, []);

    // Load valuation for the selected barcode
    useEffect(() => {
        if (!barcode) { setVal(null); setError(''); return; }
        const controller = new AbortController();
        setLoading(true);
        setError('');
        api.getSkuValuation(barcode)
            .then(r => {
                if (controller.signal.aborted) return;
                setVal(r);
                setLoading(false);
            })
            .catch((e: unknown) => {
                if (controller.signal.aborted) return;
                setError(e instanceof Error ? e.message : 'Ошибка');
                setVal(null);
                setLoading(false);
            });
        return () => controller.abort();
    }, [barcode, reloadKey]);

    // SKU search suggestions (barcode / article_seller / article_wb)
    const suggestions = React.useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return [] as Nomenclature[];
        return nomenclature.filter(n =>
            (n.barcode && n.barcode.toLowerCase().includes(q)) ||
            (n.article_seller && n.article_seller.toLowerCase().includes(q)) ||
            (n.article_wb && String(n.article_wb).includes(q))
        ).slice(0, 20);
    }, [query, nomenclature]);

    const pickSku = (n: Nomenclature) => {
        if (!n.barcode) return;
        setBarcode(n.barcode);
        setQuery(n.article_seller || n.barcode);
    };

    const applyMethodToAll = async () => {
        const ok = window.confirm(
            `Применить метод «${METHOD_LABELS[previewMethod]}» ко ВСЕМ отчётам проекта?\n\n` +
            `Это изменит расчёт себестоимости (COGS, прибыль, маржинальность) во всех отчётах. ` +
            `Сейчас применён: «${savedMethod ? METHOD_LABELS[savedMethod] : '—'}».`
        );
        if (!ok) return;
        setApplying(true);
        try {
            const r = await api.setValuationMethod(previewMethod);
            setSavedMethod(r.method);
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка');
        }
        setApplying(false);
    };

    const selectStyle: React.CSSProperties = {
        padding: '8px 12px', borderRadius: 8,
        border: '1px solid var(--color-border)',
        background: 'var(--color-bg-input)', color: 'var(--color-text)',
    };

    return (
        <div>
            {/* ─── SKU picker ─── */}
            <div style={{ position: 'relative', marginBottom: 16, maxWidth: 420 }}>
                <input
                    type="text"
                    placeholder="🔍 Артикул / WB артикул / баркод"
                    value={query}
                    onChange={(e: ChangeEvent<HTMLInputElement>) => setQuery(e.target.value)}
                    style={{ ...selectStyle, width: '100%' }}
                />
                {query.trim() && suggestions.length > 0 && barcode !== suggestions[0]?.barcode && (
                    <div className="glass-card" style={{
                        position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 20,
                        marginTop: 4, maxHeight: 280, overflow: 'auto', padding: 4,
                    }}>
                        {suggestions.map((n, i) => (
                            <div
                                key={`${n.barcode || n.article_wb}-${i}`}
                                onClick={() => pickSku(n)}
                                style={{
                                    padding: '8px 10px', borderRadius: 8, cursor: 'pointer',
                                    fontSize: 13, display: 'flex', justifyContent: 'space-between', gap: 8,
                                }}
                                onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-bg-hover)')}
                                onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                            >
                                <span style={{ fontWeight: 600 }}>{n.article_seller || n.barcode}</span>
                                <span style={{ opacity: 0.5, fontFamily: 'monospace', fontSize: 12 }}>{n.barcode}</span>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* ─── Data-start cutoff (project-wide) ─── */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 16, fontSize: 13 }}>
                <span style={{ opacity: 0.7 }}>Учитывать продажи с:</span>
                <input
                    type="date"
                    value={cutoff}
                    onChange={(e: ChangeEvent<HTMLInputElement>) => setCutoff(e.target.value)}
                    style={{ ...selectStyle, padding: '6px 10px' }}
                />
                <button className="btn btn-secondary btn-sm" disabled={savingCutoff} onClick={() => saveCutoff(cutoff || null)}>
                    {savingCutoff ? 'Сохранение...' : 'Применить'}
                </button>
                {cutoff && (
                    <button className="btn btn-sm" style={{ opacity: 0.6 }} disabled={savingCutoff} onClick={() => saveCutoff(null)}>
                        Сбросить
                    </button>
                )}
                <span style={{ opacity: 0.45, fontSize: 12 }}>продажи до даты игнорируются (неполные ранние данные)</span>
            </div>

            {/* ─── States ─── */}
            {!barcode && (
                <ValuationOverview
                    cutoff={cutoff}
                    savedMethod={savedMethod}
                    setSavedMethod={setSavedMethod}
                    onPick={(bc, name) => { setBarcode(bc); setQuery(name); }}
                />
            )}
            {barcode && loading && (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>⏳ Загрузка...</div>
            )}
            {barcode && error && (
                <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>❌ {error}</div>
            )}
            {barcode && !loading && !error && val && (
                <SkuAnalyticsPanel
                    val={val}
                    previewMethod={previewMethod}
                    setPreviewMethod={setPreviewMethod}
                    savedMethod={savedMethod}
                    applying={applying}
                    applyMethodToAll={applyMethodToAll}
                    reload={reload}
                />
            )}
        </div>
    );
}

interface OverviewGroup {
    key: string;
    qty: number;
    cogs_current: number;
    cogs_fifo: number;
    distortion: number;
    barcode?: string | null;
}

/** Empty-state overview: global method switch + project-wide FIFO-vs-current
 *  distortion, grouped by brand / category / top SKU. */
function ValuationOverview({ cutoff, savedMethod, setSavedMethod, onPick }: {
    cutoff: string;
    savedMethod: ValuationMethod | null;
    setSavedMethod: (m: ValuationMethod) => void;
    onPick: (barcode: string, name: string) => void;
}) {
    const [summary, setSummary] = useState<ValuationSummaryRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [groupBy, setGroupBy] = useState<'brand' | 'subject' | 'sku'>('brand');
    const [applying, setApplying] = useState(false);

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        const from = cutoff || '2025-01-01';
        const to = new Date().toISOString().slice(0, 10);
        api.getValuationSummary(from, to)
            .then(rows => { if (controller.signal.aborted) return; setSummary(rows); setLoading(false); })
            .catch(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [cutoff]);

    const applyMethod = async (m: ValuationMethod) => {
        if (m === savedMethod) return;
        if (!window.confirm(
            `Переключить ВСЕ отчёты проекта на метод «${METHOD_LABELS[m]}»?\n\n` +
            `Это изменит себестоимость, прибыль и маржу в ОПиУ, BDR, воронке и на складе. ` +
            `Сейчас: «${savedMethod ? METHOD_LABELS[savedMethod] : '—'}».`
        )) return;
        setApplying(true);
        try { const r = await api.setValuationMethod(m); setSavedMethod(r.method); }
        catch (e: unknown) { alert(e instanceof Error ? e.message : 'Ошибка'); }
        setApplying(false);
    };

    const groups: OverviewGroup[] = React.useMemo(() => {
        if (groupBy === 'sku') {
            return summary.slice(0, 50).map(r => ({
                key: r.sku, qty: r.qty, cogs_current: r.cogs_current,
                cogs_fifo: r.cogs_fifo, distortion: r.distortion, barcode: r.barcode,
            }));
        }
        const map = new Map<string, OverviewGroup>();
        for (const r of summary) {
            const key = (groupBy === 'brand' ? r.brand : r.subject) || '—';
            const g = map.get(key) || { key, qty: 0, cogs_current: 0, cogs_fifo: 0, distortion: 0 };
            g.qty += r.qty; g.cogs_current += r.cogs_current;
            g.cogs_fifo += r.cogs_fifo; g.distortion += r.distortion;
            map.set(key, g);
        }
        return [...map.values()].sort((a, b) => Math.abs(b.distortion) - Math.abs(a.distortion));
    }, [summary, groupBy]);

    const totalDistortion = summary.reduce((s, r) => s + Math.abs(r.distortion), 0);

    const gbBtn = (k: 'brand' | 'subject' | 'sku', label: string) => (
        <button className={`btn ${groupBy === k ? 'btn-primary' : 'btn-secondary'} btn-sm`} onClick={() => setGroupBy(k)}>{label}</button>
    );

    return (
        <div>
            {/* Global method switch (answers "как переключить весь себес") */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 14, fontWeight: 600 }}>Метод себестоимости в отчётах:</span>
                <div style={{ display: 'flex', gap: 4 }}>
                    {METHOD_ORDER.map(m => (
                        <button key={m} className={`btn ${savedMethod === m ? 'btn-primary' : 'btn-secondary'} btn-sm`} disabled={applying} onClick={() => applyMethod(m)}>
                            {METHOD_LABELS[m]}{savedMethod === m ? ' ✓' : ''}
                        </button>
                    ))}
                </div>
                <span style={{ fontSize: 12, opacity: 0.55 }}>применяется к ОПиУ, BDR, воронке, складу — пересчёт всех отчётов</span>
            </div>

            {/* Project-wide distortion */}
            <div className="glass-card" style={{ padding: 16 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
                    <span style={{ fontSize: 15, fontWeight: 600 }}>Искажение прибыли: FIFO vs текущий метод</span>
                    <span style={{ fontSize: 22, fontWeight: 700, color: 'var(--color-danger)' }}>{formatNumber(totalDistortion)} ₽</span>
                    <span style={{ fontSize: 12, opacity: 0.5 }}>суммарно по проекту с {cutoff || '2025-01-01'}</span>
                    <div style={{ flex: 1 }} />
                    {gbBtn('brand', 'По брендам')}
                    {gbBtn('subject', 'По категориям')}
                    {gbBtn('sku', 'Топ SKU')}
                </div>
                {loading ? (
                    <div style={{ padding: 24, textAlign: 'center', opacity: 0.6 }}>⏳ Считаем по всему проекту...</div>
                ) : groups.length === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', opacity: 0.6 }}>Нет данных за период.</div>
                ) : (
                    <div style={{ overflow: 'auto' }}>
                        <table className="data-table" style={{ marginBottom: 0, fontSize: 13 }}>
                            <thead>
                                <tr>
                                    <th>{groupBy === 'brand' ? 'Бренд' : groupBy === 'subject' ? 'Категория' : 'Артикул'}</th>
                                    <th style={{ textAlign: 'right' }}>Продажи (шт)</th>
                                    <th style={{ textAlign: 'right' }}>Себес текущая ₽</th>
                                    <th style={{ textAlign: 'right' }}>Себес FIFO ₽</th>
                                    <th style={{ textAlign: 'right' }}>Δ прибыли (→FIFO) ₽</th>
                                </tr>
                            </thead>
                            <tbody>
                                {groups.slice(0, 50).map(g => {
                                    const delta = g.cogs_current - g.cogs_fifo; // меньше COGS под FIFO → +прибыль
                                    const color = delta > 0 ? 'var(--color-success)' : delta < 0 ? 'var(--color-danger)' : 'var(--color-text)';
                                    const clickable = groupBy === 'sku' && !!g.barcode;
                                    return (
                                        <tr key={g.key}
                                            style={clickable ? { cursor: 'pointer' } : undefined}
                                            onClick={clickable ? () => onPick(g.barcode as string, g.key) : undefined}
                                            title={clickable ? 'Открыть детальную аналитику' : undefined}>
                                            <td style={{ fontWeight: 600 }}>{g.key}{clickable ? ' →' : ''}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(g.qty, 0)}</td>
                                            <td style={{ textAlign: 'right', opacity: 0.8 }}>{formatNumber(g.cogs_current)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(g.cogs_fifo)}</td>
                                            <td style={{ textAlign: 'right', color, fontWeight: 600 }}>{delta > 0 ? '+' : ''}{formatNumber(delta)}</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
                <div style={{ fontSize: 12, opacity: 0.5, marginTop: 10 }}>
                    Или выберите товар выше — детальная аналитика по партиям, методам и помесячно.
                </div>
            </div>
        </div>
    );
}

interface PanelProps {
    val: SkuValuation;
    previewMethod: ValuationMethod;
    setPreviewMethod: (m: ValuationMethod) => void;
    savedMethod: ValuationMethod | null;
    applying: boolean;
    applyMethodToAll: () => void;
    reload: () => void;
}

function SkuAnalyticsPanel({
    val, previewMethod, setPreviewMethod, savedMethod, applying, applyMethodToAll, reload,
}: PanelProps) {
    // KPI: profit distortion = Σ|cogs_fifo − cogs_avg| over months
    const distortion = val.monthly.reduce((s, m) => s + Math.abs(m.cogs_fifo - m.cogs_avg), 0);
    const onHandPreview = val.on_hand_value[previewMethod];
    const effPreview = val.eff_now[previewMethod];

    // Inline arrival-date editor (per batch row); RETURN/OPENING rows aren't editable.
    const [editOrder, setEditOrder] = useState<string | null>(null);
    const [editDate, setEditDate] = useState('');
    const [savingArr, setSavingArr] = useState(false);

    const saveArrival = async (orderNo: string) => {
        setSavingArr(true);
        try {
            await api.setOrderArrivalDate(orderNo, editDate || null);
            setEditOrder(null);
            reload();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка');
        }
        setSavingArr(false);
    };

    const kpiCardStyle: React.CSSProperties = {
        flex: '1 1 180px', padding: 16, borderRadius: 16,
        background: 'var(--color-bg-card)',
        border: '1px solid var(--color-border)',
    };
    const kpiLabel: React.CSSProperties = { fontSize: 12, opacity: 0.6, marginBottom: 6 };
    const kpiValue: React.CSSProperties = { fontSize: 24, fontWeight: 700 };

    const maxBatchQty = Math.max(1, ...val.ledger.map(l => l.qty));

    return (
        <div>
            {/* Header line */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 18, fontWeight: 700 }}>{val.sku}</span>
                {val.article_wb != null && <span className="badge badge-info" style={{ fontSize: 11 }}>WB {val.article_wb}</span>}
                {val.brand && <span className="badge badge-secondary" style={{ fontSize: 11 }}>{val.brand}</span>}
                {val.subject && <span style={{ opacity: 0.6, fontSize: 13 }}>{val.subject}</span>}
                {val.barcode && <span style={{ opacity: 0.4, fontFamily: 'monospace', fontSize: 12 }}>{val.barcode}</span>}
            </div>

            {/* Warnings */}
            {(val.is_estimated || val.warnings.length > 0) && (
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
                    {val.is_estimated && (
                        <span className="badge badge-warning" style={{ fontSize: 11 }}>⚠ Оценочная себестоимость</span>
                    )}
                    {val.warnings.map((w, i) => (
                        <span key={i} className="badge badge-warning" style={{ fontSize: 11 }}>{w}</span>
                    ))}
                </div>
            )}

            {/* KPI cards */}
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
                <div style={kpiCardStyle}>
                    <div style={kpiLabel}>Себес сейчас (пожизненная)</div>
                    <div style={{ ...kpiValue, color: 'var(--color-accent)' }}>{formatNumber(val.lifetime_avg)} ₽</div>
                </div>
                <div style={kpiCardStyle}>
                    <div style={kpiLabel}>FIFO сейчас</div>
                    <div style={{ ...kpiValue, color: 'var(--color-success)' }}>{formatNumber(val.eff_now.fifo)} ₽</div>
                </div>
                <div style={kpiCardStyle}>
                    <div style={kpiLabel}>Оценка остатка ({METHOD_LABELS[previewMethod]})</div>
                    <div style={kpiValue}>{formatNumber(onHandPreview)} ₽</div>
                    <div style={{ fontSize: 11, opacity: 0.5, marginTop: 2 }}>{formatNumber(val.on_hand_qty, 0)} шт</div>
                </div>
                <div style={kpiCardStyle}>
                    <div style={kpiLabel}>Искажение прибыли (FIFO vs пожизненная)</div>
                    <div style={{ ...kpiValue, color: distortion > 0 ? 'var(--color-danger)' : 'var(--color-text)' }}>
                        {formatNumber(distortion)} ₽
                    </div>
                </div>
            </div>

            {/* Method toggle (PREVIEW) + apply button */}
            <div style={{
                display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                marginBottom: 16, padding: 12, borderRadius: 12,
                background: 'var(--color-bg)', border: '1px solid var(--color-border)',
            }}>
                <span style={{ fontSize: 13, opacity: 0.7 }}>Метод (превью):</span>
                <div style={{ display: 'flex', gap: 4 }}>
                    {METHOD_ORDER.map(m => (
                        <button
                            key={m}
                            className={`btn ${previewMethod === m ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                            onClick={() => setPreviewMethod(m)}
                        >
                            {METHOD_LABELS[m]}
                        </button>
                    ))}
                </div>
                <div style={{ flex: 1 }} />
                <span style={{ fontSize: 12, opacity: 0.6 }}>
                    В отчётах: <b>{savedMethod ? METHOD_LABELS[savedMethod] : '—'}</b>
                </span>
                <button
                    className="btn btn-success btn-sm"
                    onClick={applyMethodToAll}
                    disabled={applying || previewMethod === savedMethod}
                    title={previewMethod === savedMethod ? 'Этот метод уже применён' : undefined}
                    style={{ opacity: previewMethod === savedMethod ? 0.5 : 1 }}
                >
                    {applying ? 'Применение...' : 'Применить метод ко всем отчётам'}
                </button>
            </div>

            {/* Monthly table */}
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>Помесячная себестоимость</div>
            {val.monthly.length === 0 ? (
                <div className="glass-card" style={{ padding: 20, textAlign: 'center', opacity: 0.6, marginBottom: 24 }}>
                    Нет продаж за период.
                </div>
            ) : (
                <div className="glass-card" style={{ overflow: 'auto', marginBottom: 24, padding: 0 }}>
                    <table className="data-table" style={{ marginBottom: 0, fontSize: 13 }}>
                        <thead>
                            <tr>
                                <th style={{ minWidth: 90 }}>Месяц</th>
                                <th style={{ textAlign: 'right' }}>Продажи (шт)</th>
                                <th style={{ textAlign: 'right' }}>Себес {METHOD_LABELS[previewMethod]} ₽</th>
                                <th style={{ textAlign: 'right' }}>Себес текущая (пожизненная) ₽</th>
                                <th style={{ textAlign: 'right' }}>Δ прибыли ₽</th>
                            </tr>
                        </thead>
                        <tbody>
                            {val.monthly.map((m) => {
                                const cogsPreview = monthlyCogs(m, previewMethod);
                                const cogsLifetime = m.cogs_avg;
                                // Δ прибыли = насколько прибыль выше/ниже при превью-методе vs текущей.
                                // Меньший COGS → больше прибыль (положительная Δ).
                                const deltaProfit = cogsLifetime - cogsPreview;
                                const color = deltaProfit > 0 ? 'var(--color-success)' : deltaProfit < 0 ? 'var(--color-danger)' : 'var(--color-text)';
                                return (
                                    <tr key={m.month}>
                                        <td style={{ fontWeight: 600 }}>{m.month}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(m.qty, 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(cogsPreview)}</td>
                                        <td style={{ textAlign: 'right', opacity: 0.8 }}>{formatNumber(cogsLifetime)}</td>
                                        <td style={{ textAlign: 'right', color, fontWeight: 600 }}>
                                            {deltaProfit > 0 ? '+' : ''}{formatNumber(deltaProfit)}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}

            {/* Batch ledger */}
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>Партии (приходы)</div>
            {val.ledger.length === 0 ? (
                <div className="glass-card" style={{ padding: 20, textAlign: 'center', opacity: 0.6 }}>
                    Нет данных о партиях прихода.
                </div>
            ) : (
                <div className="glass-card" style={{ padding: 16 }}>
                    {val.ledger.map((l: CostLayer, i) => {
                        const consumedPct = l.qty > 0 ? (l.consumed / l.qty) * 100 : 0;
                        const widthPct = (l.qty / maxBatchQty) * 100;
                        return (
                            <div key={`${l.order_no}-${i}`} style={{ marginBottom: i < val.ledger.length - 1 ? 14 : 0 }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, marginBottom: 4, fontSize: 13 }}>
                                    <span style={{ fontWeight: 600 }}>
                                        {l.order_no}
                                        {!l.arrival_known && (
                                            <span className="badge badge-warning" style={{ fontSize: 10, marginLeft: 6 }}>дата прихода?</span>
                                        )}
                                    </span>
                                    <span style={{ opacity: 0.6, display: 'flex', alignItems: 'center', gap: 6 }}>
                                        {editOrder === l.order_no ? (
                                            <>
                                                <input
                                                    type="date"
                                                    value={editDate}
                                                    onChange={(e: ChangeEvent<HTMLInputElement>) => setEditDate(e.target.value)}
                                                    style={{ padding: '2px 6px', borderRadius: 6, border: '1px solid var(--color-border)', background: 'var(--color-bg-input)', color: 'var(--color-text)', fontSize: 12 }}
                                                />
                                                <button className="btn btn-primary btn-sm" disabled={savingArr} onClick={() => saveArrival(l.order_no)}>✓</button>
                                                <button className="btn btn-sm" disabled={savingArr} onClick={() => setEditOrder(null)}>✕</button>
                                            </>
                                        ) : l.order_no !== 'RETURN' && l.order_no !== 'OPENING' && l.order_no !== 'AUTO-OPENING' ? (
                                            <>
                                                <span
                                                    style={{ cursor: 'pointer', textDecoration: 'underline dotted' }}
                                                    title="Изменить дату прихода"
                                                    onClick={() => { setEditOrder(l.order_no); setEditDate(l.avail_date || ''); }}
                                                >
                                                    {l.avail_date ? formatDate(l.avail_date) : 'задать дату прихода'}
                                                </span>
                                                <span> · {formatNumber(l.unit_cost)} ₽/шт</span>
                                            </>
                                        ) : (
                                            <span>{l.avail_date ? formatDate(l.avail_date) : '—'} · {formatNumber(l.unit_cost)} ₽/шт</span>
                                        )}
                                    </span>
                                </div>
                                {/* Bar: consumed vs remaining */}
                                <div style={{
                                    width: `${widthPct}%`, minWidth: 60, height: 22, borderRadius: 6,
                                    background: 'var(--color-bg)', border: '1px solid var(--color-border)',
                                    display: 'flex', overflow: 'hidden',
                                }}>
                                    <div style={{
                                        width: `${consumedPct}%`, background: 'var(--color-accent)',
                                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                                        fontSize: 10, color: '#fff', whiteSpace: 'nowrap',
                                    }} title={`Списано: ${l.consumed} шт`}>
                                        {consumedPct > 18 ? `${formatNumber(l.consumed, 0)}` : ''}
                                    </div>
                                    <div style={{
                                        flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
                                        fontSize: 10, opacity: 0.7, whiteSpace: 'nowrap',
                                    }} title={`Остаток: ${l.remaining} шт`}>
                                        {100 - consumedPct > 18 ? `${formatNumber(l.remaining, 0)}` : ''}
                                    </div>
                                </div>
                                <div style={{ fontSize: 11, opacity: 0.5, marginTop: 3 }}>
                                    Партия {formatNumber(l.qty, 0)} шт · списано {formatNumber(l.consumed, 0)} · остаток {formatNumber(l.remaining, 0)}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}

            {/* ─── Opening balance editor ─── */}
            {val.barcode && (
                <OpeningBalanceEditor barcode={val.barcode} suggestedCost={val.lifetime_avg} reload={reload} />
            )}
        </div>
    );
}

function OpeningBalanceEditor({ barcode, suggestedCost, reload }: { barcode: string; suggestedCost: number; reload: () => void }) {
    const [open, setOpen] = useState(false);
    const [qty, setQty] = useState('');
    const [cost, setCost] = useState('');
    const [asOf, setAsOf] = useState('');
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        const controller = new AbortController();
        api.getOpeningBalances().then(rows => {
            if (controller.signal.aborted) return;
            const cur = rows.find(r => r.barcode === barcode);
            if (cur) { setQty(String(cur.qty)); setCost(String(cur.unit_cost)); setAsOf(cur.as_of_date || ''); }
        }).catch(() => {});
        return () => controller.abort();
    }, [barcode]);

    const save = async () => {
        setSaving(true);
        try {
            await api.setOpeningBalances([{ barcode, qty: Number(qty) || 0, unit_cost: Number(cost) || 0, as_of_date: asOf || null }]);
            reload();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка');
        }
        setSaving(false);
    };

    const inp: React.CSSProperties = {
        padding: '6px 10px', borderRadius: 8, border: '1px solid var(--color-border)',
        background: 'var(--color-bg-input)', color: 'var(--color-text)', fontSize: 13, width: 130,
    };

    return (
        <div style={{ marginTop: 20 }}>
            <button className="btn btn-secondary btn-sm" onClick={() => setOpen(o => !o)}>
                {open ? '▾' : '▸'} Стартовый остаток (опенинг-баланс)
            </button>
            {open && (
                <div className="glass-card" style={{ padding: 16, marginTop: 8, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                    <label style={{ fontSize: 12, opacity: 0.7 }}>
                        Кол-во, шт<br />
                        <input type="number" value={qty} onChange={(e: ChangeEvent<HTMLInputElement>) => setQty(e.target.value)} style={inp} />
                    </label>
                    <label style={{ fontSize: 12, opacity: 0.7 }}>
                        Себес/шт, ₽<br />
                        <input type="number" step="0.01" value={cost} placeholder={String(suggestedCost)} onChange={(e: ChangeEvent<HTMLInputElement>) => setCost(e.target.value)} style={inp} />
                    </label>
                    <label style={{ fontSize: 12, opacity: 0.7 }}>
                        На дату<br />
                        <input type="date" value={asOf} onChange={(e: ChangeEvent<HTMLInputElement>) => setAsOf(e.target.value)} style={inp} />
                    </label>
                    <button className="btn btn-primary btn-sm" disabled={saving} onClick={save}>
                        {saving ? 'Сохранение...' : 'Сохранить'}
                    </button>
                    <span style={{ fontSize: 11, opacity: 0.5, flexBasis: '100%' }}>
                        Старейшая партия — гасится FIFO первой. Нужен, если продажи начинаются раньше первой завезённой партии.
                    </span>
                </div>
            )}
        </div>
    );
}

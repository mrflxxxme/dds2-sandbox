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

import type { MissingCostItem } from '@/types/api';

function BulkCost() {
    const [missing, setMissing] = useState<MissingCostItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [result, setResult] = useState<{ saved: number; not_found: string[] } | null>(null);
    const [costs, setCosts] = useState<Record<number, string>>({});
    const [search, setSearch] = useState('');

    const loadMissing = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const data = await api.getMissingCosts();
            setMissing(data);
        } catch (e: any) { setError(e.message || 'Ошибка'); }  // noqa: RUF001
        setLoading(false);
    }, []);

    useEffect(() => { loadMissing(); }, [loadMissing]);

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
        } catch (e: any) { alert(e.message); }
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
        }));
        exportToExcel(data, 'Товары_без_себестоимости');
    };

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--danger)' }}>{error}</div>;

    return (
        <div>
            {/* Summary */}
            {missing.length > 0 ? (
                <div style={{
                    background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)',
                    borderRadius: 8, padding: 14, marginBottom: 16, fontSize: 13,
                }}>
                    <b>{missing.length}</b> товаров без себестоимости участвуют в расчётах.
                    Суммарные заказы: <b>{formatNumber(totalOrdersSum)}</b>.
                    Прибыль и маржинальность завышены для этих товаров.
                </div>
            ) : (
                <div style={{
                    background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.3)',
                    borderRadius: 8, padding: 14, marginBottom: 16, fontSize: 13, color: 'var(--success)',
                }}>
                    Все товары в расчётах имеют себестоимость.
                </div>
            )}

            {result && result.saved > 0 && (
                <div style={{
                    background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.3)',
                    borderRadius: 8, padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--success)',
                }}>
                    Сохранено: <b>{result.saved}</b> шт.
                </div>
            )}

            {/* Toolbar */}
            {missing.length > 0 && (
                <>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
                        <input
                            type="text" placeholder="Поиск по nm_id, баркоду, артикулу, категории..."
                            value={search} onChange={e => setSearch(e.target.value)}
                            style={{
                                padding: '8px 12px', borderRadius: 8, border: '1px solid var(--border)',
                                background: 'var(--bg-secondary)', color: 'var(--text-primary)', width: 320,
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

                    {/* Table */}
                    <div className="glass-card" style={{ overflow: 'auto', maxHeight: 'calc(100vh - 380px)', padding: 0 }}>
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
                                    <th style={{ width: 130 }}>Себестоимость</th>
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

                    {/* Save footer */}
                    <div style={{
                        position: 'sticky', bottom: 0, background: 'var(--color-bg)',
                        padding: '12px 0', display: 'flex', justifyContent: 'flex-end', gap: 10,
                        borderTop: '1px solid var(--color-border)', marginTop: 8,
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
        </div>
    );
}

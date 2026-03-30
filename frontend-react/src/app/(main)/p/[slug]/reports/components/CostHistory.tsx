'use client';
import React, { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';

export function CostHistory() {
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
                <input type="text" placeholder="🔍 Поиск по артикулу / WB артикулу"
                    value={search} onChange={(e: any) => setSearch(e.target.value)}
                    onKeyDown={(e: any) => e.key === 'Enter' && load()}
                    style={{ ...selectStyle, width: 280 }} />
                <select value={brand} onChange={(e: any) => setBrand(e.target.value)} style={selectStyle}>
                    <option value="">Все бренды</option>
                    {brands.map((b: string) => <option key={b} value={b}>{b}</option>)}
                </select>
                <button className="btn btn-secondary btn-sm" onClick={load}>🔄</button>
                <button className="btn btn-secondary btn-sm" onClick={handleExport}>📥 Excel</button>
                <span style={{ opacity: 0.5, fontSize: 13 }}>{articles.length} артикулов × {orders.length} заказов</span>
            </div>

            <div className="glass-card" style={{ overflow: 'auto', maxHeight: 'calc(100vh - 220px)' }}>
                {/* TODO: migrate to TanStackDataTable */}
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
                        {articles.map((a: any, i: number) => (
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
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

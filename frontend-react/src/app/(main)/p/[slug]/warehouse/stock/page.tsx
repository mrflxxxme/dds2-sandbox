'use client';
import React, { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Warehouse, StockSummaryRow } from '@/types/api';
import type { Column } from '@/components/DataTable';

function StockCell({ qty, reserved }: { qty: number; reserved: number }) {
    if (!qty && !reserved) return <span style={{ color: 'var(--color-text-muted)' }}>{'\u2014'}</span>;
    const available = Math.max(0, qty - reserved);
    if (!reserved) return <>{formatNumber(qty)}</>;
    return (
        <>
            <span>{formatNumber(available)}</span>
            <span style={{ color: 'var(--color-warning)' }}> / {formatNumber(reserved)}</span>
        </>
    );
}

export default function StockSummaryPage() {
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [summary, setSummary] = useState<StockSummaryRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [search, setSearch] = useState('');
    const [filter, setFilter] = useState<'all' | 'reserved'>('all');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [wh, sm] = await Promise.all([
                api.getWarehouses(),
                api.getStockSummary(),
            ]);
            setWarehouses(wh);
            setSummary(sm);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    }, []);

    useEffect(() => { load(); }, [load]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;

    // Filter by barcode search + reserved filter
    let filtered = search
        ? summary.filter(r => r.barcode.includes(search))
        : summary;

    if (filter === 'reserved') {
        filtered = filtered.filter(r => (r.total_reserved || 0) > 0);
    }

    // Build dynamic columns: barcode + per-warehouse qty + in_transit + total
    const cols: Column[] = [
        { key: 'barcode', label: 'ШК' },
    ];

    for (const wh of warehouses) {
        cols.push({
            key: `wh_${wh.id}`,
            label: wh.name,
            align: 'right',
            render: (_: unknown, row: StockSummaryRow) => {
                const qty = row.warehouses[wh.id] || 0;
                const res = (row.reserved || {})[wh.id] || 0;
                return <StockCell qty={qty} reserved={res} />;
            },
        });
    }

    cols.push(
        {
            key: 'total_in_transit',
            label: 'В пути',
            align: 'right',
            render: (v: number) => v > 0 ? formatNumber(v) : '\u2014',
        },
        {
            key: 'total',
            label: 'Итого',
            align: 'right',
            render: (_: unknown, row: StockSummaryRow) => {
                const res = row.total_reserved || 0;
                if (!res) return <strong>{formatNumber(row.total)}</strong>;
                return (
                    <strong>
                        <span>{formatNumber(row.total_available || 0)}</span>
                        <span style={{ color: 'var(--color-warning)' }}> / {formatNumber(res)}</span>
                    </strong>
                );
            },
        },
    );

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Сводные остатки</h1>
                    <p className="page-subtitle">Остатки по всем складам</p>
                </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
                <input
                    className="form-input"
                    placeholder="Поиск по баркоду..."
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    style={{ maxWidth: 300 }}
                />
                <div style={{ display: 'flex', border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden' }}>
                    <button
                        onClick={() => setFilter('all')}
                        style={{
                            padding: '6px 14px', fontSize: 13, border: 'none', cursor: 'pointer',
                            background: filter === 'all' ? 'var(--color-primary)' : 'var(--color-bg)',
                            color: filter === 'all' ? '#fff' : 'var(--color-text)',
                        }}
                    >Все остатки</button>
                    <button
                        onClick={() => setFilter('reserved')}
                        style={{
                            padding: '6px 14px', fontSize: 13, border: 'none', cursor: 'pointer',
                            borderLeft: '1px solid var(--color-border)',
                            background: filter === 'reserved' ? 'var(--color-warning)' : 'var(--color-bg)',
                            color: filter === 'reserved' ? '#fff' : 'var(--color-text)',
                        }}
                    >Зарезервировано</button>
                </div>
            </div>

            {filtered.length === 0 ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', opacity: 0.6 }}>
                    <div style={{ fontSize: 48 }}>{'\uD83D\uDCE6'}</div>
                    <div>{filter === 'reserved' ? 'Нет зарезервированных позиций' : 'Нет данных по остаткам'}</div>
                </div>
            ) : (
                <TanStackDataTable
                    columns={cols}
                    data={filtered}
                    exportName="stock_summary"
                    enableSorting
                    enablePagination
                    pageSize={50}
                    actions={
                        <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                            {filtered.length} позиций
                        </span>
                    }
                />
            )}
        </div>
    );
}

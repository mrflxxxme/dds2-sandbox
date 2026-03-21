'use client';
import React, { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { DataTable } from '@/components';
import type { Warehouse, StockSummaryRow } from '@/types/api';
import type { Column } from '@/components/DataTable';

export default function StockSummaryPage() {
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [summary, setSummary] = useState<StockSummaryRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [search, setSearch] = useState('');

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

    // Filter by barcode search
    const filtered = search
        ? summary.filter(r => r.barcode.includes(search))
        : summary;

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
                const qty = row.warehouses[wh.id];
                return qty ? formatNumber(qty) : '—';
            },
        });
    }

    cols.push(
        {
            key: 'total_in_transit',
            label: 'В пути',
            align: 'right',
            render: (v: number) => v > 0 ? formatNumber(v) : '—',
        },
        {
            key: 'total',
            label: 'Итого',
            align: 'right',
            render: (v: number) => <strong>{formatNumber(v)}</strong>,
        },
    );

    // Transform data for export
    const exportData = filtered.map(row => {
        const obj: Record<string, string | number> = { 'ШК': row.barcode };
        for (const wh of warehouses) {
            obj[wh.name] = row.warehouses[wh.id] || 0;
        }
        obj['В пути'] = row.total_in_transit;
        obj['Итого'] = row.total;
        return obj;
    });

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Сводные остатки</h1>
                    <p className="page-subtitle">Остатки по всем складам</p>
                </div>
            </div>

            <div style={{ marginBottom: 16 }}>
                <input
                    className="form-input"
                    placeholder="Поиск по баркоду..."
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    style={{ maxWidth: 300 }}
                />
            </div>

            {filtered.length === 0 ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', opacity: 0.6 }}>
                    <div style={{ fontSize: 48 }}>📦</div>
                    <div>Нет данных по остаткам</div>
                </div>
            ) : (
                <DataTable
                    columns={cols}
                    data={filtered}
                    exportName="stock_summary"
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

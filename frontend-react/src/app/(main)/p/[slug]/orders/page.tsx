'use client';
import { useEffect, useState, useMemo } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';

export default function OrdersPage() {
    const [orders, setOrders] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        api.getCostOrders().then(res => {
            setOrders(Array.isArray(res) ? res : []);
        }).catch((e: any) => {
            setError(e?.message || 'Ошибка загрузки');
        }).finally(() => setLoading(false));
    }, []);

    const columns: Column[] = useMemo(() => {
        if (orders.length === 0) return [];
        return Object.keys(orders[0]).slice(0, 8).map(key => ({
            key,
            label: key,
            align: (typeof orders[0][key] === 'number' ? 'right' : 'left') as 'right' | 'left',
            render: (val: any) => (
                <span style={{
                    fontWeight: typeof val === 'number' ? 600 : 400,
                }}>
                    {typeof val === 'number' ? formatNumber(val) : (val ?? '—')}
                </span>
            ),
        }));
    }, [orders]);

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Заказы</h1>
                    <p className="page-subtitle">Заказы и себестоимость</p>
                </div>
            </div>

            {error && (
                <div className="glass-card" style={{ color: 'var(--color-danger)', marginBottom: 16, padding: 16 }}>
                    {error}
                </div>
            )}

            <TanStackDataTable
                columns={columns}
                data={orders}
                loading={loading}
                emptyIcon="📦"
                emptyText="Заказов пока нет"
                exportName="orders"
                enableSorting
                enablePagination
                pageSize={50}
            />
        </div>
    );
}

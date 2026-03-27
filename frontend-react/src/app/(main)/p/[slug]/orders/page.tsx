'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';

export default function OrdersPage() {
    const [orders, setOrders] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        api.getOrders().then(res => {
            setOrders(Array.isArray(res) ? res : res.orders || []);
        }).catch(() => { }).finally(() => setLoading(false));
    }, []);

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Заказы</h1>
                    <p className="page-subtitle">Заказы и себестоимость</p>
                </div>
                <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(orders, 'orders')}
                    disabled={orders.length === 0}>
                    📥 Экспорт Excel
                </button>
            </div>

            <div className="glass-card">
                {loading ? (
                    <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
                ) : orders.length === 0 ? (
                    <div className="empty-state">
                        <div className="empty-state-icon">📦</div>
                        <div className="empty-state-text">Заказов пока нет</div>
                    </div>
                ) : (
                    <div style={{ overflowX: 'auto' }}>
                        <table className="data-table">
                            <thead>
                                <tr>
                                    {Object.keys(orders[0] || {}).slice(0, 8).map(key => (
                                        <th key={key}>{key}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {orders.slice(0, 100).map((row: any, i: number) => (
                                    <tr key={i}>
                                        {Object.values(row).slice(0, 8).map((val: any, j: number) => (
                                            <td key={j} style={{
                                                textAlign: typeof val === 'number' ? 'right' : 'left',
                                                fontWeight: typeof val === 'number' ? 600 : 400,
                                            }}>
                                                {typeof val === 'number' ? formatNumber(val) : (val ?? '—')}
                                            </td>
                                        ))}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}

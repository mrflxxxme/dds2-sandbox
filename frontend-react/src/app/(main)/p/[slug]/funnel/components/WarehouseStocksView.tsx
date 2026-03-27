'use client';
import { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';

export function WarehouseStocksView() {
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [syncing, setSyncing] = useState(false);

    const load = async () => {
        setLoading(true);
        try { setData(await api.getWarehouseStocks()); } catch { }
        setLoading(false);
    };

    const sync = async () => {
        setSyncing(true);
        try {
            const res = await api.syncWarehouseStocks();
            alert(`Синхронизировано ${res.synced} записей`);
            await load();
        } catch (e: any) {
            alert(e?.message || 'Ошибка синхронизации');
        }
        setSyncing(false);
    };

    useEffect(() => { load(); }, []);

    if (loading && !data) return <div className="glass-card" style={{ textAlign: 'center', padding: 40 }}>Загрузка складов...</div>;

    return (
        <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div>
                    <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>🏭 Остатки по складам</h2>
                    <span style={{ fontSize: 13, opacity: 0.6 }}>
                        {data ? `${data.total_warehouses} складов · ${formatNumber(data.total_qty)} шт` : 'Нет данных'}
                    </span>
                </div>
                <button className="btn btn-sm btn-primary" onClick={sync} disabled={syncing}>
                    {syncing ? '⏳ Синхронизация...' : '🔄 Синхронизировать с WB'}
                </button>
            </div>

            {data && data.warehouses && data.warehouses.length > 0 ? (
                <div className="glass-card" style={{ overflowX: 'auto' }}>
                    <table className="data-table" style={{ width: '100%', fontSize: 13 }}>
                        <thead>
                            <tr>
                                <th style={{ textAlign: 'left' }}>Склад</th>
                                <th style={{ textAlign: 'right' }}>Остаток (шт)</th>
                                <th style={{ textAlign: 'right' }}>Артикулов</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr style={{ fontWeight: 700, background: 'rgba(0,0,0,0.03)' }}>
                                <td>ИТОГО</td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(data.total_qty)}</td>
                                <td style={{ textAlign: 'right' }}>{data.total_warehouses}</td>
                            </tr>
                            {data.warehouses.map((wh: any) => (
                                <tr key={wh.name}>
                                    <td>{wh.name}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(wh.total_qty)}</td>
                                    <td style={{ textAlign: 'right' }}>{wh.articles_count}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : (
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-text">Нет данных по складам. Нажмите «Синхронизировать с WB».</div>
                    </div>
                </div>
            )}
        </div>
    );
}

'use client';
import { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';

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
                (() => {
                    const totalRow = { name: 'ИТОГО', total_qty: data.total_qty, articles_count: data.total_warehouses, _isTotal: true };
                    const tableData = [totalRow, ...data.warehouses];
                    const cols: Column[] = [
                        { key: 'name', label: 'Склад', render: (v: string, row: any) => row._isTotal ? <strong>{v}</strong> : v },
                        { key: 'total_qty', label: 'Остаток (шт)', align: 'right', render: (v: number, row: any) => row._isTotal ? <strong>{formatNumber(v)}</strong> : formatNumber(v) },
                        { key: 'articles_count', label: 'Артикулов', align: 'right', render: (v: number, row: any) => row._isTotal ? <strong>{v}</strong> : v },
                    ];
                    return (
                        <TanStackDataTable
                            columns={cols}
                            data={tableData}
                            enableSorting
                            enablePagination={false}
                            rowClassName={(row: any) => row._isTotal ? 'font-bold' : ''}
                        />
                    );
                })()
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

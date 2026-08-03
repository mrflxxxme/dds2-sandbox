'use client';
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type {
    WbStocksResponse, WbWarehouseRow,
    WbStocksArticlesResponse, WbArticleRow,
    WbStockHistoryResponse, WbStockHistoryDay,
} from '@/types/api';

type Tab = 'warehouses' | 'articles' | 'history';

// ─── KPI Card ─────────────────────────────────────────────────────────────
function KpiCard({ label, value, prev, suffix }: {
    label: string; value: number; prev?: number; suffix?: string;
}) {
    const change = prev && prev > 0 ? value - prev : 0;
    const pct = prev && prev > 0 ? ((value - prev) / prev * 100) : 0;
    return (
        <div className="glass-card" style={{ padding: '14px 18px', textAlign: 'center' }}>
            <div style={{ fontSize: 26, fontWeight: 700 }}>
                {formatNumber(value, 0)}{suffix ? ` ${suffix}` : ''}
            </div>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2 }}>{label}</div>
            {prev !== undefined && prev > 0 && (
                <div style={{
                    fontSize: 12, marginTop: 4,
                    color: change > 0 ? 'var(--color-success)' : change < 0 ? 'var(--color-danger)' : 'var(--color-text-muted)',
                }}>
                    {change > 0 ? '+' : ''}{formatNumber(change, 0)} ({pct > 0 ? '+' : ''}{pct.toFixed(1)}%)
                </div>
            )}
        </div>
    );
}

// ─── Change badge ─────────────────────────────────────────────────────────
function ChangeBadge({ value }: { value: number }) {
    if (value === 0) return <span style={{ color: 'var(--color-text-muted)' }}>—</span>;
    return (
        <span style={{
            color: value > 0 ? 'var(--color-success)' : 'var(--color-danger)',
            fontWeight: 500,
        }}>
            {value > 0 ? '+' : ''}{formatNumber(value, 0)}
        </span>
    );
}

// ─── Tab: Warehouses ──────────────────────────────────────────────────────
function WarehousesTab({ data }: { data: WbStocksResponse }) {
    const [sort, setSort] = useState<{ key: keyof WbWarehouseRow; dir: 'asc' | 'desc' }>({ key: 'total_qty', dir: 'desc' });

    const sorted = useMemo(() => {
        return [...data.warehouses].sort((a, b) => {
            const av = a[sort.key] as number;
            const bv = b[sort.key] as number;
            return sort.dir === 'asc' ? av - bv : bv - av;
        });
    }, [data.warehouses, sort]);

    const toggleSort = (key: keyof WbWarehouseRow) => {
        setSort(prev => prev.key === key ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'desc' });
    };

    const sortIcon = (key: keyof WbWarehouseRow) => sort.key === key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : '';

    const handleExport = () => {
        exportToExcel(data.warehouses.map(w => ({
            'Склад': w.name,
            'Остаток': w.total_qty,
            'К клиенту': w.in_way_to_client,
            'От клиента': w.in_way_from_client,
            'Артикулов': w.articles_count,
            'Вчера': w.yesterday_qty,
            'Изменение': w.change,
        })), 'wb_stocks_warehouses');
    };

    return (
        <>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
                <button className="btn btn-sm" onClick={handleExport}>📥 Excel</button>
            </div>
            {/* TODO: migrate to TanStackDataTable — has custom TOTAL summary row at top */}
            <div className="glass-card" style={{ overflowX: 'auto' }}>
                <table className="data-table" style={{ width: '100%', fontSize: 13 }}>
                    <thead>
                        <tr>
                            <th style={{ textAlign: 'left' }}>Склад</th>
                            <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('total_qty')}>
                                Остаток{sortIcon('total_qty')}
                            </th>
                            <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('in_way_to_client')}>
                                К клиенту{sortIcon('in_way_to_client')}
                            </th>
                            <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('in_way_from_client')}>
                                От клиента{sortIcon('in_way_from_client')}
                            </th>
                            <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('articles_count')}>
                                Артикулов{sortIcon('articles_count')}
                            </th>
                            <th style={{ textAlign: 'right' }}>Вчера</th>
                            <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('change')}>
                                Изм.{sortIcon('change')}
                            </th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr style={{ fontWeight: 700, background: 'rgba(0,0,0,0.03)' }}>
                            <td>ИТОГО</td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(data.total_qty, 0)}</td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(data.total_in_way_to_client, 0)}</td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(data.total_in_way_from_client, 0)}</td>
                            <td style={{ textAlign: 'right' }}>{data.total_warehouses}</td>
                            <td style={{ textAlign: 'right' }}>{data.yesterday_total_qty > 0 ? formatNumber(data.yesterday_total_qty, 0) : '—'}</td>
                            <td style={{ textAlign: 'right' }}><ChangeBadge value={data.change_total} /></td>
                        </tr>
                        {sorted.map((wh) => (
                            <tr key={wh.name}>
                                <td>{wh.name}</td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(wh.total_qty, 0)}</td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(wh.in_way_to_client, 0)}</td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(wh.in_way_from_client, 0)}</td>
                                <td style={{ textAlign: 'right' }}>{wh.articles_count}</td>
                                <td style={{ textAlign: 'right' }}>{wh.yesterday_qty > 0 ? formatNumber(wh.yesterday_qty, 0) : '—'}</td>
                                <td style={{ textAlign: 'right' }}><ChangeBadge value={wh.change} /></td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </>
    );
}

// ─── Tab: Articles ────────────────────────────────────────────────────────
function ArticlesTab() {
    const [data, setData] = useState<WbStocksArticlesResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState('');
    const [searchInput, setSearchInput] = useState('');
    const [expanded, setExpanded] = useState<Set<number>>(new Set());
    const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' }>({ key: 'total_qty', dir: 'desc' });

    const load = useCallback(async () => {
        setLoading(true);
        try {
            setData(await api.getWarehouseStocksByArticle(search || undefined));
        } catch { /* ignore */ }
        setLoading(false);
    }, [search]);

    useEffect(() => { load(); }, [load]);

    const toggleExpand = (nmId: number) => {
        setExpanded(prev => {
            const next = new Set(prev);
            if (next.has(nmId)) next.delete(nmId); else next.add(nmId);
            return next;
        });
    };

    const sorted = useMemo(() => {
        if (!data) return [];
        return [...data.articles].sort((a, b) => {
            const key = sort.key as keyof WbArticleRow;
            const av = (a[key] ?? 0) as number | string;
            const bv = (b[key] ?? 0) as number | string;
            if (typeof av === 'string') return sort.dir === 'asc' ? av.localeCompare(bv as string) : (bv as string).localeCompare(av);
            return sort.dir === 'asc' ? (av as number) - (bv as number) : (bv as number) - (av as number);
        });
    }, [data, sort]);

    const toggleSort = (key: string) => {
        setSort(prev => prev.key === key ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'desc' });
    };

    const sortIcon = (key: string) => sort.key === key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : '';

    const handleSearch = () => setSearch(searchInput);

    const handleExport = () => {
        if (!data) return;
        const rows: Record<string, unknown>[] = [];
        for (const art of data.articles) {
            rows.push({
                'nm_id': art.nm_id,
                'Артикул': art.vendor_code,
                'Предмет': art.subject,
                'Бренд': art.brand,
                'Остаток': art.total_qty,
                'К клиенту': art.in_way_to_client,
                'От клиента': art.in_way_from_client,
            });
        }
        exportToExcel(rows, 'wb_stocks_articles');
    };

    if (loading && !data) return <div className="glass-card" style={{ textAlign: 'center', padding: 40 }}>Загрузка...</div>;

    return (
        <>
            <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <input
                    type="text"
                    placeholder="Поиск по артикулу или nm_id..."
                    value={searchInput}
                    onChange={e => setSearchInput(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && handleSearch()}
                    className="form-input"
                    style={{ flex: 1, minWidth: 200, maxWidth: 320 }}
                />
                <button className="btn btn-sm" onClick={handleSearch}>🔍</button>
                <div style={{ flex: 1 }} />
                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                    {data?.total_articles ?? 0} артикулов
                </span>
                <button className="btn btn-sm" onClick={handleExport}>📥 Excel</button>
            </div>
            {/* TODO: migrate to TanStackDataTable — has expandable rows with sub-rows per warehouse */}
            <div className="glass-card" style={{ overflowX: 'auto' }}>
                <table className="data-table" style={{ width: '100%', fontSize: 13 }}>
                    <thead>
                        <tr>
                            <th style={{ width: 30 }}></th>
                            <th style={{ textAlign: 'left', cursor: 'pointer' }} onClick={() => toggleSort('vendor_code')}>
                                Артикул{sortIcon('vendor_code')}
                            </th>
                            <th style={{ textAlign: 'left' }}>Предмет</th>
                            <th style={{ textAlign: 'left' }}>Бренд</th>
                            <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('total_qty')}>
                                Остаток{sortIcon('total_qty')}
                            </th>
                            <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('in_way_to_client')}>
                                К клиенту{sortIcon('in_way_to_client')}
                            </th>
                            <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('in_way_from_client')}>
                                От клиента{sortIcon('in_way_from_client')}
                            </th>
                            <th style={{ textAlign: 'right' }}>Складов</th>
                        </tr>
                    </thead>
                    <tbody>
                        {sorted.map((art) => {
                            const isExpanded = expanded.has(art.nm_id);
                            return (
                                <React.Fragment key={art.nm_id}>
                                    <tr
                                        onClick={() => toggleExpand(art.nm_id)}
                                        style={{ cursor: 'pointer' }}
                                    >
                                        <td style={{ textAlign: 'center' }}>{isExpanded ? '▾' : '▸'}</td>
                                        <td>
                                            <div style={{ fontWeight: 500 }}>{art.vendor_code}</div>
                                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>nm: {art.nm_id}</div>
                                        </td>
                                        <td>{art.subject}</td>
                                        <td>{art.brand}</td>
                                        <td style={{ textAlign: 'right', fontWeight: 600 }}>{formatNumber(art.total_qty, 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(art.in_way_to_client, 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(art.in_way_from_client, 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{art.warehouses.length}</td>
                                    </tr>
                                    {isExpanded && art.warehouses.map((wh) => (
                                        <tr key={`${art.nm_id}-${wh.name}`} style={{ background: 'rgba(0,0,0,0.02)' }}>
                                            <td></td>
                                            <td colSpan={3} style={{ paddingLeft: 28, color: 'var(--color-text-muted)', fontSize: 12 }}>
                                                ↳ {wh.name}
                                            </td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(wh.quantity, 0)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(wh.in_way_to_client, 0)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(wh.in_way_from_client, 0)}</td>
                                            <td></td>
                                        </tr>
                                    ))}
                                </React.Fragment>
                            );
                        })}
                        {sorted.length === 0 && (
                            <tr><td colSpan={8} style={{ textAlign: 'center', padding: 32, color: 'var(--color-text-muted)' }}>Нет данных</td></tr>
                        )}
                    </tbody>
                </table>
            </div>
        </>
    );
}

// ─── Tab: History ─────────────────────────────────────────────────────────
function HistoryTab() {
    const [data, setData] = useState<WbStockHistoryResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [warehouse, setWarehouse] = useState<string>('');
    const [dateFrom, setDateFrom] = useState(() => {
        const d = new Date(); d.setDate(d.getDate() - 14);
        return d.toISOString().slice(0, 10);
    });
    const [dateTo, setDateTo] = useState(() => new Date().toISOString().slice(0, 10));

    const load = useCallback(async () => {
        setLoading(true);
        try {
            setData(await api.getStockHistory(dateFrom, dateTo, warehouse || undefined));
        } catch { /* ignore */ }
        setLoading(false);
    }, [dateFrom, dateTo, warehouse]);

    useEffect(() => { load(); }, [load]);

    // Simple bar chart using div bars
    const maxQty = useMemo(() => {
        if (!data?.days.length) return 1;
        return Math.max(...data.days.map(d => d.total), 1);
    }, [data]);

    const handleExport = () => {
        if (!data) return;
        exportToExcel(data.days.map(d => ({
            'Дата': d.date,
            'Всего': d.total,
            'На складе': d.total_qty,
            'К клиенту': d.in_way_to_client,
            'От клиента': d.in_way_from_client,
            'Артикулов': d.articles_count,
        })), 'wb_stock_history');
    };

    return (
        <>
            <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="form-input" style={{ width: 140 }} />
                <span>—</span>
                <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} className="form-input" style={{ width: 140 }} />
                <select
                    value={warehouse}
                    onChange={e => setWarehouse(e.target.value)}
                    className="form-input"
                    style={{ minWidth: 160 }}
                >
                    <option value="">Все склады</option>
                    {data?.warehouses.map(w => (
                        <option key={w} value={w}>{w}</option>
                    ))}
                </select>
                <div style={{ flex: 1 }} />
                <button className="btn btn-sm" onClick={handleExport}>📥 Excel</button>
            </div>

            {loading && !data ? (
                <div className="glass-card" style={{ textAlign: 'center', padding: 40 }}>Загрузка...</div>
            ) : !data || data.days.length === 0 ? (
                <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Нет данных за выбранный период
                </div>
            ) : (
                <>
                    {/* Bar chart */}
                    <div className="glass-card" style={{ padding: 16, marginBottom: 12 }}>
                        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Динамика остатков</div>
                        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 160, overflow: 'hidden' }}>
                            {data.days.map((d) => {
                                const h = Math.max(4, (d.total / maxQty) * 140);
                                return (
                                    <div key={d.date} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: 0 }}>
                                        <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginBottom: 2, whiteSpace: 'nowrap', overflow: 'hidden' }}>
                                            {formatNumber(d.total, 0)}
                                        </div>
                                        <div
                                            style={{
                                                width: '80%', height: h, borderRadius: '4px 4px 0 0',
                                                background: 'var(--color-accent)',
                                                opacity: 0.8,
                                            }}
                                            title={`${d.date}: всего ${formatNumber(d.total, 0)} шт (склад: ${formatNumber(d.total_qty, 0)}, к кл: ${formatNumber(d.in_way_to_client, 0)}, от кл: ${formatNumber(d.in_way_from_client, 0)})`}
                                        />
                                        <div style={{ fontSize: 9, color: 'var(--color-text-muted)', marginTop: 2, whiteSpace: 'nowrap' }}>
                                            {d.date.slice(5)}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>

                    {/* Table */}
                    {(() => {
                        const historyTableData = data.days.map((d, i) => ({
                            ...d,
                            _change: i > 0 ? d.total - data.days[i - 1].total : null,
                            _index: i,
                        }));
                        const historyColumns: Column[] = [
                            { key: 'date', label: 'Дата', format: 'date' },
                            { key: 'total', label: 'Всего', align: 'right', render: (v: number) => <span style={{ fontWeight: 600 }}>{formatNumber(v, 0)}</span> },
                            { key: 'total_qty', label: 'На складе', align: 'right', format: 'number' },
                            { key: 'in_way_to_client', label: 'К клиенту', align: 'right', format: 'number' },
                            { key: 'in_way_from_client', label: 'От клиента', align: 'right', format: 'number' },
                            { key: 'articles_count', label: 'Артикулов', align: 'right', format: 'number' },
                            { key: '_change', label: 'Изм.', align: 'right', render: (_v: number | null, row: any) => row._index > 0 ? <ChangeBadge value={row._change} /> : '—' },
                        ];
                        return (
                            <TanStackDataTable
                                columns={historyColumns}
                                data={historyTableData}
                                enableSorting
                                enablePagination={false}
                            />
                        );
                    })()}
                </>
            )}
        </>
    );
}

// ─── Main Page ────────────────────────────────────────────────────────────
export default function WbStocksPage() {
    const [tab, setTab] = useState<Tab>('warehouses');
    const [data, setData] = useState<WbStocksResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);
    const [error, setError] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setData(await api.getWarehouseStocks());
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, []);

    const sync = async () => {
        setSyncing(true);
        try {
            // Отчёт кабинета (warehouse_remains), НЕ старый statistics supplier/stocks:
            // тот с 2026-07-15 отвечает 429/пустотой, и кнопка «висела» на ретраях по
            // 60с. Remains-синк мостом пересобирает и wb_warehouse_stocks — обновляется
            // и эта страница, и матрица потребности. Отчёт готовится до ~90с.
            const res = await api.syncWarehouseRemains();
            alert(`Синхронизировано ${res.synced} записей отчёта кабинета`);
            await load();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка синхронизации (отчёт WB готовится до ~90с — попробуйте ещё раз через минуту)');
        }
        setSyncing(false);
    };

    useEffect(() => { load(); }, [load]);

    const tabs: { key: Tab; label: string }[] = [
        { key: 'warehouses', label: 'По складам' },
        { key: 'articles', label: 'По артикулам' },
        { key: 'history', label: 'История' },
    ];

    return (
        <div>
            {/* Header */}
            <div className="page-header">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
                    <div>
                        <h1 style={{ margin: 0 }}>Остатки WB</h1>
                        {data?.last_synced_at && (
                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 4 }}>
                                Обновлено: {formatDate(data.last_synced_at)}
                            </div>
                        )}
                    </div>
                    <button className="btn btn-primary" onClick={sync} disabled={syncing}>
                        {syncing ? '⏳ Синхронизация...' : '🔄 Синхронизировать'}
                    </button>
                </div>
            </div>

            {loading && !data ? (
                <div className="glass-card" style={{ textAlign: 'center', padding: 40 }}>Загрузка...</div>
            ) : error ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-danger)' }}>{error}</div>
            ) : !data || data.warehouses.length === 0 ? (
                <div className="glass-card" style={{ padding: 64, textAlign: 'center' }}>
                    <div style={{ fontSize: 48, marginBottom: 16 }}>🏭</div>
                    <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>Нет данных по складам</div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14, marginBottom: 16 }}>
                        Нажмите «Синхронизировать» чтобы загрузить остатки с WB
                    </div>
                </div>
            ) : (
                <>
                    {/* KPI Cards */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 16 }}>
                        <KpiCard label="Всего" value={data.total_qty + data.total_in_way_to_client + data.total_in_way_from_client} suffix="шт" />
                        <KpiCard label="На складах WB" value={data.total_qty} prev={data.yesterday_total_qty} suffix="шт" />
                        <KpiCard label="К клиенту" value={data.total_in_way_to_client} />
                        <KpiCard label="От клиента" value={data.total_in_way_from_client} />
                        <KpiCard label="Складов" value={data.total_warehouses} />
                    </div>

                    {/* Tabs */}
                    <div style={{ display: 'flex', gap: 0, marginBottom: 16, borderBottom: '2px solid var(--color-border)' }}>
                        {tabs.map(t => (
                            <button
                                key={t.key}
                                onClick={() => setTab(t.key)}
                                style={{
                                    padding: '8px 20px',
                                    background: 'none',
                                    border: 'none',
                                    borderBottom: tab === t.key ? '2px solid var(--color-accent)' : '2px solid transparent',
                                    marginBottom: -2,
                                    fontWeight: tab === t.key ? 600 : 400,
                                    color: tab === t.key ? 'var(--color-text)' : 'var(--color-text-muted)',
                                    cursor: 'pointer',
                                    fontSize: 14,
                                }}
                            >
                                {t.label}
                            </button>
                        ))}
                    </div>

                    {/* Tab content */}
                    {tab === 'warehouses' && <WarehousesTab data={data} />}
                    {tab === 'articles' && <ArticlesTab />}
                    {tab === 'history' && <HistoryTab />}
                </>
            )}
        </div>
    );
}

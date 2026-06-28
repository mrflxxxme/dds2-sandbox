'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { formatNumber } from '@/lib/utils';
import { ffListStock, ffMe } from '@/lib/api/ff';
import type { FfStockRow, FfMe } from '@/types/ff';
import { ProjectBadge } from '../../../_components/badges';
import { useTableSort, SortableTh, FfTabs, type SortValue } from '../../../_components/table';

const ALL_TAB = '__all__';

export default function FfStockPage() {
    const [rows, setRows] = useState<FfStockRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [barcode, setBarcode] = useState('');
    const [search, setSearch] = useState('');

    // «Все» (no filter) by default; one tab per ffMe project.
    const [activeProject, setActiveProject] = useState(ALL_TAB);
    const [me, setMe] = useState<FfMe | null>(null);
    // Server-side totals over ALL matching rows (the page is capped at 500).
    const [totals, setTotals] = useState({ total: 0, totalQuantity: 0 });

    const load = useCallback(
        (opts: { project_slug: string; barcode: string }, signal?: AbortSignal) => {
            setLoading(true);
            setError('');
            ffListStock({
                limit: 500,
                project_slug: opts.project_slug === ALL_TAB ? undefined : opts.project_slug,
                barcode: opts.barcode || undefined,
            })
                .then((res) => {
                    if (signal?.aborted) return;
                    setRows(res.items);
                    setTotals({ total: res.total, totalQuantity: res.total_quantity });
                })
                .catch((err: unknown) => {
                    if (signal?.aborted) return;
                    setError(err instanceof Error ? err.message : 'Не удалось загрузить остатки');
                })
                .finally(() => {
                    if (signal?.aborted) return;
                    setLoading(false);
                });
        },
        [],
    );

    useEffect(() => {
        const controller = new AbortController();
        load({ project_slug: activeProject, barcode: search }, controller.signal);
        return () => controller.abort();
    }, [load, activeProject, search]);

    useEffect(() => {
        const controller = new AbortController();
        ffMe()
            .then((res) => {
                if (controller.signal.aborted) return;
                setMe(res);
            })
            .catch(() => {
                /* tabs degrade to «Все» only */
            });
        return () => controller.abort();
    }, []);

    const reload = useCallback(() => {
        load({ project_slug: activeProject, barcode: search });
    }, [load, activeProject, search]);

    const onSearch = (e: React.FormEvent) => {
        e.preventDefault();
        setSearch(barcode.trim());
    };

    const tabs = useMemo(
        () => [
            { key: ALL_TAB, label: 'Все' },
            ...(me?.projects ?? []).map((p) => ({ key: p.slug, label: p.name })),
        ],
        [me],
    );

    const getValue = useCallback((row: FfStockRow, key: string): SortValue => {
        switch (key) {
            case 'project':
                return row.project_name;
            case 'warehouse':
                return row.warehouse_name;
            case 'barcode':
                return row.barcode;
            case 'product':
                return row.product_name;
            case 'quantity':
                return row.quantity;
            case 'available':
                return row.available;
            case 'reserved':
                return row.reserved;
            case 'defect':
                return row.defect_quantity;
            default:
                return null;
        }
    }, []);

    const { sorted, sortKey, sortDir, toggleSort } = useTableSort(rows, getValue);

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Остатки</h1>
                    <p className="page-subtitle">Товар на складах фулфилмента</p>
                </div>
            </div>

            {tabs.length > 1 && (
                <FfTabs items={tabs} active={activeProject} onChange={setActiveProject} />
            )}

            {!loading && !error && rows.length > 0 && (
                <div className="stats-grid">
                    <div className="stat-card">
                        <div className="stat-card-label">Всего на складах, шт</div>
                        <div className="stat-card-value">{formatNumber(totals.totalQuantity, 0)}</div>
                    </div>
                    <div className="stat-card">
                        <div className="stat-card-label">Позиций</div>
                        <div className="stat-card-value" style={{ color: 'var(--color-accent)' }}>{formatNumber(totals.total, 0)}</div>
                    </div>
                </div>
            )}

            <form onSubmit={onSearch} className="ff-toolbar">
                <div className="ff-toolbar-field" style={{ flex: 1, minWidth: 200, maxWidth: 420 }}>
                    <label htmlFor="stock-search">Штрихкод</label>
                    <input
                        id="stock-search"
                        type="text"
                        inputMode="numeric"
                        placeholder="Поиск по штрихкоду"
                        value={barcode}
                        onChange={(e) => setBarcode(e.target.value)}
                    />
                </div>
                <button type="submit" className="btn btn-primary" style={{ height: 38 }}>
                    Найти
                </button>
            </form>

            {loading && (
                <div className="ff-feed">
                    <div className="ff-skeleton" />
                    <div className="ff-skeleton" />
                    <div className="ff-skeleton" />
                </div>
            )}

            {!loading && error && (
                <div className="glass-card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, color: 'var(--color-danger)' }}>
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={reload}>
                        Повторить
                    </button>
                </div>
            )}

            {!loading && !error && rows.length === 0 && (
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-text">
                            {search ? 'По этому штрихкоду ничего не найдено' : 'Остатков нет'}
                        </div>
                    </div>
                </div>
            )}

            {!loading && !error && sorted.length > 0 && (
                <div className="glass-card" style={{ overflow: 'auto', padding: 0 }}>
                    <table className="data-table">
                        <thead>
                            <tr>
                                <SortableTh label="Проект" sortKey="project" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <SortableTh label="Склад" sortKey="warehouse" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <SortableTh label="ШК" sortKey="barcode" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <SortableTh label="Товар" sortKey="product" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <SortableTh label="Остаток" sortKey="quantity" activeKey={sortKey} dir={sortDir} onSort={toggleSort} align="right" />
                                <SortableTh label="Доступно" sortKey="available" activeKey={sortKey} dir={sortDir} onSort={toggleSort} align="right" />
                                <SortableTh label="Резерв" sortKey="reserved" activeKey={sortKey} dir={sortDir} onSort={toggleSort} align="right" />
                                <SortableTh label="Брак" sortKey="defect" activeKey={sortKey} dir={sortDir} onSort={toggleSort} align="right" />
                            </tr>
                        </thead>
                        <tbody>
                            {sorted.map((row) => (
                                <tr key={`${row.project_slug}-${row.warehouse_id}-${row.barcode}`}>
                                    <td><ProjectBadge name={row.project_name} /></td>
                                    <td>{row.warehouse_name}</td>
                                    <td style={{ color: 'var(--color-text-dim)', fontVariantNumeric: 'tabular-nums' }}>
                                        <div>{row.barcode}</div>
                                        {row.article && <div style={{ color: 'var(--color-text-dim)' }}>{row.article}</div>}
                                    </td>
                                    <td>{row.product_name || '—'}</td>
                                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{formatNumber(row.quantity, 0)}</td>
                                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{formatNumber(row.available, 0)}</td>
                                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{formatNumber(row.reserved, 0)}</td>
                                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: row.defect_quantity > 0 ? 'var(--color-danger)' : undefined }}>{formatNumber(row.defect_quantity, 0)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

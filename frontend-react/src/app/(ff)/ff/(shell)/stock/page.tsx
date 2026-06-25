'use client';

import { useCallback, useEffect, useState } from 'react';
import { formatNumber } from '@/lib/utils';
import { ffListStock } from '@/lib/api/ff';
import type { FfStockRow } from '@/types/ff';
import { ProjectBadge } from '../../../_components/badges';

export default function FfStockPage() {
    const [rows, setRows] = useState<FfStockRow[]>([]);
    const [totalQty, setTotalQty] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [barcode, setBarcode] = useState('');
    const [search, setSearch] = useState('');

    const load = useCallback(
        (term: string, signal?: AbortSignal) => {
            setLoading(true);
            setError('');
            ffListStock({ barcode: term || undefined, limit: 100 })
                .then((res) => {
                    if (signal?.aborted) return;
                    setRows(res.items);
                    setTotalQty(res.total_quantity);
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
        load(search, controller.signal);
        return () => controller.abort();
    }, [load, search]);

    const onSearch = (e: React.FormEvent) => {
        e.preventDefault();
        setSearch(barcode.trim());
    };

    return (
        <div>
            <h1 className="ff-section-title">Остатки</h1>
            <p className="ff-section-sub">
                Всего на складах: {formatNumber(totalQty, 0)} шт
            </p>

            <form onSubmit={onSearch} className="ff-form-grid" style={{ borderTop: 'none' }}>
                <div className="form-group" style={{ gridColumn: '1 / 2' }}>
                    <label className="form-label">Поиск по штрихкоду</label>
                    <input
                        className="form-input"
                        type="text"
                        inputMode="numeric"
                        placeholder="Штрихкод"
                        value={barcode}
                        onChange={(e) => setBarcode(e.target.value)}
                    />
                </div>
                <button type="submit" className="btn btn-primary">
                    Найти
                </button>
            </form>

            <div style={{ height: 12 }} />

            {loading && (
                <div className="ff-card">
                    <div className="ff-muted">Загрузка…</div>
                </div>
            )}

            {!loading && error && (
                <div className="ff-error">
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => load(search)}>
                        Повторить
                    </button>
                </div>
            )}

            {!loading && !error && rows.length === 0 && (
                <div className="ff-card">
                    <div className="ff-muted">Остатков не найдено</div>
                </div>
            )}

            {!loading && !error && rows.length > 0 && (
                <div className="ff-card ff-stock-scroll">
                    <table className="ff-stock-table">
                        <thead>
                            <tr>
                                <th>Товар</th>
                                <th className="num">Всего</th>
                                <th className="num">Дост.</th>
                                <th className="num">Резерв</th>
                                <th className="num">Брак</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((row) => (
                                <tr key={`${row.project_slug}-${row.warehouse_id}-${row.barcode}`}>
                                    <td>
                                        <div style={{ marginBottom: 4 }}>
                                            <ProjectBadge name={row.project_name} />
                                        </div>
                                        <div>{row.product_name || '—'}</div>
                                        <div className="ff-item-bc">{row.barcode}</div>
                                        <div className="ff-item-bc">{row.warehouse_name}</div>
                                    </td>
                                    <td className="num">{formatNumber(row.quantity, 0)}</td>
                                    <td className="num">{formatNumber(row.available, 0)}</td>
                                    <td className="num">{formatNumber(row.reserved, 0)}</td>
                                    <td className="num">{formatNumber(row.defect_quantity, 0)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

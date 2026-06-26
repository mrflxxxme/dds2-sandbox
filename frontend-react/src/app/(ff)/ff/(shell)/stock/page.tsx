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

    const load = useCallback((term: string, signal?: AbortSignal) => {
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
    }, []);

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
            <div className="page-header">
                <div>
                    <h1 className="page-title">Остатки</h1>
                    <p className="page-subtitle">Всего на складах: {formatNumber(totalQty, 0)} шт</p>
                </div>
            </div>

            <form onSubmit={onSearch} style={{ display: 'flex', gap: 8, marginBottom: 16, maxWidth: 360 }}>
                <input
                    className="form-input"
                    type="text"
                    inputMode="numeric"
                    placeholder="Поиск по штрихкоду"
                    style={{ flex: 1 }}
                    value={barcode}
                    onChange={(e) => setBarcode(e.target.value)}
                />
                <button type="submit" className="btn btn-primary">
                    Найти
                </button>
            </form>

            {loading && (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Загрузка…
                </div>
            )}

            {!loading && error && (
                <div className="auth-error" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => load(search)}>
                        Повторить
                    </button>
                </div>
            )}

            {!loading && !error && rows.length === 0 && (
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-text">Остатков не найдено</div>
                    </div>
                </div>
            )}

            {!loading && !error && rows.length > 0 && (
                <div className="glass-card" style={{ overflow: 'auto', padding: 0 }}>
                    <table className="data-table" style={{ fontSize: 13 }}>
                        <thead>
                            <tr>
                                <th>Проект</th>
                                <th>Склад</th>
                                <th>ШК</th>
                                <th>Товар</th>
                                <th style={{ textAlign: 'right' }}>Остаток</th>
                                <th style={{ textAlign: 'right' }}>Доступно</th>
                                <th style={{ textAlign: 'right' }}>Резерв</th>
                                <th style={{ textAlign: 'right' }}>Брак</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((row) => (
                                <tr key={`${row.project_slug}-${row.warehouse_id}-${row.barcode}`}>
                                    <td><ProjectBadge name={row.project_name} /></td>
                                    <td>{row.warehouse_name}</td>
                                    <td style={{ color: 'var(--color-text-dim)' }}>{row.barcode}</td>
                                    <td>{row.product_name || '—'}</td>
                                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{formatNumber(row.quantity, 0)}</td>
                                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{formatNumber(row.available, 0)}</td>
                                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{formatNumber(row.reserved, 0)}</td>
                                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{formatNumber(row.defect_quantity, 0)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

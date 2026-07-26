'use client';
/**
 * Матрица «товар × склад продавца WB»: что стоит на FBS и что могли бы туда
 * поставить.
 *
 * Колонки — только склады со связкой на наши: без привязки поставить нечего.
 * Обе цифры ячейки приходят из общего превью остатков, поэтому эта вкладка и
 * «Остатки» не могут разойтись в числах.
 *
 * 🔴 Деньги (реализация, маржа) считаются ПО КАРТОЧКЕ и по складам не делятся —
 * воронка WB не знает, с какого склада уехала единица. Поэтому они живут
 * отдельными колонками справа, а не в ячейках складов.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { exportToExcel } from '@/lib/utils';
import type { FbsMatrix, FbsMatrixRow } from '@/types/api';

const TREND_OPTIONS = [7, 14, 30] as const;

/** Ячейка склада: слева — что стоит в WB, справа — что можем довезти. */
function Cell({ wb, can }: { wb: number | null | undefined; can: number }) {
    const has = (wb ?? 0) > 0 || can > 0;
    if (!has) return <span className="sc-matrix-cell-empty">—</span>;
    // Пусто в кабинете при живом «можем» — главный сигнал: везти сюда.
    const gap = can > 0 && (wb ?? 0) === 0;
    return (
        <span title={`Стоит на FBS: ${wb == null ? 'неизвестно' : formatNumber(wb, 0)} · `
            + `можем поставить: ${formatNumber(can, 0)}`}>
            <span style={{ fontWeight: 600, color: gap ? 'var(--color-danger)' : 'var(--color-text)' }}>
                {wb == null ? '—' : formatNumber(wb, 0)}
            </span>
            <span style={{ color: 'var(--color-text-dim)' }}> / </span>
            <span style={{ color: 'var(--color-text-muted)' }}>{formatNumber(can, 0)}</span>
        </span>
    );
}

function Margin({ pct }: { pct: number | null | undefined }) {
    if (pct == null) return <span className="sc-matrix-cell-empty">—</span>;
    const color = pct < 0 ? 'var(--color-danger)'
        : pct < 15 ? 'var(--color-warning)'
        : 'var(--color-success)';
    return <span style={{ color, fontWeight: 600 }}>{formatNumber(pct, 1)}%</span>;
}

export default function FbsMatrixTab({ refreshTick }: { refreshTick: number }) {
    const [data, setData] = useState<FbsMatrix | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [trendDays, setTrendDays] = useState<7 | 14 | 30>(14);
    const [search, setSearch] = useState('');
    const [onlyGap, setOnlyGap] = useState(false);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.getFbsStockMatrix(trendDays);
            if (signal?.aborted) return;
            setData(res);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Не удалось загрузить матрицу');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [trendDays]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load, refreshTick]);

    // useMemo, а не `?? []`: новый литерал на каждый рендер пересобирал бы
    // фильтрацию строк вхолостую (react-hooks/exhaustive-deps).
    const warehouses = useMemo(() => data?.warehouses ?? [], [data]);

    const rows = useMemo(() => {
        const q = search.trim().toLowerCase();
        return (data?.rows ?? []).filter(r => {
            if (onlyGap) {
                // «Есть что везти»: хоть на одном складе пусто, а поставить можем.
                const gap = warehouses.some(w => {
                    const c = r.cells?.[String(w.wb_warehouse_id)];
                    return c && (c.wb ?? 0) === 0 && c.can > 0;
                });
                if (!gap) return false;
            }
            if (!q) return true;
            return (
                (r.barcode ?? '').toLowerCase().includes(q)
                || (r.article_seller ?? '').toLowerCase().includes(q)
                || (r.brand ?? '').toLowerCase().includes(q)
                || (r.subject ?? '').toLowerCase().includes(q)
                || String(r.nm_id ?? '').includes(q)
            );
        });
    }, [data, search, onlyGap, warehouses]);

    const handleExport = () => {
        exportToExcel(
            rows.map(r => ({
                Товар: r.article_seller || r.barcode || '',
                Баркод: r.barcode ?? '',
                Бренд: r.brand ?? '',
                ...Object.fromEntries(warehouses.map(w => {
                    const c = r.cells?.[String(w.wb_warehouse_id)];
                    return [w.name || `#${w.wb_warehouse_id}`,
                        `${c?.wb ?? '—'} / ${c?.can ?? 0}`];
                })),
                'Итого стоит': r.total_wb,
                'Итого можем': r.total_can,
                Реализация: Number(r.revenue ?? 0),
                'Маржа, %': r.margin_pct ?? '',
            })),
            'Матрица FBS-складов',
        );
    };

    if (loading && !data) {
        return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка матрицы…</div>;
    }
    if (error) {
        return (
            <div className="glass-card" style={{ padding: 24 }}>
                <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>
                <button className="btn btn-secondary btn-sm" onClick={() => load()}>Повторить</button>
            </div>
        );
    }
    if (!warehouses.length) {
        return (
            <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                Нет складов продавца WB со связкой на наши склады.<br />
                Свяжите их на вкладке «Склады» — без привязки поставлять нечего.
            </div>
        );
    }

    return (
        <div className="sc-matrix-card">
            <div className="sc-matrix-filter-bar">
                <input
                    className="sc-matrix-search-input"
                    placeholder="Артикул, баркод, бренд, предмет, nmId"
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                />
                <div className="sc-matrix-mode-toggle">
                    {TREND_OPTIONS.map(d => (
                        <button
                            key={d}
                            className={`btn btn-sm ${trendDays === d ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setTrendDays(d)}
                        >
                            {d} дн
                        </button>
                    ))}
                </div>
                <label className="sc-matrix-unshipped-toggle">
                    <input type="checkbox" checked={onlyGap} onChange={e => setOnlyGap(e.target.checked)} />
                    Только недовоз
                </label>
                <button className="sc-matrix-excel-btn" onClick={handleExport}>📊 Excel</button>
            </div>

            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', margin: '0 0 12px' }}>
                В ячейке: <strong>стоит на FBS</strong> / <span style={{ color: 'var(--color-text-muted)' }}>можем поставить</span>.
                Красное слева — на складе пусто, а везти есть что.
                {data && !data.wb_stock_known && ' Остаток кабинета прочитать не удалось — слева прочерки.'}
                {' '}Реализация и маржа — по карточке за {trendDays} дн, по складам не делятся.
            </div>

            <div className="sc-matrix-wrap">
                <table className="sc-matrix-table sc-matrix-table-sticky">
                    <thead>
                        <tr>
                            <th className="sc-matrix-th-fixed" style={{ left: 0, minWidth: 240 }}>Товар</th>
                            {warehouses.map(w => (
                                <th key={w.wb_warehouse_id} className="sc-matrix-th-num" style={{ minWidth: 110 }}>
                                    {w.name || `#${w.wb_warehouse_id}`}
                                </th>
                            ))}
                            <th className="sc-matrix-th-num">Итого</th>
                            <th className="sc-matrix-th-num">Реализация</th>
                            <th className="sc-matrix-th-num">Маржа</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((r: FbsMatrixRow) => (
                            <tr key={r.nomenclature_id}>
                                <td className="sc-matrix-td-fixed" style={{ left: 0 }}>
                                    <div style={{ fontWeight: 500 }}>{r.article_seller || r.barcode}</div>
                                    <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                        {r.barcode}{r.brand ? ` · ${r.brand}` : ''}
                                    </div>
                                </td>
                                {warehouses.map(w => {
                                    const c = r.cells?.[String(w.wb_warehouse_id)];
                                    return (
                                        <td key={w.wb_warehouse_id} className="sc-matrix-td-num">
                                            <Cell wb={c?.wb} can={c?.can ?? 0} />
                                        </td>
                                    );
                                })}
                                <td className="sc-matrix-td-num">
                                    <strong>{formatNumber(r.total_wb, 0)}</strong>
                                    <span style={{ color: 'var(--color-text-dim)' }}> / </span>
                                    <span style={{ color: 'var(--color-text-muted)' }}>{formatNumber(r.total_can, 0)}</span>
                                </td>
                                <td className="sc-matrix-td-num">
                                    {Number(r.revenue ?? 0) > 0
                                        ? formatNumber(Number(r.revenue), 0)
                                        : <span className="sc-matrix-cell-empty">—</span>}
                                </td>
                                <td className="sc-matrix-td-num"><Margin pct={r.margin_pct} /></td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {rows.length === 0 && (
                <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Ничего не найдено под текущими фильтрами.
                </div>
            )}
        </div>
    );
}

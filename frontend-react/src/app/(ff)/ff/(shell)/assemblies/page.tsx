'use client';

import { useCallback, useEffect, useState } from 'react';
import { formatNumber, formatDate } from '@/lib/utils';
import {
    ffListAssemblies,
    ffStartAssembly,
    ffReadyAssembly,
    ffShipAssembly,
} from '@/lib/api/ff';
import type { FfAssemblyRow } from '@/types/ff';
import { ProjectBadge, AssemblyStatusBadge } from '../../../_components/badges';

export default function FfAssembliesPage() {
    const [rows, setRows] = useState<FfAssemblyRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [busyId, setBusyId] = useState<number | null>(null);
    const [expandedId, setExpandedId] = useState<number | null>(null);
    const [readyRow, setReadyRow] = useState<FfAssemblyRow | null>(null);
    const [pallets, setPallets] = useState('');
    const [weight, setWeight] = useState('');
    const [readyError, setReadyError] = useState('');

    const load = useCallback((signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        ffListAssemblies({ limit: 50 })
            .then((res) => {
                if (signal?.aborted) return;
                setRows(res.items);
            })
            .catch((err: unknown) => {
                if (signal?.aborted) return;
                setError(err instanceof Error ? err.message : 'Не удалось загрузить заявки');
            })
            .finally(() => {
                if (signal?.aborted) return;
                setLoading(false);
            });
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    const replaceRow = (updated: FfAssemblyRow) =>
        setRows((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));

    const handleStart = async (row: FfAssemblyRow) => {
        setBusyId(row.id);
        setError('');
        try {
            replaceRow(await ffStartAssembly(row.id));
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : 'Ошибка');
        } finally {
            setBusyId(null);
        }
    };

    const handleShip = async (row: FfAssemblyRow) => {
        setBusyId(row.id);
        setError('');
        try {
            replaceRow(await ffShipAssembly(row.id));
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : 'Ошибка отгрузки');
        } finally {
            setBusyId(null);
        }
    };

    const openReady = (row: FfAssemblyRow) => {
        setReadyError('');
        setPallets(row.pallets_count != null ? String(row.pallets_count) : '');
        setWeight(row.pallet_weight_kg != null ? String(Number(row.pallet_weight_kg)) : '');
        setReadyRow(row);
    };

    const submitReady = async () => {
        if (!readyRow) return;
        const pc = Number(pallets);
        const pw = Number(weight);
        if (!(pc > 0) || !(pw > 0)) {
            setReadyError('Укажите число паллет и вес паллеты больше нуля');
            return;
        }
        setReadyError('');
        setBusyId(readyRow.id);
        try {
            const updated = await ffReadyAssembly(readyRow.id, {
                pallets_count: pc,
                pallet_weight_kg: pw,
            });
            replaceRow(updated);
            setReadyRow(null);
        } catch (err: unknown) {
            setReadyError(err instanceof Error ? err.message : 'Ошибка');
        } finally {
            setBusyId(null);
        }
    };

    return (
        <div>
            <div className="page-header">
                <div>
                    <h1 className="page-title">Заявки на сборку</h1>
                    <p className="page-subtitle">Сборка и отгрузка поставок</p>
                </div>
            </div>

            {loading && (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Загрузка…
                </div>
            )}

            {!loading && error && (
                <div className="auth-error" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => load()}>
                        Повторить
                    </button>
                </div>
            )}

            {!loading && !error && rows.length === 0 && (
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-text">Нет заявок</div>
                    </div>
                </div>
            )}

            {!loading && !error && rows.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                    {rows.map((row) => {
                        const expanded = expandedId === row.id;
                        return (
                            <div key={row.id} className="glass-card">
                                <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
                                    <span style={{ fontSize: 16, fontWeight: 700 }}>№ {row.number}</span>
                                    <ProjectBadge name={row.project_name} />
                                    <AssemblyStatusBadge status={row.status} />
                                </div>
                                <div style={{ color: 'var(--color-text-muted)', fontSize: 14, lineHeight: 1.6 }}>
                                    <div>
                                        <span style={{ color: 'var(--color-text)', fontWeight: 600 }}>{row.warehouse_name}</span>
                                        {row.wb_warehouse_name ? ` → ${row.wb_warehouse_name}` : ''}
                                    </div>
                                    <div>
                                        {row.package_type && <>Упаковка: {row.package_type} · </>}
                                        Позиций: {formatNumber(row.items.length, 0)}
                                    </div>
                                    {row.pallets_count != null && (
                                        <div>
                                            Паллет: {formatNumber(row.pallets_count, 0)}
                                            {row.pallet_weight_kg != null && (
                                                <> · {formatNumber(Number(row.pallet_weight_kg), 0)} кг/паллета</>
                                            )}
                                        </div>
                                    )}
                                    {row.actual_ready_date ? (
                                        <div>Готово: {formatDate(row.actual_ready_date)}</div>
                                    ) : row.estimated_ready_date ? (
                                        <div>Ожид. готовность: {formatDate(row.estimated_ready_date)}</div>
                                    ) : null}
                                </div>

                                {expanded && (
                                    <div style={{ overflowX: 'auto', marginTop: 12 }}>
                                        <table className="data-table" style={{ fontSize: 13 }}>
                                            <thead>
                                                <tr>
                                                    <th>Товар</th>
                                                    <th style={{ textAlign: 'right' }}>Кол-во</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {row.items.map((it) => (
                                                    <tr key={it.barcode}>
                                                        <td>
                                                            <div>{it.product_name || '—'}</div>
                                                            <div style={{ color: 'var(--color-text-dim)', fontSize: 11 }}>{it.barcode}</div>
                                                        </td>
                                                        <td style={{ textAlign: 'right' }}>{formatNumber(it.quantity, 0)}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                )}

                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
                                    <button
                                        className="btn btn-secondary"
                                        onClick={() => setExpandedId(expanded ? null : row.id)}
                                    >
                                        {expanded ? 'Скрыть состав' : `Показать состав (${formatNumber(row.items.length, 0)})`}
                                    </button>
                                    {row.status === 'PENDING' && (
                                        <button
                                            className="btn btn-primary"
                                            disabled={busyId === row.id}
                                            onClick={() => handleStart(row)}
                                        >
                                            {busyId === row.id ? '…' : 'Взять в работу'}
                                        </button>
                                    )}
                                    {row.status === 'IN_PROGRESS' && (
                                        <button className="btn btn-primary" onClick={() => openReady(row)}>
                                            Готово
                                        </button>
                                    )}
                                    {row.status === 'READY' && (
                                        <button className="btn btn-secondary" disabled title="Ждём назначения машины логистикой">
                                            Ждём машину
                                        </button>
                                    )}
                                    {row.status === 'VEHICLE_ASSIGNED' && (
                                        <button
                                            className="btn btn-success"
                                            disabled={busyId === row.id || !row.vehicle_assigned}
                                            title={row.vehicle_assigned ? undefined : 'Ждём назначения машины логистикой'}
                                            onClick={() => handleShip(row)}
                                        >
                                            {busyId === row.id ? '…' : 'Отгрузил'}
                                        </button>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}

            {readyRow && (
                <div className="modal-overlay" onClick={() => setReadyRow(null)}>
                    <div className="modal-card" onClick={(e) => e.stopPropagation()}>
                        <h2 className="modal-title">Готовность № {readyRow.number}</h2>
                        <div className="form-group" style={{ marginBottom: 16 }}>
                            <label className="form-label">Паллет, шт</label>
                            <input
                                className="form-input"
                                type="number"
                                min={1}
                                inputMode="numeric"
                                value={pallets}
                                onChange={(e) => setPallets(e.target.value)}
                            />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Вес паллеты, кг</label>
                            <input
                                className="form-input"
                                type="number"
                                min={0}
                                step="0.1"
                                inputMode="decimal"
                                value={weight}
                                onChange={(e) => setWeight(e.target.value)}
                            />
                        </div>
                        {readyError && (
                            <div className="auth-error" style={{ marginTop: 16 }}>{readyError}</div>
                        )}
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 24 }}>
                            <button
                                className="btn btn-secondary"
                                disabled={busyId === readyRow.id}
                                onClick={() => setReadyRow(null)}
                            >
                                Отмена
                            </button>
                            <button
                                className="btn btn-primary"
                                disabled={busyId === readyRow.id}
                                onClick={submitReady}
                            >
                                {busyId === readyRow.id ? 'Сохранение…' : 'Готово'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

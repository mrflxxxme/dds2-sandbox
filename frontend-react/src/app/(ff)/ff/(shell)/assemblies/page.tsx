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
    const [readyId, setReadyId] = useState<number | null>(null);
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
        setReadyId(row.id);
    };

    const submitReady = async (row: FfAssemblyRow) => {
        const pc = Number(pallets);
        const pw = Number(weight);
        if (!(pc > 0) || !(pw > 0)) {
            setReadyError('Укажите число паллет и вес паллеты больше нуля');
            return;
        }
        setReadyError('');
        setBusyId(row.id);
        try {
            const updated = await ffReadyAssembly(row.id, {
                pallets_count: pc,
                pallet_weight_kg: pw,
            });
            replaceRow(updated);
            setReadyId(null);
        } catch (err: unknown) {
            setReadyError(err instanceof Error ? err.message : 'Ошибка');
        } finally {
            setBusyId(null);
        }
    };

    return (
        <div>
            <h1 className="ff-section-title">Заявки на сборку</h1>
            <p className="ff-section-sub">Сборка и отгрузка поставок</p>

            {loading && (
                <div className="ff-card">
                    <div className="ff-muted">Загрузка…</div>
                </div>
            )}

            {!loading && error && (
                <div className="ff-error">
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => load()}>
                        Повторить
                    </button>
                </div>
            )}

            {!loading && !error && rows.length === 0 && (
                <div className="ff-card">
                    <div className="ff-muted">Нет заявок</div>
                </div>
            )}

            {!loading && !error &&
                rows.map((row) => {
                    const expanded = expandedId === row.id;
                    const readyOpen = readyId === row.id;
                    return (
                        <div key={row.id} className="ff-card">
                            <div className="ff-row-head">
                                <ProjectBadge name={row.project_name} />
                                <AssemblyStatusBadge status={row.status} />
                            </div>
                            <div className="ff-number">№ {row.number}</div>
                            <div className="ff-meta">
                                <strong>{row.warehouse_name}</strong>
                                {row.wb_warehouse_name ? ` → ${row.wb_warehouse_name}` : ''}
                            </div>
                            <div className="ff-meta">
                                {row.package_type && <>Упаковка: {row.package_type} · </>}
                                Позиций: {formatNumber(row.items.length, 0)}
                            </div>
                            {row.pallets_count != null && (
                                <div className="ff-meta">
                                    Паллет: {formatNumber(row.pallets_count, 0)}
                                    {row.pallet_weight_kg != null && (
                                        <> · {formatNumber(Number(row.pallet_weight_kg), 0)} кг/паллета</>
                                    )}
                                </div>
                            )}
                            <div className="ff-meta">
                                {row.actual_ready_date
                                    ? `Готово: ${formatDate(row.actual_ready_date)}`
                                    : row.estimated_ready_date
                                      ? `Ожид. готовность: ${formatDate(row.estimated_ready_date)}`
                                      : ''}
                            </div>

                            <button
                                className="ff-disclosure"
                                onClick={() => setExpandedId(expanded ? null : row.id)}
                            >
                                {expanded ? 'Скрыть состав' : `Показать состав (${formatNumber(row.items.length, 0)})`}
                            </button>
                            {expanded && (
                                <div>
                                    {row.items.map((it) => (
                                        <div key={it.barcode} className="ff-item-line">
                                            <span className="ff-item-name">
                                                {it.product_name || '—'}
                                                <br />
                                                <span className="ff-item-bc">{it.barcode}</span>
                                            </span>
                                            <span className="ff-item-qty">
                                                {formatNumber(it.quantity, 0)} шт
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            )}

                            {/* Ready form */}
                            {readyOpen && (
                                <div style={{ marginTop: 8 }}>
                                    <div className="ff-form-grid">
                                        <div className="form-group">
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
                                    </div>
                                    {readyError && (
                                        <div className="ff-hint-warn" style={{ marginTop: 4 }}>
                                            {readyError}
                                        </div>
                                    )}
                                    <div className="ff-actions">
                                        <button
                                            className="btn btn-primary"
                                            disabled={busyId === row.id}
                                            onClick={() => submitReady(row)}
                                        >
                                            {busyId === row.id ? 'Сохранение…' : 'Подтвердить готовность'}
                                        </button>
                                        <button
                                            className="btn btn-secondary"
                                            disabled={busyId === row.id}
                                            onClick={() => setReadyId(null)}
                                        >
                                            Отмена
                                        </button>
                                    </div>
                                </div>
                            )}

                            {/* Status-driven actions */}
                            {!readyOpen && (
                                <div className="ff-actions">
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
                                        <button
                                            className="btn btn-secondary"
                                            disabled
                                            title="Ждём назначения машины логистикой"
                                        >
                                            Ждём машину
                                        </button>
                                    )}
                                    {row.status === 'VEHICLE_ASSIGNED' && (
                                        <button
                                            className="btn btn-primary"
                                            disabled={busyId === row.id || !row.vehicle_assigned}
                                            title={
                                                row.vehicle_assigned
                                                    ? undefined
                                                    : 'Ждём назначения машины логистикой'
                                            }
                                            onClick={() => handleShip(row)}
                                        >
                                            {busyId === row.id ? '…' : 'Отгрузил'}
                                        </button>
                                    )}
                                </div>
                            )}
                        </div>
                    );
                })}
        </div>
    );
}

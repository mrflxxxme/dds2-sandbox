'use client';

import { useCallback, useEffect, useState } from 'react';
import { formatNumber, formatDate } from '@/lib/utils';
import {
    ffListAcceptances,
    ffStartAcceptance,
    ffAcceptAcceptance,
} from '@/lib/api/ff';
import type { FfAcceptanceRow, FfAcceptItemInput } from '@/types/ff';
import { ProjectBadge, AcceptanceStatusBadge, ReturnBadge } from '../../../_components/badges';

interface AcceptDraftItem {
    item_id: number;
    actual_qty: string;
    defect_qty: string;
    defect_reason: string;
}

export default function FfAcceptancesPage() {
    const [rows, setRows] = useState<FfAcceptanceRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [busyId, setBusyId] = useState<number | null>(null);
    const [acceptId, setAcceptId] = useState<number | null>(null);
    const [draft, setDraft] = useState<AcceptDraftItem[]>([]);
    const [submitError, setSubmitError] = useState('');

    const load = useCallback((signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        ffListAcceptances({ limit: 50 })
            .then((res) => {
                if (signal?.aborted) return;
                setRows(res.items);
            })
            .catch((err: unknown) => {
                if (signal?.aborted) return;
                setError(err instanceof Error ? err.message : 'Не удалось загрузить приёмки');
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

    const handleStart = async (row: FfAcceptanceRow) => {
        setBusyId(row.id);
        setError('');
        try {
            const updated = await ffStartAcceptance(row.id);
            setRows((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : 'Ошибка');
        } finally {
            setBusyId(null);
        }
    };

    const openAccept = (row: FfAcceptanceRow) => {
        setSubmitError('');
        setDraft(
            row.items.map((it) => ({
                item_id: it.item_id,
                actual_qty: String(it.expected_qty),
                defect_qty: '0',
                defect_reason: '',
            })),
        );
        setAcceptId(row.id);
    };

    const updateDraft = (itemId: number, patch: Partial<AcceptDraftItem>) => {
        setDraft((prev) => prev.map((d) => (d.item_id === itemId ? { ...d, ...patch } : d)));
    };

    const submitAccept = async (row: FfAcceptanceRow) => {
        setSubmitError('');
        const items: FfAcceptItemInput[] = draft.map((d) => ({
            item_id: d.item_id,
            actual_qty: Number(d.actual_qty) || 0,
            defect_qty: Number(d.defect_qty) || 0,
            defect_reason: d.defect_reason.trim() || undefined,
        }));
        setBusyId(row.id);
        try {
            const updated = await ffAcceptAcceptance(row.id, items);
            setRows((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
            setAcceptId(null);
        } catch (err: unknown) {
            setSubmitError(err instanceof Error ? err.message : 'Ошибка приёмки');
        } finally {
            setBusyId(null);
        }
    };

    return (
        <div>
            <h1 className="ff-section-title">Приёмки</h1>
            <p className="ff-section-sub">Входящие поставки и возвраты WB</p>

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
                    <div className="ff-muted">Нет приёмок</div>
                </div>
            )}

            {!loading && !error &&
                rows.map((row) => {
                    const expanded = acceptId === row.id;
                    const totalItems = row.items.length;
                    const canStart = row.status === 'EXPECTED' && !row.in_work;
                    const canAccept = row.status === 'EXPECTED' && row.in_work;
                    return (
                        <div key={row.id} className="ff-card">
                            <div className="ff-row-head">
                                <ProjectBadge name={row.project_name} />
                                {row.kind === 'return' && <ReturnBadge />}
                                <AcceptanceStatusBadge status={row.status} />
                                {row.in_work && row.status === 'EXPECTED' && (
                                    <span className="badge badge-info">В работе</span>
                                )}
                            </div>
                            <div className="ff-number">№ {row.number}</div>
                            <div className="ff-meta">
                                <strong>{row.warehouse_name}</strong>
                            </div>
                            <div className="ff-meta">
                                План: {formatDate(row.planned_date)} · Позиций:{' '}
                                {formatNumber(totalItems, 0)}
                            </div>
                            {row.actual_date && (
                                <div className="ff-meta">Принято: {formatDate(row.actual_date)}</div>
                            )}
                            {row.comment && <div className="ff-meta">{row.comment}</div>}

                            {/* Item list — always visible (read-only) when not accepting */}
                            {!expanded && (
                                <div style={{ marginTop: 8 }}>
                                    {row.items.map((it) => (
                                        <div key={it.item_id} className="ff-item-line">
                                            <span className="ff-item-name">
                                                {it.product_name || '—'}
                                                <br />
                                                <span className="ff-item-bc">{it.barcode}</span>
                                            </span>
                                            <span className="ff-item-qty">
                                                {row.status === 'ACCEPTED'
                                                    ? `${formatNumber(it.actual_qty, 0)} / ${formatNumber(it.expected_qty, 0)} шт`
                                                    : `${formatNumber(it.expected_qty, 0)} шт`}
                                                {it.defect_qty > 0 && (
                                                    <>
                                                        <br />
                                                        <span className="ff-hint-warn">
                                                            брак {formatNumber(it.defect_qty, 0)}
                                                        </span>
                                                    </>
                                                )}
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            )}

                            {/* Accept form */}
                            {expanded && (
                                <div style={{ marginTop: 8 }}>
                                    {draft.map((d) => {
                                        const item = row.items.find((i) => i.item_id === d.item_id);
                                        if (!item) return null;
                                        const shortfall =
                                            Number(d.actual_qty) < item.expected_qty;
                                        return (
                                            <div key={d.item_id} className="ff-accept-item">
                                                <div className="ff-item-name">
                                                    {item.product_name || '—'}{' '}
                                                    <span className="ff-item-bc">{item.barcode}</span>
                                                </div>
                                                <div className="ff-hint">
                                                    Ожидалось: {formatNumber(item.expected_qty, 0)} шт
                                                </div>
                                                <div className="ff-accept-inputs">
                                                    <div className="form-group">
                                                        <label className="form-label">Факт</label>
                                                        <input
                                                            className="form-input"
                                                            type="number"
                                                            min={0}
                                                            inputMode="numeric"
                                                            value={d.actual_qty}
                                                            onChange={(e) =>
                                                                updateDraft(d.item_id, {
                                                                    actual_qty: e.target.value,
                                                                })
                                                            }
                                                        />
                                                    </div>
                                                    <div className="form-group">
                                                        <label className="form-label">Брак</label>
                                                        <input
                                                            className="form-input"
                                                            type="number"
                                                            min={0}
                                                            inputMode="numeric"
                                                            value={d.defect_qty}
                                                            onChange={(e) =>
                                                                updateDraft(d.item_id, {
                                                                    defect_qty: e.target.value,
                                                                })
                                                            }
                                                        />
                                                    </div>
                                                </div>
                                                <div className="form-group" style={{ marginTop: 8 }}>
                                                    <label className="form-label">
                                                        Причина брака (необязательно)
                                                    </label>
                                                    <input
                                                        className="form-input"
                                                        type="text"
                                                        value={d.defect_reason}
                                                        onChange={(e) =>
                                                            updateDraft(d.item_id, {
                                                                defect_reason: e.target.value,
                                                            })
                                                        }
                                                    />
                                                </div>
                                                {shortfall && (
                                                    <div className="ff-hint-warn" style={{ marginTop: 4 }}>
                                                        Недовоз: факт меньше ожидаемого
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })}
                                    {submitError && (
                                        <div className="ff-hint-warn" style={{ marginTop: 8 }}>
                                            {submitError}
                                        </div>
                                    )}
                                    <div className="ff-actions">
                                        <button
                                            className="btn btn-primary"
                                            disabled={busyId === row.id}
                                            onClick={() => submitAccept(row)}
                                        >
                                            {busyId === row.id ? 'Сохранение…' : 'Сохранить приёмку'}
                                        </button>
                                        <button
                                            className="btn btn-secondary"
                                            disabled={busyId === row.id}
                                            onClick={() => setAcceptId(null)}
                                        >
                                            Отмена
                                        </button>
                                    </div>
                                </div>
                            )}

                            {!expanded && (canStart || canAccept) && (
                                <div className="ff-actions">
                                    {canStart && (
                                        <button
                                            className="btn btn-primary"
                                            disabled={busyId === row.id}
                                            onClick={() => handleStart(row)}
                                        >
                                            {busyId === row.id ? '…' : 'Взять в работу'}
                                        </button>
                                    )}
                                    {canAccept && (
                                        <button
                                            className="btn btn-primary"
                                            onClick={() => openAccept(row)}
                                        >
                                            Принять
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

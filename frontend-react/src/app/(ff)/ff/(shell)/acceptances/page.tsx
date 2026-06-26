'use client';

import { useCallback, useEffect, useState } from 'react';
import { formatNumber, formatDate } from '@/lib/utils';
import {
    ffListAcceptances,
    ffStartAcceptance,
    ffAcceptAcceptance,
} from '@/lib/api/ff';
import type { FfAcceptanceRow, FfAcceptItemInput } from '@/types/ff';
import { ProjectBadge, AcceptanceStatusBadge, InWorkBadge, ReturnBadge } from '../../../_components/badges';

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
    const [acceptRow, setAcceptRow] = useState<FfAcceptanceRow | null>(null);
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
        setAcceptRow(row);
    };

    const updateDraft = (itemId: number, patch: Partial<AcceptDraftItem>) => {
        setDraft((prev) => prev.map((d) => (d.item_id === itemId ? { ...d, ...patch } : d)));
    };

    const submitAccept = async () => {
        if (!acceptRow) return;
        setSubmitError('');
        const items: FfAcceptItemInput[] = draft.map((d) => ({
            item_id: d.item_id,
            actual_qty: Number(d.actual_qty) || 0,
            defect_qty: Number(d.defect_qty) || 0,
            defect_reason: d.defect_reason.trim() || undefined,
        }));
        setBusyId(acceptRow.id);
        try {
            const updated = await ffAcceptAcceptance(acceptRow.id, items);
            setRows((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
            setAcceptRow(null);
        } catch (err: unknown) {
            setSubmitError(err instanceof Error ? err.message : 'Ошибка приёмки');
        } finally {
            setBusyId(null);
        }
    };

    return (
        <div>
            <div className="page-header">
                <div>
                    <h1 className="page-title">Приёмки</h1>
                    <p className="page-subtitle">Входящие поставки и возвраты WB</p>
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
                        <div className="empty-state-text">Нет приёмок</div>
                    </div>
                </div>
            )}

            {!loading && !error && rows.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                    {rows.map((row) => {
                        const canStart = row.status === 'EXPECTED' && !row.in_work;
                        const canAccept = row.status === 'EXPECTED' && row.in_work;
                        return (
                            <div key={row.id} className="glass-card">
                                <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
                                    <span style={{ fontSize: 16, fontWeight: 700 }}>№ {row.number}</span>
                                    <ProjectBadge name={row.project_name} />
                                    <AcceptanceStatusBadge status={row.status} />
                                    {row.kind === 'return' && <ReturnBadge />}
                                    {row.in_work && row.status === 'EXPECTED' && <InWorkBadge />}
                                </div>
                                <div style={{ color: 'var(--color-text-muted)', fontSize: 14, lineHeight: 1.6 }}>
                                    <div><span style={{ color: 'var(--color-text)', fontWeight: 600 }}>{row.warehouse_name}</span></div>
                                    <div>План: {formatDate(row.planned_date)} · Позиций: {formatNumber(row.items.length, 0)}</div>
                                    {row.actual_date && <div>Принято: {formatDate(row.actual_date)}</div>}
                                    {row.comment && <div>{row.comment}</div>}
                                </div>

                                <div style={{ overflowX: 'auto', marginTop: 12 }}>
                                    <table className="data-table" style={{ fontSize: 13 }}>
                                        <thead>
                                            <tr>
                                                <th>Товар</th>
                                                <th style={{ textAlign: 'right' }}>Ожидалось</th>
                                                {row.status === 'ACCEPTED' && <th style={{ textAlign: 'right' }}>Факт</th>}
                                                {row.status === 'ACCEPTED' && <th style={{ textAlign: 'right' }}>Брак</th>}
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {row.items.map((it) => (
                                                <tr key={it.item_id}>
                                                    <td>
                                                        <div>{it.product_name || '—'}</div>
                                                        <div style={{ color: 'var(--color-text-dim)', fontSize: 11 }}>{it.barcode}</div>
                                                    </td>
                                                    <td style={{ textAlign: 'right' }}>{formatNumber(it.expected_qty, 0)}</td>
                                                    {row.status === 'ACCEPTED' && (
                                                        <td style={{ textAlign: 'right' }}>{formatNumber(it.actual_qty, 0)}</td>
                                                    )}
                                                    {row.status === 'ACCEPTED' && (
                                                        <td style={{ textAlign: 'right' }}>{formatNumber(it.defect_qty, 0)}</td>
                                                    )}
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>

                                {(canStart || canAccept) && (
                                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
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
                                            <button className="btn btn-success" onClick={() => openAccept(row)}>
                                                Принять
                                            </button>
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}

            {acceptRow && (
                <div className="modal-overlay" onClick={() => setAcceptRow(null)}>
                    <div
                        className="modal-card modal-card-wide"
                        onClick={(e) => e.stopPropagation()}
                        style={{ maxHeight: '90vh', overflow: 'auto' }}
                    >
                        <h2 className="modal-title">Приёмка № {acceptRow.number}</h2>
                        <div style={{ overflowX: 'auto' }}>
                            <table className="data-table" style={{ fontSize: 13 }}>
                                <thead>
                                    <tr>
                                        <th>Товар</th>
                                        <th style={{ textAlign: 'right' }}>Ожидалось</th>
                                        <th>Факт</th>
                                        <th>Брак</th>
                                        <th>Причина</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {draft.map((d) => {
                                        const item = acceptRow.items.find((i) => i.item_id === d.item_id);
                                        if (!item) return null;
                                        const shortfall = Number(d.actual_qty) < item.expected_qty;
                                        return (
                                            <tr key={d.item_id}>
                                                <td>
                                                    <div>{item.product_name || '—'}</div>
                                                    <div style={{ color: 'var(--color-text-dim)', fontSize: 11 }}>{item.barcode}</div>
                                                    {shortfall && (
                                                        <div style={{ color: 'var(--color-warning)', fontSize: 12, marginTop: 4 }}>
                                                            Недовоз: факт меньше ожидаемого
                                                        </div>
                                                    )}
                                                </td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(item.expected_qty, 0)}</td>
                                                <td>
                                                    <input
                                                        className="form-input"
                                                        type="number"
                                                        min={0}
                                                        inputMode="numeric"
                                                        style={{ width: 90 }}
                                                        value={d.actual_qty}
                                                        onChange={(e) => updateDraft(d.item_id, { actual_qty: e.target.value })}
                                                    />
                                                </td>
                                                <td>
                                                    <input
                                                        className="form-input"
                                                        type="number"
                                                        min={0}
                                                        inputMode="numeric"
                                                        style={{ width: 90 }}
                                                        value={d.defect_qty}
                                                        onChange={(e) => updateDraft(d.item_id, { defect_qty: e.target.value })}
                                                    />
                                                </td>
                                                <td>
                                                    <input
                                                        className="form-input"
                                                        type="text"
                                                        style={{ width: 160 }}
                                                        value={d.defect_reason}
                                                        onChange={(e) => updateDraft(d.item_id, { defect_reason: e.target.value })}
                                                    />
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                        {submitError && (
                            <div className="auth-error" style={{ marginTop: 16 }}>{submitError}</div>
                        )}
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 24 }}>
                            <button
                                className="btn btn-secondary"
                                disabled={busyId === acceptRow.id}
                                onClick={() => setAcceptRow(null)}
                            >
                                Отмена
                            </button>
                            <button
                                className="btn btn-primary"
                                disabled={busyId === acceptRow.id}
                                onClick={submitAccept}
                            >
                                {busyId === acceptRow.id ? 'Сохранение…' : 'Принять'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

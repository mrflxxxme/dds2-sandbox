'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { formatNumber, formatDate } from '@/lib/utils';
import {
    ffGetAcceptance,
    ffStartAcceptance,
    ffAcceptAcceptance,
    ffArchiveAcceptance,
    FfApiError,
} from '@/lib/api/ff';
import type { FfAcceptanceRow, FfAcceptItemInput } from '@/types/ff';
import { ProjectBadge, AcceptanceStatusBadge, ReturnBadge } from '../../../../_components/badges';

interface AcceptDraftItem {
    item_id: number;
    actual_qty: string;
    defect_qty: string;
    defect_reason: string;
}

function errMsg(err: unknown, fallback: string): string {
    if (err instanceof FfApiError) return err.message;
    if (err instanceof Error) return err.message;
    return fallback;
}

export default function FfAcceptanceDetailPage() {
    const params = useParams();
    const router = useRouter();
    const id = Number(params.id);

    const [data, setData] = useState<FfAcceptanceRow | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const [busy, setBusy] = useState(false);
    const [actionError, setActionError] = useState('');

    // Editable accept draft (shown when EXPECTED & in_work).
    const [draft, setDraft] = useState<AcceptDraftItem[]>([]);

    const buildDraft = useCallback(
        (row: FfAcceptanceRow): AcceptDraftItem[] =>
            row.items.map((it) => ({
                item_id: it.item_id,
                actual_qty: String(it.actual_qty || it.expected_qty),
                defect_qty: String(it.defect_qty || 0),
                defect_reason: '',
            })),
        [],
    );

    const load = useCallback(
        (signal?: AbortSignal) => {
            if (!Number.isFinite(id)) {
                setError('Некорректный идентификатор приёмки');
                setLoading(false);
                return;
            }
            setLoading(true);
            setError('');
            ffGetAcceptance(id)
                .then((res) => {
                    if (signal?.aborted) return;
                    setData(res);
                    setDraft(buildDraft(res));
                })
                .catch((err: unknown) => {
                    if (signal?.aborted) return;
                    setError(errMsg(err, 'Не удалось загрузить приёмку'));
                })
                .finally(() => {
                    if (signal?.aborted) return;
                    setLoading(false);
                });
        },
        [id, buildDraft],
    );

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    const reload = useCallback(async () => {
        const res = await ffGetAcceptance(id);
        setData(res);
        setDraft(buildDraft(res));
    }, [id, buildDraft]);

    const updateDraft = (itemId: number, patch: Partial<AcceptDraftItem>) => {
        setDraft((prev) => prev.map((d) => (d.item_id === itemId ? { ...d, ...patch } : d)));
    };

    const acceptAsExpected = () => {
        if (!data) return;
        setDraft(
            data.items.map((it) => ({
                item_id: it.item_id,
                actual_qty: String(it.expected_qty),
                defect_qty: '0',
                defect_reason: '',
            })),
        );
    };

    const draftTotals = useMemo(() => {
        let accepted = 0;
        let defect = 0;
        let shortfall = 0;
        if (data) {
            for (const d of draft) {
                const act = Number(d.actual_qty) || 0;
                const def = Number(d.defect_qty) || 0;
                accepted += act;
                defect += def;
                const item = data.items.find((i) => i.item_id === d.item_id);
                if (item) {
                    const short = item.expected_qty - act - def;
                    if (short > 0) shortfall += short;
                }
            }
        }
        return { accepted, defect, shortfall };
    }, [draft, data]);

    const runAction = async (fn: () => Promise<unknown>, fallback: string) => {
        setBusy(true);
        setActionError('');
        try {
            await fn();
            await reload();
        } catch (err: unknown) {
            setActionError(errMsg(err, fallback));
        } finally {
            setBusy(false);
        }
    };

    const handleStart = () => runAction(() => ffStartAcceptance(id), 'Не удалось взять в работу');

    const handleAccept = () =>
        runAction(() => {
            const items: FfAcceptItemInput[] = draft.map((d) => ({
                item_id: d.item_id,
                actual_qty: Number(d.actual_qty) || 0,
                defect_qty: Number(d.defect_qty) || 0,
                defect_reason: d.defect_reason.trim() || undefined,
            }));
            return ffAcceptAcceptance(id, items);
        }, 'Не удалось принять');

    const handleArchive = () =>
        runAction(
            () => ffArchiveAcceptance(id, !(data?.is_archived ?? false)),
            'Не удалось изменить архив',
        );

    // ── States ──────────────────────────────────────────────────────────────────
    if (loading) {
        return (
            <div className="animate-in">
                <div className="ff-feed">
                    <div className="ff-skeleton" style={{ height: 48 }} />
                    <div className="ff-skeleton" />
                    <div className="ff-skeleton" />
                </div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="animate-in">
                <button className="ff-back" onClick={() => router.push('/ff/acceptances')}>
                    ← К приёмкам
                </button>
                <div
                    className="glass-card"
                    style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, color: 'var(--color-danger)' }}
                >
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => load()}>
                        Повторить
                    </button>
                </div>
            </div>
        );
    }

    if (!data) {
        return (
            <div className="animate-in">
                <button className="ff-back" onClick={() => router.push('/ff/acceptances')}>
                    ← К приёмкам
                </button>
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-text">Приёмка не найдена</div>
                    </div>
                </div>
            </div>
        );
    }

    const canStart = data.status === 'EXPECTED' && !data.in_work;
    const canAccept = data.status === 'EXPECTED' && data.in_work;
    const editing = canAccept;

    return (
        <div className="animate-in">
            <button className="ff-back" onClick={() => router.push('/ff/acceptances')}>
                ← К приёмкам
            </button>

            <div className="page-header">
                <div>
                    <h1 className="page-title">Приёмка № {data.number}</h1>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                        <AcceptanceStatusBadge status={data.status} />
                        <ProjectBadge name={data.project_name} />
                        {data.kind === 'return' && <ReturnBadge />}
                        {data.in_work && <span className="badge badge-info">В работе</span>}
                        {data.is_archived && <span className="badge badge-secondary">В архиве</span>}
                        <span className="page-subtitle" style={{ margin: 0 }}>{data.warehouse_name}</span>
                    </div>
                </div>
            </div>

            <div className="ff-detail-grid">
                {/* ─── Состав ─── */}
                <div className="glass-card" style={{ gridColumn: '1 / -1' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
                        <h2 className="page-subtitle" style={{ margin: 0, fontWeight: 600, color: 'var(--color-text)' }}>
                            Состав
                        </h2>
                        {editing && (
                            <button className="btn btn-secondary btn-sm" onClick={acceptAsExpected}>
                                Принять как ожидалось
                            </button>
                        )}
                    </div>

                    {data.items.length === 0 ? (
                        <div className="empty-state">
                            <div className="empty-state-text">Нет позиций</div>
                        </div>
                    ) : (
                        <div style={{ overflowX: 'auto' }}>
                            <table className="data-table" style={{ fontSize: 13 }}>
                                <thead>
                                    <tr>
                                        <th>Товар</th>
                                        <th style={{ textAlign: 'right' }}>Ожидалось</th>
                                        <th style={{ textAlign: 'right' }}>Факт</th>
                                        <th style={{ textAlign: 'right' }}>Брак</th>
                                        {editing && <th>Причина</th>}
                                        <th style={{ textAlign: 'right' }}>Недовоз</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.items.map((item) => {
                                        const d = draft.find((x) => x.item_id === item.item_id);
                                        const act = editing ? Number(d?.actual_qty) || 0 : item.actual_qty;
                                        const def = editing ? Number(d?.defect_qty) || 0 : item.defect_qty;
                                        const short = item.expected_qty - act - def;
                                        return (
                                            <tr key={item.item_id}>
                                                <td>
                                                    <div>{item.product_name || '—'}</div>
                                                    <div style={{ color: 'var(--color-text-dim)', fontSize: 11, fontVariantNumeric: 'tabular-nums' }}>
                                                        {item.barcode}{item.article ? ` · ${item.article}` : ''}
                                                    </div>
                                                </td>
                                                <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                                                    {formatNumber(item.expected_qty, 0)}
                                                </td>
                                                <td style={{ textAlign: 'right' }}>
                                                    {editing && d ? (
                                                        <input
                                                            className="form-input"
                                                            type="number"
                                                            min={0}
                                                            inputMode="numeric"
                                                            value={d.actual_qty}
                                                            onChange={(e) => updateDraft(item.item_id, { actual_qty: e.target.value })}
                                                            style={{ width: 90, textAlign: 'right', padding: '4px 8px' }}
                                                            aria-label={`Факт ${item.product_name || item.barcode}`}
                                                        />
                                                    ) : (
                                                        <span style={{ fontVariantNumeric: 'tabular-nums' }}>{formatNumber(act, 0)}</span>
                                                    )}
                                                </td>
                                                <td style={{ textAlign: 'right' }}>
                                                    {editing && d ? (
                                                        <input
                                                            className="form-input"
                                                            type="number"
                                                            min={0}
                                                            inputMode="numeric"
                                                            value={d.defect_qty}
                                                            onChange={(e) => updateDraft(item.item_id, { defect_qty: e.target.value })}
                                                            style={{ width: 90, textAlign: 'right', padding: '4px 8px' }}
                                                            aria-label={`Брак ${item.product_name || item.barcode}`}
                                                        />
                                                    ) : (
                                                        <span style={{ fontVariantNumeric: 'tabular-nums', color: def > 0 ? 'var(--color-danger)' : undefined }}>
                                                            {formatNumber(def, 0)}
                                                        </span>
                                                    )}
                                                </td>
                                                {editing && d && (
                                                    <td>
                                                        <input
                                                            className="form-input"
                                                            type="text"
                                                            placeholder={def > 0 ? 'Причина брака' : '—'}
                                                            value={d.defect_reason}
                                                            onChange={(e) => updateDraft(item.item_id, { defect_reason: e.target.value })}
                                                            style={{ minWidth: 140, padding: '4px 8px' }}
                                                            aria-label={`Причина брака ${item.product_name || item.barcode}`}
                                                        />
                                                    </td>
                                                )}
                                                <td
                                                    style={{
                                                        textAlign: 'right',
                                                        fontVariantNumeric: 'tabular-nums',
                                                        color: short > 0 ? 'var(--color-warning)' : undefined,
                                                        fontWeight: short > 0 ? 600 : undefined,
                                                    }}
                                                >
                                                    {short > 0 ? formatNumber(short, 0) : '—'}
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}

                    {editing && (
                        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--color-border)' }}>
                            <div>
                                <div className="stat-card-label">Принято</div>
                                <div style={{ fontSize: 20, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
                                    {formatNumber(draftTotals.accepted, 0)}
                                </div>
                            </div>
                            <div>
                                <div className="stat-card-label">Брак</div>
                                <div style={{ fontSize: 20, fontWeight: 700, fontVariantNumeric: 'tabular-nums', color: 'var(--color-danger)' }}>
                                    {formatNumber(draftTotals.defect, 0)}
                                </div>
                            </div>
                            <div>
                                <div className="stat-card-label">Недовоз</div>
                                <div style={{ fontSize: 20, fontWeight: 700, fontVariantNumeric: 'tabular-nums', color: 'var(--color-warning)' }}>
                                    {formatNumber(draftTotals.shortfall, 0)}
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                {/* ─── Параметры ─── */}
                <div className="glass-card">
                    <h2 className="page-subtitle" style={{ margin: '0 0 12px', fontWeight: 600, color: 'var(--color-text)' }}>
                        Параметры
                    </h2>
                    <div className="ff-kv">
                        <span className="ff-kv-key">Проект</span>
                        <span className="ff-kv-val">{data.project_name}</span>
                    </div>
                    <div className="ff-kv">
                        <span className="ff-kv-key">Склад</span>
                        <span className="ff-kv-val">{data.warehouse_name}</span>
                    </div>
                    <div className="ff-kv">
                        <span className="ff-kv-key">Тип</span>
                        <span className="ff-kv-val">{data.kind === 'return' ? 'Возврат WB' : 'Поставка'}</span>
                    </div>
                    <div className="ff-kv">
                        <span className="ff-kv-key">Создана</span>
                        <span className="ff-kv-val">{formatDate(data.created_at)}</span>
                    </div>
                    <div className="ff-kv">
                        <span className="ff-kv-key">Плановая дата</span>
                        <span className="ff-kv-val">{data.planned_date ? formatDate(data.planned_date) : '—'}</span>
                    </div>
                    <div className="ff-kv">
                        <span className="ff-kv-key">Факт. дата</span>
                        <span className="ff-kv-val">{data.actual_date ? formatDate(data.actual_date) : '—'}</span>
                    </div>
                    {data.comment && (
                        <div className="ff-kv" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 4 }}>
                            <span className="ff-kv-key">Комментарий</span>
                            <span className="ff-kv-val" style={{ textAlign: 'left', fontWeight: 400 }}>{data.comment}</span>
                        </div>
                    )}
                </div>
            </div>

            {/* ─── Action bar ─── */}
            <div className="ff-actionbar">
                {actionError && (
                    <span style={{ color: 'var(--color-danger)', fontSize: 13, marginRight: 'auto', alignSelf: 'center' }}>
                        {actionError}
                    </span>
                )}

                <button className="btn btn-secondary" disabled={busy} onClick={handleArchive}>
                    {data.is_archived ? 'Вернуть из архива' : 'В архив'}
                </button>

                {canStart && (
                    <button className="btn btn-primary" disabled={busy} onClick={handleStart}>
                        {busy ? '…' : 'Взять в работу'}
                    </button>
                )}

                {canAccept && (
                    <button className="btn btn-success" disabled={busy} onClick={handleAccept}>
                        {busy ? 'Сохранение…' : 'Принять'}
                    </button>
                )}
            </div>
        </div>
    );
}

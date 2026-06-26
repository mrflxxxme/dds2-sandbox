'use client';
import React, { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import type { GazelkaOrderRow, GazelkaOrderList, GazelkaMatchCandidate } from '@/types/api';
import GazelkaModal from './GazelkaModal';

// ─── Helpers ─────────────────────────────────────────────────────────────────

function statusBadgeClass(status: string): string {
    const s = status.toLowerCase();
    if (s.includes('отмен') || s.includes('cancel')) return 'badge-secondary';
    if (s.includes('выполн') || s.includes('доставл') || s.includes('complete')) return 'badge-success';
    if (s.includes('в пути') || s.includes('active') || s.includes('transit')) return 'badge-info';
    if (s.includes('запланир') || s.includes('plan')) return 'badge-warning';
    return 'badge-secondary';
}

// ─── Match candidate picker ───────────────────────────────────────────────────

interface MatchPickerProps {
    title: string;
    onPick: (candidate: GazelkaMatchCandidate) => void;
    onClose: () => void;
    busy: boolean;
}

function MatchPicker({ title, onPick, onClose, busy }: MatchPickerProps) {
    const [search, setSearch] = useState('');
    const [candidates, setCandidates] = useState<GazelkaMatchCandidate[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const cleanupRef = React.useRef<(() => void) | null>(null);

    const loadCandidates = useCallback((q: string) => {
        setLoading(true);
        setError('');
        const controller = new AbortController();
        api.getGazelkaMatchCandidates(q || undefined).then(rows => {
            if (controller.signal.aborted) return;
            setCandidates(rows);
        }).catch((e: unknown) => {
            if (controller.signal.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки кандидатов');
        }).finally(() => {
            if (!controller.signal.aborted) setLoading(false);
        });
        return () => controller.abort();
    }, []);

    // Перезагрузка при изменении строки поиска (лёгкий debounce 300мс).
    useEffect(() => {
        const t = setTimeout(() => {
            cleanupRef.current = loadCandidates(search);
        }, search ? 300 : 0);
        return () => {
            clearTimeout(t);
            cleanupRef.current?.();
        };
    }, [search, loadCandidates]);

    return (
        <div
            className="modal-overlay"
            style={{ padding: '24px 16px' }}
            onClick={e => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div
                className="modal-card modal-card-solid"
                style={{
                    width: 560, maxWidth: '94vw',
                    maxHeight: 'calc(100vh - 48px)',
                    padding: 0,
                    display: 'flex', flexDirection: 'column',
                    overflow: 'hidden',
                }}
                onClick={e => e.stopPropagation()}
            >
                {/* Header */}
                <div style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    padding: '16px 20px', borderBottom: '1px solid var(--color-border)', flexShrink: 0,
                }}>
                    <div>
                        <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Сопоставить со сборкой</h2>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2 }}>{title}</div>
                    </div>
                    <button
                        onClick={onClose}
                        style={{ background: 'none', border: 'none', fontSize: 24, cursor: 'pointer', color: 'var(--color-text-muted)', lineHeight: 1, padding: '0 4px' }}
                        aria-label="Закрыть"
                    >
                        ×
                    </button>
                </div>

                {/* Search */}
                <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--color-border)', flexShrink: 0 }}>
                    <input
                        className="form-input"
                        type="search"
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        placeholder="Поиск: № сборки, склад или поставка WB"
                        autoFocus
                        style={{ width: '100%' }}
                    />
                </div>

                {/* Body */}
                <div style={{ overflowY: 'auto', flex: 1, padding: '8px 0' }}>
                    {loading && (
                        <div style={{ padding: '32px', textAlign: 'center', color: 'var(--color-text-muted)' }}>
                            Загрузка...
                        </div>
                    )}
                    {!loading && error && (
                        <div style={{ padding: '16px 20px', color: 'var(--color-danger)', fontSize: 13 }}>
                            {error}
                            <button className="btn btn-secondary btn-sm" onClick={() => loadCandidates(search)} style={{ marginLeft: 12 }}>
                                Повторить
                            </button>
                        </div>
                    )}
                    {!loading && !error && candidates.length === 0 && (
                        <div style={{ padding: '32px', textAlign: 'center', color: 'var(--color-text-muted)' }}>
                            Подходящих сборок не найдено
                        </div>
                    )}
                    {!loading && !error && candidates.map(c => {
                        const linked = !!c.already_linked_to;
                        return (
                            <button
                                key={c.assembly_id}
                                onClick={() => onPick(c)}
                                disabled={busy}
                                style={{
                                    display: 'block', width: '100%', textAlign: 'left',
                                    padding: '10px 20px', border: 'none',
                                    borderBottom: '1px solid var(--color-border)',
                                    background: 'transparent', cursor: busy ? 'wait' : 'pointer',
                                    color: linked ? 'var(--color-text-muted)' : 'var(--color-text)',
                                    opacity: linked ? 0.75 : 1,
                                }}
                            >
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
                                    <span style={{ fontWeight: 600 }}>{c.number}</span>
                                    {c.delivery_date && (
                                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{formatDate(c.delivery_date)}</span>
                                    )}
                                </div>
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                                    {c.warehouse_name && <span>{c.warehouse_name}</span>}
                                    {c.wb_supply_id && <span style={{ fontFamily: 'monospace' }}>{c.wb_supply_id}</span>}
                                    {c.pallets_count != null && <span>{formatNumber(c.pallets_count, 0)} пал</span>}
                                    {c.status && <span>{c.status}</span>}
                                </div>
                                {linked && (
                                    <div style={{ fontSize: 11, color: 'var(--color-warning)', marginTop: 2 }}>
                                        уже привязана к #{c.already_linked_to} — клик переназначит
                                    </div>
                                )}
                            </button>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}

// ─── Link cell (общая для обеих таблиц) ───────────────────────────────────────

interface LinkCellProps {
    row: GazelkaOrderRow;
    onUnmatch: (planId: number) => void;
    onQuickMatch: (planId: number, assemblyId: number) => void;
    onOpenPicker: (row: GazelkaOrderRow) => void;
    matchBusy: number | null;
}

function LinkCell({ row, onUnmatch, onQuickMatch, onOpenPicker, matchBusy }: LinkCellProps) {
    const planId = Number(row.gazelka_id);
    const busy = matchBusy === planId;

    if (row.linked_assembly_number) {
        return (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                <span className="badge badge-success" style={{ fontSize: 11 }} title="Связанная сборка">
                    наша {row.linked_assembly_number}
                </span>
                <button
                    onClick={() => onUnmatch(planId)}
                    disabled={busy}
                    style={{
                        background: 'none', border: 'none', padding: 0,
                        color: 'var(--color-danger)', fontSize: 11, cursor: busy ? 'wait' : 'pointer',
                        textDecoration: 'underline',
                    }}
                    title="Отвязать сборку"
                >
                    Отвязать
                </button>
            </div>
        );
    }

    // Несвязанная заявка
    if (row.suggested_assembly_number && row.suggested_assembly_id != null) {
        return (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                <button
                    className="btn btn-success btn-sm"
                    onClick={() => onQuickMatch(planId, row.suggested_assembly_id as number)}
                    disabled={busy}
                    title="Сопоставить с авто-подсказкой"
                    style={{ fontSize: 12 }}
                >
                    {busy ? '...' : `✓ Сопоставить с ${row.suggested_assembly_number}`}
                </button>
                <button
                    onClick={() => onOpenPicker(row)}
                    disabled={busy}
                    style={{
                        background: 'none', border: 'none', padding: 0,
                        color: 'var(--color-accent)', fontSize: 11, cursor: busy ? 'wait' : 'pointer',
                        textDecoration: 'underline',
                    }}
                    title="Выбрать другую сборку"
                >
                    другая…
                </button>
            </div>
        );
    }

    return (
        <button
            className="btn btn-secondary btn-sm"
            onClick={() => onOpenPicker(row)}
            disabled={busy}
            title="Сопоставить со сборкой"
            style={{ fontSize: 12 }}
        >
            {busy ? '...' : 'Сопоставить'}
        </button>
    );
}

// ─── Planned table ────────────────────────────────────────────────────────────

interface PlannedTableProps {
    items: GazelkaOrderRow[];
    onTtn: (planId: number) => void;
    onEdit: (row: GazelkaOrderRow) => void;
    ttnLoading: number | null;
    linkProps: Omit<LinkCellProps, 'row'>;
}

function PlannedTable({ items, onTtn, onEdit, ttnLoading, linkProps }: PlannedTableProps) {
    if (items.length === 0) {
        return (
            <div style={{ padding: '32px', textAlign: 'center', color: 'var(--color-text-muted)' }}>
                Нет запланированных заявок
            </div>
        );
    }
    return (
        <div style={{ overflow: 'auto' }}>
            <table className="data-table" style={{ fontSize: 13 }}>
                <thead>
                    <tr>
                        <th>№ Газельки</th>
                        <th>Статус</th>
                        <th>Сборка</th>
                        <th>Отправка</th>
                        <th>Доставка</th>
                        <th>Адрес доставки</th>
                        <th>Маркетплейс</th>
                        <th>Моно/Микс</th>
                        <th style={{ textAlign: 'right' }}>Пал</th>
                        <th style={{ textAlign: 'right' }}>Кор</th>
                        <th style={{ textAlign: 'right' }}>Вес, кг</th>
                        <th>№ поставки</th>
                        <th style={{ textAlign: 'right' }}>Стоимость</th>
                        <th>Примечания</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>
                    {items.map(row => (
                        <tr key={row.gazelka_id}>
                            <td style={{ whiteSpace: 'nowrap', fontFamily: 'monospace', fontSize: 12, color: 'var(--color-text-muted)' }}>
                                {row.gazelka_id}
                            </td>
                            <td style={{ whiteSpace: 'nowrap' }}>
                                <span className={`badge ${statusBadgeClass(row.status_label || row.status)}`}>
                                    {row.status_label || row.status}
                                </span>
                            </td>
                            <td style={{ whiteSpace: 'nowrap' }}>
                                <LinkCell row={row} {...linkProps} />
                            </td>
                            <td style={{ whiteSpace: 'nowrap' }}>
                                {row.departure_date ? formatDate(row.departure_date) : '—'}
                                {row.departure_time && (
                                    <span style={{ color: 'var(--color-text-muted)', marginLeft: 4, fontSize: 12 }}>
                                        {row.departure_time}
                                    </span>
                                )}
                            </td>
                            <td style={{ whiteSpace: 'nowrap' }}>
                                {row.delivery_date ? formatDate(row.delivery_date) : '—'}
                                {row.delivery_time && (
                                    <span style={{ color: 'var(--color-text-muted)', marginLeft: 4, fontSize: 12 }}>
                                        {row.delivery_time}
                                    </span>
                                )}
                            </td>
                            <td style={{ maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={row.delivery_address ?? undefined}>
                                {row.delivery_address || '—'}
                            </td>
                            <td>{row.marketplace || '—'}</td>
                            <td>{row.monomix || '—'}</td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(row.pallets, 0)}</td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(row.boxes, 0)}</td>
                            <td style={{ textAlign: 'right' }}>
                                {row.weight ? formatNumber(Number(row.weight), 1) : '—'}
                            </td>
                            <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{row.supply_id || '—'}</td>
                            <td style={{ textAlign: 'right', fontWeight: 600 }}>
                                {row.rate ? `${formatNumber(Number(row.rate), 0)} ₽` : '—'}
                            </td>
                            <td style={{ maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--color-text-muted)', fontSize: 12 }} title={row.notes ?? undefined}>
                                {row.notes || '—'}
                            </td>
                            <td style={{ whiteSpace: 'nowrap' }}>
                                <div style={{ display: 'flex', gap: 4 }}>
                                    <button
                                        className="btn btn-secondary btn-sm"
                                        onClick={() => onTtn(Number(row.gazelka_id))}
                                        disabled={ttnLoading === Number(row.gazelka_id)}
                                        title="Открыть ТТН"
                                    >
                                        {ttnLoading === Number(row.gazelka_id) ? '...' : 'ТТН'}
                                    </button>
                                    {row.editable && (
                                        <button
                                            className="btn btn-secondary btn-sm"
                                            onClick={() => onEdit(row)}
                                            title="Редактировать заявку"
                                        >
                                            Ред.
                                        </button>
                                    )}
                                </div>
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

// ─── Active table ─────────────────────────────────────────────────────────────

interface ActiveTableProps {
    items: GazelkaOrderRow[];
    onTtn: (planId: number) => void;
    ttnLoading: number | null;
    linkProps: Omit<LinkCellProps, 'row'>;
}

function ActiveTable({ items, onTtn, ttnLoading, linkProps }: ActiveTableProps) {
    if (items.length === 0) {
        return (
            <div style={{ padding: '32px', textAlign: 'center', color: 'var(--color-text-muted)' }}>
                Нет активных заявок
            </div>
        );
    }
    return (
        <div style={{ overflow: 'auto' }}>
            <table className="data-table" style={{ fontSize: 13 }}>
                <thead>
                    <tr>
                        <th>№ Газельки</th>
                        <th>Статус</th>
                        <th>Сборка</th>
                        <th>Маршрут</th>
                        <th>Перевозчик</th>
                        <th>Водитель</th>
                        <th>ТС</th>
                        <th>Адрес доставки</th>
                        <th>Дата сдачи</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>
                    {items.map(row => (
                        <tr key={row.gazelka_id}>
                            <td style={{ whiteSpace: 'nowrap', fontFamily: 'monospace', fontSize: 12, color: 'var(--color-text-muted)' }}>
                                {row.gazelka_id}
                            </td>
                            <td style={{ whiteSpace: 'nowrap' }}>
                                <span className={`badge ${statusBadgeClass(row.status_label || row.status)}`}>
                                    {row.status_label || row.status}
                                </span>
                            </td>
                            <td style={{ whiteSpace: 'nowrap' }}>
                                <LinkCell row={row} {...linkProps} />
                            </td>
                            <td style={{ whiteSpace: 'nowrap', fontFamily: 'monospace', fontSize: 12 }}>
                                {row.route_number || '—'}
                                {row.route_date && (
                                    <span style={{ color: 'var(--color-text-muted)', marginLeft: 4 }}>
                                        {formatDate(row.route_date)}
                                    </span>
                                )}
                            </td>
                            <td>{row.carrier || '—'}</td>
                            <td style={{ whiteSpace: 'nowrap' }}>
                                {row.driver_name || '—'}
                                {row.driver_phone && (
                                    <div style={{ fontSize: 11, color: 'var(--color-text-muted)', fontFamily: 'monospace' }}>
                                        {row.driver_phone}
                                    </div>
                                )}
                            </td>
                            <td>{row.vehicle || '—'}</td>
                            <td style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={row.delivery_address ?? undefined}>
                                {row.delivery_address || '—'}
                            </td>
                            <td style={{ whiteSpace: 'nowrap' }}>
                                {row.delivery_date ? formatDate(row.delivery_date) : '—'}
                            </td>
                            <td>
                                <button
                                    className="btn btn-secondary btn-sm"
                                    onClick={() => onTtn(Number(row.gazelka_id))}
                                    disabled={ttnLoading === Number(row.gazelka_id)}
                                    title="Открыть ТТН"
                                >
                                    {ttnLoading === Number(row.gazelka_id) ? '...' : 'ТТН'}
                                </button>
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function GazelkaOrdersTab() {
    const [planned, setPlanned] = useState<GazelkaOrderRow[]>([]);
    const [active, setActive] = useState<GazelkaOrderRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // ТТН: один per planId, null = никто не грузится
    const [ttnLoading, setTtnLoading] = useState<number | null>(null);
    const [ttnError, setTtnError] = useState('');

    // Матчинг: planId в работе (match/unmatch) + ошибка
    const [matchBusy, setMatchBusy] = useState<number | null>(null);
    const [matchError, setMatchError] = useState('');

    // Редактирование
    const [editRow, setEditRow] = useState<GazelkaOrderRow | null>(null);

    // Пикер кандидатов: открытая строка (null = закрыт)
    const [pickerRow, setPickerRow] = useState<GazelkaOrderRow | null>(null);

    const load = useCallback(() => {
        setLoading(true);
        setError('');
        const controller = new AbortController();
        Promise.all([
            api.getGazelkaPlanned(),
            api.getGazelkaActive(),
        ]).then(([p, a]: [GazelkaOrderList, GazelkaOrderList]) => {
            if (controller.signal.aborted) return;
            setPlanned(p.items);
            setActive(a.items);
        }).catch((e: unknown) => {
            if (controller.signal.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки данных Газельки');
        }).finally(() => {
            if (!controller.signal.aborted) setLoading(false);
        });
        return () => controller.abort();
    }, []);

    useEffect(() => load(), [load]);

    const handleTtn = useCallback(async (planId: number) => {
        setTtnLoading(planId);
        setTtnError('');
        try {
            await api.openGazelkaTtn(planId);
        } catch (e: unknown) {
            setTtnError(e instanceof Error ? e.message : 'Ошибка открытия ТТН');
        } finally {
            setTtnLoading(null);
        }
    }, []);

    const handleMatch = useCallback(async (planId: number, assemblyId: number) => {
        setMatchBusy(planId);
        setMatchError('');
        try {
            const res = await api.matchGazelkaOrder(planId, assemblyId);
            if (!res.ok) {
                setMatchError('Не удалось сопоставить заявку');
                return;
            }
            setPickerRow(null);
            load();
        } catch (e: unknown) {
            setMatchError(e instanceof Error ? e.message : 'Ошибка сопоставления');
        } finally {
            setMatchBusy(null);
        }
    }, [load]);

    const handleUnmatch = useCallback(async (planId: number) => {
        setMatchBusy(planId);
        setMatchError('');
        try {
            const res = await api.unmatchGazelkaOrder(planId);
            if (!res.ok) {
                setMatchError('Не удалось отвязать сборку');
                return;
            }
            load();
        } catch (e: unknown) {
            setMatchError(e instanceof Error ? e.message : 'Ошибка отвязки');
        } finally {
            setMatchBusy(null);
        }
    }, [load]);

    const handleEditSuccess = useCallback(() => {
        setEditRow(null);
        load();
    }, [load]);

    const linkProps: Omit<LinkCellProps, 'row'> = {
        onUnmatch: handleUnmatch,
        onQuickMatch: handleMatch,
        onOpenPicker: setPickerRow,
        matchBusy,
    };

    return (
        <div className="animate-in">
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div style={{ fontSize: 14, color: 'var(--color-text-muted)' }}>
                    Заявки из кабинета перевозчика gazelka.space
                </div>
                <button className="btn btn-secondary" onClick={load} disabled={loading}>
                    {loading ? 'Загрузка...' : 'Обновить'}
                </button>
            </div>

            {/* TTN error */}
            {ttnError && (
                <div className="glass-card" style={{ padding: '10px 16px', marginBottom: 12, color: 'var(--color-danger)', fontSize: 13, display: 'flex', alignItems: 'center', gap: 12 }}>
                    {ttnError}
                    <button className="btn btn-secondary btn-sm" onClick={() => setTtnError('')}>
                        Закрыть
                    </button>
                </div>
            )}

            {/* Match error */}
            {matchError && (
                <div className="glass-card" style={{ padding: '10px 16px', marginBottom: 12, color: 'var(--color-danger)', fontSize: 13, display: 'flex', alignItems: 'center', gap: 12 }}>
                    {matchError}
                    <button className="btn btn-secondary btn-sm" onClick={() => setMatchError('')}>
                        Закрыть
                    </button>
                </div>
            )}

            {/* Loading */}
            {loading && (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Загрузка...
                </div>
            )}

            {/* Error */}
            {!loading && error && (
                <div className="glass-card" style={{ padding: 16, color: 'var(--color-danger)', display: 'flex', alignItems: 'center', gap: 12 }}>
                    {error}
                    <button className="btn btn-secondary btn-sm" onClick={load}>
                        Повторить
                    </button>
                </div>
            )}

            {/* Data */}
            {!loading && !error && (
                <>
                    {/* ── Запланированные ── */}
                    <div className="glass-card" style={{ marginBottom: 20, padding: 0, overflow: 'hidden' }}>
                        <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--color-border)', display: 'flex', alignItems: 'center', gap: 10 }}>
                            <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>Запланированные</h3>
                            <span className="badge badge-secondary">{planned.length}</span>
                        </div>
                        <PlannedTable
                            items={planned}
                            onTtn={handleTtn}
                            onEdit={setEditRow}
                            ttnLoading={ttnLoading}
                            linkProps={linkProps}
                        />
                    </div>

                    {/* ── Активные ── */}
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                        <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--color-border)', display: 'flex', alignItems: 'center', gap: 10 }}>
                            <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>Активные</h3>
                            <span className="badge badge-info">{active.length}</span>
                        </div>
                        <ActiveTable
                            items={active}
                            onTtn={handleTtn}
                            ttnLoading={ttnLoading}
                            linkProps={linkProps}
                        />
                    </div>
                </>
            )}

            {/* Match picker */}
            {pickerRow && (
                <MatchPicker
                    title={`Заявка ${pickerRow.gazelka_id}${pickerRow.supply_id ? ` · ${pickerRow.supply_id}` : ''}`}
                    onPick={c => handleMatch(Number(pickerRow.gazelka_id), c.assembly_id)}
                    onClose={() => setPickerRow(null)}
                    busy={matchBusy === Number(pickerRow.gazelka_id)}
                />
            )}

            {/* Edit modal */}
            {editRow && (
                <GazelkaModal
                    assemblyId={0}
                    assemblyNumber=""
                    editPlanId={Number(editRow.gazelka_id)}
                    editTitle={`Заявка ${editRow.gazelka_id}${editRow.supply_id ? ` · ${editRow.supply_id}` : ''}`}
                    onClose={() => setEditRow(null)}
                    onSuccess={handleEditSuccess}
                />
            )}
        </div>
    );
}

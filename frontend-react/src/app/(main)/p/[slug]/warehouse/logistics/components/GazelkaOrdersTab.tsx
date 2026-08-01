'use client';
import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import type { GazelkaLinkKind, GazelkaOrderRow, GazelkaOrderList, GazelkaMatchCandidate } from '@/types/api';
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

// ─── Наш документ за заказом портала ─────────────────────────────────────────
// Заказ Газельки закрывает ЛИБО сборку, ЛИБО переезд (в БД это гарантирует
// CHECK). Тип определяет ТОЛЬКО `linked_kind` — угадывать по id нельзя:
// id сборки и id переезда живут в разных пространствах и свободно совпадают,
// так что «переезд 42» уводил бы на сборку 42.

interface LinkedDoc {
    kind: GazelkaLinkKind;
    /** null — бэкенд дал номер без id (старый ответ): показываем без ссылки. */
    id: number | null;
    number: string;
    status: string | null;
}

/**
 * Связанный документ строки. Читаем обобщённые `linked_*`, а старые
 * `linked_assembly_*` остаются фолбэком: их бэкенд заполняет по-прежнему, и в
 * окне деплоя (новый фронт против старого бэка) связка не должна исчезать.
 */
function linkedDoc(row: GazelkaOrderRow): LinkedDoc | null {
    if (row.linked_number) {
        return {
            kind: row.linked_kind ?? 'assembly',
            id: row.linked_id ?? null,
            number: row.linked_number,
            status: row.linked_status ?? null,
        };
    }
    if (row.linked_assembly_number) {
        return {
            kind: 'assembly',
            id: row.linked_assembly_id ?? null,
            number: row.linked_assembly_number,
            status: row.linked_assembly_status ?? null,
        };
    }
    return null;
}

/** Авто-подсказка матчинга — та же пара (kind, id), с тем же фолбэком. */
function suggestedDoc(row: GazelkaOrderRow): { kind: GazelkaLinkKind; id: number; number: string } | null {
    if (row.suggested_id != null && row.suggested_number) {
        return { kind: row.suggested_kind ?? 'assembly', id: row.suggested_id, number: row.suggested_number };
    }
    if (row.suggested_assembly_id != null && row.suggested_assembly_number) {
        return { kind: 'assembly', id: row.suggested_assembly_id, number: row.suggested_assembly_number };
    }
    return null;
}

/** Куда ведёт номер документа: у переезда своя деталка, у сборки своя. */
function docHref(slug: string, kind: GazelkaLinkKind, id: number): string {
    return kind === 'transfer'
        ? `/p/${slug}/warehouse/transfers/${id}`
        : `/p/${slug}/warehouse/assembly/${id}`;
}

/** Подпись документа: «наша ASM-…» / «переезд TR-…» — род и слово разные. */
function docLabel(kind: GazelkaLinkKind, number: string): string {
    return kind === 'transfer' ? `переезд ${number}` : `наша ${number}`;
}

const KIND_TITLE: Record<GazelkaLinkKind, string> = {
    assembly: 'Сборки',
    transfer: 'Переезды',
};

// ─── Match candidate picker ───────────────────────────────────────────────────

interface MatchPickerProps {
    title: string;
    /** С какого типа документа открыть пикер (обычно — тип авто-подсказки). */
    initialKind: GazelkaLinkKind;
    onPick: (candidate: GazelkaMatchCandidate, kind: GazelkaLinkKind) => void;
    onClose: () => void;
    busy: boolean;
}

function MatchPicker({ title, initialKind, onPick, onClose, busy }: MatchPickerProps) {
    const [search, setSearch] = useState('');
    // Тип документа — часть ЗАПРОСА, а не фильтр по загруженному: сборок и
    // переездов вместе бывает много, и бэкенд отдаёт по одному типу за раз.
    const [kind, setKind] = useState<GazelkaLinkKind>(initialKind);
    const [candidates, setCandidates] = useState<GazelkaMatchCandidate[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const cleanupRef = React.useRef<(() => void) | null>(null);

    const loadCandidates = useCallback((q: string, k: GazelkaLinkKind) => {
        setLoading(true);
        setError('');
        const controller = new AbortController();
        api.getGazelkaMatchCandidates(q || undefined, k).then(rows => {
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

    // Перезагрузка при изменении строки поиска (лёгкий debounce 300мс) и при
    // смене типа документа — там ждать нечего, список меняется целиком.
    useEffect(() => {
        const t = setTimeout(() => {
            cleanupRef.current = loadCandidates(search, kind);
        }, search ? 300 : 0);
        return () => {
            clearTimeout(t);
            cleanupRef.current?.();
        };
    }, [search, kind, loadCandidates]);

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
                        <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Сопоставить с нашим документом</h2>
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

                {/* Тип документа + поиск */}
                <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--color-border)', flexShrink: 0 }}>
                    <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
                        {(['assembly', 'transfer'] as GazelkaLinkKind[]).map(k => (
                            <button
                                key={k}
                                className={`btn btn-sm ${kind === k ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setKind(k)}
                                disabled={busy}
                                title={k === 'transfer'
                                    ? 'Переезды между нашими складами'
                                    : 'Заявки на сборку (отгрузка на маркетплейс)'}
                            >
                                {KIND_TITLE[k]}
                            </button>
                        ))}
                    </div>
                    <input
                        className="form-input"
                        type="search"
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        placeholder={kind === 'transfer'
                            ? 'Поиск: № переезда или склад'
                            : 'Поиск: № сборки, склад или поставка WB'}
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
                            <button className="btn btn-secondary btn-sm" onClick={() => loadCandidates(search, kind)} style={{ marginLeft: 12 }}>
                                Повторить
                            </button>
                        </div>
                    )}
                    {!loading && !error && candidates.length === 0 && (
                        <div style={{ padding: '32px', textAlign: 'center', color: 'var(--color-text-muted)' }}>
                            {kind === 'transfer' ? 'Подходящих переездов не найдено' : 'Подходящих сборок не найдено'}
                        </div>
                    )}
                    {!loading && !error && candidates.map(c => {
                        const linked = !!c.already_linked_to;
                        const cKind = c.kind ?? kind;
                        return (
                            <button
                                // id сборки и id переезда пересекаются — ключ
                                // обязан нести тип, иначе React схлопнет строки.
                                key={`${cKind}-${c.assembly_id}`}
                                onClick={() => onPick(c, cKind)}
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
    slug: string;
    onUnmatch: (planId: number) => void;
    onQuickMatch: (planId: number, entityId: number, kind: GazelkaLinkKind) => void;
    onOpenPicker: (row: GazelkaOrderRow) => void;
    matchBusy: number | null;
}

/** Номер нашего документа: ссылка на деталку, а без id — просто бейдж. */
function DocBadge({ doc, slug }: { doc: LinkedDoc; slug: string }) {
    const label = docLabel(doc.kind, doc.number);
    const title = doc.kind === 'transfer'
        ? 'Связанный переезд между нашими складами'
        : 'Связанная заявка на сборку';
    if (doc.id == null) {
        return <span className="badge badge-success" style={{ fontSize: 11 }} title={title}>{label}</span>;
    }
    return (
        <Link
            href={docHref(slug, doc.kind, doc.id)}
            className="badge badge-success"
            style={{ fontSize: 11, textDecoration: 'none' }}
            title={`${title} — открыть`}
        >
            {label} →
        </Link>
    );
}

function LinkCell({ row, slug, onUnmatch, onQuickMatch, onOpenPicker, matchBusy }: LinkCellProps) {
    const planId = Number(row.gazelka_id);
    const busy = matchBusy === planId;
    const linked = linkedDoc(row);
    const suggested = suggestedDoc(row);

    if (linked) {
        return (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                <DocBadge doc={linked} slug={slug} />
                {linked.status && (
                    <span style={{ fontSize: 11, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }} title="Статус нашего документа — где он находится">
                        · {linked.status}
                    </span>
                )}
                <button
                    onClick={() => onUnmatch(planId)}
                    disabled={busy}
                    style={{
                        background: 'none', border: 'none', padding: 0,
                        color: 'var(--color-danger)', fontSize: 11, cursor: busy ? 'wait' : 'pointer',
                        textDecoration: 'underline',
                    }}
                    title="Отвязать наш документ от заказа портала"
                >
                    Отвязать
                </button>
            </div>
        );
    }

    // Несвязанный заказ портала
    if (suggested) {
        return (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                <button
                    className="btn btn-success btn-sm"
                    onClick={() => onQuickMatch(planId, suggested.id, suggested.kind)}
                    disabled={busy}
                    title="Сопоставить с авто-подсказкой"
                    style={{ fontSize: 12 }}
                >
                    {busy ? '...' : `✓ Сопоставить с ${suggested.number}`}
                </button>
                <button
                    onClick={() => onOpenPicker(row)}
                    disabled={busy}
                    style={{
                        background: 'none', border: 'none', padding: 0,
                        color: 'var(--color-accent)', fontSize: 11, cursor: busy ? 'wait' : 'pointer',
                        textDecoration: 'underline',
                    }}
                    title="Выбрать другой документ — сборку или переезд"
                >
                    другой…
                </button>
            </div>
        );
    }

    return (
        <button
            className="btn btn-secondary btn-sm"
            onClick={() => onOpenPicker(row)}
            disabled={busy}
            title="Сопоставить со сборкой или переездом"
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
                        <th>Наш документ</th>
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
                        <th>Наш документ</th>
                        <th>Маршрут</th>
                        <th>Перевозчик</th>
                        <th>Водитель</th>
                        <th>ТС</th>
                        <th>Адрес доставки</th>
                        <th>Дата сдачи</th>
                        <th style={{ textAlign: 'right' }}>Стоимость</th>
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
                            <td style={{ textAlign: 'right', fontWeight: 600 }}>
                                {row.rate ? `${formatNumber(Number(row.rate), 0)} ₽` : '—'}
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

// ─── Completed table (из наших данных — у портала архива нет) ─────────────────

function CompletedTable({ items, slug }: { items: GazelkaOrderRow[]; slug: string }) {
    if (items.length === 0) {
        return (
            <div style={{ padding: '32px', textAlign: 'center', color: 'var(--color-text-muted)' }}>
                Нет завершённых заявок
            </div>
        );
    }
    return (
        <div style={{ overflow: 'auto' }}>
            <table className="data-table" style={{ fontSize: 13 }}>
                <thead>
                    <tr>
                        <th>№ Газельки</th>
                        <th>Наш документ</th>
                        <th>Статус</th>
                        <th>Перевозчик</th>
                        <th>Водитель</th>
                        <th>ТС</th>
                        <th>Адрес доставки</th>
                        <th>Дата отгрузки</th>
                        <th style={{ textAlign: 'right' }}>Стоимость</th>
                    </tr>
                </thead>
                <tbody>
                    {items.map(row => {
                        // Завершённые матчить уже нечем (поездка позади) — только
                        // показываем, что за документ она закрыла.
                        const linked = linkedDoc(row);
                        return (
                        <tr key={row.gazelka_id}>
                            <td style={{ whiteSpace: 'nowrap', fontFamily: 'monospace', fontSize: 12, color: 'var(--color-text-muted)' }}>
                                {row.gazelka_id}
                            </td>
                            <td style={{ whiteSpace: 'nowrap' }}>
                                {linked ? <DocBadge doc={linked} slug={slug} /> : '—'}
                            </td>
                            <td style={{ whiteSpace: 'nowrap' }}>
                                <span className={`badge ${statusBadgeClass(row.status_label || row.status)}`}>
                                    {row.status_label || row.status}
                                </span>
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
                            <td style={{ textAlign: 'right', fontWeight: 600 }}>
                                {row.rate ? `${formatNumber(Number(row.rate), 0)} ₽` : '—'}
                            </td>
                        </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function GazelkaOrdersTab() {
    // slug нужен ссылкам на наши документы: у сборки и переезда разные деталки.
    const params = useParams();
    const slug = params.slug as string;
    const [planned, setPlanned] = useState<GazelkaOrderRow[]>([]);
    const [active, setActive] = useState<GazelkaOrderRow[]>([]);
    const [completed, setCompleted] = useState<GazelkaOrderRow[]>([]);
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
            api.getGazelkaCompleted(),
        ]).then(([p, a, c]: [GazelkaOrderList, GazelkaOrderList, GazelkaOrderList]) => {
            if (controller.signal.aborted) return;
            setPlanned(p.items);
            setActive(a.items);
            setCompleted(c.items);
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

    const handleMatch = useCallback(async (planId: number, entityId: number, kind: GazelkaLinkKind) => {
        setMatchBusy(planId);
        setMatchError('');
        try {
            // kind решает, в какое поле уедет id: заказ портала закрывает ЛИБО
            // сборку, ЛИБО переезд — обе ссылки сразу запрещены CHECK'ом в БД.
            const res = await api.matchGazelkaOrder(planId, entityId, kind);
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
                setMatchError('Не удалось отвязать документ');
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
        slug,
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

                    {/* ── Завершённые (из наших данных) ── */}
                    <div className="glass-card" style={{ marginTop: 20, padding: 0, overflow: 'hidden' }}>
                        <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--color-border)', display: 'flex', alignItems: 'center', gap: 10 }}>
                            <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>Завершённые</h3>
                            <span className="badge badge-secondary">{completed.length}</span>
                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                отгруженные заявки (у портала архива нет — данные наши)
                            </span>
                        </div>
                        <CompletedTable items={completed} slug={slug} />
                    </div>
                </>
            )}

            {/* Match picker */}
            {pickerRow && (
                <MatchPicker
                    title={`Заявка ${pickerRow.gazelka_id}${pickerRow.supply_id ? ` · ${pickerRow.supply_id}` : ''}`}
                    // Открываем на типе авто-подсказки: если бэкенд уже понял,
                    // что заказ похож на переезд, логисту не надо это повторять.
                    initialKind={suggestedDoc(pickerRow)?.kind ?? 'assembly'}
                    onPick={(c, kind) => handleMatch(Number(pickerRow.gazelka_id), c.assembly_id, kind)}
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

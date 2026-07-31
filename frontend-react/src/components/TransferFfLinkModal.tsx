'use client';
import { useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import { ffLinkLabel, ffLinkStage, filterTransferFfLinks } from '@/lib/transfer';
import type { TransferFfLink, TransferFfSide } from '@/types/api';

/**
 * Модалка «Связать заявку ФФ» карточки переезда.
 *
 * Выбор МНОЖЕСТВЕННЫЙ намеренно: на одну сторону маршрута приходится несколько
 * заявок ФФ — у Натали короба и штучные приезжают отдельными документами, и
 * связывать их по одной означало бы открывать модалку N раз.
 *
 * Связывание идёт существующими ручками ФФ (`/warehouse/{wh}/fulfillment/
 * requests/{id}/link` со слотом `stock_transfer_id`) по одной заявке —
 * пакетной ручки нет. Поэтому отказ на середине оставляет ЧАСТЬ связанной:
 * молча закрываться нельзя, показываем что прошло, что нет, и оставляем в
 * выборе только упавшие.
 */

interface Props {
    transferId: number;
    transferNumber: string;
    side: TransferFfSide;
    /** Склад стороны — фолбэк, если у кандидата не пришёл warehouse_id. */
    warehouseId: number;
    warehouseName: string | null;
    /** Закрыть без изменений. */
    onClose: () => void;
    /** Хотя бы одна заявка связана — родитель перезагружает карточку. */
    onLinked: () => void | Promise<void>;
}

const SIDE_TITLE: Record<TransferFfSide, string> = {
    source: 'Отгрузка у склада-источника',
    dest: 'Приёмка у склада-получателя',
};

/**
 * Лимит выборки кандидатов на бэкенде (`_LINK_CANDIDATES_LIMIT`, свежие сверху).
 * Поиск здесь КЛИЕНТСКИЙ: на упёршемся в лимит ответе старые заявки не найдутся
 * вообще — «ничего не найдено» соврало бы. Упёрлись — говорим об этом прямо.
 */
const CANDIDATES_LIMIT = 300;

/** Вид заявки ФФ словами — бейдж строки кандидата. */
function kindLabel(kind: string): string {
    if (kind === 'assembly') return 'сборка';
    if (kind === 'inbound') return 'приёмка';
    if (kind === 'return') return 'возврат';
    return kind || 'заявка';
}

export default function TransferFfLinkModal({
    transferId,
    transferNumber,
    side,
    warehouseId,
    warehouseName,
    onClose,
    onLinked,
}: Props) {
    const [candidates, setCandidates] = useState<TransferFfLink[] | null>(null);
    const [loadError, setLoadError] = useState('');
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState('');
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [linking, setLinking] = useState(false);
    const [linkError, setLinkError] = useState('');

    // Повтор после ошибки — через счётчик, а не прямым вызовом загрузки: иначе
    // ответ отменённого запроса мог бы перезаписать результат нового.
    const [reloadKey, setReloadKey] = useState(0);
    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setLoadError('');
        api.getTransferFfCandidates(transferId, side)
            .then(rows => { if (!cancelled) setCandidates(rows); })
            .catch((e: unknown) => {
                if (!cancelled) setLoadError(e instanceof Error ? e.message : 'Не удалось загрузить заявки ФФ');
            })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [transferId, side, reloadKey]);

    const filtered = useMemo(
        () => filterTransferFfLinks(candidates ?? [], search),
        [candidates, search],
    );

    const toggle = (id: number) => {
        setSelected(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id); else next.add(id);
            return next;
        });
    };

    const handleLink = async () => {
        const chosen = (candidates ?? []).filter(c => selected.has(c.id));
        if (chosen.length === 0 || linking) return;
        setLinking(true);
        setLinkError('');
        const failed: { link: TransferFfLink; message: string }[] = [];
        const linked = new Set<number>();
        for (const c of chosen) {
            try {
                await api.linkFulfillmentRequest(c.warehouse_id ?? warehouseId, c.id, { stock_transfer_id: transferId });
                linked.add(c.id);
            } catch (e: unknown) {
                failed.push({ link: c, message: e instanceof Error ? e.message : 'ошибка связывания' });
            }
        }
        if (linked.size > 0) await onLinked();
        if (failed.length === 0) {
            onClose();
            return;
        }
        // Часть связалась — убираем её из кандидатов и из выбора, чтобы повтор
        // не ударил второй раз по уже связанным.
        setCandidates(prev => (prev ?? []).filter(c => !linked.has(c.id)));
        setSelected(new Set(failed.map(f => f.link.id)));
        setLinkError(
            `${linked.size > 0 ? `Связано ${formatNumber(linked.size, 0)}. ` : ''}`
            + `Не удалось: ${failed.map(f => `${ffLinkLabel(f.link)} — ${f.message}`).join('; ')}`,
        );
        setLinking(false);
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card modal-card-wide modal-card-solid" onClick={e => e.stopPropagation()}>
                <h2 className="modal-title" style={{ marginBottom: 8 }}>
                    Связать заявки ФФ · {SIDE_TITLE[side]}
                </h2>
                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                    Переезд {transferNumber}
                    {warehouseName ? <> · склад <b style={{ color: 'var(--color-text)' }}>{warehouseName}</b></> : null}
                    {' · '}
                    можно выбрать несколько: короба и штучные у ФФ бывают отдельными заявками.
                </p>

                {linkError && (
                    <div style={{
                        marginBottom: 12, padding: '8px 12px', borderRadius: 8,
                        background: 'rgba(239, 68, 68, 0.12)', color: 'var(--color-danger)', fontSize: 13,
                    }}>
                        {linkError}
                    </div>
                )}

                {loading ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка заявок ФФ...</div>
                ) : loadError ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-danger)', fontSize: 13 }}>
                        {loadError}
                        <div style={{ marginTop: 12 }}>
                            <button className="btn btn-secondary btn-sm" onClick={() => setReloadKey(k => k + 1)}>Повторить</button>
                        </div>
                    </div>
                ) : (candidates?.length ?? 0) === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>
                        {side === 'dest'
                            ? 'На складе-получателе нет заявок ФФ — интеграция не подключена.'
                            : 'На складе-источнике нет свободных заявок ФФ — все уже связаны с другими документами.'}
                    </div>
                ) : (
                    <>
                        <input
                            type="text"
                            className="form-input ff-link-search"
                            placeholder="Поиск: номер, стадия, статус"
                            value={search}
                            onChange={e => setSearch(e.target.value)}
                        />
                        {(candidates?.length ?? 0) >= CANDIDATES_LIMIT && (
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>
                                Показаны {formatNumber(CANDIDATES_LIMIT, 0)} самых свежих заявок склада — более
                                старые в список не попали, поиск идёт по этим же.
                            </div>
                        )}
                        {filtered.length === 0 ? (
                            <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>
                                Ничего не найдено по запросу
                            </div>
                        ) : (
                            <div className="ff-link-list">
                                {filtered.map(c => (
                                    <label key={c.id} className="ff-link-row" style={{ cursor: 'pointer' }}>
                                        <div className="ff-link-row-main">
                                            <div className="ff-link-row-head">
                                                <input
                                                    type="checkbox"
                                                    checked={selected.has(c.id)}
                                                    onChange={() => toggle(c.id)}
                                                    disabled={linking}
                                                />
                                                <span className="ff-link-row-number">{ffLinkLabel(c)}</span>
                                                <span className="badge badge-secondary" style={{ fontSize: 11, padding: '2px 8px' }}>
                                                    {kindLabel(c.kind)}
                                                </span>
                                                <span className="badge badge-info" style={{ fontSize: 11, padding: '2px 8px' }}>
                                                    {ffLinkStage(c)}
                                                </span>
                                            </div>
                                            <span className="ff-link-row-meta">
                                                {c.external_created_at ? `${formatDate(c.external_created_at)} · ` : ''}
                                                {c.total_qty == null ? 'количество неизвестно' : `${formatNumber(c.total_qty, 0)} шт`}
                                            </span>
                                        </div>
                                    </label>
                                ))}
                            </div>
                        )}
                    </>
                )}

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={linking}>Отмена</button>
                    <button
                        className="btn btn-primary"
                        onClick={handleLink}
                        disabled={linking || selected.size === 0}
                    >
                        {linking ? 'Связывание...' : `Связать выбранные (${formatNumber(selected.size, 0)})`}
                    </button>
                </div>
            </div>
        </div>
    );
}

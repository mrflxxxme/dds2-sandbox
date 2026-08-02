'use client';
import { useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import { ffLinkLabel, ffLinkStage, filterTransferFfLinks } from '@/lib/transfer';
import type { AssemblyFfCandidate, FfLinkPayload, TransferFfSide } from '@/types/api';

/**
 * Модалка «Связать заявку ФФ» — ОДНА на два наших документа: переезд и заявку
 * на сборку (включая учётное зеркало FBS). Внутри всё одинаково: список
 * свободных заявок ФФ склада → чекбоксы → те же ручки link. Различаются только
 * подписи и слот связи, поэтому разводим их пропсами, а не копией компонента.
 *
 * Имя файла осталось от переезда (первого потребителя) — переименование стоило
 * бы правок во всех местах импорта без выигрыша по смыслу.
 *
 * Выбор МНОЖЕСТВЕННЫЙ намеренно: на одну сторону маршрута (и на одну сборку)
 * приходится несколько заявок ФФ — у Натали короба и штучные приезжают
 * отдельными документами, и связывать их по одной означало бы открывать
 * модалку N раз.
 *
 * Связывание идёт существующими ручками ФФ (`/warehouse/{wh}/fulfillment/
 * requests/{id}/link` со слотом `stock_transfer_id` / `assembly_request_id`)
 * по одной заявке — пакетной ручки нет. Поэтому отказ на середине оставляет
 * ЧАСТЬ связанной: молча закрываться нельзя, показываем что прошло, что нет,
 * и оставляем в выборе только упавшие.
 */

/** Общее для обоих документов: куда вернуть управление и чем закрыть дыры. */
interface BaseProps {
    /** Склад документа — фолбэк, если у кандидата не пришёл warehouse_id. */
    warehouseId: number;
    warehouseName: string | null;
    /** Закрыть без изменений. */
    onClose: () => void;
    /** Хотя бы одна заявка связана — родитель перезагружает карточку. */
    onLinked: () => void | Promise<void>;
}

/**
 * Переезд. `kind` необязателен: так все существующие вызовы (карточка переезда,
 * вкладка склада) продолжают работать без правок, а новый режим обязан назваться
 * явно.
 */
interface TransferProps extends BaseProps {
    kind?: 'transfer';
    transferId: number;
    transferNumber: string;
    /** Сторона маршрута: у переезда документы источника и получателя разные. */
    side: TransferFfSide;
}

/** Заявка на сборку: сторон маршрута нет — склад ровно один, склад сборки. */
interface AssemblyProps extends BaseProps {
    kind: 'assembly';
    assemblyId: number;
    assemblyNumber: string;
}

type Props = TransferProps | AssemblyProps;

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

export default function TransferFfLinkModal(props: Props) {
    const { warehouseId, warehouseName, onClose, onLinked } = props;
    // Разбираем union в примитивы СРАЗУ: дальше по компоненту нужен только id
    // документа и подписи, а хуки требуют примитивных зависимостей.
    const isAssembly = props.kind === 'assembly';
    const docId = props.kind === 'assembly' ? props.assemblyId : props.transferId;
    const docNumber = props.kind === 'assembly' ? props.assemblyNumber : props.transferNumber;
    // У сборки стороны нет — держим null, чтобы это не читалось как «source».
    const side: TransferFfSide | null = props.kind === 'assembly' ? null : props.side;

    // Общий тип строки на оба режима — `AssemblyFfCandidate` (= TransferFfLink
    // без `side`): ответ переезда в него укладывается, а сборке лишнего поля не
    // приписывает.
    const [candidates, setCandidates] = useState<AssemblyFfCandidate[] | null>(null);
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
        const request = side === null
            ? api.getAssemblyFfCandidates(docId)
            : api.getTransferFfCandidates(docId, side);
        request
            .then(rows => { if (!cancelled) setCandidates(rows); })
            .catch((e: unknown) => {
                if (!cancelled) setLoadError(e instanceof Error ? e.message : 'Не удалось загрузить заявки ФФ');
            })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [docId, side, reloadKey]);

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
        const failed: { link: AssemblyFfCandidate; message: string }[] = [];
        const linked = new Set<number>();
        // Слот связи — единственное, чем два режима различаются на записи:
        // одна и та же ручка ФФ кладёт id в assembly_request_id либо в
        // stock_transfer_id.
        const payload: FfLinkPayload = isAssembly
            ? { assembly_request_id: docId }
            : { stock_transfer_id: docId };
        for (const c of chosen) {
            try {
                await api.linkFulfillmentRequest(c.warehouse_id ?? warehouseId, c.id, payload);
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
                    {side === null ? 'Заявка ФФ по этой сборке' : `Связать заявки ФФ · ${SIDE_TITLE[side]}`}
                </h2>
                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                    {side === null ? 'Сборка' : 'Переезд'} {docNumber}
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
                        {side === null
                            ? 'У склада сборки нет свободных заявок ФФ: либо все уже связаны с другими документами, либо ФФ-интеграция не подключена.'
                            : side === 'dest'
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

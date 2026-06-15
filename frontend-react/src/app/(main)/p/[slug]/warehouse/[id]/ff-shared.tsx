'use client';
/** Общие помощники вкладок «ФФ сборка»/«ФФ приёмки» и страницы деталки заявки ФФ */
import React, { useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import { filterFfLinkCandidates, splitFfLinkCandidates } from '@/lib/utils/ffLinkCandidates';
import type { FfLinkCandidate, FfLinkCandidatesResponse, FfRequestKind, FfRequestRow, FfStatusEvent } from '@/types/api';

// Статусы наших документов (заявки на сборку + приёмки) — для колонки/строки «Связь»
export const FF_LINKED_STATUS_LABELS: Record<string, string> = {
    // AssemblyRequest
    PENDING: 'Ожидает',
    IN_PROGRESS: 'В сборке',
    READY: 'Готова',
    VEHICLE_ASSIGNED: 'Машина назначена',
    SHIPPED: 'Отгружена',
    DELIVERED: 'Доставлена',
    CLOSED: 'Закрыта',
    CANCELLED: 'Отменена',
    // InboundReceipt
    DRAFT: 'Черновик',
    EXPECTED: 'Ожидается',
    ACCEPTED: 'Принята',
};

// Предупреждение о пропущенных ШК при создании сборки из ФФ-заявки (длинный список усекаем)
const FF_SKIPPED_SHOWN_MAX = 5;
export function ffSkippedNotice(assemblyNumber: string, skipped: string[]): string {
    const shown = skipped.slice(0, FF_SKIPPED_SHOWN_MAX).join(', ');
    const rest = skipped.length - FF_SKIPPED_SHOWN_MAX;
    return `Заявка № ${assemblyNumber} создана без ${formatNumber(skipped.length, 0)} ШК: ${shown}`
        + (rest > 0 ? ` и ещё ${formatNumber(rest, 0)}` : '');
}

// Бейдж стадии заявки ФФ — общий для таблицы и страницы деталки
export function ffStageBadge(row: FfRequestRow) {
    if (row.is_completed) return <span className="badge badge-success" style={{ fontSize: 11, padding: '2px 8px' }}>Завершена</span>;
    if (row.expired) return <span className="badge badge-warning" style={{ fontSize: 11, padding: '2px 8px' }}>Просрочена</span>;
    if (row.archived) return <span className="badge badge-secondary" style={{ fontSize: 11, padding: '2px 8px' }}>Архив</span>;
    return null;
}

/* ─── История синхронизации: помощники отображения событий ───────────────── */

// Человекочитаемая стадия из снимка события (title → status → прочерк)
function ffEventStage(stageTitle: string | null, status: string | null): string {
    return stageTitle || status || '—';
}

// Краткое описание изменения — для таблицы и Excel-экспорта
export function ffEventSummary(e: FfStatusEvent): string {
    const newStage = ffEventStage(e.new_stage_title, e.new_status);
    if (e.event_type === 'created') return `Заявка появилась · ${newStage}`;

    const oldStage = ffEventStage(e.old_stage_title, e.old_status);
    const parts: string[] = [];
    if (oldStage !== newStage) parts.push(`${oldStage} → ${newStage}`);
    if (e.new_is_completed && !e.old_is_completed) parts.push('завершена');
    if (!e.new_is_completed && e.old_is_completed) parts.push('возобновлена');
    if (e.new_archived && !e.old_archived) parts.push('в архив (провайдер)');
    if (!e.new_archived && e.old_archived) parts.push('из архива (провайдер)');
    return parts.length ? parts.join(' · ') : newStage;
}

// Бейдж типа события для строки истории
export function ffEventBadge(e: FfStatusEvent) {
    if (e.event_type === 'created') {
        return <span className="badge badge-info" style={{ fontSize: 11, padding: '2px 8px' }}>Появилась</span>;
    }
    if (e.new_is_completed && !e.old_is_completed) {
        return <span className="badge badge-success" style={{ fontSize: 11, padding: '2px 8px' }}>Завершена</span>;
    }
    if (e.new_archived && !e.old_archived) {
        return <span className="badge badge-secondary" style={{ fontSize: 11, padding: '2px 8px' }}>Архив</span>;
    }
    return <span className="badge badge-warning" style={{ fontSize: 11, padding: '2px 8px' }}>Смена стадии</span>;
}

/* ─── Модал «Связать заявку» — пикер документа склада ────────────────────── */

export function FfLinkModal({ warehouseId, kind, request, onClose, onLinked }: {
    warehouseId: number;
    kind: FfRequestKind;
    /** Заявка ФФ, которую связываем (id для POST, номер для заголовка) */
    request: { id: number; number: string | null; external_id: string };
    onClose: () => void;
    /** Успешное связывание: обновлённая строка заявки от бэкенда */
    onLinked: (updated: FfRequestRow) => void;
}) {
    const [data, setData] = useState<FfLinkCandidatesResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [acting, setActing] = useState(false);
    const [search, setSearch] = useState('');

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError('');
        api.getFfLinkCandidates(warehouseId, request.id)
            .then(r => { if (!controller.signal.aborted) setData(r); })
            .catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка загрузки документов'); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [warehouseId, request.id]);

    const handleLink = async (docId: number) => {
        setActing(true);
        setError('');
        try {
            const payload = kind === 'assembly'
                ? { assembly_request_id: docId }
                : { inbound_receipt_id: docId };
            const updated = await api.linkFulfillmentRequest(warehouseId, request.id, payload);
            onLinked(updated);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка связывания');
            setActing(false);
        }
    };

    // Клиентский поиск + разбивка: «похожие по наполнению» / «все документы»
    const { scored, others } = useMemo(
        () => splitFfLinkCandidates(filterFfLinkCandidates(data?.candidates ?? [], search)),
        [data, search],
    );
    const hasCandidates = (data?.candidates.length ?? 0) > 0;

    const candidateRow = (c: FfLinkCandidate, withScore: boolean) => (
        <div key={c.doc_id} className="ff-link-row">
            <div className="ff-link-row-main">
                <div className="ff-link-row-head">
                    <span className="ff-link-row-number">{c.number}</span>
                    <span className="badge badge-secondary" style={{ fontSize: 11, padding: '2px 8px' }}>
                        {FF_LINKED_STATUS_LABELS[c.status] || c.status}
                    </span>
                    {withScore && c.score != null && (
                        <span className={`badge ${c.score >= 70 ? 'badge-success' : 'badge-info'}`} style={{ fontSize: 11, padding: '2px 8px' }}>
                            {formatNumber(c.score, 0)}%
                        </span>
                    )}
                    <span className="ff-link-row-meta">
                        {c.created_at ? `${formatDate(c.created_at)} · ` : ''}{formatNumber(c.total_qty, 0)} шт
                        {c.fbo_supply_number ? ` · ФБО ${c.fbo_supply_number}` : ''}
                        {c.dest_warehouse ? ` · ${c.dest_warehouse}` : ''}
                    </span>
                </div>
                {withScore && c.reason && <div className="ff-link-row-reason">{c.reason}</div>}
            </div>
            <button className="btn btn-sm btn-primary" onClick={() => handleLink(c.doc_id)} disabled={acting}>
                {acting ? '...' : 'Выбрать'}
            </button>
        </div>
    );

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card modal-card-wide modal-card-solid" onClick={e => e.stopPropagation()}>
                <h2 className="modal-title" style={{ marginBottom: 8 }}>
                    Связать заявку {data?.ff_number || request.number || request.external_id}
                    {data?.ff_total_qty != null && (
                        <span style={{ fontWeight: 500, color: 'var(--color-text-muted)' }}> · {formatNumber(data.ff_total_qty, 0)} шт</span>
                    )}
                </h2>
                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                    Выберите {kind === 'assembly' ? 'заявку на сборку' : 'приёмку'} этого склада:
                </p>

                {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 12 }}>{error}</div>}

                {loading ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
                ) : !hasCandidates ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                        {kind === 'assembly' ? 'Нет заявок на сборку у этого склада' : 'Нет приёмок у этого склада'}
                    </div>
                ) : (
                    <>
                        <input
                            type="text"
                            className="form-input ff-link-search"
                            placeholder="Поиск: номер, ФБО-поставка, склад назначения"
                            value={search}
                            onChange={e => setSearch(e.target.value)}
                        />
                        {data != null && !data.composition_available && (
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>
                                Состав ФФ-заявки недоступен — похожие по наполнению не рассчитаны
                            </div>
                        )}
                        {scored.length === 0 && others.length === 0 ? (
                            <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                                Ничего не найдено по запросу
                            </div>
                        ) : (
                            <div className="ff-link-list">
                                {scored.length > 0 && (
                                    <>
                                        <div className="ff-link-section-title">Похожие по наполнению</div>
                                        {scored.map(c => candidateRow(c, true))}
                                    </>
                                )}
                                {others.length > 0 && (
                                    <>
                                        <div className="ff-link-section-title">Все документы</div>
                                        {others.map(c => candidateRow(c, false))}
                                    </>
                                )}
                            </div>
                        )}
                    </>
                )}

                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
                </div>
            </div>
        </div>
    );
}

'use client';
/** Общие помощники вкладок «ФФ сборка»/«ФФ приёмки» и страницы деталки заявки ФФ */
import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate } from '@/lib/utils';
import type { FfRequestKind, FfRequestRow } from '@/types/api';

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

// Бейдж стадии заявки ФФ — общий для таблицы и страницы деталки
export function ffStageBadge(row: FfRequestRow) {
    if (row.is_completed) return <span className="badge badge-success" style={{ fontSize: 11, padding: '2px 8px' }}>Завершена</span>;
    if (row.expired) return <span className="badge badge-warning" style={{ fontSize: 11, padding: '2px 8px' }}>Просрочена</span>;
    if (row.archived) return <span className="badge badge-secondary" style={{ fontSize: 11, padding: '2px 8px' }}>Архив</span>;
    return null;
}

/* ─── Модал «Связать заявку» — пикер документа склада ────────────────────── */

type FfLinkCandidate = { id: number; number: string; status: string; date: string | null };

export function FfLinkModal({ warehouseId, kind, request, onClose, onLinked }: {
    warehouseId: number;
    kind: FfRequestKind;
    /** Заявка ФФ, которую связываем (id для POST, номер для заголовка) */
    request: { id: number; number: string | null; external_id: string };
    onClose: () => void;
    /** Успешное связывание: обновлённая строка заявки от бэкенда */
    onLinked: (updated: FfRequestRow) => void;
}) {
    const [candidates, setCandidates] = useState<FfLinkCandidate[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [acting, setActing] = useState(false);

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError('');
        const load = async () => {
            try {
                if (kind === 'assembly') {
                    const r = await api.getAssemblyRequests({ warehouse_id: warehouseId, limit: 200 });
                    if (!controller.signal.aborted) setCandidates(r.items.map(a => ({ id: a.id, number: a.number, status: a.status, date: a.created_at ?? null })));
                } else {
                    const r = await api.getReceipts(warehouseId);
                    if (!controller.signal.aborted) setCandidates(r.map(x => ({ id: x.id, number: x.number, status: x.status, date: x.created_at ?? null })));
                }
            } catch (e: unknown) {
                if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка загрузки документов');
            } finally {
                if (!controller.signal.aborted) setLoading(false);
            }
        };
        load();
        return () => controller.abort();
    }, [warehouseId, kind]);

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

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 520 }}>
                <h2 className="modal-title">Связать заявку {request.number || request.external_id}</h2>
                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                    Выберите {kind === 'assembly' ? 'заявку на сборку' : 'приёмку'} этого склада:
                </p>

                {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 12 }}>{error}</div>}

                {loading ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
                ) : candidates.length === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                        {kind === 'assembly' ? 'Нет заявок на сборку у этого склада' : 'Нет приёмок у этого склада'}
                    </div>
                ) : (
                    <div style={{ maxHeight: 360, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6 }}>
                        {candidates.map(d => (
                            <div
                                key={d.id}
                                style={{
                                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                    padding: '10px 12px', border: '1px solid var(--color-border)', borderRadius: 8,
                                }}
                            >
                                <div>
                                    <span style={{ fontWeight: 600 }}>{d.number}</span>
                                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)', marginLeft: 8 }}>
                                        {FF_LINKED_STATUS_LABELS[d.status] || d.status}
                                        {d.date ? ` · ${formatDate(d.date)}` : ''}
                                    </span>
                                </div>
                                <button
                                    className="btn btn-sm btn-primary"
                                    onClick={() => handleLink(d.id)}
                                    disabled={acting}
                                >
                                    {acting ? '...' : 'Выбрать'}
                                </button>
                            </div>
                        ))}
                    </div>
                )}

                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
                </div>
            </div>
        </div>
    );
}

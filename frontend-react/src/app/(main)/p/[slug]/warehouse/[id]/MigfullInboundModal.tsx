'use client';
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type {
    MigfullInboundDraft,
    MigfullInboundSendRequest,
    MigfullSendResult,
} from '@/types/api';

/**
 * Confirm-модалка «Создать поставку у Натали»: превью состава нашей приёмки
 * машины (позиции / штуки / короба) → РЕАЛЬНОЕ создание поставки (приёмки)
 * в портале ФФ «Натали» (migfull). Создание НЕОБРАТИМО — портал не даёт
 * удалить/отменить документ, поэтому повтор требует явного подтверждения.
 */
interface Props {
    /** id нашей приёмки машины (InboundReceipt) — источник поставки */
    receiptId: number;
    /** номер машины (V-…) для заголовка */
    vehicleOrderNo: string;
    onClose: () => void;
    /** поставка создана (res.ok): родитель показывает тост и перезагружает списки */
    onSuccess: (res: MigfullSendResult) => void;
}

export default function MigfullInboundModal({ receiptId, vehicleOrderNo, onClose, onSuccess }: Props) {
    const [draft, setDraft] = useState<MigfullInboundDraft | null>(null);
    const [loadingDraft, setLoadingDraft] = useState(true);
    const [draftError, setDraftError] = useState('');

    // Поля шапки (инициализируются из prefill после загрузки draft).
    const [number, setNumber] = useState('');
    const [submissionDate, setSubmissionDate] = useState('');
    const [notes, setNotes] = useState('');

    // Подтверждение повторной отправки (already_sent или ответ 409).
    const [confirmResend, setConfirmResend] = useState(false);

    const [submitting, setSubmitting] = useState(false);
    const [submitError, setSubmitError] = useState('');
    const [result, setResult] = useState<MigfullSendResult | null>(null);

    // ─── Загрузка draft (маунт + кнопка «Повторить»). StrictMode-safe. ──────────
    const loadDraft = useCallback(() => {
        setLoadingDraft(true);
        setDraftError('');
        const controller = new AbortController();
        api.migfullInboundDraft(receiptId).then(d => {
            if (controller.signal.aborted) return;
            setDraft(d);
            setNumber(d.prefill.number ?? '');
            setSubmissionDate(d.prefill.submission_date ?? '');
            setNotes(d.prefill.notes ?? '');
            setConfirmResend(false);
        }).catch((e: unknown) => {
            if (controller.signal.aborted) return;
            setDraftError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }).finally(() => {
            if (!controller.signal.aborted) setLoadingDraft(false);
        });
        return () => controller.abort();
    }, [receiptId]);

    useEffect(() => loadDraft(), [loadDraft]);

    const hasOpis = !!draft && draft.opis_lines.length > 0;
    // Кнопка требует подтверждения, если поставка уже создавалась/связана.
    const needsConfirm = !!draft?.already_sent;
    const canSubmit = !!draft && draft.eligible && hasOpis && (!needsConfirm || confirmResend);

    const doSend = async (forceResend: boolean) => {
        if (!draft) return;
        const body: MigfullInboundSendRequest = {
            number: number.trim() || null,
            submission_date: submissionDate || null,
            notes: notes.trim() || null,
            force_resend: forceResend,
        };
        setSubmitting(true);
        setSubmitError('');
        try {
            const res = await api.migfullInboundSend(receiptId, body);
            if (res.ok) {
                setResult(res);
                onSuccess(res);
            } else {
                setSubmitError(res.message || 'Не удалось создать поставку');
            }
        } catch (e: unknown) {
            // 409 от бэка (повторная отправка без force_resend) → подтверждение и повтор.
            if (e && typeof e === 'object' && (e as { code?: string }).code === 'conflict') {
                setConfirmResend(true);
                setSubmitError('Поставка для этой приёмки уже есть у ФФ. Подтвердите повторную отправку и нажмите «Создать поставку» ещё раз.');
            } else {
                setSubmitError(e instanceof Error ? e.message : 'Ошибка отправки');
            }
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div
            className="modal-overlay"
            style={{ padding: '24px 16px' }}
            onClick={e => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div
                className="modal-card modal-card-wide modal-card-solid"
                style={{
                    width: 720, maxWidth: '94vw',
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
                    padding: '18px 24px', borderBottom: '1px solid var(--color-border)', flexShrink: 0,
                }}>
                    <div>
                        <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>Создать поставку у Натали</h2>
                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 2 }}>
                            Машина {vehicleOrderNo}
                            {draft?.prefill.receipt_number ? ` · приёмка ${draft.prefill.receipt_number}` : ''}
                        </div>
                    </div>
                    <button
                        onClick={onClose}
                        style={{
                            background: 'none', border: 'none', fontSize: 24,
                            cursor: 'pointer', color: 'var(--color-text-muted)',
                            lineHeight: 1, padding: '0 4px',
                        }}
                        aria-label="Закрыть"
                    >
                        ×
                    </button>
                </div>

                {/* Body */}
                <div style={{ padding: '20px 24px', overflowY: 'auto', flex: 1 }}>

                    {loadingDraft && (
                        <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--color-text-muted)' }}>
                            Загрузка состава...
                        </div>
                    )}

                    {!loadingDraft && draftError && (
                        <div style={{ padding: 16, borderRadius: 8, background: 'rgba(239,68,68,0.1)', color: 'var(--color-danger)' }}>
                            {draftError}
                            <button className="btn btn-secondary btn-sm" onClick={loadDraft} style={{ marginLeft: 12 }}>
                                Повторить
                            </button>
                        </div>
                    )}

                    {!loadingDraft && !draftError && draft && !draft.eligible && (
                        <div style={{ padding: 16, borderRadius: 8, background: 'rgba(245,158,11,0.1)', color: 'var(--color-warning)' }}>
                            Склад приёмки не совпадает со складом интеграции ФФ Натали — поставку создать нельзя.
                        </div>
                    )}

                    {/* Success */}
                    {result && (
                        <div style={{ padding: '14px 16px', borderRadius: 8, background: 'rgba(34,197,94,0.1)', color: 'var(--color-success)' }}>
                            <div style={{ fontWeight: 600, marginBottom: 4 }}>✓ Поставка создана у Натали</div>
                            {result.shipment_number && <div style={{ fontSize: 14 }}>Номер поставки: <strong>{result.shipment_number}</strong></div>}
                            {result.message && <div style={{ fontSize: 13, marginTop: 4 }}>{result.message}</div>}
                        </div>
                    )}

                    {/* Форма + состав (пока нет результата) */}
                    {!loadingDraft && !draftError && draft && draft.eligible && !result && (
                        <>
                            {/* Уже отправляли / уже связана PVB */}
                            {draft.already_sent && (
                                <div style={{ padding: '12px 16px', borderRadius: 8, background: 'rgba(245,158,11,0.12)', color: 'var(--color-warning)', marginBottom: 16, fontSize: 14 }}>
                                    <div style={{ fontWeight: 600 }}>
                                        Поставка для этой приёмки уже есть{draft.sent_number ? ` (${draft.sent_number})` : ''}.
                                    </div>
                                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8, cursor: 'pointer' }}>
                                        <input
                                            type="checkbox"
                                            checked={confirmResend}
                                            onChange={e => setConfirmResend(e.target.checked)}
                                            style={{ width: 16, height: 16, accentColor: 'var(--color-accent)', cursor: 'pointer' }}
                                        />
                                        <span style={{ color: 'var(--color-text)' }}>Подтверждаю — создать ещё одну поставку</span>
                                    </label>
                                </div>
                            )}

                            {/* Warnings */}
                            {draft.warnings.length > 0 && (
                                <div style={{ padding: '12px 16px', borderRadius: 8, background: 'rgba(245,158,11,0.1)', color: 'var(--color-warning)', marginBottom: 16, fontSize: 13 }}>
                                    {draft.warnings.map((w, i) => (
                                        <div key={i}>⚠ {w}</div>
                                    ))}
                                </div>
                            )}

                            {/* Шапка поставки */}
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 20 }}>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                    <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-muted)' }}>Номер (для оператора)</label>
                                    <input
                                        value={number}
                                        onChange={e => setNumber(e.target.value)}
                                        placeholder="V-…"
                                        style={{
                                            padding: '7px 10px', borderRadius: 8,
                                            border: '1px solid var(--color-border)',
                                            background: 'var(--color-bg-card)', color: 'var(--color-text)', fontSize: 14,
                                        }}
                                    />
                                </div>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                    <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-muted)' }}>Дата поставки</label>
                                    <input
                                        type="date"
                                        value={submissionDate}
                                        onChange={e => setSubmissionDate(e.target.value)}
                                        style={{
                                            padding: '7px 10px', borderRadius: 8,
                                            border: '1px solid var(--color-border)',
                                            background: 'var(--color-bg-card)', color: 'var(--color-text)', fontSize: 14,
                                        }}
                                    />
                                </div>
                                <div style={{ gridColumn: '1 / -1', display: 'flex', flexDirection: 'column', gap: 4 }}>
                                    <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-muted)' }}>Комментарий</label>
                                    <textarea
                                        value={notes}
                                        onChange={e => setNotes(e.target.value)}
                                        rows={2}
                                        placeholder="Дополнительные комментарии..."
                                        style={{
                                            width: '100%', padding: '8px 10px', borderRadius: 8,
                                            border: '1px solid var(--color-border)',
                                            background: 'var(--color-bg-card)', color: 'var(--color-text)',
                                            fontSize: 14, resize: 'vertical', boxSizing: 'border-box',
                                        }}
                                    />
                                </div>
                            </div>

                            {/* Состав (превью описи) */}
                            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                                Состав поставки
                            </div>
                            {!hasOpis ? (
                                <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 14 }}>
                                    В приёмке нет позиций — поставку создать нельзя
                                </div>
                            ) : (
                                <div style={{ overflowX: 'auto', border: '1px solid var(--color-border)', borderRadius: 8 }}>
                                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                                        <thead>
                                            <tr style={{ background: 'var(--color-bg-card)', textAlign: 'left' }}>
                                                <th style={{ padding: '8px 10px', fontWeight: 600 }}>ШК</th>
                                                <th style={{ padding: '8px 10px', fontWeight: 600 }}>Наименование</th>
                                                <th style={{ padding: '8px 10px', fontWeight: 600, textAlign: 'right' }}>Кол-во</th>
                                                <th style={{ padding: '8px 10px', fontWeight: 600 }}>Тип</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {draft.opis_lines.map((l, i) => (
                                                <tr key={`${l.barcode}-${i}`} style={{ borderTop: '1px solid var(--color-border)' }}>
                                                    <td style={{ padding: '8px 10px', fontFamily: 'monospace', fontSize: 12 }}>{l.barcode}</td>
                                                    <td style={{ padding: '8px 10px' }}>{l.name || '—'}</td>
                                                    <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 500 }}>
                                                        {formatNumber(l.quantity, 0)}
                                                        <span style={{ color: 'var(--color-text-muted)', fontWeight: 400 }}>
                                                            {l.is_box ? ' кор' : ' шт'}
                                                        </span>
                                                    </td>
                                                    <td style={{ padding: '8px 10px' }}>
                                                        {l.is_box ? (
                                                            <span className="badge badge-info" style={{ fontSize: 11 }} title={`${formatNumber(l.units_per_box, 0)} шт/короб · всего ${formatNumber(l.pieces, 0)} шт`}>
                                                                короб
                                                            </span>
                                                        ) : (
                                                            <span className="badge badge-secondary" style={{ fontSize: 11 }}>россыпь</span>
                                                        )}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}

                            {/* Итоги: позиции / короба / штуки */}
                            {hasOpis && (
                                <div style={{ display: 'flex', gap: 24, marginTop: 12, fontSize: 14 }}>
                                    <span>Позиций: <strong>{formatNumber(draft.opis_lines.length, 0)}</strong></span>
                                    <span>Коробов: <strong>{formatNumber(draft.total_boxes, 0)}</strong></span>
                                    <span>Штук: <strong>{formatNumber(draft.total_pieces, 0)}</strong></span>
                                </div>
                            )}
                        </>
                    )}
                </div>{/* /body */}

                {/* Footer */}
                <div style={{
                    padding: '14px 24px', borderTop: '1px solid var(--color-border)',
                    flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 10,
                }}>
                    {submitError && (
                        <div style={{ padding: '8px 12px', borderRadius: 8, background: 'rgba(239,68,68,0.1)', color: 'var(--color-danger)', fontSize: 13 }}>
                            {submitError}
                        </div>
                    )}
                    <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                        <button className="btn btn-secondary" onClick={onClose} disabled={submitting}>
                            {result ? 'Закрыть' : 'Отмена'}
                        </button>
                        {!result && draft?.eligible && (
                            <button
                                className="btn btn-primary"
                                onClick={() => doSend(confirmResend)}
                                disabled={submitting || !canSubmit}
                            >
                                {submitting ? 'Отправка...' : 'Создать поставку'}
                            </button>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}

'use client';
/**
 * Модалка «Статус заказа» — история статусов задания FBS вертикальной лентой,
 * как в кабинете WB: точка → ярлык → дата-время, свежее сверху (бэкенд отдаёт
 * события уже отсортированными DESC).
 *
 * Времена двух сортов: якоря из точных дат (оформлен, скан QR, списание DDS)
 * и переходы из журнала `wb_fbs_order_events` — их время зафиксировано синком
 * (каденс ~5 мин), поэтому помечено «≈» с подсказкой.
 */
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/utils';
import WbThumb from '@/components/WbThumb';
import { wbProductUrl } from '@/lib/wbMedia';
import type { FbsOrderTimeline } from '@/types/api';
import { TIMELINE_APPROX_HINT, timelineLabel } from './fbsShared';

interface Props {
    wbOrderId: number;
    /** Мета из строки списка — шапка рисуется сразу, не дожидаясь ручки. */
    article?: string | null;
    nmId?: number | null;
    onClose: () => void;
}

export default function OrderTimelineModal({ wbOrderId, article, nmId, onClose }: Props) {
    const [data, setData] = useState<FbsOrderTimeline | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.getFbsOrderTimeline(wbOrderId);
            if (signal?.aborted) return;
            setData(res);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки истории статусов');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [wbOrderId]);

    useEffect(() => {
        const controller = new AbortController();
        void load(controller.signal);
        return () => controller.abort();
    }, [load]);

    // Мета ручки точнее переданной из строки, но до её ответа живём на пропсах.
    const headArticle = data?.article ?? article ?? null;
    const headNmId = data?.nm_id ?? nmId ?? null;
    const events = data?.events ?? [];

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div
                className="modal-card modal-card-solid"
                onClick={e => e.stopPropagation()}
                style={{ maxWidth: 520, width: '100%' }}
            >
                <h2 className="modal-title">Статус заказа</h2>

                {/* Шапка: фото + артикул — сборщик сразу видит, о КАКОМ товаре история */}
                <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', marginBottom: 16 }}>
                    {headNmId ? (
                        <a
                            href={wbProductUrl(headNmId)}
                            target="_blank"
                            rel="noopener noreferrer"
                            title="Открыть карточку товара на Wildberries"
                        >
                            <WbThumb nmId={headNmId} size={44} />
                        </a>
                    ) : (
                        <WbThumb nmId={null} size={44} />
                    )}
                    <div style={{ minWidth: 0 }}>
                        <div style={{ fontWeight: 600 }}>{headArticle || '—'}</div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                            {data?.subject ? `${data.subject} · ` : ''}
                            задание <span style={{ fontFamily: 'monospace' }}>{wbOrderId}</span>
                        </div>
                    </div>
                </div>

                {loading ? (
                    <div style={{ padding: 16, fontSize: 13, color: 'var(--color-text-muted)' }}>
                        Загрузка истории...
                    </div>
                ) : error ? (
                    <div style={{ padding: 16, fontSize: 13, color: 'var(--color-danger)' }}>
                        {error}
                        <button className="btn btn-sm" style={{ marginLeft: 12 }} onClick={() => load()}>
                            Повторить
                        </button>
                    </div>
                ) : events.length === 0 ? (
                    <div style={{ padding: 16, fontSize: 13, color: 'var(--color-text-muted)' }}>
                        Истории пока нет — журнал наполняется с первого синка.
                    </div>
                ) : (
                    // Вертикальная лента: точка → ярлык → время, свежее сверху
                    <div style={{ maxHeight: '56vh', overflowY: 'auto', padding: '4px 0' }}>
                        {events.map((ev, i) => (
                            <div key={`${ev.code}-${ev.at ?? 'no-at'}-${i}`} style={{ display: 'flex', gap: 12 }}>
                                <div style={{
                                    display: 'flex', flexDirection: 'column', alignItems: 'center', width: 12,
                                }}>
                                    <span style={{
                                        width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
                                        background: 'var(--color-accent)', marginTop: 5,
                                    }} />
                                    {i < events.length - 1 && (
                                        <span style={{
                                            width: 2, flex: 1, background: 'var(--color-border)', marginTop: 2,
                                        }} />
                                    )}
                                </div>
                                <div style={{ paddingBottom: i < events.length - 1 ? 16 : 0, minWidth: 0 }}>
                                    <div style={{ fontSize: 14, fontWeight: 600 }}>{timelineLabel(ev.code)}</div>
                                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                        {ev.at == null ? (
                                            '—'
                                        ) : ev.approx ? (
                                            // «≈» — время события зафиксировал синк, не WB
                                            <span title={TIMELINE_APPROX_HINT}>≈ {formatDateTime(ev.at)}</span>
                                        ) : (
                                            formatDateTime(ev.at)
                                        )}
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                )}

                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary" onClick={onClose}>Закрыть</button>
                </div>
            </div>
        </div>
    );
}

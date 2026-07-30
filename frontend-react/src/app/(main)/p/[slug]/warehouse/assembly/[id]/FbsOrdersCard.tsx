'use client';
/**
 * Наполнение УЧЁТНОЙ заявки FBS — задания поставки, как на экране поставки
 * в разделе FBS (решение владельца 30.07): сборку ведёт фулфилмент, поэтому
 * вместо FBO-позиций (короба/паллеты/«на складе») показываем сами заказы —
 * когда поступил (с точностью до минуты + «N ч назад»), что за товар, цена,
 * статус по обеим осям (продавца и WB). Read-only: никаких действий.
 */
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate, formatDateTime, formatNumber } from '@/lib/utils';
import WbThumb from '@/components/WbThumb';
import { wbProductUrl } from '@/lib/wbMedia';
import type { FbsOrder } from '@/types/api';
import OrderTimelineModal from '../../fbs/OrderTimelineModal';
import {
    CABINET_STATUS_LABEL,
    NOT_SCANNED_CABINET_KEYS,
    cabinetOrderStatus,
    durationSinceLabel,
    hoursAgoLabel,
    num,
    orderAgeColor,
    transitDaysColor,
} from '../../fbs/fbsShared';

export default function FbsOrdersCard({
    fbsSupplyId,
    supplyStatus,
    scanDt,
}: {
    fbsSupplyId: string;
    /** Производный статус поставки (active/to_ship/in_delivery/rejected) — фаза кабинета. */
    supplyStatus?: string | null;
    /** Момент скана QR поставки — граница «наша зона / зона WB» и якорь «В пути». */
    scanDt?: string | null;
}) {
    const [orders, setOrders] = useState<FbsOrder[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    /** Задание, чью историю статусов смотрим (модалка «Статус заказа»). */
    const [timelineOrder, setTimelineOrder] = useState<FbsOrder | null>(null);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        try {
            const res = await api.getFbsSupplyOrders(fbsSupplyId);
            if (signal?.aborted) return;
            setOrders(res);
            setError('');
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки заданий поставки');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [fbsSupplyId]);

    useEffect(() => {
        const controller = new AbortController();
        void load(controller.signal);
        return () => controller.abort();
    }, [load]);

    const items = orders ?? [];

    return (
        <div className="glass-card" style={{ padding: 24, marginBottom: 24 }}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>
                Задания поставки
                {!loading && !error && (
                    <span style={{ fontWeight: 400, color: 'var(--color-text-muted)' }}>
                        {' · '}{formatNumber(items.length, 0)} шт
                    </span>
                )}
            </div>

            {loading ? (
                <div style={{ padding: 16, fontSize: 13, color: 'var(--color-text-muted)' }}>Загрузка заданий...</div>
            ) : error ? (
                <div style={{ padding: 16, fontSize: 13, color: 'var(--color-danger)' }}>
                    {error}
                    <button className="btn btn-sm" style={{ marginLeft: 12 }} onClick={() => load()}>Повторить</button>
                </div>
            ) : items.length === 0 ? (
                <div style={{ padding: 16, fontSize: 13, color: 'var(--color-text-muted)' }}>
                    Задания поставки ещё не синхронизированы из WB.
                </div>
            ) : (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Заказ поступил</th>
                                <th>Товар</th>
                                <th style={{ textAlign: 'right' }}>Цена, ₽</th>
                                <th>Статус</th>
                                <th title="Сколько заказ едет с передачи поставки, пока СЦ не принял">В пути</th>
                                <th>Срок</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items.map(o => {
                                const now = Date.now();
                                // Фаза поставки для кабинетного статуса: done = закрыта
                                // (to_ship/in_delivery), scanned = QR отсканирован.
                                const done = supplyStatus === 'to_ship' || supplyStatus === 'in_delivery';
                                const scanned = supplyStatus === 'in_delivery' || !!scanDt;
                                const cab = cabinetOrderStatus(o.supplier_status, o.wb_status, done, scanned);
                                const ageColor = orderAgeColor(o.created_at_wb, cab, now);
                                // Подсветка строки — ТОЛЬКО пока WB не отсканировал заказ
                                // (после скана QR это зона логистики WB) и ждём ≥ суток.
                                const stuck = ageColor === 'var(--color-danger)';
                                return (
                                <tr key={o.wb_order_id} className={stuck ? 'fbs-row-stuck' : undefined}>
                                    <td>
                                        <div style={{ fontFamily: 'monospace', fontSize: 12 }}>{o.wb_order_id}</div>
                                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                            {o.created_at_wb ? formatDateTime(o.created_at_wb) : '—'}
                                        </div>
                                        {o.created_at_wb && (
                                            <div style={{ fontSize: 13, fontWeight: 600, color: ageColor ?? 'var(--color-text-muted)' }}>
                                                {hoursAgoLabel(o.created_at_wb, now)}
                                            </div>
                                        )}
                                    </td>
                                    <td>
                                        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                                            {o.nm_id ? (
                                                <a
                                                    href={wbProductUrl(o.nm_id)}
                                                    target="_blank"
                                                    rel="noopener noreferrer"
                                                    title="Открыть карточку товара на Wildberries"
                                                >
                                                    <WbThumb nmId={o.nm_id} size={36} />
                                                </a>
                                            ) : (
                                                <WbThumb nmId={null} size={36} />
                                            )}
                                            <div style={{ minWidth: 0 }}>
                                                <div style={{ fontWeight: 500 }}>{o.article || o.barcode || '—'}</div>
                                                <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                                    {o.subject ? `${o.subject} · ` : ''}{o.nm_id ? `nm ${o.nm_id}` : ''}
                                                </div>
                                            </div>
                                        </div>
                                    </td>
                                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                        {o.sale_price == null ? '—' : formatNumber(num(o.sale_price))}
                                    </td>
                                    <td>
                                        {/* Статусы кабинета WB: «Отгрузите товар» / «Ждёт сортировки» /
                                            «Отсортировано»… — одна шкала с кабинетом (канон 30.07).
                                            Клик — модалка «Статус заказа» с историей переходов. */}
                                        <span
                                            className={`badge ${CABINET_STATUS_LABEL[cab].badge}`}
                                            style={{ cursor: 'pointer' }}
                                            title="История статусов"
                                            onClick={() => setTimelineOrder(o)}
                                        >
                                            {CABINET_STATUS_LABEL[cab].label}
                                        </span>
                                    </td>
                                    <td style={{ color: transitDaysColor(o.transit_days) ?? undefined, whiteSpace: 'nowrap', fontWeight: 500 }}>
                                        {/* «В пути» — от СКАНА QR (до скана товар ещё у нас).
                                            Часы/дни: целые сутки бэка давали «0» для вчерашнего. */}
                                        {cab === 'awaiting_sort' && scanDt
                                            ? durationSinceLabel(scanDt, now) ?? '—'
                                            : NOT_SCANNED_CABINET_KEYS.includes(cab) || o.transit_days == null
                                                ? '—'
                                                : formatNumber(o.transit_days, 0)}
                                    </td>
                                    <td>{o.ddate ? formatDate(o.ddate) : '—'}</td>
                                </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}

            {timelineOrder && (
                <OrderTimelineModal
                    wbOrderId={timelineOrder.wb_order_id}
                    article={timelineOrder.article}
                    nmId={timelineOrder.nm_id}
                    onClose={() => setTimelineOrder(null)}
                />
            )}
        </div>
    );
}

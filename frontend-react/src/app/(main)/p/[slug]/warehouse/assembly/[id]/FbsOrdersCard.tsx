'use client';
/**
 * Наполнение УЧЁТНОЙ заявки FBS — задания поставки, как на экране поставки
 * в разделе FBS (решение владельца 30.07): сборку ведёт фулфилмент, поэтому
 * вместо FBO-позиций (короба/паллеты/«на складе») показываем сами заказы —
 * когда поступил (с точностью до минуты + «N ч назад»), что за товар, цена,
 * статус кабинета WB. Read-only; таблица — TanStack (сортировка + Excel).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate, formatDateTime, formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
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
    isStuckAfterScan,
    num,
    orderAgeColor,
    parseUtcMs,
    transitDaysColor,
} from '../../fbs/fbsShared';
import type { FbsCabinetStatusKey } from '../../fbs/fbsShared';

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

    // Фаза поставки одна на все строки: done = закрыта, scanned = QR отсканирован.
    const done = supplyStatus === 'to_ship' || supplyStatus === 'in_delivery';
    const scanned = supplyStatus === 'in_delivery' || !!scanDt;
    const now = Date.now();
    const cabOf = useCallback(
        (o: FbsOrder): FbsCabinetStatusKey => cabinetOrderStatus(o.supplier_status, o.wb_status, done, scanned),
        [done, scanned],
    );

    const cols: Column[] = useMemo(() => [
        {
            key: 'wb_order_id', label: 'Заказ поступил',
            // Сортировка по времени поступления — самый частый вопрос сборщика.
            getValue: (o: FbsOrder) => parseUtcMs(o.created_at_wb) ?? 0,
            sortingFn: 'basic',
            exportValue: (o: FbsOrder) => `${o.wb_order_id} · ${o.created_at_wb ?? ''}`,
            render: (_v, o: FbsOrder) => {
                const ageColor = orderAgeColor(o.created_at_wb, cabOf(o), now);
                return (
                    <div>
                        <div style={{ fontFamily: 'monospace', fontSize: 12 }}>{o.wb_order_id}</div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                            {o.created_at_wb ? formatDateTime(o.created_at_wb) : '—'}
                        </div>
                        {o.created_at_wb && (
                            <div style={{ fontSize: 13, fontWeight: 600, color: ageColor ?? 'var(--color-text-muted)' }}>
                                {hoursAgoLabel(o.created_at_wb, now)}
                            </div>
                        )}
                    </div>
                );
            },
        },
        {
            key: 'article', label: 'Товар',
            getValue: (o: FbsOrder) => o.article || o.barcode || '',
            render: (_v, o: FbsOrder) => (
                <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                    {o.nm_id ? (
                        <a
                            href={wbProductUrl(o.nm_id)}
                            target="_blank"
                            rel="noopener noreferrer"
                            title="Открыть карточку товара на Wildberries"
                            onClick={e => e.stopPropagation()}
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
            ),
        },
        {
            key: 'sale_price', label: 'Цена, ₽', align: 'right',
            // Канон бэкенд-статистики: coalesce(sale_price, price). salePrice WB
            // шлёт единицам заказов (скидка покупателя) — колонка по нему одному
            // стояла в прочерках при заполненном price у всех заданий.
            getValue: (o: FbsOrder) => num(o.sale_price ?? o.price),
            sortingFn: 'basic',
            render: (_v, o: FbsOrder) => {
                const price = o.sale_price ?? o.price;
                return (
                    <span style={{ whiteSpace: 'nowrap' }}>
                        {price == null ? '—' : formatNumber(num(price))}
                    </span>
                );
            },
        },
        {
            key: '_status', label: 'Статус',
            getValue: (o: FbsOrder) => CABINET_STATUS_LABEL[cabOf(o)].label,
            render: (_v, o: FbsOrder) => {
                const cab = cabOf(o);
                return (
                    <span
                        className={`badge ${CABINET_STATUS_LABEL[cab].badge}`}
                        style={{ cursor: 'pointer' }}
                        title="История статусов"
                        onClick={e => { e.stopPropagation(); setTimelineOrder(o); }}
                    >
                        {CABINET_STATUS_LABEL[cab].label}
                    </span>
                );
            },
        },
        {
            key: 'transit_days', label: 'В пути',
            headerTitle: 'Сколько заказ едет с момента скана QR, пока СЦ не принял',
            getValue: (o: FbsOrder) => o.transit_days ?? -1,
            sortingFn: 'basic',
            render: (_v, o: FbsOrder) => {
                const cab = cabOf(o);
                return (
                    <span style={{ color: transitDaysColor(o.transit_days) ?? undefined, whiteSpace: 'nowrap', fontWeight: 500 }}>
                        {cab === 'awaiting_sort' && scanDt
                            ? durationSinceLabel(scanDt, now) ?? '—'
                            : NOT_SCANNED_CABINET_KEYS.includes(cab) || o.transit_days == null
                                ? '—'
                                : formatNumber(o.transit_days, 0)}
                    </span>
                );
            },
        },
        // «Срок» — обязательная дата доставки от WB (`ddate`): бывает только у
        // заказов с выбранной покупателем датой / КГТ-слотом. У остальных её
        // нет — пустую колонку не показываем, чтобы не плодить прочерки.
        ...(items.some(o => o.ddate) ? [{
            key: 'ddate', label: 'Срок',
            headerTitle: 'Обязательная дата доставки от WB — есть только у заказов с выбранной датой',
            render: (_v: unknown, o: FbsOrder) => o.ddate ? formatDate(o.ddate) : '—',
        } satisfies Column] : []),
    ], [cabOf, now, scanDt, items]);

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
                <TanStackDataTable
                    columns={cols}
                    data={items}
                    exportName={`FBS_${fbsSupplyId}`}
                    enableSorting
                    enablePagination={false}
                    // Подсветка зависших: WB не отсканировал ≥ суток (наша зона)
                    // ИЛИ отсканировал, а СЦ ≥ суток не принимает («Ждёт
                    // сортировки» — канон 30.07, от scan-якоря поставки).
                    rowClassName={(o: FbsOrder) =>
                        orderAgeColor(o.created_at_wb, cabOf(o), now) === 'var(--color-danger)'
                        || (cabOf(o) === 'awaiting_sort' && isStuckAfterScan(scanDt, now))
                            ? 'fbs-row-stuck'
                            : ''}
                />
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

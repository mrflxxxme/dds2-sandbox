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
import {
    SupplierStatusBadge,
    WB_STATUS_LABEL,
    durationSinceLabel,
    hoursAgoLabel,
    isAwaitingWbAccept,
    num,
    orderAgeColor,
    transitDaysColor,
} from '../../fbs/fbsShared';

export default function FbsOrdersCard({
    fbsSupplyId,
    shippedAt,
}: {
    fbsSupplyId: string;
    /** Момент передачи поставки (assembly.shipped_at) — якорь колонки «В пути». */
    shippedAt?: string | null;
}) {
    const [orders, setOrders] = useState<FbsOrder[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

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
                                const ageColor = orderAgeColor(o.created_at_wb, o.supplier_status, o.wb_status, now);
                                // Подсветка строки — ТОЛЬКО пока WB не отсканировал заказ
                                // (после приёмки СЦ это зона логистики WB) и ждём ≥ суток.
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
                                        <SupplierStatusBadge status={o.supplier_status} />
                                        {o.wb_status && WB_STATUS_LABEL[o.wb_status] && (
                                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 2 }}>
                                                {WB_STATUS_LABEL[o.wb_status]}
                                            </div>
                                        )}
                                    </td>
                                    <td style={{ color: transitDaysColor(o.transit_days) ?? undefined, whiteSpace: 'nowrap', fontWeight: 500 }}>
                                        {/* transit_days бэка — целые СУТКИ: переданное вчера вечером
                                            показывало «0», как будто колонка сломана. Часы/дни считаем
                                            от момента передачи поставки; цвет — по суточным порогам. */}
                                        {o.transit_days == null
                                            ? '—'
                                            : (isAwaitingWbAccept(o.supplier_status, o.wb_status) && shippedAt
                                                ? durationSinceLabel(shippedAt) ?? formatNumber(o.transit_days, 0)
                                                : formatNumber(o.transit_days, 0))}
                                    </td>
                                    <td>{o.ddate ? formatDate(o.ddate) : '—'}</td>
                                </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

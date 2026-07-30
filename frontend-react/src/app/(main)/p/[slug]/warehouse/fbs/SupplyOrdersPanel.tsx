'use client';
/**
 * Список заданий поставки + панель её действий — тело экрана поставки.
 *
 * В кабинете WB единица работы — поставка: сборщик открывает её, берёт лист
 * подбора, печатает стикеры на ВСЕ задания разом и передаёт в доставку.
 * Панель повторяет этот порядок: действия одной строкой, под ними — задания.
 */
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate, formatDateTime, formatNumber } from '@/lib/utils';
import WbThumb from '@/components/WbThumb';
import { wbProductUrl } from '@/lib/wbMedia';
import type { FbsOrder, FbsStickerType, FbsSupply } from '@/types/api';
import {
    SupplierStatusBadge,
    WB_STATUS_LABEL,
    deliverStickers,
    fetchStickersChunked,
    hoursAgoLabel,
    isActiveOrder,
    isStickerReady,
    num,
} from './fbsShared';

interface Props {
    supply: FbsSupply;
    /** Режим контура разрешает запись в WB: передача — запись, стикеры — чтение. */
    writeEnabled: boolean;
    writeHint: string;
    /** Идёт действие над этой поставкой снаружи (передача / удаление / QR). */
    busy: boolean;
    onDeliver: (s: FbsSupply) => void;
    onBarcode: (s: FbsSupply) => void;
    /** Открыть лист подбора. Чтение — кнопка живёт и в safe-режиме. */
    onPickList?: () => void;
    onToast: (msg: string) => void;
}

export default function SupplyOrdersPanel({
    supply, writeEnabled, writeHint, busy, onDeliver, onBarcode, onPickList, onToast,
}: Props) {
    const [orders, setOrders] = useState<FbsOrder[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [stickerBusy, setStickerBusy] = useState(false);
    const [stickerError, setStickerError] = useState('');
    const [type, setType] = useState<FbsStickerType>('png');
    const [size, setSize] = useState('58x40');

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.getFbsSupplyOrders(supply.wb_supply_id);
            if (signal?.aborted) return;
            setOrders(res);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки заданий поставки');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
        // `done` в зависимостях не для другого запроса, а чтобы после «Передать
        // в доставку» панель перечитала состав: экран поставки остаётся открытым
        // на том же wb_supply_id, шапка уже показывает QR, а таблица иначе
        // застревала бы на статусе «На сборке».
    }, [supply.wb_supply_id, supply.done]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    const items = orders ?? [];
    // Отменённое задание остаётся в поставке (`cancel_order` чистит статус, но
    // не `supply_id`), а бэк роняет ВЕСЬ запрос стикеров на первом негодном id.
    const stickerItems = items.filter(o => isStickerReady(o.supplier_status));
    const activeItems = items.filter(o => isActiveOrder(o.supplier_status));

    const handleStickers = async () => {
        const ids = stickerItems.map(o => o.wb_order_id);
        if (ids.length === 0) return;
        setStickerBusy(true);
        setStickerError('');
        const [width, height] = size.split('x').map(Number);
        try {
            const stickers = await fetchStickersChunked(ids, type, width, height);
            const count = deliverStickers(stickers, type);
            if (count === 0) {
                setStickerError(
                    'WB не вернул файлы стикеров — задания должны быть в статусе «На сборке» или «В доставке»',
                );
                return;
            }
            onToast(`Стикеров получено: ${formatNumber(count, 0)}`);
        } catch (e: unknown) {
            setStickerError(e instanceof Error ? e.message : 'Ошибка получения стикеров');
        } finally {
            setStickerBusy(false);
        }
    };

    return (
        <div>
            {/* Панель действий поставки — как в кабинете WB, над списком заданий */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
                {onPickList && (
                    <button className="btn btn-secondary btn-sm" onClick={onPickList}
                        title="Что и сколько снять со склада — агрегат по товару, а не по заданиям">
                        📋 Лист подбора
                    </button>
                )}
                <div style={{ flex: 1 }} />
                <select className="form-input" style={{ width: 130, padding: '4px 8px', fontSize: 12 }}
                    value={type} onChange={e => setType(e.target.value as FbsStickerType)}>
                    <option value="png">PNG</option>
                    <option value="svg">SVG</option>
                    <option value="zplv">ZPL вертик.</option>
                    <option value="zplh">ZPL гориз.</option>
                </select>
                <select className="form-input" style={{ width: 100, padding: '4px 8px', fontSize: 12 }}
                    value={size} onChange={e => setSize(e.target.value)}>
                    <option value="58x40">58 × 40</option>
                    <option value="40x30">40 × 30</option>
                </select>
                {/* Стикеры — чтение: в safe-режиме тоже доступны */}
                <button className="btn btn-secondary btn-sm" onClick={handleStickers}
                    disabled={stickerBusy || loading || stickerItems.length === 0}
                    title="Печать этикеток на задания поставки (запросы к WB идут пачками по 100)">
                    {stickerBusy ? 'Запрос...' : '🏷️ Распечатать стикеры'}
                </button>
                {!supply.done && (
                    <button className="btn btn-primary btn-sm"
                        disabled={!writeEnabled || busy || activeItems.length === 0}
                        title={writeEnabled ? undefined : writeHint}
                        onClick={() => onDeliver(supply)}>
                        🚚 Передать в доставку
                    </button>
                )}
                {supply.done && (
                    <button className="btn btn-secondary btn-sm" disabled={busy}
                        onClick={() => onBarcode(supply)}>
                        📄 QR поставки
                    </button>
                )}
            </div>

            {stickerError && (
                <div style={{ marginBottom: 12, fontSize: 13, color: 'var(--color-danger)' }}>{stickerError}</div>
            )}

            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
                Задания поставки
                <span style={{ fontWeight: 400, color: 'var(--color-text-muted)' }}>
                    {' · '}{formatNumber(items.length, 0)} шт
                    {/* Отменённые остаются в поставке — честно говорим, сколько поедет и напечатается */}
                    {stickerItems.length !== items.length && (
                        <>
                            {' · стикеры: '}
                            {formatNumber(stickerItems.length, 0)} из {formatNumber(items.length, 0)}
                            {' (отменённые не печатаются)'}
                        </>
                    )}
                </span>
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
                    В поставке нет заданий — добавьте их со вкладки «Заказы» кнопкой «В поставку».
                </div>
            ) : (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Задание</th>
                                <th>Товар</th>
                                <th style={{ textAlign: 'right' }}>Цена, ₽</th>
                                <th>Статус</th>
                                <th>Срок</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items.map(o => (
                                <tr key={o.wb_order_id}>
                                    <td>
                                        <div style={{ fontFamily: 'monospace', fontSize: 12 }}>{o.wb_order_id}</div>
                                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                            {o.created_at_wb ? formatDateTime(o.created_at_wb) : '—'}
                                        </div>
                                        {/* Возраст заказа — как в кабинете WB («5 ч 53 мин назад»):
                                            сборщику важнее, сколько заказ ЖДЁТ, чем календарная дата. */}
                                        {o.created_at_wb && (
                                            <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                                                {hoursAgoLabel(o.created_at_wb)}
                                            </div>
                                        )}
                                    </td>
                                    <td>
                                        {/* Фото — самый быстрый способ понять, ТО ЛИ собирают:
                                            артикулы вида divan_lightbrown / divan_darkblue
                                            различаются одним словом, а лежат в разных коробах. */}
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
                                        {/* Ось WB детальнее supplier_status: «Готово к выдаче» и
                                            «Отсортировано» видны только здесь. */}
                                        {o.wb_status && WB_STATUS_LABEL[o.wb_status] && (
                                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 2 }}>
                                                {WB_STATUS_LABEL[o.wb_status]}
                                            </div>
                                        )}
                                    </td>
                                    <td>{o.ddate ? formatDate(o.ddate) : '—'}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

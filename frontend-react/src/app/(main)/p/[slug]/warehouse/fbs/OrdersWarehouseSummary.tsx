'use client';
/**
 * Сводка «сколько заданий на каждом складе WB и на какой они фазе» — прямой
 * ответ на вопрос «а если у меня несколько складов»: работа с FBS всегда идёт
 * по одному складу за раз (в поставку нельзя смешивать склады), и сборщику
 * надо видеть, где очередь.
 *
 * Четыре фазы жизни задания, и они делятся на две пары с РАЗНОЙ семантикой:
 *
 *   Новые · На сборке   — ОЧЕРЕДЬ, показывается целиком, без периода.
 *                          Задание, созданное два месяца назад и до сих пор не
 *                          собранное, обязано быть в цифре сборщика.
 *   В доставке · Отсортировано — за выбранный ПЕРИОД. `supplierStatus`
 *                          застывает на `complete` навсегда, поэтому без окна
 *                          цифра копит всё, что когда-либо уезжало, и ничего
 *                          не значит.
 *
 * Данные — одним запросом на весь экран (`GET /fbs/orders/warehouse-summary`);
 * прежняя схема «limit=1 на каждый склад» упиралась в 9 параллельных походов
 * ради девяти чисел.
 */
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import type { FbsWarehouse, FbsWarehouseSummary } from '@/types/api';

interface Props {
    warehouses: FbsWarehouse[];
    /** Меняется после синка/мутаций заданий — повод пересчитать сводку. */
    reloadKey: number;
    /** Окно для фаз доставки — общее с блоком статистики. */
    dateFrom: string;
    dateTo: string;
    /** Клик по цифре — фильтр «этот склад + эта фаза». */
    onPick: (wbWarehouseId: number | '', status: string) => void;
}

interface WhCounts {
    wbWarehouseId: number | '';
    name: string;
    isNew: number;
    confirm: number;
    inDelivery: number;
    sorted: number;
}

export default function OrdersWarehouseSummary({
    warehouses, reloadKey, dateFrom, dateTo, onPick,
}: Props) {
    const [data, setData] = useState<FbsWarehouseSummary | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.getFbsWarehouseSummary({ dateFrom, dateTo });
            if (signal?.aborted) return;
            setData(res);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки сводки по складам');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [dateFrom, dateTo]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
        // reloadKey — внешний триггер пересчёта после синка/раскладки заданий
    }, [load, reloadKey]);

    if (loading && data === null) {
        return (
            <div className="glass-card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-text-muted)' }}>
                Сводка по складам: загрузка...
            </div>
        );
    }

    if (error) {
        return (
            <div className="glass-card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-danger)' }}>
                {error}
                <button className="btn btn-sm" style={{ marginLeft: 12 }} onClick={() => load()}>Повторить</button>
            </div>
        );
    }

    if (warehouses.length === 0) {
        return (
            <div className="glass-card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-text-muted)' }}>
                Складов продавца нет — заведите их на вкладке «Склады», иначе задания некуда собирать.
            </div>
        );
    }

    const byId = new Map((data?.warehouses ?? []).map(w => [w.wb_warehouse_id, w]));
    const rows: WhCounts[] = warehouses.map(w => {
        const c = byId.get(w.wb_warehouse_id);
        return {
            wbWarehouseId: w.wb_warehouse_id,
            name: w.name || `#${w.wb_warehouse_id}`,
            isNew: c?.new ?? 0,
            confirm: c?.confirm ?? 0,
            inDelivery: c?.in_delivery ?? 0,
            sorted: c?.sorted ?? 0,
        };
    });
    const t = data?.totals ?? {};
    const total: WhCounts = {
        wbWarehouseId: '',
        name: 'Все склады',
        isNew: t.new ?? 0,
        confirm: t.confirm ?? 0,
        inDelivery: t.in_delivery ?? 0,
        sorted: t.sorted ?? 0,
    };
    // Задания склада, которого нет в нашем справочнике, иначе бы просто
    // потерялись из сводки — показываем остаток честной строкой.
    const known = new Set(warehouses.map(w => w.wb_warehouse_id));
    const rest = (data?.warehouses ?? []).filter(w => !known.has(w.wb_warehouse_id));
    const orphan: WhCounts | null = rest.length > 0
        ? {
            wbWarehouseId: '',
            name: 'Без склада в справочнике',
            isNew: rest.reduce((a, r) => a + r.new, 0),
            confirm: rest.reduce((a, r) => a + r.confirm, 0),
            inDelivery: rest.reduce((a, r) => a + r.in_delivery, 0),
            sorted: rest.reduce((a, r) => a + r.sorted, 0),
        }
        : null;

    const cards = [total, ...rows, ...(orphan ? [orphan] : [])];
    const periodLabel = data?.date_from && data?.date_to
        ? `${formatDate(data.date_from)} — ${formatDate(data.date_to)}`
        : 'за всё время';

    return (
        <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 6 }}>
                Очередь по складам WB — в одну поставку задания разных складов не кладутся.{' '}
                «Новые» и «На сборке» — вся очередь целиком; «В доставке» и «Отсортировано» —
                за период <b style={{ color: 'var(--color-text)' }}>{periodLabel}</b>.
            </div>
            {/* 300px — минимум под ЧЕТЫРЕ ячейки: ширину держит подпись, не число. */}
            <div style={{
                display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 12,
            }}>
                {cards.map((c, i) => (
                    <div key={`${c.name}-${i}`} className="glass-card" style={{ padding: '12px 16px' }}>
                        <div style={{
                            fontSize: 13, fontWeight: 600, marginBottom: 8,
                            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }} title={c.name}>
                            {c.name}
                        </div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'space-between' }}>
                            <CountCell label="Новые" value={c.isNew} accent
                                onClick={() => onPick(c.wbWarehouseId, 'new')} />
                            <CountCell label="На сборке" value={c.confirm}
                                onClick={() => onPick(c.wbWarehouseId, 'confirm')} />
                            {/* Две фазы после передачи: пока задание не отсортировано,
                                вопросы по нему ещё к нам; после — к логистике WB. */}
                            <CountCell
                                label="В доставке"
                                value={c.inDelivery}
                                title="Передано в WB, сортировочный центр ещё не принял"
                                onClick={() => onPick(c.wbWarehouseId, 'in_delivery')}
                            />
                            <CountCell
                                label="Отсортировано"
                                value={c.sorted}
                                title="Принято сортировочным центром WB, покупатель ещё не получил"
                                onClick={() => onPick(c.wbWarehouseId, 'sorted')}
                            />
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

function CountCell({ label, value, accent, title, onClick }: {
    label: string;
    value: number;
    accent?: boolean;
    /** Чем ячейка отличается от статуса задания; по умолчанию — «Показать: …». */
    title?: string;
    onClick: () => void;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            title={title ?? `Показать: ${label.toLowerCase()}`}
            style={{
                background: 'none', border: 'none', padding: 0, cursor: 'pointer', textAlign: 'left',
                // Ячейка меряется по подписи и не сжимается: без этого flex ужимал
                // её до ширины числа, и подпись переносилась по слогам.
                flex: '0 0 auto', minWidth: 0,
            }}
        >
            <div style={{
                fontSize: 20, fontWeight: 700, lineHeight: 1.2,
                color: accent && value > 0 ? 'var(--color-accent)' : undefined,
            }}>
                {formatNumber(value, 0)}
            </div>
            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
                {label}
            </div>
        </button>
    );
}

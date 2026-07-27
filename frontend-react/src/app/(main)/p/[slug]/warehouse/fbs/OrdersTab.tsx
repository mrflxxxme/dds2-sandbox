'use client';
/**
 * Вкладка «Заказы» — источник, из которого набираются поставки: очередь по
 * складам WB сверху, фильтры по статусу/складу/датам, массовые действия
 * (стикеры, раскладка по поставкам) и отмена задания.
 *
 * «В поставку» больше не шлёт выделение одной пачкой: сначала план
 * (POST /fbs/supplies/plan) показывает, на сколько поставок распадётся
 * выделенное — в одну поставку WB не пускает задания разных складов и
 * габаритов, и при нескольких складах разбиение неизбежно.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate, formatDateTime, formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import WbThumb from '@/components/WbThumb';
import { wbProductUrl } from '@/lib/wbMedia';
import type { Column } from '@/components/DataTable';
import type {
    FbsOrder,
    FbsOrderListResponse,
    FbsStickerType,
    FbsWarehouse,
} from '@/types/api';
import {
    BACKFILL_DAYS_DEFAULT,
    BACKFILL_DAYS_OPTIONS,
    SUPPLIER_STATUS_LABEL,
    SupplierStatusBadge,
    WB_STATUS_LABEL,
    SELECT_ALL_MAX,
    WB_STICKER_CHUNK,
    backfillPeriodLabel,
    backfillResultMessage,
    cargoLabel,
    collectAllOrderIds,
    deliverStickers,
    fetchStickersChunked,
    num,
    selectStickerIds,
} from './fbsShared';
import OrdersWarehouseSummary from './OrdersWarehouseSummary';
import OrdersStats from './OrdersStats';
import SupplyPlanModal, { WB_PLAN_LIMIT } from './SupplyPlanModal';

const PAGE_SIZE = 100;
/**
 * Сколько заданий за раз отдаём на стикеры без предупреждения. Жёсткого
 * потолка нет: запросы к WB идут пачками по 100 (`fetchStickersChunked`),
 * но сотня пачек — это сотня походов в WB, о чём честно предупреждаем.
 */
const STICKER_WARN_LIMIT = 500;

const STATUS_TABS: { key: string; label: string }[] = [
    { key: '', label: 'Все' },
    { key: 'new', label: SUPPLIER_STATUS_LABEL.new },
    { key: 'confirm', label: SUPPLIER_STATUS_LABEL.confirm },
    { key: 'complete', label: SUPPLIER_STATUS_LABEL.complete },
    { key: 'cancel', label: SUPPLIER_STATUS_LABEL.cancel },
];

interface Props {
    warehouses: FbsWarehouse[];
    supplyFilter: string;
    /**
     * Режим контура разрешает запись в WB. Отмена задания и добавление в
     * поставку меняют кабинет → гасятся; стикеры и синк — чтение.
     */
    writeEnabled: boolean;
    /** Текст подсказки на выключенной кнопке: safe-режим или «режим не загружен». */
    writeHint: string;
    onSupplyFilterChange: (v: string) => void;
    onToast: (msg: string) => void;
    /** Счётчик тиков автообновления страницы; 0 — тиков ещё не было. */
    refreshTick: number;
}

export default function OrdersTab({
    warehouses, supplyFilter, writeEnabled, writeHint, onSupplyFilterChange, onToast, refreshTick,
}: Props) {
    const [data, setData] = useState<FbsOrderListResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [actionError, setActionError] = useState('');

    const [status, setStatus] = useState('');
    const [whFilter, setWhFilter] = useState<number | ''>('');
    const [dateFrom, setDateFrom] = useState('');
    const [dateTo, setDateTo] = useState('');
    const [page, setPage] = useState(0);

    const [selected, setSelected] = useState<Set<number>>(new Set());
    /**
     * Статус каждого ВИДЕННОГО задания: выделение живёт за пределами страницы
     * (пагинация, «Выбрать все»), а стикеры печатаются только для confirm /
     * complete — иначе бэк роняет ВСЮ пачку на первом негодном id. Выделить
     * можно только то, что уже приезжало в выдаче, поэтому карта копится по
     * мере загрузок и закрывает любой путь выделения.
     */
    const [statusById, setStatusById] = useState<Map<number, string>>(new Map());
    /** Идёт сбор id всех отфильтрованных заданий (страницами по 500). */
    const [selectingAll, setSelectingAll] = useState(false);
    const [syncing, setSyncing] = useState(false);
    /** Идёт обратная загрузка истории (долгий запрос — до минуты). */
    const [backfilling, setBackfilling] = useState(false);
    const [backfillDays, setBackfillDays] = useState(BACKFILL_DAYS_DEFAULT);
    const [busy, setBusy] = useState(false);
    const [stickerModal, setStickerModal] = useState(false);
    const [supplyModal, setSupplyModal] = useState(false);
    /** Пересчитать сводку по складам после синка / раскладки по поставкам. */
    const [summaryKey, setSummaryKey] = useState(0);
    /**
     * Локальный тик для блока статистики: он перечитывается по ЛЮБОЙ смене
     * `refreshTick`, поэтому свои поводы (бэкфилл истории) прибавляем к тику
     * автообновления — сумма монотонна, лишней загрузки не будет.
     */
    const [statsTick, setStatsTick] = useState(0);

    /**
     * Единый набор фильтров для списка и для «выбрать все»: если собирать их
     * в двух местах, выделение однажды разъедется с тем, что видно на экране.
     */
    const filters = useMemo(() => ({
        status: status || undefined,
        supplyId: supplyFilter || undefined,
        wbWarehouseId: whFilter === '' ? undefined : whFilter,
        dateFrom: dateFrom || undefined,
        dateTo: dateTo || undefined,
    }), [status, supplyFilter, whFilter, dateFrom, dateTo]);

    /** Запомнить статусы приехавших заданий — см. `statusById`. */
    const rememberStatuses = useCallback((rows: FbsOrder[]) => {
        if (rows.length === 0) return;
        setStatusById(prev => {
            const next = new Map(prev);
            for (const o of rows) next.set(o.wb_order_id, o.supplier_status);
            return next;
        });
    }, []);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.getFbsOrders({
                ...filters,
                limit: PAGE_SIZE,
                offset: page * PAGE_SIZE,
            });
            if (signal?.aborted) return;
            setData(res);
            rememberStatuses(res.items ?? []);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки заданий');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [filters, page, rememberStatuses]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    // Автообновление: загрузчик берём через ref, иначе эффект срабатывал бы на
    // каждую смену фильтра и дублировал обычную загрузку.
    const reloadRef = useRef<(signal?: AbortSignal) => void>(() => {});
    useEffect(() => { reloadRef.current = load; });
    useEffect(() => {
        if (refreshTick === 0) return; // первичную загрузку делает эффект выше
        const controller = new AbortController();
        reloadRef.current(controller.signal);
        return () => controller.abort();
    }, [refreshTick]);

    /**
     * Поколение фильтров: «Выбрать все» идёт страницами ПОСЛЕДОВАТЕЛЬНО и
     * занимает секунды, а поля фильтров всё это время живые. Сбор, стартовавший
     * до смены фильтров, обязан выкинуть свой результат — иначе он применяет
     * выделение СТАРОЙ выборки поверх уже очищенного.
     */
    const filtersGen = useRef(0);

    // Смена фильтров — с первой страницы и без «залипшего» выделения
    useEffect(() => {
        filtersGen.current += 1;
        setPage(0);
        setSelected(new Set());
    }, [status, supplyFilter, whFilter, dateFrom, dateTo]);

    const items = useMemo(() => data?.items ?? [], [data]);
    const counts = data?.status_counts ?? {};

    /**
     * Группировка «На сборке» по поставке — тот же приём, что в сводке остатков:
     * отдельная таблица-сводка над строками. В статусе `confirm` задание уже
     * принадлежит поставке, и работать сборщику удобно поставками, а не строками.
     * Считается по показанной странице — так и подписано, чтобы цифра не врала.
     */
    const supplyGroups = useMemo(() => {
        if (status !== 'confirm') return [];
        const map = new Map<string, {
            supply_id: string;
            wb_warehouse_id: number | null;
            wb_warehouse_name: string;
            cargo_type: number | null;
            orders_count: number;
        }>();
        for (const o of items) {
            const key = o.supply_id || '—';
            let g = map.get(key);
            if (!g) {
                const wh = warehouses.find(w => w.wb_warehouse_id === o.wb_warehouse_id);
                g = {
                    supply_id: key,
                    wb_warehouse_id: o.wb_warehouse_id ?? null,
                    wb_warehouse_name: wh?.name
                        || (o.wb_warehouse_id != null ? `#${o.wb_warehouse_id}` : '—'),
                    cargo_type: o.cargo_type ?? null,
                    orders_count: 0,
                };
                map.set(key, g);
            }
            g.orders_count += 1;
        }
        return [...map.values()].sort((a, b) => b.orders_count - a.orders_count);
    }, [items, status, warehouses]);

    const supplyGroupCols: Column[] = [
        {
            key: 'supply_id', label: 'Поставка',
            render: (v: string) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span>,
        },
        { key: 'wb_warehouse_name', label: 'Склад WB' },
        {
            key: 'cargo_type', label: 'Груз',
            render: (v: number | null) => cargoLabel(v),
        },
        {
            key: 'orders_count', label: 'Заданий', align: 'right',
            render: (v: number, row: { supply_id: string }) => (
                <button
                    className="btn btn-sm"
                    title="Показать только задания этой поставки"
                    disabled={row.supply_id === '—'}
                    onClick={() => onSupplyFilterChange(row.supply_id)}
                >
                    {formatNumber(v, 0)}
                </button>
            ),
            exportValue: (row: { orders_count: number }) => row.orders_count,
        },
    ];

    const toggleRow = (id: number) => {
        setSelected(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id); else next.add(id);
            return next;
        });
    };

    const allOnPageSelected = items.length > 0 && items.every(o => selected.has(o.wb_order_id));

    const toggleAll = () => {
        setSelected(prev => {
            const next = new Set(prev);
            if (allOnPageSelected) items.forEach(o => next.delete(o.wb_order_id));
            else items.forEach(o => next.add(o.wb_order_id));
            return next;
        });
    };

    const totalFiltered = data?.total ?? 0;
    const allFilteredSelected = totalFiltered > 0 && selected.size >= totalFiltered;

    /**
     * «Выбрать все» = ВСЕ задания под текущими фильтрами, а не видимая страница.
     * Страница отдаёт 100 строк, а заданий в поставке бывает 47 на трёх экранах
     * или тысяча — раньше «выбрать» брало только показанное, и массовые действия
     * молча уезжали с половиной выделения.
     */
    const handleSelectAllFiltered = async () => {
        const gen = filtersGen.current;
        setSelectingAll(true);
        setActionError('');
        try {
            const { ids, truncated } = await collectAllOrderIds(
                async (offset, limit) => {
                    const res = await api.getFbsOrders({ ...filters, limit, offset });
                    // Статусы собранных заданий нужны отбору годных к стикеру:
                    // выделение уходит далеко за пределы показанной страницы.
                    rememberStatuses(res.items ?? []);
                    return res;
                },
                totalFiltered,
            );
            // Фильтры сменились, пока шёл сбор → выделять нечего: выборка другая.
            if (filtersGen.current !== gen) return;
            setSelected(new Set(ids));
            onToast(
                truncated
                    ? `Выделено ${formatNumber(ids.length, 0)} заданий из `
                      + `${formatNumber(totalFiltered, 0)} — за раз берём не больше `
                      + `${formatNumber(SELECT_ALL_MAX, 0)}`
                    : `Выделено заданий: ${formatNumber(ids.length, 0)}`,
            );
        } catch (e: unknown) {
            if (filtersGen.current !== gen) return;
            setActionError(e instanceof Error ? e.message : 'Ошибка выделения всех заданий');
        } finally {
            setSelectingAll(false);
        }
    };

    const handleSync = async () => {
        setSyncing(true);
        setActionError('');
        try {
            const res = await api.syncFbsOrders();
            onToast(
                typeof res?.affected === 'number'
                    ? `Новых заданий: ${formatNumber(res.affected, 0)}`
                    : 'Задания синхронизированы',
            );
            setSummaryKey(k => k + 1);
            await load();
        } catch (e: unknown) {
            setActionError(e instanceof Error ? e.message : 'Ошибка синхронизации заданий');
        } finally {
            setSyncing(false);
        }
    };

    /**
     * Обратная загрузка истории заданий. «Забрать новые» тянет только то, что
     * WB отдаёт как новое, поэтому всё, что было до подключения раздела, в
     * зеркале отсутствует — и вкладки, кроме «Новых», выглядят пустыми.
     *
     * Запрос долгий (WB отдаёт историю окнами) — двойной клик защищён и
     * состоянием, и ref'ом: `disabled` ставится следующим рендером, а второй
     * клик по той же кнопке успевает пройти раньше него.
     */
    const backfillRef = useRef(false);
    const handleBackfill = async () => {
        if (backfillRef.current) return;
        backfillRef.current = true;
        setBackfilling(true);
        setActionError('');
        try {
            const res = await api.backfillFbsOrders(backfillDays);
            onToast(backfillResultMessage(res, backfillDays));
            setSummaryKey(k => k + 1);
            setStatsTick(t => t + 1);
            await load();
        } catch (e: unknown) {
            setActionError(e instanceof Error ? e.message : 'Ошибка загрузки истории заданий');
        } finally {
            backfillRef.current = false;
            setBackfilling(false);
        }
    };

    const handleCancel = async (order: FbsOrder) => {
        if (!confirm(`Отменить сборочное задание ${order.wb_order_id}? Действие необратимо.`)) return;
        setBusy(true);
        setActionError('');
        try {
            await api.cancelFbsOrder(order.wb_order_id);
            onToast('Задание отменено');
            setSummaryKey(k => k + 1);
            await load();
        } catch (e: unknown) {
            setActionError(e instanceof Error ? e.message : 'Ошибка отмены задания');
        } finally {
            setBusy(false);
        }
    };

    // Колонки строятся каждый рендер: замыкаются на текущее выделение и busy
    const cols: Column[] = [
        {
            key: '_select', label: '✓', sortable: false, width: '36px', align: 'center',
            render: (_v: unknown, row: FbsOrder) => (
                <input
                    type="checkbox"
                    checked={selected.has(row.wb_order_id)}
                    onChange={() => toggleRow(row.wb_order_id)}
                    onClick={e => e.stopPropagation()}
                />
            ),
            exportValue: () => '',
        },
        {
            key: 'wb_order_id', label: 'Задание',
            render: (v: number, row: FbsOrder) => (
                <div>
                    <div style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</div>
                    <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                        {row.created_at_wb ? formatDateTime(row.created_at_wb) : '—'}
                    </div>
                </div>
            ),
        },
        {
            key: 'article', label: 'Товар', width: '250px',
            render: (v: string | null, row: FbsOrder) => (
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                    {row.nm_id ? (
                        <a
                            href={wbProductUrl(row.nm_id)}
                            target="_blank"
                            rel="noopener noreferrer"
                            title="Открыть карточку товара на Wildberries"
                        >
                            <WbThumb nmId={row.nm_id} size={36} />
                        </a>
                    ) : (
                        <WbThumb nmId={null} size={36} />
                    )}
                    <div style={{ minWidth: 0 }}>
                        <div style={{ fontWeight: 500 }}>{v || row.barcode || '—'}</div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                            {row.subject ? `${row.subject} · ` : ''}
                            {row.nm_id ? (
                                <a
                                    href={wbProductUrl(row.nm_id)}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    style={{ color: 'var(--color-accent)' }}
                                    title="Открыть карточку товара на Wildberries"
                                >
                                    nm {row.nm_id}
                                </a>
                            ) : null}
                            {row.chrt_id ? ` · chrt ${row.chrt_id}` : ''}
                        </div>
                    </div>
                </div>
            ),
            exportValue: (row: FbsOrder) => row.article || row.barcode || '',
        },
        {
            key: 'sale_price', label: 'Цена, ₽', align: 'right',
            headerTitle: 'salePrice задания (WB отдаёт в копейках — приведено к рублям)',
            render: (v: number | string | null) => v == null ? '—' : formatNumber(num(v)),
            exportValue: (row: FbsOrder) => num(row.sale_price),
        },
        {
            key: 'supplier_status', label: 'Статус',
            render: (v: string) => <SupplierStatusBadge status={v} />,
        },
        {
            key: 'wb_status', label: 'Статус WB',
            render: (v: string | null) => v
                ? <span style={{ fontSize: 13 }}>{WB_STATUS_LABEL[v] ?? v}</span>
                : '—',
        },
        {
            key: 'supply_id', label: 'Поставка',
            render: (v: string | null) => v
                ? <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span>
                : <span style={{ color: 'var(--color-text-dim)' }}>—</span>,
        },
        {
            key: 'wb_warehouse_id', label: 'Склад WB',
            render: (v: number | null, row: FbsOrder) => {
                const wh = warehouses.find(w => w.wb_warehouse_id === v);
                return (
                    <div style={{ fontSize: 13 }}>
                        <div>{wh?.name || (v != null ? `#${v}` : '—')}</div>
                        {row.office_name && (
                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{row.office_name}</div>
                        )}
                    </div>
                );
            },
        },
        {
            key: 'cargo_type', label: 'Груз', align: 'center',
            headerTitle: 'Габаритная группа задания: в одной поставке допустим только один тип',
            render: (v: number | null) => <span style={{ fontSize: 13 }}>{cargoLabel(v)}</span>,
        },
        {
            key: 'ddate', label: 'Срок', render: (v: string | null) => v ? formatDate(v) : '—',
        },
        {
            key: 'written_off_at', label: 'Списано', align: 'center',
            headerTitle: 'Списание со склада после передачи поставки',
            render: (v: string | null) => v
                ? <span title={formatDateTime(v)}>✓</span>
                : <span style={{ color: 'var(--color-text-dim)' }}>—</span>,
        },
        {
            key: '_actions', label: '', sortable: false,
            render: (_v: unknown, row: FbsOrder) => row.is_cancellable ? (
                <button
                    className="btn btn-danger btn-sm"
                    disabled={busy || !writeEnabled}
                    title={writeEnabled ? undefined : writeHint}
                    onClick={e => { e.stopPropagation(); handleCancel(row); }}
                >
                    Отменить
                </button>
            ) : null,
            exportValue: () => '',
        },
    ];

    const selectedIds = [...selected];
    /**
     * Что реально уедет в печать. Бэк валидирует ВСЮ пачку и роняет её на
     * первом же задании вне confirm/complete — а «Выбрать все» на вкладке
     * «Все» гарантированно тащит и `new`, и `cancel`. Отбирать годные обязан
     * фронт (см. `isStickerReady`), иначе сборщик не получает НИ ОДНОГО
     * стикера, а уже полученные из WB пачки сгорают вместе с исключением.
     */
    const stickerIds = useMemo(
        () => selectStickerIds(selected, statusById),
        [selected, statusById],
    );

    return (
        <>
            {/* Сводка «сколько ждёт на каждом складе» — ответ на «а если складов несколько» */}
            <OrdersWarehouseSummary
                warehouses={warehouses}
                reloadKey={summaryKey}
                onPick={(wh, st) => {
                    setWhFilter(wh);
                    setStatus(st);
                    onSupplyFilterChange('');
                }}
            />

            {/* Аналитика периода: выручка, разрезы, доля FBS в объёме воронки */}
            <OrdersStats wbWarehouseId={whFilter} refreshTick={refreshTick + statsTick} />

            {/* Статусные вкладки */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
                {STATUS_TABS.map(t => (
                    <button
                        key={t.key || 'all'}
                        className={`btn btn-sm ${status === t.key ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setStatus(t.key)}
                    >
                        {t.label}
                        {t.key && counts[t.key] != null && ` · ${formatNumber(counts[t.key], 0)}`}
                    </button>
                ))}
            </div>

            {/* Фильтры */}
            <div className="glass-card" style={{
                padding: 16, marginBottom: 16, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end',
            }}>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>Склад WB</label>
                    <select className="form-input" style={{ width: 200 }} value={whFilter}
                        onChange={e => setWhFilter(e.target.value ? Number(e.target.value) : '')}>
                        <option value="">Все склады</option>
                        {warehouses.map(w => (
                            <option key={w.id} value={w.wb_warehouse_id}>{w.name || `#${w.wb_warehouse_id}`}</option>
                        ))}
                    </select>
                </div>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>С</label>
                    <input className="form-input" type="date" style={{ width: 150 }}
                        value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
                </div>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>По</label>
                    <input className="form-input" type="date" style={{ width: 150 }}
                        value={dateTo} onChange={e => setDateTo(e.target.value)} />
                </div>
                {supplyFilter && (
                    <button className="btn btn-secondary btn-sm" onClick={() => onSupplyFilterChange('')}>
                        Поставка {supplyFilter} ✕
                    </button>
                )}
                <div style={{ flex: 1 }} />
                <button className="btn btn-secondary btn-sm" onClick={handleSync} disabled={syncing || backfilling}>
                    {syncing ? 'Синхронизация...' : '🔄 Забрать новые'}
                </button>
                {/* Глубина истории: WB хранит задания 3 месяца, дальше не отдаёт */}
                <select
                    className="form-input"
                    style={{ width: 110 }}
                    value={backfillDays}
                    disabled={backfilling}
                    title="Насколько глубоко тянуть историю заданий. WB хранит их 3 месяца"
                    onChange={e => setBackfillDays(Number(e.target.value))}
                >
                    {BACKFILL_DAYS_OPTIONS.map(d => (
                        <option key={d} value={d}>{formatNumber(d, 0)} дн.</option>
                    ))}
                </select>
                <button
                    className="btn btn-secondary btn-sm"
                    onClick={handleBackfill}
                    disabled={backfilling || syncing}
                    title={'Догрузить задания за прошлые дни: «Забрать новые» приносит только свежие, '
                        + 'а история до подключения раздела в зеркале не появляется сама'}
                >
                    {backfilling ? 'Загрузка истории…' : '⏬ Загрузить историю'}
                </button>
            </div>

            {/* Прогресс долгого запроса: без подписи бэкфилл выглядит зависанием */}
            {backfilling && (
                <div className="glass-card" style={{
                    padding: 12, marginBottom: 12, display: 'flex', gap: 10, alignItems: 'center',
                    borderLeft: '4px solid var(--color-accent)', fontSize: 13,
                }}>
                    <span>⏳</span>
                    <span>
                        Идёт обратная загрузка заданий {backfillPeriodLabel(backfillDays)} — WB отдаёт
                        историю окнами, это может занять до минуты. Страницу можно не трогать: по
                        завершении список и статистика обновятся сами.
                    </span>
                </div>
            )}

            {/* Массовые действия */}
            {selectedIds.length > 0 && (
                <div className="glass-card" style={{
                    padding: 12, marginBottom: 12, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap',
                }}>
                    <span style={{ fontSize: 13 }}>Выбрано: <strong>{formatNumber(selectedIds.length, 0)}</strong></span>
                    {selectedIds.length > WB_PLAN_LIMIT && (
                        <span style={{ fontSize: 12, color: 'var(--color-warning)' }}>
                            За один раз в поставки уезжает не больше {formatNumber(WB_PLAN_LIMIT, 0)} заданий
                            — снимите лишние
                        </span>
                    )}
                    <button className="btn btn-secondary btn-sm" onClick={() => setStickerModal(true)}
                        disabled={busy || stickerIds.length === 0}
                        title={stickerIds.length === 0
                            ? 'Среди выделенных нет заданий на сборке или в доставке — WB печатает стикеры только для них'
                            : undefined}>
                        🏷️ Стикеры
                        {stickerIds.length !== selectedIds.length
                            && ` (${formatNumber(stickerIds.length, 0)})`}
                    </button>
                    {/* План ничего не пишет в WB — кнопка живёт и в safe-режиме,
                        гасится только подтверждение внутри модалки */}
                    <button className="btn btn-secondary btn-sm" onClick={() => setSupplyModal(true)}
                        disabled={busy || selectedIds.length > WB_PLAN_LIMIT}>
                        📦 В поставку
                    </button>
                    <button className="btn btn-sm" onClick={() => setSelected(new Set())}>Снять выделение</button>
                </div>
            )}

            {actionError && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 12, color: 'var(--color-danger)' }}>
                    {actionError}
                </div>
            )}

            {/* Таблица */}
            {loading && !data ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            ) : error ? (
                <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>
            ) : items.length === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    {status === 'new' ? (
                        <>📭 Новых сборочных заданий нет — нажмите «Забрать новые», если ждёте заказы из WB.</>
                    ) : (
                        <>
                            <div>
                                📭 {status
                                    ? `Заданий в статусе «${SUPPLIER_STATUS_LABEL[status] ?? status}» нет.`
                                    : 'Сборочных заданий нет.'}
                            </div>
                            <div style={{ marginTop: 8, fontSize: 13 }}>
                                «Забрать новые» приносит только свежие задания — прошлые в зеркало
                                попадают лишь обратной загрузкой истории.
                            </div>
                            <button
                                className="btn btn-primary btn-sm"
                                style={{ marginTop: 12 }}
                                onClick={handleBackfill}
                                disabled={backfilling || syncing}
                            >
                                {backfilling
                                    ? 'Загрузка истории…'
                                    : `⏬ Загрузить историю ${backfillPeriodLabel(backfillDays)}`}
                            </button>
                        </>
                    )}
                </div>
            ) : (
                <>
                    {/* «На сборке» — работа идёт поставками: сводка по ним над строками */}
                    {status === 'confirm' && !supplyFilter && supplyGroups.length > 0 && (
                        <div style={{ marginBottom: 16 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                                <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Сводка: поставки</h3>
                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                    по заданиям этой страницы; полный список — на вкладке «Поставки»
                                </span>
                            </div>
                            <TanStackDataTable
                                columns={supplyGroupCols}
                                data={supplyGroups}
                                exportName="FBS_задания_по_поставкам"
                                enableSorting
                                enablePagination={supplyGroups.length > 50}
                                emptyText="Поставок нет"
                            />
                        </div>
                    )}

                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8, flexWrap: 'wrap' }}>
                        <button className="btn btn-sm" onClick={toggleAll}>
                            {allOnPageSelected ? 'Снять со страницы' : 'Выбрать страницу'}
                        </button>
                        {/* Выделение ВСЕХ отфильтрованных: заданий больше, чем помещается на страницу */}
                        <button
                            className="btn btn-sm"
                            onClick={handleSelectAllFiltered}
                            disabled={selectingAll || totalFiltered === 0 || allFilteredSelected}
                            title="Выделить все задания под текущими фильтрами, а не только эту страницу"
                        >
                            {selectingAll
                                ? 'Выделение...'
                                : `Выбрать все (${formatNumber(totalFiltered, 0)})`}
                        </button>
                        {selected.size > 0 && (
                            <button className="btn btn-sm" onClick={() => setSelected(new Set())}>
                                Снять выделение
                            </button>
                        )}
                        <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                            Всего: {formatNumber(totalFiltered || items.length, 0)}
                            {selected.size > 0 && ` · выбрано: ${formatNumber(selected.size, 0)}`}
                        </span>
                    </div>

                    <TanStackDataTable
                        columns={cols}
                        data={items}
                        exportName="FBS_задания"
                        enableSorting
                        enablePagination={false}
                    />

                    {/* Пагинация */}
                    {(page > 0 || (data?.total ?? 0) > (page + 1) * PAGE_SIZE) && (
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginTop: 12 }}>
                            <button className="btn btn-sm" disabled={page === 0 || loading}
                                onClick={() => setPage(p => Math.max(0, p - 1))}>
                                ← Назад
                            </button>
                            <span style={{ fontSize: 13, alignSelf: 'center' }}>стр. {page + 1}</span>
                            <button className="btn btn-sm" disabled={loading || (data?.total ?? 0) <= (page + 1) * PAGE_SIZE}
                                onClick={() => setPage(p => p + 1)}>
                                Вперёд →
                            </button>
                        </div>
                    )}
                </>
            )}

            {stickerModal && (
                <StickerModal
                    orderIds={stickerIds}
                    selectedCount={selectedIds.length}
                    onClose={() => setStickerModal(false)}
                    onDone={(msg) => { setStickerModal(false); onToast(msg); }}
                />
            )}

            {supplyModal && (
                <SupplyPlanModal
                    orderIds={selectedIds}
                    writeEnabled={writeEnabled}
                    writeHint={writeHint}
                    onClose={() => setSupplyModal(false)}
                    onDone={async (msg) => {
                        setSupplyModal(false);
                        setSelected(new Set());
                        onToast(msg);
                        setSummaryKey(k => k + 1);
                        await load();
                    }}
                />
            )}
        </>
    );
}

// ─── Стикеры ────────────────────────────────────────────────────────────────

function StickerModal({ orderIds, selectedCount, onClose, onDone }: {
    /** Уже ОТОБРАННЫЕ годные задания (confirm/complete) — бэк роняет пачку целиком. */
    orderIds: number[];
    /** Сколько всего выделено: разницу показываем честно, а не молча теряем. */
    selectedCount?: number;
    onClose: () => void;
    onDone: (msg: string) => void;
}) {
    const [type, setType] = useState<FbsStickerType>('png');
    const [size, setSize] = useState('58x40');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    const handleGet = async () => {
        setBusy(true);
        setError('');
        const [width, height] = size.split('x').map(Number);
        try {
            const stickers = await fetchStickersChunked(orderIds, type, width, height);
            const count = deliverStickers(stickers, type);
            if (count === 0) {
                setError('WB не вернул файлы стикеров — задания должны быть в статусе «На сборке» или «В доставке»');
                return;
            }
            onDone(`Стикеров получено: ${formatNumber(count, 0)}`);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка получения стикеров');
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 460 }}>
                <h2 className="modal-title">Стикеры заданий ({formatNumber(orderIds.length, 0)})</h2>
                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 16 }}>
                    WB выдаёт стикеры только для заданий на сборке и в доставке. Картинки открываются
                    одним листом на печать, ZPL скачивается одним файлом.
                    {orderIds.length > WB_STICKER_CHUNK && (
                        <> Запросы уйдут пачками по {WB_STICKER_CHUNK} — это лимит WB.</>
                    )}
                </p>
                {selectedCount != null && selectedCount !== orderIds.length && (
                    <div style={{ marginBottom: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>
                        Печатается {formatNumber(orderIds.length, 0)} из {formatNumber(selectedCount, 0)}
                        {' '}выделенных — новые и отменённые задания WB не печатает.
                    </div>
                )}
                {orderIds.length > STICKER_WARN_LIMIT && (
                    <div style={{ marginBottom: 12, fontSize: 12, color: 'var(--color-warning)' }}>
                        Выбрано много заданий: получение займёт время
                        ({Math.ceil(orderIds.length / WB_STICKER_CHUNK)} запросов к WB подряд).
                    </div>
                )}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div className="form-group">
                        <label className="form-label">Формат</label>
                        <select className="form-input" value={type} onChange={e => setType(e.target.value as FbsStickerType)}>
                            <option value="png">PNG</option>
                            <option value="svg">SVG</option>
                            <option value="zplv">ZPL вертикальный</option>
                            <option value="zplh">ZPL горизонтальный</option>
                        </select>
                    </div>
                    <div className="form-group">
                        <label className="form-label">Размер, мм</label>
                        <select className="form-input" value={size} onChange={e => setSize(e.target.value)}>
                            <option value="58x40">58 × 40</option>
                            <option value="40x30">40 × 30</option>
                        </select>
                    </div>
                </div>
                {error && <div style={{ marginBottom: 12, fontSize: 13, color: 'var(--color-danger)' }}>{error}</div>}
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={busy}>Отмена</button>
                    <button className="btn btn-primary" onClick={handleGet} disabled={busy}>
                        {busy ? 'Запрос...' : 'Получить'}
                    </button>
                </div>
            </div>
        </div>
    );
}

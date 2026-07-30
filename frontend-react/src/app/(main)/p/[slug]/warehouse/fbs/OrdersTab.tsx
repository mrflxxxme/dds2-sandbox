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
    CABINET_STATUS_LABEL,
    NOT_SCANNED_CABINET_KEYS,
    ORDER_PHASE_LABEL,
    PSEUDO_STATUS_LABEL,
    TRANSIT_STALE_DAYS,
    TRANSIT_WARN_DAYS,
    SELECT_ALL_MAX,
    WB_STICKER_CHUNK,
    backfillPeriodLabel,
    backfillResultMessage,
    cabinetOrderStatus,
    cargoLabel,
    collectAllOrderIds,
    deliverStickers,
    durationSinceLabel,
    fetchStickersChunked,
    hoursAgoLabel,
    isStuckAfterScan,
    num,
    orderAgeColor,
    selectStickerIds,
    transitDaysColor,
} from './fbsShared';
import type { FbsCabinetStatusKey } from './fbsShared';
import WriteoffIssuesPanel from './WriteoffIssuesPanel';
import OrderTimelineModal from './OrderTimelineModal';
import OrdersWarehouseSummary from './OrdersWarehouseSummary';
import OrdersStats, { isoDaysAgo as statsIsoDaysAgo, todayIso as statsTodayIso } from './OrdersStats';
import SupplyPlanModal, { WB_PLAN_LIMIT } from './SupplyPlanModal';

const PAGE_SIZE = 100;
/**
 * Сколько заданий за раз отдаём на стикеры без предупреждения. Жёсткого
 * потолка нет: запросы к WB идут пачками по 100 (`fetchStickersChunked`),
 * но сотня пачек — это сотня походов в WB, о чём честно предупреждаем.
 */
const STICKER_WARN_LIMIT = 500;

/**
 * Фазовые чипы — как вкладки кабинета WB (`ORDER_PHASE_LABEL`). Счётчик
 * «В доставке» считается честно: complete_total − delivered_count —
 * `complete` включает и доставленное, и прежний ярлык «Переданы в WB»
 * зелёным бейджем врал про неотсканированные поставки. Отмены — ДВА чипа
 * (канон 30.07): «Отмена клиента» (canceled_by_client / declined_by_client)
 * и «Наша отмена» (продавец / перевозчик / wb canceled) — счётчики
 * cancel_client_count / cancel_seller_count, их сумма равна прежнему
 * cancel + cancel_carrier.
 */
const PHASE_TABS: { key: string; label: string; title?: string }[] = [
    { key: '', label: 'Все' },
    { key: 'new', label: ORDER_PHASE_LABEL.new },
    { key: 'confirm', label: ORDER_PHASE_LABEL.confirm, title: 'Добавлены в поставку, поставка ещё не закрыта' },
    {
        key: 'complete',
        label: ORDER_PHASE_LABEL.complete,
        title: 'Поставка закрыта у нас, покупатель ещё не получил — от «Отгрузите товар» до «Готово к выдаче»',
    },
    { key: 'delivered', label: ORDER_PHASE_LABEL.delivered, title: 'Получено покупателем или брак' },
    {
        key: 'cancel_client',
        label: ORDER_PHASE_LABEL.cancel_client,
        title: 'Покупатель отменил или отказался от заказа',
    },
    {
        key: 'cancel_seller',
        label: ORDER_PHASE_LABEL.cancel_seller,
        title: 'Отменено нашей стороной: продавцом, перевозчиком или WB',
    },
];

/**
 * Под-фильтры фазы «В доставке» — второй ряд помельче, виден только внутри
 * фазы: это подмножества `complete`, отдельными вкладками верхнего ряда они
 * дублировали бы друг друга.
 */
const DELIVERY_SUB_TABS: { key: string; label: string; title?: string }[] = [
    { key: 'in_delivery', label: PSEUDO_STATUS_LABEL.in_delivery, title: 'Переданы в WB, сортировочный центр ещё не принял' },
    { key: 'sorted', label: PSEUDO_STATUS_LABEL.sorted, title: 'Принято сортировочным центром WB, покупатель ещё не получил' },
    {
        key: 'in_delivery_stuck',
        label: PSEUDO_STATUS_LABEL.in_delivery_stuck,
        title: `Переданы ≥ ${TRANSIT_WARN_DAYS} дней назад, а сортировочный центр так и не принял `
            + `— товар мог потеряться по дороге на СЦ (окно — последние ${TRANSIT_STALE_DAYS} дней)`,
    },
];

/** Статусы семейства «В доставке»: сама фаза + её под-фильтры. */
const DELIVERY_FAMILY: readonly string[] = ['complete', ...DELIVERY_SUB_TABS.map(t => t.key)];

/** Фазы отмен — только у них живут сводка отмен и колонка «Штраф ≈». */
const CANCEL_PHASES: readonly string[] = ['cancel_client', 'cancel_seller'];

/** Допущения оценки штрафов WB — тултип сводки отмен и колонки «Штраф ≈». */
const CANCEL_PENALTY_ASSUMPTIONS = 'Оценка по правилам WB (верхняя граница): двойная комиссия предмета, '
    + 'но не выше 50% цены и 10 000 ₽/шт (рейтинг доставки <95%), минимум 10 ₽. '
    + 'WB считает от розничной цены со скидкой, у нас чаще цена до скидки — оценка выше факта. '
    + 'Факт — удержания «Невыполненный заказ (отмена продавцом)» из финотчёта WB, '
    + 'приходят примерно на 5-й день после даты заказа.';

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
    const [busy, setBusy] = useState(false);
    const [stickerModal, setStickerModal] = useState(false);
    const [supplyModal, setSupplyModal] = useState(false);
    /** Задание, чью историю статусов смотрим (модалка «Статус заказа»). */
    const [timelineOrder, setTimelineOrder] = useState<FbsOrder | null>(null);
    /** Пересчитать сводку по складам после синка / раскладки по поставкам. */
    const [summaryKey, setSummaryKey] = useState(0);
    /**
     * Локальный тик для блока статистики: он перечитывается по ЛЮБОЙ смене
     * `refreshTick`, поэтому свои поводы (бэкфилл истории) прибавляем к тику
     * автообновления — сумма монотонна, лишней загрузки не будет.
     */
    const [statsTick, setStatsTick] = useState(0);
    /**
     * Период живёт ЗДЕСЬ, а не внутри блока статистики: тем же окном считаются
     * фазы доставки в сводке по складам. Два независимых периода означали бы,
     * что «В доставке» вверху и график внизу говорят про разные отрезки.
     */
    const [period, setPeriod] = useState(() => ({
        dateFrom: statsIsoDaysAgo(30),
        dateTo: statsTodayIso(),
        preset: '30',
    }));

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
            const res = await api.backfillFbsOrders(BACKFILL_DAYS_DEFAULT);
            onToast(backfillResultMessage(res, BACKFILL_DAYS_DEFAULT));
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

    /** Фазы после передачи поставки — только у них живёт «В пути». */
    const showTransit = DELIVERY_FAMILY.includes(status);
    /** Фазы отмен — сводка потерь живёт только здесь. */
    const showCancelSummary = CANCEL_PHASES.includes(status);
    /** Колонка «Штраф ≈» — только «Наша отмена»: клиентские WB не штрафует. */
    const showCancelPenalty = status === 'cancel_seller';

    /**
     * Кабинетный статус строки: две оси + фаза ЕЁ поставки. `supplier_status
     * = 'complete'` значит лишь «поставка закрыта у нас» — без done/scan_dt
     * строка «complete + waiting» читалась как противоречие («В доставке»
     * зелёным при неотсканированном QR).
     */
    const cabOf = (o: FbsOrder): FbsCabinetStatusKey =>
        cabinetOrderStatus(o.supplier_status, o.wb_status, !!o.supply_done, !!o.supply_scan_dt);
    const now = Date.now();

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
            render: (v: number, row: FbsOrder) => {
                // Возраст красится, пока WB не отсканировал QR (наша зона).
                const ageColor = orderAgeColor(row.created_at_wb, cabOf(row), now);
                return (
                    <div>
                        <div style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                            {row.created_at_wb ? formatDateTime(row.created_at_wb) : '—'}
                        </div>
                        {row.created_at_wb && (
                            <div style={{ fontSize: 12, fontWeight: 600, color: ageColor ?? 'var(--color-text-muted)' }}>
                                {hoursAgoLabel(row.created_at_wb, now)}
                            </div>
                        )}
                    </div>
                );
            },
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
            headerTitle: 'Цена задания: salePrice (со скидкой покупателя), если WB его прислал, иначе price',
            // Канон бэкенд-статистики: coalesce(sale_price, price) — salePrice
            // WB шлёт единицам заказов, price заполнен у всех (тот же фикс, что
            // в FbsOrdersCard и SupplyOrdersPanel).
            getValue: (row: FbsOrder) => num(row.sale_price ?? row.price),
            render: (_v: unknown, row: FbsOrder) => {
                const price = row.sale_price ?? row.price;
                return price == null ? '—' : formatNumber(num(price));
            },
            exportValue: (row: FbsOrder) => num(row.sale_price ?? row.price),
        },
        // «Штраф ≈» — только «Наша отмена»: у прочих фаз (включая клиентские
        // отмены — WB их не штрафует) поле всегда пустое, и колонка лишь
        // съедала бы ширину таблицы (паттерн «В пути»).
        ...(showCancelPenalty ? [{
            key: 'penalty_est', label: 'Штраф ≈', align: 'right',
            headerTitle: CANCEL_PENALTY_ASSUMPTIONS,
            // Numeric приходит строкой — сортировка и экспорт по числу через num()
            getValue: (row: FbsOrder) => num(row.penalty_est),
            render: (_v: unknown, row: FbsOrder) => {
                // null = нет ставки комиссии предмета — оценка пропущена, не выдумана
                if (row.penalty_est == null) return <span className="fbs-penalty-dim" title="Нет ставки комиссии предмета в тарифах">—</span>;
                const p = num(row.penalty_est);
                if (p === 0) return <span className="fbs-penalty-none">без штрафа</span>;
                return <span className="fbs-penalty-value">≈{formatNumber(p, 0)} ₽</span>;
            },
            exportValue: (row: FbsOrder) => row.penalty_est == null ? '' : num(row.penalty_est),
        }] as Column[] : []),
        {
            key: 'supplier_status', label: 'Статус',
            // Кабинетный бейдж: обе оси + фаза поставки — включает бывшую
            // колонку «Статус WB». Клик — модалка «Статус заказа».
            render: (_v: string, row: FbsOrder) => {
                const cab = cabOf(row);
                return (
                    <span
                        className={`badge ${CABINET_STATUS_LABEL[cab].badge}`}
                        style={{ cursor: 'pointer' }}
                        title="История статусов"
                        onClick={e => { e.stopPropagation(); setTimelineOrder(row); }}
                    >
                        {CABINET_STATUS_LABEL[cab].label}
                    </span>
                );
            },
            exportValue: (row: FbsOrder) => CABINET_STATUS_LABEL[cabOf(row)].label,
        },
        // «В пути» — только в фазах после передачи: у прочих значение
        // всегда пустое, и колонка лишь съедала бы ширину таблицы.
        ...(showTransit ? [{
            key: 'transit_days', label: 'В пути', align: 'right',
            headerTitle: 'Сколько задание едет после передачи поставки: до скана QR — «—» '
                + '(ещё наша зона), после скана — часы/дни до приёма СЦ, дальше — дни числом. '
                + `Подсветка: ≥ ${TRANSIT_WARN_DAYS} дн — задержка, дальше — ЧП; `
                + `> ${TRANSIT_STALE_DAYS} дн — застывший статус старого задания `
                + '(в чипе «Зависли в пути» не считается), не живой груз',
            render: (v: number | null, row: FbsOrder) => {
                const cab = cabOf(row);
                // Паттерн FbsOrdersCard: ждёт сортировки — точная длительность
                // от скана ЕГО поставки; до скана — «—»; после — дни числом.
                if (cab === 'awaiting_sort' && row.supply_scan_dt) {
                    return (
                        <span style={{ color: transitDaysColor(v) ?? undefined, whiteSpace: 'nowrap', fontWeight: 500 }}>
                            {durationSinceLabel(row.supply_scan_dt, now) ?? '—'}
                        </span>
                    );
                }
                if (NOT_SCANNED_CABINET_KEYS.includes(cab) || v == null) {
                    return <span style={{ color: 'var(--color-text-dim)' }}>—</span>;
                }
                const color = transitDaysColor(v);
                return (
                    <span style={{ color: color ?? undefined, fontWeight: color ? 700 : 400 }}>
                        {formatNumber(num(v), 0)}
                    </span>
                );
            },
            exportValue: (row: FbsOrder) => row.transit_days ?? '',
        }] as Column[] : []),
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
            {/* Переданные задания, которые нечем списать: тревога выше сводок —
                пока панель непуста, часть FBS-продаж не проведена по книгам */}
            <WriteoffIssuesPanel reloadKey={summaryKey} refreshTick={refreshTick} />

            {/* Сводка «сколько ждёт на каждом складе» — ответ на «а если складов несколько» */}
            <OrdersWarehouseSummary
                warehouses={warehouses}
                reloadKey={summaryKey}
                dateFrom={period.dateFrom}
                dateTo={period.dateTo}
                onPick={(wh, st) => {
                    setWhFilter(wh);
                    setStatus(st);
                    onSupplyFilterChange('');
                }}
            />

            {/* Аналитика периода: выручка, разрезы, доля FBS в объёме воронки */}
            <OrdersStats
                wbWarehouseId={whFilter}
                refreshTick={refreshTick + statsTick}
                dateFrom={period.dateFrom}
                dateTo={period.dateTo}
                preset={period.preset}
                onPeriodChange={setPeriod}
            />

            {/* Фазовые чипы — как вкладки кабинета WB */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                {PHASE_TABS.map(t => {
                    // «В доставке» честно: complete включает и доставленное.
                    const count = t.key === 'complete'
                        ? data ? Math.max(0, (counts.complete ?? 0) - (data.delivered_count ?? 0)) : undefined
                        : t.key === 'delivered'
                            ? data?.delivered_count
                            : t.key === 'cancel_client'
                                ? data?.cancel_client_count
                                : t.key === 'cancel_seller'
                                    ? data?.cancel_seller_count
                                    : counts[t.key];
                    // Родительский чип «В доставке» подсвечен и под её под-фильтрами.
                    const active = t.key === 'complete'
                        ? DELIVERY_FAMILY.includes(status)
                        : status === t.key;
                    return (
                        <button
                            key={t.key || 'all'}
                            className={`btn btn-sm ${active ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setStatus(t.key)}
                            title={t.title}
                        >
                            {t.label}
                            {t.key && count != null && ` · ${formatNumber(count, 0)}`}
                        </button>
                    );
                })}
            </div>

            {/* Под-фильтры внутри «В доставке» — второй ряд помельче */}
            {DELIVERY_FAMILY.includes(status) && (
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12, paddingLeft: 8 }}>
                    {DELIVERY_SUB_TABS.map(t => {
                        const count = t.key === 'in_delivery'
                            ? data?.in_delivery_count
                            : t.key === 'sorted'
                                ? data?.sorted_count
                                : data?.in_delivery_stuck_count;
                        return (
                            <button
                                key={t.key}
                                className={`btn btn-sm ${status === t.key ? 'btn-primary' : 'btn-secondary'}`}
                                style={{ fontSize: 12, padding: '2px 10px' }}
                                title={t.title}
                                // Повторный клик по активному под-фильтру возвращает всю фазу.
                                onClick={() => setStatus(status === t.key ? 'complete' : t.key)}
                            >
                                {t.label}
                                {count != null && ` · ${formatNumber(count, 0)}`}
                            </button>
                        );
                    })}
                </div>
            )}

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
                {/* Селект глубины убран (решение владельца 30.07): он выглядел
                    фильтром периода списка и путал — фильтрует только календарь
                    «С/По». История всегда тянется на максимум глубины WB (90 дн). */}
                <button
                    className="btn btn-secondary btn-sm"
                    onClick={handleBackfill}
                    disabled={backfilling || syncing}
                    title={'Догрузить задания за 90 дней (максимум глубины WB): «Забрать новые» приносит '
                        + 'только свежие, а история до подключения раздела сама не появляется'}
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
                        Идёт обратная загрузка заданий {backfillPeriodLabel(BACKFILL_DAYS_DEFAULT)} — WB отдаёт
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

            {/* Сводка отмен: потерянная выручка по ВСЕЙ выборке фильтра;
                у «Нашей отмены» — штраф WB фактом (финотчёт, с лагом ~5 дн)
                и оценкой по правилам (сразу, верхняя граница). Старый бэк
                cancel_stats не шлёт — блока просто нет, без ошибок. */}
            {showCancelSummary && data?.cancel_stats && (
                <div className="glass-card fbs-cancel-summary" title={CANCEL_PENALTY_ASSUMPTIONS}>
                    <span>
                        Потерянная выручка:{' '}
                        <strong>{formatNumber(Number(data.cancel_stats.revenue), 0)} ₽</strong>
                        {' '}({formatNumber(data.cancel_stats.orders, 0)} заданий)
                        {showCancelPenalty && (
                            <>
                                {' · '}Штраф WB (факт):{' '}
                                {data.cancel_stats.fact_scoped_out ? (
                                    <span className="fbs-penalty-dim" title="У строк финотчёта нет склада — при фильтре по складу факт не сопоставим">
                                        скрыт фильтром склада
                                    </span>
                                ) : (
                                    <strong className="fbs-penalty-value">
                                        {formatNumber(Number(data.cancel_stats.penalty_fact), 0)} ₽
                                    </strong>
                                )}
                                {!data.cancel_stats.fact_scoped_out && (
                                    <> ({formatNumber(data.cancel_stats.penalty_fact_count, 0)} удержаний)</>
                                )}
                                {' · '}Оценка по правилам:{' '}
                                <strong>≈{formatNumber(Number(data.cancel_stats.penalty_est), 0)} ₽</strong>
                                {' '}({formatNumber(data.cancel_stats.penalty_est_count, 0)} заданий)
                            </>
                        )}
                    </span>
                    {!showCancelPenalty && (
                        <span className="fbs-cancel-summary-note">
                            отмены покупателя WB не штрафует — считаем только потерянную выручку
                        </span>
                    )}
                    {showCancelPenalty && data.cancel_stats.fact_covered_to && (
                        <span className="fbs-cancel-summary-note">
                            финотчёт доехал до {formatDate(data.cancel_stats.fact_covered_to)} —
                            {' '}по более свежим отменам факт ещё не выставлен
                        </span>
                    )}
                    {showCancelPenalty && data.cancel_stats.no_commission_count > 0 && (
                        <span className="fbs-cancel-summary-note">
                            у {formatNumber(data.cancel_stats.no_commission_count, 0)} заданий
                            {' '}нет ставки комиссии — оценка по ним пропущена
                        </span>
                    )}
                    {showCancelPenalty && data.cancel_stats.estimate_truncated && (
                        <span className="fbs-cancel-summary-note">
                            оценка неполная: выборка шире лимита расчёта — сузьте период
                        </span>
                    )}
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
                                    // Фазы и псевдо-статусы живут в разных словарях;
                                    // промах обоих — показать код как есть.
                                    ? `Заданий в статусе «${ORDER_PHASE_LABEL[status]
                                        ?? PSEUDO_STATUS_LABEL[status] ?? status}» нет.`
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
                                    : `⏬ Загрузить историю ${backfillPeriodLabel(BACKFILL_DAYS_DEFAULT)}`}
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
                        // Подсветка зависших: WB не отсканировал ≥ суток (наша зона)
                        // ИЛИ отсканировал, а СЦ ≥ суток не принимает («Ждёт
                        // сортировки» — канон 30.07, от scan-якоря поставки).
                        rowClassName={(o: FbsOrder) =>
                            orderAgeColor(o.created_at_wb, cabOf(o), now) === 'var(--color-danger)'
                            || (cabOf(o) === 'awaiting_sort' && isStuckAfterScan(o.supply_scan_dt, now))
                                ? 'fbs-row-stuck'
                                : ''}
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

            {timelineOrder && (
                <OrderTimelineModal
                    wbOrderId={timelineOrder.wb_order_id}
                    article={timelineOrder.article}
                    nmId={timelineOrder.nm_id}
                    onClose={() => setTimelineOrder(null)}
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

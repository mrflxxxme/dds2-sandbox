'use client';
/**
 * Вкладка «Остатки» — превью того, что уйдёт в WB, с ПОЛНОЙ расшифровкой вычетов
 * (сток → в сборке → открытые FBS-заказы → буфер → расчёт → ручное кол-во → отдаём),
 * выбор «что отдаём» (все остатки / только то, чего нет на FBO), потоварная
 * замена количества прямо в строке, массовые действия, кнопка «Передать остатки»
 * (асинхронно) и лог прогонов трансляции.
 *
 * Ручное количество пишется в НАШУ базу и в WB ничего не меняет, поэтому его
 * поля работают в любом режиме контура (writeEnabled гасит только передачу).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type {
    FbsStockPreview,
    FbsStockPush,
    FbsStockRow,
    FbsWarehouse,
} from '@/types/api';
import {
    OVERRIDE_HINT,
    giveModeOf,
    giveModePayloadValue,
    isTranslating,
    num,
    type FbsGiveMode,
} from './fbsShared';
import { EnableTranslationModal, ReconcileModal, isFirstTimeWarehouse } from './fbsReconcile';
import {
    GROUP_LABEL,
    GROUP_NONE_KEY,
    MiniKpi,
    buildGroupColumns,
    buildPushColumns,
    buildStockColumns,
    parseOverrideInput,
    type GroupAgg,
    type GroupBy,
} from './stockColumns';

/** Опрос лога: 3 с × 40 = 2 минуты — дольше прогон не живёт (таймаут джоба 300 c). */
const POLL_INTERVAL_MS = 3000;
const POLL_MAX_TICKS = 40;

/** Контрактный предел FbsOverrideSet.nomenclature_ids. */
const BULK_MAX_IDS = 5000;

interface Props {
    warehouses: FbsWarehouse[];
    warehousesLoading: boolean;
    /** Режим контура разрешает запись в WB (safe → false, кнопка передачи гасится). */
    writeEnabled: boolean;
    /** Текст подсказки на выключенной кнопке: safe-режим или «режим не загружен». */
    writeHint: string;
    onToast: (msg: string) => void;
    /** Переход на вкладку «Склады» — из пустого состояния «нет привязок». */
    onGoToWarehouses: () => void;
    /** Перечитать склады после включения трансляции прямо отсюда. */
    onReloadWarehouses: () => void;
    /** Счётчик тиков автообновления страницы; 0 — тиков ещё не было. */
    refreshTick: number;
}

export default function StockTab({
    warehouses, warehousesLoading, writeEnabled, writeHint, onToast, onGoToWarehouses,
    onReloadWarehouses, refreshTick,
}: Props) {
    const [wbWarehouseId, setWbWarehouseId] = useState<number | ''>('');
    const [preview, setPreview] = useState<FbsStockPreview | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [search, setSearch] = useState('');
    const [onlyProblems, setOnlyProblems] = useState(false);
    /** Показывать позиции, придержанные фильтром «нет на FBO» (по умолчанию скрыты). */
    const [showFboHeld, setShowFboHeld] = useState(false);
    const [onlyOverridden, setOnlyOverridden] = useState(false);
    /** Спрятать позиции с нулевым расчётом — их в списке большинство, а уедет ноль. */
    const [hideZero, setHideZero] = useState(false);

    // Разрезы: фильтры + группировка
    const [brandFilter, setBrandFilter] = useState('');
    const [subjectFilter, setSubjectFilter] = useState('');
    const [subcatFilter, setSubcatFilter] = useState<number | ''>('');
    const [groupBy, setGroupBy] = useState<GroupBy>('');

    // Включение трансляции прямо отсюда — не гоняем пользователя на вкладку «Склады»
    const [activating, setActivating] = useState(false);
    /** Спрашиваем про сверку ДО включения: дельта не обнулит руками проставленное. */
    const [enableAsk, setEnableAsk] = useState(false);

    // Первичная сверка с кабинетом WB
    const [reconcileOpen, setReconcileOpen] = useState(false);

    // Ручное количество
    const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
    const [qtyBusy, setQtyBusy] = useState(false);
    const [qtyError, setQtyError] = useState('');
    const [bulkQty, setBulkQty] = useState('');

    // «Что отдаём»: все остатки / только то, чего нет на FBO
    const [giveBusy, setGiveBusy] = useState(false);

    const [pushes, setPushes] = useState<FbsStockPush[]>([]);
    const [pushesError, setPushesError] = useState('');
    const [pushing, setPushing] = useState(false);
    const [polling, setPolling] = useState(false);
    const [force, setForce] = useState(false);

    // Дефолтный склад — первый активный (иначе просто первый)
    useEffect(() => {
        if (wbWarehouseId !== '' || warehouses.length === 0) return;
        const active = warehouses.find(w => w.is_active) ?? warehouses[0];
        setWbWarehouseId(active.wb_warehouse_id);
    }, [warehouses, wbWarehouseId]);

    const selected = useMemo(
        () => warehouses.find(w => w.wb_warehouse_id === wbWarehouseId) ?? null,
        [warehouses, wbWarehouseId],
    );

    const giveMode = giveModeOf(selected?.fbo_max_qty);
    const translating = isTranslating(selected?.mode);

    // Смена склада WB обнуляет выделение: id номенклатур на другом складе другие
    useEffect(() => {
        setSelectedIds(new Set());
    }, [wbWarehouseId]);

    const loadPreview = useCallback(async (signal?: AbortSignal) => {
        if (wbWarehouseId === '') { setPreview(null); return; }
        setLoading(true);
        setError('');
        try {
            const res = await api.getFbsStockPreview(wbWarehouseId);
            if (signal?.aborted) return;
            setPreview(res);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки превью остатков');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [wbWarehouseId]);

    useEffect(() => {
        const controller = new AbortController();
        loadPreview(controller.signal);
        return () => controller.abort();
    }, [loadPreview]);

    const loadPushes = useCallback(async (signal?: AbortSignal): Promise<FbsStockPush[]> => {
        try {
            const res = await api.getFbsStockPushes(20);
            if (signal?.aborted) return [];
            setPushes(res);
            setPushesError('');
            return res;
        } catch (e: unknown) {
            if (signal?.aborted) return [];
            setPushesError(e instanceof Error ? e.message : 'Ошибка загрузки лога трансляции');
            return [];
        }
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        loadPushes(controller.signal);
        return () => controller.abort();
    }, [loadPushes]);

    // Опрос лога, пока есть незавершённые прогоны — UI не блокируется на поход в WB
    useEffect(() => {
        if (!polling) return;
        const controller = new AbortController();
        let ticks = 0;
        const timer = setInterval(async () => {
            ticks += 1;
            const fresh = await loadPushes(controller.signal);
            if (controller.signal.aborted) return;
            const running = fresh.some(p => p.status === 'RUNNING');
            // Первые тики не останавливаем: строка прогона могла ещё не появиться,
            // а POST — быть в полёте (иначе бросим опрос, не дождавшись RUNNING).
            if ((!running && ticks >= 2) || ticks >= POLL_MAX_TICKS) {
                setPolling(false);
                // Без signal: cleanup эффекта сработает от setPolling(false) в этом же
                // тике и прервал бы запрос на середине, оставив loading=true навсегда.
                loadPreview();
            }
        }, POLL_INTERVAL_MS);
        return () => { controller.abort(); clearInterval(timer); };
    }, [polling, loadPushes, loadPreview]);

    // ─── Автообновление ─────────────────────────────────────────────────────
    // Тик приходит со страницы. Читаем и «занятость», и сами загрузчики через
    // ref: положи их в зависимости эффекта — и он срабатывал бы на каждую смену
    // склада, дублируя обычную загрузку.
    const busyRef = useRef(false);
    const refreshRef = useRef<(signal?: AbortSignal) => void>(() => {});
    useEffect(() => {
        busyRef.current = qtyBusy || giveBusy || activating || pushing || polling;
        refreshRef.current = (signal?: AbortSignal) => { loadPreview(signal); loadPushes(signal); };
    });

    useEffect(() => {
        // Тик 0 — первичная загрузка, её делают эффекты выше. Мутация в полёте:
        // перезагрузка на её середине показала бы состояние ДО записи и
        // выглядела бы как «кнопка не сработала».
        if (refreshTick === 0 || busyRef.current) return;
        // AbortController обязателен: тик может прийти за миг до смены склада, и
        // без отмены ответ по ПРЕЖНЕМУ складу прилетит позже и перезапишет
        // превью уже выбранного — таблица покажет чужие остатки.
        const controller = new AbortController();
        refreshRef.current(controller.signal);
        return () => controller.abort();
    }, [refreshTick]);

    /**
     * Запуск трансляции. `chrtIds` — точечная отправка одной/нескольких позиций:
     * правка одного товара не должна гонять весь склад и жечь лимит WB.
     */
    const handlePush = async (chrtIds?: number[]) => {
        if (wbWarehouseId === '') return;
        setPushing(true);
        setError('');
        setPolling(true); // лог начинает обновляться, пока запрос ещё в полёте
        try {
            await api.pushFbsStocks({
                wb_warehouse_ids: [wbWarehouseId],
                // Точечная отправка всегда идёт force: смысл кнопки в строке —
                // «продавить именно это число», а дельта-режим счёл бы позицию
                // неизменившейся и молча ничего не отправил.
                force: chrtIds?.length ? true : force,
                ...(chrtIds?.length ? { chrt_ids: chrtIds } : {}),
            });
            onToast(chrtIds?.length
                ? `Отправляем ${chrtIds.length === 1 ? 'позицию' : `${chrtIds.length} позиции`} в WB`
                : 'Трансляция запущена — следите за логом ниже');
        } catch (e: unknown) {
            setPolling(false);
            setError(e instanceof Error ? e.message : 'Ошибка запуска трансляции');
        } finally {
            setPushing(false);
            await loadPushes();
        }
    };

    // ─── Что отдаём ─────────────────────────────────────────────────────────

    /**
     * Переключатель «все остатки» / «только то, чего нет на FBO».
     * Это НАША настройка склада (`fbo_max_qty`), в WB она ничего не пишет,
     * поэтому режимом контура не гейтится. «Снять гейт» кодируется -1: null в
     * теле PATCH неотличим от «поле не передано».
     */
    const handleGiveMode = useCallback(async (mode: FbsGiveMode) => {
        if (!selected || mode === giveMode) return;
        setGiveBusy(true);
        setError('');
        try {
            await api.updateFbsWarehouseSettings(selected.wb_warehouse_id, {
                fbo_max_qty: giveModePayloadValue(mode),
            });
            onToast(mode === 'all'
                ? 'Отдаём все остатки'
                : 'Отдаём только то, чего нет на FBO');
            onReloadWarehouses();
            await loadPreview();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Не удалось сохранить настройку «что отдаём»');
        } finally {
            setGiveBusy(false);
        }
    }, [selected, giveMode, onToast, onReloadWarehouses, loadPreview]);

    /**
     * Включить трансляцию для выбранного склада, не уходя на вкладку «Склады».
     * Это наша настройка (`wb_fbs_warehouses.is_active`), а не запись в WB,
     * поэтому режимом контура не гейтится.
     */
    const doEnableTranslation = useCallback(async () => {
        if (!selected) return;
        setActivating(true);
        setError('');
        try {
            await api.updateFbsWarehouseSettings(selected.wb_warehouse_id, { is_active: true });
            setEnableAsk(false);
            onToast('Трансляция включена — склад попадёт в ближайший прогон джоба');
            onReloadWarehouses();
            await loadPreview();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Не удалось включить трансляцию');
        } finally {
            setActivating(false);
        }
    }, [selected, onToast, onReloadWarehouses, loadPreview]);

    // ─── Ручное количество ──────────────────────────────────────────────────

    /** Общая точка записи: и строка, и массовая панель шлют один и тот же запрос. */
    const applyOverride = useCallback(async (
        ids: number[], qty: number | null, message: string,
    ) => {
        if (wbWarehouseId === '' || ids.length === 0) return;
        if (ids.length > BULK_MAX_IDS) {
            setQtyError(`За раз можно применить максимум ${formatNumber(BULK_MAX_IDS, 0)} позиций — сузьте выделение`);
            return;
        }
        setQtyBusy(true);
        setQtyError('');
        try {
            await api.setFbsStockOverride({
                wb_warehouse_id: wbWarehouseId,
                nomenclature_ids: ids,
                qty,
            });
            onToast(message);
            await loadPreview();
        } catch (e: unknown) {
            setQtyError(e instanceof Error ? e.message : 'Ошибка сохранения количества');
        } finally {
            setQtyBusy(false);
        }
    }, [wbWarehouseId, loadPreview, onToast]);

    const setRowOverride = useCallback((row: FbsStockRow, qty: number | null) => {
        if (row.nomenclature_id == null) return;
        const name = row.article_seller || row.barcode;
        applyOverride(
            [row.nomenclature_id],
            qty,
            qty == null
                ? `Ограничение снято — вернулся расчётный остаток (${name})`
                : qty === 0
                    ? `${name}: не отдаём на WB`
                    : `${name}: не больше ${formatNumber(qty, 0)} шт — итог не выше доступного`,
        );
    }, [applyOverride]);

    const toggleSelected = useCallback((nomenclatureId: number) => {
        setSelectedIds(prev => {
            const next = new Set(prev);
            if (next.has(nomenclatureId)) next.delete(nomenclatureId);
            else next.add(nomenclatureId);
            return next;
        });
    }, []);

    // ─── Фильтрация ─────────────────────────────────────────────────────────

    const allRows = useMemo(() => preview?.rows ?? [], [preview]);

    /**
     * Позиции, придержанные фильтром «только то, чего нет на FBO».
     * По умолчанию их в таблице НЕТ: пользователь выбрал «не отдавать то, что
     * лежит на WB» — значит в списке должно остаться только то, что реально
     * уедет. Показать их можно тумблером, чтобы было видно, что именно придержано.
     */
    const fboHeldRows = useMemo(
        () => allRows.filter(r => r.blocked_reason === 'fbo_in_stock'),
        [allRows],
    );

    const rows = useMemo(() => {
        const q = search.trim().toLowerCase();
        return allRows.filter(r => {
            if (!showFboHeld && r.blocked_reason === 'fbo_in_stock') return false;
            if (onlyProblems && !r.blocked_reason && r.chrt_id != null) return false;
            if (onlyOverridden && r.override_qty == null) return false;
            // Нули в списке не мусор: позиция могла быть продана (открытый
            // FBS-заказ) или уже уехать в WB и теперь обязана уехать нулём.
            // Поэтому это фильтр показа, а не отбрасывание строк из расчёта.
            if (hideZero && num(r.qty_computed) === 0) return false;
            if (brandFilter && (r.brand ?? '') !== brandFilter) return false;
            if (subjectFilter && (r.subject ?? '') !== subjectFilter) return false;
            if (subcatFilter !== '' && r.subcategory_id !== subcatFilter) return false;
            if (!q) return true;
            return (
                r.barcode.toLowerCase().includes(q)
                || (r.article_seller ?? '').toLowerCase().includes(q)
                || (r.brand ?? '').toLowerCase().includes(q)
                || (r.subject ?? '').toLowerCase().includes(q)
                || (r.subcategory_name ?? '').toLowerCase().includes(q)
                || String(r.nm_id ?? '').includes(q)
                || String(r.chrt_id ?? '').includes(q)
            );
        });
    }, [allRows, search, showFboHeld, onlyProblems, onlyOverridden, hideZero,
        brandFilter, subjectFilter, subcatFilter]);

    /** Варианты разрезов берём из самих строк — это ровно то, что есть на складе. */
    const facets = useMemo(() => {
        const brands = new Set<string>();
        const subjects = new Set<string>();
        const subcats = new Map<number, string>();
        for (const r of allRows) {
            if (r.brand) brands.add(r.brand);
            if (r.subject) subjects.add(r.subject);
            if (r.subcategory_id != null) {
                subcats.set(r.subcategory_id, r.subcategory_name || `Под-категория #${r.subcategory_id}`);
            }
        }
        return {
            brands: [...brands].sort((a, b) => a.localeCompare(b, 'ru')),
            subjects: [...subjects].sort((a, b) => a.localeCompare(b, 'ru')),
            subcategories: [...subcats.entries()]
                .map(([id, name]) => ({ id, name }))
                .sort((a, b) => a.name.localeCompare(b.name, 'ru')),
        };
    }, [allRows]);

    const groups = useMemo<GroupAgg[]>(() => {
        if (!groupBy) return [];
        const map = new Map<string, GroupAgg>();
        for (const r of rows) {
            const rawKey = groupBy === 'subcategory'
                ? (r.subcategory_id != null ? String(r.subcategory_id) : '')
                : groupBy === 'brand' ? (r.brand ?? '') : (r.subject ?? '');
            const key = rawKey || GROUP_NONE_KEY;
            const label = key === GROUP_NONE_KEY
                ? '— не указано —'
                : groupBy === 'subcategory'
                    ? (r.subcategory_name || `Под-категория #${r.subcategory_id}`)
                    : rawKey;
            let agg = map.get(key);
            if (!agg) {
                agg = {
                    key,
                    label,
                    subcategory_id: groupBy === 'subcategory' ? r.subcategory_id ?? null : null,
                    brand: groupBy === 'brand' ? r.brand ?? null : null,
                    subject: groupBy === 'subject' ? r.subject ?? null : null,
                    positions: 0,
                    units_computed: 0,
                    units_available: 0,
                    overridden: 0,
                };
                map.set(key, agg);
            }
            agg.positions += 1;
            agg.units_computed += r.qty_computed ?? 0;
            agg.units_available += r.qty_available ?? 0;
            if (r.override_qty != null) agg.overridden += 1;
        }
        return [...map.values()].sort((a, b) => b.units_available - a.units_available);
    }, [rows, groupBy]);

    const selectableIds = useMemo(
        () => rows.map(r => r.nomenclature_id).filter((v): v is number => v != null),
        [rows],
    );
    const uniqueSelectable = useMemo(() => [...new Set(selectableIds)], [selectableIds]);
    const selectedCount = selectedIds.size;

    /** Сколько ПОКАЗАННЫХ строк выделено — состояние чекбокса в шапке таблицы. */
    const shownSelectedCount = useMemo(
        () => uniqueSelectable.filter(id => selectedIds.has(id)).length,
        [uniqueSelectable, selectedIds],
    );

    /**
     * Чекбокс шапки: выделить все показанные / снять выделение с показанных.
     * Снимаем ИМЕННО показанные, а не всё: под фильтром может лежать выделение,
     * сделанное до его применения, и терять его от клика по шапке нельзя.
     */
    const toggleAllShown = useCallback((checked: boolean) => {
        setSelectedIds(prev => {
            const next = new Set(prev);
            for (const id of uniqueSelectable) {
                if (checked) next.add(id);
                else next.delete(id);
            }
            return next;
        });
    }, [uniqueSelectable]);

    /** Позиции с ручным количеством — их и снимаем массово. */
    const selectedOverriddenCount = useMemo(() => {
        let n = 0;
        for (const r of allRows) {
            if (r.nomenclature_id == null || !selectedIds.has(r.nomenclature_id)) continue;
            if (r.override_qty != null) n += 1;
        }
        return n;
    }, [allRows, selectedIds]);

    const overriddenCount = useMemo(
        () => allRows.filter(r => r.override_qty != null).length,
        [allRows],
    );

    /** Сколько выделенных позиций получат своё число из «Источника». */
    const selectedFactCount = useMemo(() => {
        let n = 0;
        for (const r of allRows) {
            if (r.nomenclature_id == null || !selectedIds.has(r.nomenclature_id)) continue;
            n += 1;
        }
        return n;
    }, [allRows, selectedIds]);

    /**
     * Проставить фактический остаток: каждой выделенной позиции — её «Источник».
     *
     * Берём именно `qty_source`, а не зеркало напрямую: источник УЖЕ собран по
     * правилу склада (есть интеграция WMS → зеркало участвует, нет — наш учёт),
     * поэтому кнопка работает и на складах без провайдера. Числа у позиций
     * разные, одним `qty` на список это не выразить — шлём `items`.
     */
    const applyFactQty = useCallback(async (ids: number[]) => {
        if (wbWarehouseId === '') return;
        const wanted = new Set(ids);
        const items = allRows
            .filter(r => r.nomenclature_id != null && wanted.has(r.nomenclature_id))
            .map(r => ({ nomenclature_id: r.nomenclature_id as number, qty: Math.max(0, num(r.qty_source)) }));
        if (items.length === 0) return;
        if (items.length > BULK_MAX_IDS) {
            setQtyError(`За раз можно применить максимум ${formatNumber(BULK_MAX_IDS, 0)} позиций — сузьте выделение`);
            return;
        }
        setQtyBusy(true);
        setQtyError('');
        try {
            await api.setFbsStockOverride({ wb_warehouse_id: wbWarehouseId, items });
            onToast(`Остаток проставлен ${formatNumber(items.length, 0)} позициям`);
            await loadPreview();
        } catch (e: unknown) {
            setQtyError(e instanceof Error ? e.message : 'Ошибка сохранения количества');
        } finally {
            setQtyBusy(false);
        }
    }, [wbWarehouseId, allRows, loadPreview, onToast]);


    /**
     * Гейт «Только то, чего нет на FBO» включён, но применить его НЕ К ЧЕМУ:
     * зеркала остатков WB нет ни одной строкой (`fbo_qty` у всех позиций пуст).
     * Бэкенд в этом случае осознанно выключает гейт целиком (иначе порог 0
     * отправил бы в FBS весь каталог по «нулю от незнания»), но наружу об этом
     * не сообщает — молчание читается как «гейт работает», и в WB уезжает товар,
     * который прямо сейчас продаётся с FBO.
     */
    const fboMirrorMissing = useMemo(
        () => giveMode === 'no_fbo' && allRows.length > 0 && allRows.every(r => r.fbo_qty == null),
        [giveMode, allRows],
    );

    /** Разбор поля массового ввода — им же гасится кнопка, пока число не введено. */
    const bulkQtyParsed = useMemo(() => parseOverrideInput(bulkQty), [bulkQty]);

    const handleBulkApply = () => {
        const parsed = bulkQtyParsed;
        if (!parsed.ok || parsed.qty == null) {
            setQtyError('Укажите количество в штуках (целое неотрицательное число). 0 — не отдавать');
            return;
        }
        applyOverride(
            [...selectedIds],
            parsed.qty,
            parsed.qty === 0
                ? `Не отдаём позиций: ${formatNumber(selectedIds.size, 0)}`
                : `Кол-во ${formatNumber(parsed.qty, 0)} шт проставлено ${formatNumber(selectedIds.size, 0)} позициям`,
        );
    };

    /** Снять ручное количество с выделенных — вернуть расчёт. */
    const handleBulkClear = () => {
        if (selectedOverriddenCount === 0) return;
        if (!confirm(
            `Снять ручное количество с ${selectedOverriddenCount} позиций? `
            + 'Они вернутся к расчётному остатку и снова начнут уходить в WB.',
        )) return;
        applyOverride(
            [...selectedIds],
            null,
            `Ограничение снято с ${formatNumber(selectedOverriddenCount, 0)} позиций`,
        );
    };

    const warehouseName = selected?.name || preview?.wb_warehouse_name || '';
    const noLinks = !!selected && selected.links.length === 0;

    // ─── Колонки ────────────────────────────────────────────────────────────

    const cols = useMemo(() => buildStockColumns({
        selectedIds,
        onToggleSelected: toggleSelected,
        busy: qtyBusy,
        onSetOverride: setRowOverride,
        onToggleAll: toggleAllShown,
        shownCount: uniqueSelectable.length,
        shownSelectedCount: shownSelectedCount,
    }), [selectedIds, toggleSelected, qtyBusy, setRowOverride, toggleAllShown,
        uniqueSelectable.length, shownSelectedCount]);

    const groupCols = useMemo(() => (groupBy ? buildGroupColumns({
        groupBy,
        onFilterGroup: (g: GroupAgg) => {
            if (groupBy === 'brand') setBrandFilter(g.brand ?? '');
            else if (groupBy === 'subject') setSubjectFilter(g.subject ?? '');
            else if (groupBy === 'subcategory') setSubcatFilter(g.subcategory_id ?? '');
        },
        onSelectGroup: (g: GroupAgg) => {
            // Выделяем строки ИМЕННО этой группы, не трогая уже выделенное:
            // сводка и таблица живут рядом, и обнуление чужого выделения
            // читалось бы как потеря работы.
            const inGroup = rows.filter(r => {
                const raw = groupBy === 'subcategory'
                    ? (r.subcategory_id != null ? String(r.subcategory_id) : '')
                    : groupBy === 'brand' ? (r.brand ?? '') : (r.subject ?? '');
                return (raw || GROUP_NONE_KEY) === g.key;
            });
            setSelectedIds(prev => {
                const next = new Set(prev);
                for (const r of inGroup) if (r.nomenclature_id != null) next.add(r.nomenclature_id);
                return next;
            });
        },
    }) : []), [groupBy, rows]);

    const pushCols = useMemo(() => buildPushColumns(warehouses), [warehouses]);

    // ─── Рендер ─────────────────────────────────────────────────────────────

    if (warehousesLoading) {
        return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    }
    if (warehouses.length === 0) {
        return (
            <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                <div style={{ fontSize: 40, marginBottom: 12 }}>📦</div>
                <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>Нет складов продавца WB</div>
                <div style={{ color: 'var(--color-text-muted)', fontSize: 14, marginBottom: 16 }}>
                    Заведите склад на вкладке «Склады» и привяжите к нему наш склад — тогда появится превью остатков.
                </div>
                <button className="btn btn-primary btn-sm" onClick={onGoToWarehouses}>
                    Перейти на вкладку «Склады»
                </button>
            </div>
        );
    }

    // Режим и тумблер «трансляция вкл» — ДВА независимых поля: бэкенд пропускает
    // склад по любому из них, и без второй проверки кнопка отвечала успехом,
    // а прогон не появлялся вовсе.
    const pushDisabledHint = !writeEnabled
        ? writeHint
        : !translating
            ? 'Склад в режиме «Наблюдение» — остатки в WB не передаются. '
              + 'Переключите режим на вкладке «Склады», когда будете готовы.'
            : selected && !selected.is_active
                ? 'Трансляция для этого склада выключена — включите её кнопкой ниже '
                  + 'или тумблером на вкладке «Склады».'
                : selected?.is_processing
                    ? 'Склад WB в обработке — остатки не принимаются'
                    : undefined;

    return (
        <>
            {/* Панель управления */}
            <div className="glass-card" style={{
                padding: 16, marginBottom: 16, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end',
            }}>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>Склад продавца WB</label>
                    <select
                        className="form-input"
                        style={{ width: 260 }}
                        value={wbWarehouseId}
                        onChange={e => setWbWarehouseId(e.target.value ? Number(e.target.value) : '')}
                    >
                        {warehouses.map(w => (
                            <option key={w.id} value={w.wb_warehouse_id}>
                                {w.name || `Склад #${w.wb_warehouse_id}`}{w.is_active ? '' : ' (выключен)'}
                            </option>
                        ))}
                    </select>
                </div>

                {/* Что отдаём — главный выбор экрана, стоит рядом со складом */}
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>Что отдаём</label>
                    <div style={{ display: 'flex', gap: 0 }}>
                        <button
                            className={`btn btn-sm ${giveMode === 'all' ? 'btn-primary' : 'btn-secondary'}`}
                            style={{ borderTopRightRadius: 0, borderBottomRightRadius: 0 }}
                            disabled={giveBusy || !selected}
                            title="Транслировать весь свободный остаток независимо от того, есть ли товар на FBO"
                            onClick={() => handleGiveMode('all')}
                        >
                            Все остатки
                        </button>
                        <button
                            className={`btn btn-sm ${giveMode === 'no_fbo' ? 'btn-primary' : 'btn-secondary'}`}
                            style={{ borderTopLeftRadius: 0, borderBottomLeftRadius: 0 }}
                            disabled={giveBusy || !selected}
                            title="Отдавать в FBS только те позиции, которых нет на складах WB (FBO)"
                            onClick={() => handleGiveMode('no_fbo')}
                        >
                            Только то, чего нет на FBO
                        </button>
                    </div>
                </div>

                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>Поиск</label>
                    <input
                        className="form-input"
                        style={{ width: 220 }}
                        placeholder="Артикул, баркод, бренд, предмет"
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                    />
                </div>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, paddingBottom: 8 }}>
                    <input type="checkbox" checked={onlyProblems} onChange={e => setOnlyProblems(e.target.checked)} />
                    Только проблемные
                </label>
                <label
                    style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, paddingBottom: 8 }}
                    title="Показать только позиции, где количество проставлено вручную"
                >
                    <input
                        type="checkbox"
                        checked={onlyOverridden}
                        onChange={e => setOnlyOverridden(e.target.checked)}
                    />
                    Только с ручным кол-вом
                </label>
                <label
                    style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, paddingBottom: 8 }}
                    title="Спрятать позиции с нулевым расчётом — они в WB уедут нулём"
                >
                    <input type="checkbox" checked={hideZero} onChange={e => setHideZero(e.target.checked)} />
                    Скрыть нулевые
                </label>
                <div style={{ flex: 1 }} />
                <label
                    style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, paddingBottom: 8 }}
                    title="Слать все позиции, а не только изменившиеся с прошлого прогона"
                >
                    <input type="checkbox" checked={force} onChange={e => setForce(e.target.checked)} />
                    Слать всё (force)
                </label>
                <button className="btn btn-secondary btn-sm" onClick={() => loadPreview()} disabled={loading}>
                    {loading ? 'Обновление...' : '🔄 Пересчитать'}
                </button>
                <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => setReconcileOpen(true)}
                    disabled={wbWarehouseId === ''}
                    title="Спросить у WB фактические остатки по всей номенклатуре и показать, чем мы не управляем"
                >
                    🔎 Сверить с WB
                </button>
                <button
                    className="btn btn-primary btn-sm"
                    onClick={() => handlePush()}
                    disabled={
                        !writeEnabled || !translating || pushing || polling
                        || wbWarehouseId === '' || !!selected?.is_processing
                        || !selected?.is_active
                    }
                    title={pushDisabledHint}
                >
                    {pushing || polling ? '⏳ Передача...' : '📤 Передать остатки'}
                </button>
            </div>

            {/* Разрезы: фильтры и группировка */}
            {!noLinks && (
                <div className="glass-card" style={{
                    padding: 12, marginBottom: 12, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end',
                }}>
                    <div>
                        <label className="form-label" style={{ fontSize: 12 }}>Бренд</label>
                        <select className="form-input" style={{ width: 180 }}
                            value={brandFilter} onChange={e => setBrandFilter(e.target.value)}>
                            <option value="">все</option>
                            {facets.brands.map(b => <option key={b} value={b}>{b}</option>)}
                        </select>
                    </div>
                    <div>
                        <label className="form-label" style={{ fontSize: 12 }}>Предмет</label>
                        <select className="form-input" style={{ width: 180 }}
                            value={subjectFilter} onChange={e => setSubjectFilter(e.target.value)}>
                            <option value="">все</option>
                            {facets.subjects.map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                    </div>
                    <div>
                        <label className="form-label" style={{ fontSize: 12 }}>Под-категория</label>
                        <select className="form-input" style={{ width: 200 }}
                            value={subcatFilter}
                            onChange={e => setSubcatFilter(e.target.value ? Number(e.target.value) : '')}>
                            <option value="">все</option>
                            {facets.subcategories.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                        </select>
                    </div>
                    <div>
                        <label className="form-label" style={{ fontSize: 12 }}>Группировка</label>
                        <select className="form-input" style={{ width: 170 }}
                            value={groupBy} onChange={e => setGroupBy(e.target.value as GroupBy)}>
                            <option value="">нет</option>
                            <option value="brand">по бренду</option>
                            <option value="subject">по предмету</option>
                            <option value="subcategory">по под-категории</option>
                        </select>
                    </div>
                    {(brandFilter || subjectFilter || subcatFilter !== '') && (
                        <button
                            className="btn btn-sm"
                            style={{ marginBottom: 8 }}
                            onClick={() => { setBrandFilter(''); setSubjectFilter(''); setSubcatFilter(''); }}
                        >
                            Сбросить разрезы
                        </button>
                    )}
                </div>
            )}

            {/* Предупреждения */}
            {selected && !translating && (
                <div className="glass-card" style={{
                    padding: 12, marginBottom: 12, fontSize: 13,
                    borderLeft: '4px solid var(--color-accent)',
                    display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                }}>
                    <span style={{ fontSize: 18, lineHeight: 1 }}>👀</span>
                    <span style={{ color: 'var(--color-text-muted)' }}>
                        Склад в режиме <strong style={{ color: 'var(--color-text)' }}>«Наблюдение»</strong>:
                        {' '}считаем и показываем, что ушло бы в WB, но в кабинет не пишем ничего. Расчёт
                        и ручное количество работают как обычно, сверка ЧИТАЕТ кабинет, но обнулить в нём
                        позиции нельзя. Переключить режим — на вкладке «Склады».
                    </span>
                </div>
            )}
            {selected && translating && !selected.is_active && (
                <div className="glass-card" style={{
                    padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-warning)',
                    display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                }}>
                    <span>⚠️ Трансляция для этого склада выключена — джоб его пропускает.</span>
                    <button
                        className="btn btn-primary btn-sm"
                        disabled={activating}
                        // Чистим прошлую ошибку до открытия: модалка показывает
                        // `error`, и чужой стейт читался бы как отказ включения.
                        onClick={() => { setError(''); setEnableAsk(true); }}
                        title="Включить трансляцию остатков на этот склад продавца WB"
                    >
                        {activating ? '…' : 'Включить трансляцию'}
                    </button>
                </div>
            )}
            {preview?.is_processing && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-warning)' }}>
                    ⚠️ Склад WB в состоянии «в обработке» — обновление остатков недоступно.
                </div>
            )}
            {fboMirrorMissing && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-warning)' }}>
                    ⚠️ Данных по остаткам на складах WB ещё нет — <strong>гейт «только то, чего нет
                    на FBO» не применяется</strong>, в WB уедет весь свободный остаток, включая товар,
                    который сейчас продаётся с FBO. Дождитесь синка отчёта «Остатки на складах»
                    (или проверьте ключ WB), затем пересчитайте превью.
                </div>
            )}
            {preview && preview.rows_no_chrt > 0 && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-warning)' }}>
                    ⚠️ Позиций без chrtId: <strong>{formatNumber(preview.rows_no_chrt, 0)}</strong> — они не транслируются
                    в WB. Заполните chrtId в номенклатуре (синк карточек WB), иначе товар не будет продаваться по FBS.
                </div>
            )}
            {qtyError && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-danger)' }}>
                    {qtyError}
                </div>
            )}

            {/* Панель массовых действий */}
            {!noLinks && selectedCount > 0 && (
                <div className="glass-card" style={{
                    padding: 12, marginBottom: 12, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center',
                    borderLeft: '4px solid var(--color-accent)',
                }}>
                    <div style={{ fontSize: 14, fontWeight: 600 }}>
                        Выделено: {formatNumber(selectedCount, 0)}
                    </div>
                    <input
                        className="form-input"
                        style={{ width: 130 }}
                        type="number"
                        min={0}
                        step="1"
                        placeholder="кол-во, шт"
                        value={bulkQty}
                        // Прошлая ошибка гаснет с первым же символом: она про
                        // ПУСТОЕ поле, и висеть над заполненным ей незачем.
                        onChange={e => { setBulkQty(e.target.value); setQtyError(''); }}
                        title={OVERRIDE_HINT}
                    />
                    <button
                        className="btn btn-primary btn-sm"
                        // Пустое поле — не ошибка пользователя, а незаконченный
                        // ввод: гасим кнопку, вместо того чтобы ругаться после клика.
                        disabled={qtyBusy || bulkQtyParsed.qty == null}
                        onClick={handleBulkApply}
                        title={bulkQtyParsed.qty == null
                            ? 'Введите количество в поле слева — либо нажмите «Проставить остаток»'
                            : OVERRIDE_HINT}
                    >
                        Проставить выделенным
                    </button>
                    <button
                        className="btn btn-secondary btn-sm"
                        disabled={qtyBusy || selectedFactCount === 0}
                        onClick={() => applyFactQty([...selectedIds])}
                        title="Каждой позиции проставить её остаток из колонки «Источник» — там, где склад интегрирован с WMS, это остаток по зеркалу ФФ"
                    >
                        📥 Проставить остаток ({selectedFactCount})
                    </button>
                    <button
                        className="btn btn-danger btn-sm"
                        disabled={qtyBusy}
                        onClick={() => applyOverride(
                            [...selectedIds],
                            0,
                            `Не отдаём позиций: ${formatNumber(selectedIds.size, 0)}`,
                        )}
                        title="Проставить 0 — выделенные позиции не уйдут на WB"
                    >
                        ⛔ Не отдавать
                    </button>
                    <button
                        className="btn btn-sm"
                        disabled={qtyBusy || selectedOverriddenCount === 0}
                        onClick={handleBulkClear}
                        title="Снять ручное количество — вернуть расчётный остаток"
                    >
                        Снять у выделенных ({selectedOverriddenCount})
                    </button>
                    <div style={{ flex: 1 }} />
                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                        склад {warehouseName || wbWarehouseId}
                    </span>
                    <button className="btn btn-sm" onClick={() => setSelectedIds(new Set())}>
                        Снять выделение
                    </button>
                </div>
            )}

            {/* Данные */}
            {noLinks ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>🔗</div>
                    <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>
                        К складу «{warehouseName || wbWarehouseId}» не привязан ни один наш склад
                    </div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14, marginBottom: 16, lineHeight: 1.5 }}>
                        Отдавать нечего: остатки берутся с наших складов, привязанных к этому складу продавца.
                        Привяжите наш склад на вкладке «Склады» — и здесь появится превью с расшифровкой вычетов.
                    </div>
                    <button className="btn btn-primary btn-sm" onClick={onGoToWarehouses}>
                        Привязать склад на вкладке «Склады»
                    </button>
                </div>
            ) : loading && !preview ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка превью...</div>
            ) : error ? (
                <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>
            ) : !preview || preview.rows.length === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    📭 Позиций к трансляции нет — на привязанных складах нет годного остатка
                    либо он целиком съеден резервами и буфером.
                </div>
            ) : (
                <>
                    <div style={{
                        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
                        gap: 12, marginBottom: 12,
                    }}>
                        <MiniKpi label="Позиций" value={preview.total_rows} />
                        <MiniKpi label="Штук к передаче" value={preview.total_units} />
                        <MiniKpi label="Без chrtId" value={preview.rows_no_chrt} danger={preview.rows_no_chrt > 0} />
                        <MiniKpi label="С ручным кол-вом" value={overriddenCount} />
                    </div>

                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>
                        Расчёт = max(0, Источник − В сборке − Буфер) по всем привязанным складам
                        − открытые FBS-заказы{selected && selected.max_qty_per_sku > 0
                            ? `, но не больше ${formatNumber(selected.max_qty_per_sku, 0)} шт на SKU`
                            : ''}. Отдаём = min(ручное кол-во, Расчёт); позиции с кол-вом 0 уходят нулём
                        {giveMode === 'no_fbo'
                            ? (fboMirrorMissing
                                // Утверждать блокировку, когда зеркала FBO нет, нельзя:
                                // гейт в этом состоянии выключен целиком (см. `fboMirrorMissing`).
                                ? '. Режим «только то, чего нет на FBO» выбран, но данных по складам WB нет — '
                                  + 'гейт не применяется, уходит весь остаток.'
                                : '. Сейчас отдаём только то, чего нет на FBO — позиции с остатком '
                                  + 'на складах WB блокируются.')
                            : '. Сейчас отдаём все остатки, наличие на FBO не смотрим.'}
                    </div>

                    {groupBy && (
                        <div style={{ marginBottom: 16 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                                <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>
                                    Сводка: {GROUP_LABEL[groupBy]}
                                </h3>
                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                    «Выделить группу» — и количество проставляется всем её позициям сразу
                                </span>
                            </div>
                            <TanStackDataTable
                                columns={groupCols}
                                data={groups}
                                exportName={`FBS_остатки_сводка_${GROUP_LABEL[groupBy]}`}
                                enableSorting
                                enablePagination={groups.length > 50}
                                pageSize={50}
                                emptyText="Групп нет"
                            />
                        </div>
                    )}

                    <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                        <button
                            className="btn btn-sm"
                            disabled={uniqueSelectable.length === 0}
                            onClick={() => setSelectedIds(new Set(uniqueSelectable))}
                        >
                            Выделить показанные ({formatNumber(uniqueSelectable.length, 0)})
                        </button>
                        {fboHeldRows.length > 0 && (
                            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                                <input
                                    type="checkbox"
                                    checked={showFboHeld}
                                    onChange={e => setShowFboHeld(e.target.checked)}
                                />
                                <span style={{ color: 'var(--color-text-muted)' }}>
                                    Скрыто {formatNumber(fboHeldRows.length, 0)} — есть на FBO
                                </span>
                            </label>
                        )}
                        {selectedCount > 0 && (
                            <button className="btn btn-sm" onClick={() => setSelectedIds(new Set())}>
                                Снять выделение
                            </button>
                        )}
                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                            Поле «Кол-во» пишется в нашу базу и работает в любом режиме контура.
                            Пусто — расчёт, 0 — не отдавать.
                        </span>
                    </div>

                    <TanStackDataTable
                        columns={cols}
                        data={rows}
                        exportName={`FBS_остатки_${warehouseName || wbWarehouseId}`}
                        enableSorting
                        enablePagination={rows.length > 100}
                        pageSize={100}
                        emptyText="Ничего не найдено"
                    />
                </>
            )}

            {/* Лог трансляции */}
            <div style={{ marginTop: 24 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                    <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Лог трансляции</h3>
                    {polling && <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>обновляется...</span>}
                    <div style={{ flex: 1 }} />
                    <button className="btn btn-sm" onClick={() => loadPushes()}>Обновить</button>
                </div>
                {pushesError ? (
                    <div className="glass-card" style={{ padding: 16, color: 'var(--color-danger)' }}>{pushesError}</div>
                ) : pushes.length === 0 ? (
                    <div className="glass-card" style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                        Прогонов ещё не было.
                    </div>
                ) : (
                    <TanStackDataTable
                        columns={pushCols}
                        data={pushes}
                        exportName="FBS_лог_трансляции"
                        enableSorting
                        enablePagination={false}
                    />
                )}
            </div>

            {reconcileOpen && wbWarehouseId !== '' && (
                <ReconcileModal
                    wbWarehouseId={wbWarehouseId}
                    warehouseName={warehouseName || String(wbWarehouseId)}
                    writeEnabled={writeEnabled}
                    writeHint={writeHint}
                    isProcessing={!!selected?.is_processing}
                    noLinks={noLinks}
                    translating={translating}
                    onToast={onToast}
                    onApplied={() => loadPreview()}
                    onClose={() => setReconcileOpen(false)}
                />
            )}

            {enableAsk && selected && (
                <EnableTranslationModal
                    wbWarehouseId={selected.wb_warehouse_id}
                    warehouseName={warehouseName || String(selected.wb_warehouse_id)}
                    firstTime={preview ? isFirstTimeWarehouse(preview.rows) : null}
                    writeEnabled={writeEnabled}
                    writeHint={writeHint}
                    isProcessing={!!selected.is_processing}
                    noLinks={noLinks}
                    translating={translating}
                    busy={activating}
                    // Ошибка включения обязана быть видна НАД оверлеем: блок
                    // с `error` в карточке под модалкой пользователь не увидит.
                    error={error}
                    onToast={onToast}
                    onConfirm={doEnableTranslation}
                    onClose={() => setEnableAsk(false)}
                />
            )}
        </>
    );
}

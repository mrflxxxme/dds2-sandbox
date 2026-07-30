'use client';
/**
 * Общее для вкладок FBS: словари статусов, бейджи, коэрс Numeric-полей
 * и сборка файлов из base64 (стикеры заданий, QR поставки).
 */
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatDateTime, formatNumber, pluralRu } from '@/lib/utils';
import type {
    FbsModeInfo,
    FbsOrderBackfillResult,
    FbsPickList,
    FbsSticker,
    FbsStickerType,
    FbsStockSource,
    FbsSupplierStatus,
    FbsSupply,
    FbsSupplyStatus,
    FbsWriteoffReason,
} from '@/types/api';
import { formatAge } from './useAutoRefresh';

// ─── Числа ──────────────────────────────────────────────────────────────────

/**
 * Numeric/Decimal-поля бэка сериализуются в JSON СТРОКОЙ (несмотря на `number`
 * в types/api.ts) — `formatNumber` на строке отдаёт её as-is. Единая точка коэрса.
 */
export function num(v: number | string | null | undefined): number {
    if (v == null) return 0;
    const n = typeof v === 'number' ? v : Number(v);
    return Number.isFinite(n) ? n : 0;
}

// ─── Словари ────────────────────────────────────────────────────────────────

export const SUPPLIER_STATUS_LABEL: Record<string, string> = {
    new: 'Новое',
    confirm: 'На сборке',
    complete: 'В доставке',
    cancel: 'Отменено',
    cancel_carrier: 'Отменено перевозчиком',
};

export const SUPPLIER_STATUS_BADGE: Record<string, string> = {
    new: 'badge-info',
    confirm: 'badge-warning',
    complete: 'badge-success',
    cancel: 'badge-danger',
    cancel_carrier: 'badge-danger',
};

/**
 * Статусы, в которых WB печатает стикер (зеркало `_STICKER_STATUSES` бэка).
 * Бэк валидирует ВСЮ пачку и на первом же негодном id роняет весь запрос —
 * значит отбирать годные обязан фронт, иначе одно отменённое задание в
 * поставке лишает сборщика стикеров на все остальные.
 */
export const STICKER_READY_STATUSES: readonly string[] = ['confirm', 'complete'];

/** Задание в терминальном статусе — ни стикера, ни передачи (зеркало `FBS_TERMINAL_STATUSES`). */
export const TERMINAL_STATUSES: readonly string[] = ['cancel', 'cancel_carrier'];

/** Можно ли напечатать стикер этого задания. */
export function isStickerReady(status: string | null | undefined): boolean {
    return !!status && STICKER_READY_STATUSES.includes(status);
}

/**
 * Из выделения — только те задания, которым WB напечатает стикер.
 *
 * Статусы приходят картой «виденных» заданий: выделение уходит за пределы
 * показанной страницы («Выбрать все» тащит и `new`, и `cancel`), а бэк
 * валидирует ВСЮ пачку и роняет её на первом негодном id — вместе с уже
 * полученными от WB стикерами. Неизвестный статус НЕ печатаем: недодать
 * лучше, чем сжечь весь запрос и лимит WB.
 */
export function selectStickerIds(
    ids: Iterable<number>,
    statusById: ReadonlyMap<number, string>,
): number[] {
    return [...ids].filter(id => isStickerReady(statusById.get(id)));
}

/** Живо ли задание: отменённое не едет и передавать его нечего. */
export function isActiveOrder(status: string | null | undefined): boolean {
    return !!status && !TERMINAL_STATUSES.includes(status);
}

/**
 * Псевдо-статусы списка заданий — подмножества `complete`, которые считает
 * бэкенд (`in_delivery_count` / `sorted_count` / `in_delivery_stuck_count`).
 * Это НЕ значения `supplierStatus`: в `SUPPLIER_STATUS_LABEL` их нет, а в
 * `GET /fbs/orders?status=…` они уходят как есть — фильтр понимает оба вида.
 */
export const PSEUDO_STATUS_LABEL: Record<string, string> = {
    in_delivery: 'Ещё в доставке',
    sorted: 'Отсортировано',
    in_delivery_stuck: 'Зависли в пути',
};

/**
 * Фазовые вкладки списка заданий — в терминах КАБИНЕТА WB. Ключи — валидные
 * значения фильтра `GET /fbs/orders?status=…` (зеркало
 * `ALLOWED_ORDER_STATUS_FILTERS` бэка): supplier-статусы + псевдо-статус
 * `delivered` («Завершённые»: complete, дошедшее до покупателя — sold/defect).
 * `complete` здесь — «В доставке», как в кабинете; прежний ярлык «Переданы
 * в WB» врал зелёным цветом про неотсканированные поставки.
 */
export const ORDER_PHASE_LABEL: Record<string, string> = {
    new: 'Новые',
    confirm: 'На сборке',
    complete: 'В доставке',
    delivered: 'Завершённые',
    cancel: 'Отменено',
    cancel_carrier: 'Отменено перевозчиком',
    // Разрез отмен (канон 30.07): чипы вкладки — два, по стороне решения.
    cancel_client: 'Отмена клиента',
    cancel_seller: 'Наша отмена',
};

// ─── Зависшие в пути на СЦ ──────────────────────────────────────────────────

/**
 * С какого дня «в пути» считаем задержкой (совпадает с порогом бэка —
 * `_TRANSIT_STUCK_DAYS`). Канон владельца 30.07: СУТКИ от скана QR поставки
 * без приёмки СЦ — уже зависание.
 */
export const TRANSIT_WARN_DAYS = 1;
/** С какого дня задержка — уже ЧП: товар передали, а СЦ так и не принял. */
export const TRANSIT_DANGER_DAYS = 3;
/**
 * Потолок окна «зависло» (совпадает с бэком): у старых `complete` wb_status
 * застывает (синк опрашивает не всё), и дальше этого срока «N дн в пути» —
 * артефакт застывшего статуса, а не живой груз на дороге.
 */
export const TRANSIT_STALE_DAYS = 30;

/**
 * Цвет значения «В пути, дн»: null — обычный текст (не подсвечиваем).
 * Пороги едины с чипом «Зависли в пути», чтобы подсветка и счётчик не спорили:
 * за потолком окна (> TRANSIT_STALE_DAYS) строка в чипе НЕ считается, поэтому
 * и красить её тревожным нельзя — приглушаем как «застывший статус».
 */
export function transitDaysColor(days: number | null | undefined): string | null {
    if (days == null) return null;
    if (days > TRANSIT_STALE_DAYS) return 'var(--color-text-dim)';
    if (days >= TRANSIT_DANGER_DAYS) return 'var(--color-danger)';
    if (days >= TRANSIT_WARN_DAYS) return 'var(--color-warning)';
    return null;
}

/**
 * Полных дней с момента `iso` до сейчас (0 — сегодня, отрицательное время не
 * бывает — будущие даты читаем как «сегодня»). Для подписи «N дн назад».
 */
export function parseUtcMs(iso: string | null | undefined): number | null {
    if (!iso) return null;
    // Бэк шлёт naive-UTC без 'Z' — Date.parse прочитал бы такое как ЛОКАЛЬНОЕ
    // время и завысил возраст на часовой пояс (паттерн warehouse/[id]/page.tsx).
    const raw = iso.endsWith('Z') || /[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z';
    const ts = Date.parse(raw);
    return Number.isFinite(ts) ? ts : null;
}

export function daysSince(iso: string | null | undefined, now: number = Date.now()): number | null {
    const ts = parseUtcMs(iso);
    if (ts == null) return null;
    return Math.max(0, Math.floor((now - ts) / 86_400_000));
}

/**
 * «Заказ завис ПОСЛЕ скана» — общий предикат подсветки строк в фазе
 * «Ждёт сортировки» (три списка заданий, канон 30.07): с момента скана QR
 * поставки прошло ≥ TRANSIT_WARN_DAYS (сутки), а СЦ заказ так и не принял.
 *
 * Считаем от scan-якоря миллисекундами, а НЕ от `transit_days` бэка: тот
 * отдаёт ЦЕЛЫЕ сутки и зажёгся бы почти на день позже. За потолком
 * TRANSIT_STALE_DAYS не подсвечиваем — там застывший статус старого задания,
 * не живой груз (симметрично чипу «Зависли в пути» и `transitDaysColor`).
 */
export function isStuckAfterScan(scanIso: string | null | undefined, now: number = Date.now()): boolean {
    const ts = parseUtcMs(scanIso);
    if (ts == null) return false;
    const elapsed = now - ts;
    return elapsed >= TRANSIT_WARN_DAYS * 86_400_000 && elapsed <= TRANSIT_STALE_DAYS * 86_400_000;
}

/** Порог «заказ ждёт подозрительно долго» (WB ещё не отсканировал), часов. */
export const ORDER_AGE_WARN_HOURS = 12;
/** Порог «заказ ЗАВИС» — сутки без скана WB (сборка + передача + приёмка СЦ). */
export const ORDER_AGE_DANGER_HOURS = 24;

/** `wbStatus`, означающие отмену заказа (обе оси схлопнуты фронтом одинаково с бэком). */
const WB_CANCEL_STATUSES: readonly string[] = ['canceled', 'canceled_by_client', 'declined_by_client'];

/**
 * Подмножество отмен, где решение принял ПОКУПАТЕЛЬ (зеркало
 * `FBS_WB_CLIENT_CANCEL_STATUSES` бэка, канон 30.07): всё остальное
 * отменённое — наша сторона (продавец / перевозчик / wb canceled).
 */
export const WB_CLIENT_CANCEL_STATUSES: readonly string[] = ['canceled_by_client', 'declined_by_client'];

/**
 * Статус задания В ТЕРМИНАХ КАБИНЕТА WB (решение владельца 30.07: «надо то же
 * самое всё это сделать»). Кабинет выводит его из ФАЗЫ ПОСТАВКИ + оси WB:
 *   поставка не закрыта → «На сборке»; закрыта, QR не отсканирован →
 *   «Отгрузите товар» (ещё НАША зона — тут живёт подсветка зависших);
 *   отсканирован → «Ждёт сортировки»; дальше говорит ось WB
 *   («Отсортировано» / «Готово к выдаче» / «Получено покупателем»).
 */
export type FbsCabinetStatusKey =
    | 'new' | 'assembling' | 'ship_goods' | 'awaiting_sort'
    | 'sorted' | 'ready_for_pickup' | 'postponed' | 'sold' | 'defect'
    | 'cancelled' | 'cancelled_client' | 'cancelled_seller' | 'cancelled_carrier';

export const CABINET_STATUS_LABEL: Record<FbsCabinetStatusKey, { label: string; badge: string }> = {
    new: { label: 'Новое', badge: 'badge-info' },
    assembling: { label: 'На сборке', badge: 'badge-info' },
    ship_goods: { label: 'Отгрузите товар', badge: 'badge-warning' },
    awaiting_sort: { label: 'Ждёт сортировки', badge: 'badge-info' },
    sorted: { label: 'Отсортировано', badge: 'badge-success' },
    ready_for_pickup: { label: 'Готово к выдаче', badge: 'badge-success' },
    postponed: { label: 'Отложенная доставка', badge: 'badge-secondary' },
    sold: { label: 'Получено покупателем', badge: 'badge-success' },
    defect: { label: 'Брак', badge: 'badge-danger' },
    // Разрез отмен (канон 30.07): бейдж строки называет сторону решения.
    cancelled_client: { label: 'Отменён клиентом', badge: 'badge-secondary' },
    cancelled_seller: { label: 'Отменён нами', badge: 'badge-secondary' },
    cancelled_carrier: { label: 'Отменён перевозчиком', badge: 'badge-secondary' },
    // Легаси-ключ: `cabinetOrderStatus` его больше не возвращает, но старые
    // консьюмеры словаря не должны падать на промахе.
    cancelled: { label: 'Отменено', badge: 'badge-secondary' },
};

/** Фазы, где заказ ещё НЕ отсканирован WB — наша зона: подсветка зависших живёт тут. */
export const NOT_SCANNED_CABINET_KEYS: readonly FbsCabinetStatusKey[] = ['new', 'assembling', 'ship_goods'];

export function cabinetOrderStatus(
    supplierStatus: string | null | undefined,
    wbStatus: string | null | undefined,
    supplyDone: boolean,
    supplyScanned: boolean,
): FbsCabinetStatusKey {
    if ((supplierStatus && TERMINAL_STATUSES.includes(supplierStatus))
        || (wbStatus && WB_CANCEL_STATUSES.includes(wbStatus))) {
        // Сторона решения (зеркало cancel_client/cancel_seller бэка): клиентский
        // wbStatus главнее — отказ покупателя случается и при supplier cancel.
        if (wbStatus && WB_CLIENT_CANCEL_STATUSES.includes(wbStatus)) return 'cancelled_client';
        if (supplierStatus === 'cancel_carrier') return 'cancelled_carrier';
        return 'cancelled_seller';
    }
    if (wbStatus === 'sold') return 'sold';
    if (wbStatus === 'defect') return 'defect';
    if (wbStatus === 'sorted') return 'sorted';
    if (wbStatus === 'ready_for_pickup') return 'ready_for_pickup';
    if (wbStatus === 'postponed_delivery') return 'postponed';
    if (supplyScanned) return 'awaiting_sort';
    if (supplyDone) return 'ship_goods';
    return supplierStatus === 'new' ? 'new' : 'assembling';
}

/**
 * Цвет возраста задания, пока WB его НЕ отсканировал (`NOT_SCANNED_CABINET_KEYS`):
 * дольше суток — красный, дольше 12 ч — оранжевый. После скана — зона
 * логистики WB, не красим.
 */
export function orderAgeColor(
    iso: string | null | undefined,
    cabinetKey: FbsCabinetStatusKey,
    now: number = Date.now(),
): string | null {
    if (!NOT_SCANNED_CABINET_KEYS.includes(cabinetKey)) return null;
    const ts = parseUtcMs(iso);
    if (ts == null) return null;
    const hours = (now - ts) / 3_600_000;
    if (hours >= ORDER_AGE_DANGER_HOURS) return 'var(--color-danger)';
    if (hours >= ORDER_AGE_WARN_HOURS) return 'var(--color-warning)';
    return null;
}

/**
 * Длительность «с момента X» без слова «назад»: «1 дн 17 ч» / «5 ч 53 мин» /
 * «12 мин». Для колонки «В пути»: бэкендные transit_days — целые СУТКИ, и
 * поставка, переданная вчера вечером, показывала бы «0» — как будто колонка
 * сломана.
 */
export function durationSinceLabel(iso: string | null | undefined, now: number = Date.now()): string | null {
    const ts = parseUtcMs(iso);
    if (ts == null) return null;
    const totalMin = Math.max(0, Math.floor((now - ts) / 60_000));
    if (totalMin < 1) return '<1 мин';
    if (totalMin < 60) return `${totalMin} мин`;
    const days = Math.floor(totalMin / 1440);
    const hours = Math.floor((totalMin % 1440) / 60);
    if (days >= 1) return hours > 0 ? `${days} дн ${hours} ч` : `${days} дн`;
    const mins = totalMin % 60;
    return mins > 0 ? `${hours} ч ${mins} мин` : `${hours} ч`;
}

/**
 * «Сколько прошло» в стиле кабинета WB: «5 ч 53 мин назад» / «12 мин назад» /
 * «3 дн 4 ч назад». Для подписи под временем поступления задания — сборщику
 * важнее возраст заказа, чем абсолютная дата.
 */
export function hoursAgoLabel(iso: string | null | undefined, now: number = Date.now()): string | null {
    const ts = parseUtcMs(iso);
    if (ts == null) return null;
    const totalMin = Math.max(0, Math.floor((now - ts) / 60_000));
    if (totalMin < 1) return 'только что';
    if (totalMin < 60) return `${totalMin} мин назад`;
    const days = Math.floor(totalMin / 1440);
    const hours = Math.floor((totalMin % 1440) / 60);
    const mins = totalMin % 60;
    if (days >= 1) return hours > 0 ? `${days} дн ${hours} ч назад` : `${days} дн назад`;
    return mins > 0 ? `${hours} ч ${mins} мин назад` : `${hours} ч назад`;
}

export const WB_STATUS_LABEL: Record<string, string> = {
    waiting: 'Ожидает сборки',
    sorted: 'Отсортировано',
    sold: 'Получено покупателем',
    canceled: 'Отменено',
    canceled_by_client: 'Отмена покупателем',
    declined_by_client: 'Отказ покупателя',
    defect: 'Брак',
    ready_for_pickup: 'Готово к выдаче',
    postponed_delivery: 'Отложенная доставка',
    accepted_by_carrier: 'Принято перевозчиком',
    sent_to_carrier: 'Передано перевозчику',
};

// ─── Таймлайн «Статус заказа» ───────────────────────────────────────────────

/**
 * Ярлыки БАЗОВЫХ кодов таймлайна (`GET /fbs/orders/{id}/timeline`).
 *
 * Тот же паттерн, что `WRITEOFF_REASON_LABEL`: ключи — машинные коды бэкенда,
 * человеческий текст живёт только здесь. Коды с префиксом резолвятся словарями
 * осей (`wb:<status>` → `WB_STATUS_LABEL`, `supplier:<status>` →
 * `SUPPLIER_STATUS_LABEL`), промах любого словаря — фолбэк «показать код как
 * есть» — контракт закреплён тестом `src/__tests__/lib/fbsOrderTimeline.test.ts`.
 */
export const TIMELINE_LABEL: Record<string, string> = {
    created: 'Оформлен',
    assembling: 'Взят в сборку',
    assembled: 'Продавец собрал заказ',
    scanned: 'Принят сортировочным центром (скан QR)',
    written_off: 'Списано со склада (учёт DDS)',
};

/** Подсказка к «≈»: время события зафиксировано синком, а не WB. */
export const TIMELINE_APPROX_HINT = 'время зафиксировано синхронизацией (точность ~5 мин)';

/** Человеческий ярлык события таймлайна по машинному коду. */
export function timelineLabel(code: string): string {
    if (code.startsWith('wb:')) {
        return WB_STATUS_LABEL[code.slice('wb:'.length)] ?? code;
    }
    if (code.startsWith('supplier:')) {
        return SUPPLIER_STATUS_LABEL[code.slice('supplier:'.length)] ?? code;
    }
    return TIMELINE_LABEL[code] ?? code;
}

/** Порядок кнопок переключателя: от консервативного к «доверяем провайдеру». */
export const STOCK_SOURCES: readonly FbsStockSource[] = ['min_of_both', 'ff_mirror', 'ledger'];

export const STOCK_SOURCE_LABEL: Record<string, string> = {
    ledger: 'Наш учёт',
    ff_mirror: 'Система ФФ',
    min_of_both: 'Минимум из двух',
};

/**
 * Что означает каждый источник — текстом под переключателем. Выбор реально
 * меняет, сколько товара увидит покупатель, поэтому цена ошибки названа прямо.
 */
export const STOCK_SOURCE_HINT: Record<FbsStockSource, string> = {
    min_of_both: 'Консервативно: берём меньшее из нашего учёта и остатка в системе ФФ. '
        + 'Там, где наши книги отстают от склада, часть товара не уедет в WB.',
    ff_mirror: 'Доверяем системе ФФ: остаток берётся из неё, наш учёт не ограничивает. '
        + 'Подходит, когда учёт на складе ведёт провайдер. Помните, что расхождения '
        + 'с нашими книгами при этом перестают удерживать выдачу.',
    ledger: 'Только наш складской учёт, зеркало ФФ игнорируется. Для складов без '
        + 'интеграции это единственный вариант — он и подставится сам.',
};

/** Нормализация значения с сервера: неизвестное/пустое читаем как «минимум из двух». */
export function stockSourceOf(v: string | null | undefined): FbsStockSource {
    return v === 'ledger' || v === 'ff_mirror' ? v : 'min_of_both';
}

export const PUSH_STATUS_BADGE: Record<string, string> = {
    RUNNING: 'badge-info',
    OK: 'badge-success',
    PARTIAL: 'badge-warning',
    ERROR: 'badge-danger',
};

export const PUSH_STATUS_LABEL: Record<string, string> = {
    RUNNING: 'Выполняется',
    OK: 'Успех',
    PARTIAL: 'Частично',
    ERROR: 'Ошибка',
};

export const PUSH_TRIGGER_LABEL: Record<string, string> = {
    auto: 'Джоб',
    manual: 'Кнопка',
};

/**
 * Причины, по которым позиция не уходит в WB (`stock_service.BLOCKED_REASONS`).
 *
 * Ключи — машинные коды бэкенда; человеческий текст живёт только здесь. Промах
 * словаря в рантайме невидим (сработает фолбэк «показать код как есть»), и в
 * колонке «Причина» — а заодно в Excel-выгрузке — вылезает `fbo_in_stock`,
 * поэтому контракт закреплён тестами с обеих сторон
 * (`tests/test_wb_fbs_overrides.py`, `src/__tests__/lib/fbsStockOverride.test.ts`).
 */
export const BLOCKED_REASON_LABEL: Record<string, string> = {
    no_chrt: 'нет chrtId — не транслируется',
    override_zero: 'ручное кол-во 0 — не отдаём',
    warehouse_processing: 'склад WB в обработке — остатки не принимаются',
    inactive: 'трансляция склада выключена',
    // Код бэкенда — `fbo_in_stock` (BLOCKED_FBO_IN_STOCK). `fbo_present` —
    // исторический ключ, оставлен алиасом на тот же текст: если он где-то ещё
    // ожидается, ячейка не должна превращаться в машинный код.
    fbo_in_stock: 'есть на FBO — в FBS не отдаём',
    fbo_present: 'есть на FBO — в FBS не отдаём',
    observe: 'склад в наблюдении — остатки не передаются',
};

export function blockedReasonLabel(reason: string): string {
    return BLOCKED_REASON_LABEL[reason] ?? reason;
}

/**
 * Причины, по которым переданное задание НЕЧЕМ списать со склада
 * (`orders_service`, ручка `GET /fbs/orders/writeoff-issues`).
 *
 * Тот же паттерн, что `BLOCKED_REASON_LABEL`: ключи — машинные коды бэкенда,
 * человеческий текст живёт только здесь, промах словаря в рантайме невидим
 * (фолбэк «показать код как есть») — контракт закреплён тестом
 * `src/__tests__/lib/fbsWriteoffIssues.test.ts`.
 */
export const WRITEOFF_REASON_LABEL: Record<string, string> = {
    no_stock: 'нет остатка на складе',
    no_card: 'нет карточки товара',
    no_link: 'склад не привязан',
    // queued — НЕ проблема: остаток есть, задание ждёт ближайшего прогона
    // 5-минутного джоба списания. Тон нейтральный, строка в панели приглушена.
    queued: 'ждёт списания (остаток есть)',
};

export function writeoffReasonLabel(reason: FbsWriteoffReason): string {
    return WRITEOFF_REASON_LABEL[reason] ?? reason;
}

// ─── Ручное количество и режим склада ───────────────────────────────────────

/**
 * Смысл поля «Кол-во» в строке остатков — один и тот же текст в подсказке
 * колонки, в массовой панели и в модалках, чтобы правило не пересказывалось
 * тремя разными формулировками.
 */
export const OVERRIDE_HINT =
    'Сколько отдаём по этому товару. Пока своего числа нет, в поле подставлен '
    + 'остаток из кабинета WB — правьте прямо в нём. Итог = min(введённое, «Можем '
    + 'отдать») — поднять выдачу выше физического остатка нельзя. 0 — не отдавать '
    + 'вовсе, пустое поле — снять ограничение и вернуться к расчёту.';

/** Режим склада продавца: наблюдение — штатное состояние, не авария. */
export const WAREHOUSE_MODE_LABEL: Record<string, string> = {
    observe: 'Наблюдение',
    translate: 'Трансляция',
};

export function warehouseModeLabel(mode: string | null | undefined): string {
    if (!mode) return WAREHOUSE_MODE_LABEL.observe;
    return WAREHOUSE_MODE_LABEL[mode] ?? mode;
}

/** Пишем ли мы остатки этого склада в WB. Неизвестное значение = наблюдение. */
export function isTranslating(mode: string | null | undefined): boolean {
    return mode === 'translate';
}

/**
 * Что отдаём в FBS (`WbFbsWarehouse.fbo_max_qty`).
 * null — все остатки; 0 — только то, чего нет на FBO.
 */
export type FbsGiveMode = 'all' | 'no_fbo';

export function giveModeOf(fboMaxQty: number | null | undefined): FbsGiveMode {
    return fboMaxQty == null ? 'all' : 'no_fbo';
}

/**
 * Значение `fbo_max_qty` для PATCH настроек: «снять гейт» кодируется -1,
 * потому что null в теле неотличим от «поле не передано».
 */
export function giveModePayloadValue(mode: FbsGiveMode): number {
    return mode === 'all' ? -1 : 0;
}

/** cargoType WB: 1 — обычный, 2 — СГТ, 3 — КГТ. */
export const CARGO_TYPE_LABEL: Record<number, string> = {
    1: 'обычный',
    2: 'СГТ',
    3: 'КГТ',
};

/** deliveryType WB: 1 — FBS (на склад WB), 2 — DBS (своими силами), 3 — самовывоз. */
export const DELIVERY_TYPE_LABEL: Record<number, string> = {
    1: 'FBS',
    2: 'DBS',
    3: 'Самовывоз',
};

export function cargoLabel(v: number | null | undefined): string {
    if (v == null) return '—';
    return CARGO_TYPE_LABEL[v] ?? `#${v}`;
}

/**
 * Тот же cargoType, но словами кабинета WB: в шапке поставки продавец видит
 * «МГТ / СГТ / КГТ+», а не «обычный». В таблицах оставляем `cargoLabel` —
 * там важнее читаемость, в шапке — совпадение с кабинетом.
 */
export const CARGO_CABINET_LABEL: Record<number, string> = {
    1: 'МГТ',
    2: 'СГТ',
    3: 'КГТ+',
};

export function cargoCabinetLabel(v: number | null | undefined): string {
    if (v == null) return '—';
    return CARGO_CABINET_LABEL[v] ?? `#${v}`;
}

/** crossBorderType поставки: 0/пусто — обычная, иначе трансграничная. */
export function crossBorderLabel(v: number | null | undefined): string {
    return v ? 'трансграничная' : 'обычная';
}

// ─── Состояние поставки ─────────────────────────────────────────────────────

/**
 * Ярлык, бейдж и подсказка на каждое состояние поставки — ЕДИНАЯ карта на
 * список и экран поставки. Раньше состояний было два (`done ? 'Передана' :
 * 'Активная'`), и все закрытые поставки выглядели одинаково, хотя в кабинете
 * WB их три: «Отгрузите поставку» (QR не отсканирован), «Завершена»
 * (отсканирована) и «Отклонена».
 */
export const SUPPLY_STATUS_LABEL: Record<FbsSupplyStatus, {
    label: string;
    badge: string;
    hint: string;
}> = {
    active: {
        label: 'Сборка заказов',
        badge: 'badge-info',
        hint: 'Поставка не закрыта: задания докладываются — в кабинете WB это вкладка «На сборке»',
    },
    to_ship: {
        label: 'Отгрузите поставку',
        badge: 'badge-warning',
        hint: 'Поставка закрыта, но QR на пункте приёма WB ещё не отсканирован — товар не сдан',
    },
    in_delivery: {
        label: 'Сортируем / в обработке',
        badge: 'badge-success',
        hint: 'QR отсканирован на пункте приёма WB — в кабинете это «Сортируем» / «Поставка в обработке». '
            + 'Факта приёмки поставка в API не несёт: он виден только по её заданиям',
    },
    rejected: {
        label: 'Отклонена',
        badge: 'badge-danger',
        hint: 'WB отклонил приёмку поставки (rejectDt)',
    },
};

/** Порядок состояний для фильтров и счётчиков — от черновика к финалу. */
export const SUPPLY_STATUSES: readonly FbsSupplyStatus[] = [
    'active', 'to_ship', 'in_delivery', 'rejected',
];

function isSupplyStatus(v: string | null | undefined): v is FbsSupplyStatus {
    return !!v && v in SUPPLY_STATUS_LABEL;
}

/**
 * Состояние поставки. Обычно приходит готовым полем `status`; фолбэк по
 * `reject_dt` / `scan_dt` / `done` нужен для строк, приехавших до появления
 * поля (кэш браузера, старый ответ) — без него бейдж молча стал бы пустым.
 */
export function supplyStatusOf(s: Pick<FbsSupply, 'status' | 'done' | 'scan_dt' | 'reject_dt'>): FbsSupplyStatus {
    if (isSupplyStatus(s.status)) return s.status;
    if (s.reject_dt) return 'rejected';
    if (!s.done) return 'active';
    return s.scan_dt ? 'in_delivery' : 'to_ship';
}

/** Ярлык/бейдж по СТРОКОВОМУ статусу поставки (напр. `fbs_supply_status` заявки). */
export function supplyStatusInfo(v: string | null | undefined) {
    return v && v in SUPPLY_STATUS_LABEL ? SUPPLY_STATUS_LABEL[v as FbsSupplyStatus] : null;
}

export function supplyStatusMeta(s: Pick<FbsSupply, 'status' | 'done' | 'scan_dt' | 'reject_dt'>) {
    return SUPPLY_STATUS_LABEL[supplyStatusOf(s)];
}

/** Бейдж состояния поставки — один и тот же в списке и в шапке экрана. */
export function SupplyStatusBadge(
    { supply }: { supply: Pick<FbsSupply, 'status' | 'done' | 'scan_dt' | 'reject_dt'> },
) {
    const meta = supplyStatusMeta(supply);
    return <span className={`badge ${meta.badge}`} title={meta.hint}>{meta.label}</span>;
}

// ─── Заданий в поставке ─────────────────────────────────────────────────────

/**
 * Сколько заданий в поставке. Приоритет у данных WB: наше зеркало наполняется
 * синком и до него честно пусто, а показывать «0» там, где WB говорит «11», —
 * прямая ложь (ровно этим колонка «Заданий» и врала).
 */
export function supplyOrdersCount(s: Pick<FbsSupply, 'orders_count' | 'wb_orders_count'>): number {
    return num(s.wb_orders_count ?? s.orders_count);
}

/**
 * Подсказка, когда WB знает о поставке больше, чем наше зеркало. null — цифры
 * сходятся (или состав у WB не спрашивали) и подсвечивать нечего.
 */
export function supplyOrdersMirrorHint(
    s: Pick<FbsSupply, 'orders_count' | 'wb_orders_count'>,
): string | null {
    if (s.wb_orders_count == null) return null;
    const wb = num(s.wb_orders_count);
    const mirror = num(s.orders_count);
    if (wb === mirror) return null;
    return `в зеркале ${formatNumber(mirror, 0)} из ${formatNumber(wb, 0)}`;
}

/** Та же мысль, но развёрнуто — для подсказки при наведении. */
export function supplyOrdersMirrorTitle(
    s: Pick<FbsSupply, 'orders_count' | 'wb_orders_count'>,
): string | undefined {
    const short = supplyOrdersMirrorHint(s);
    return short
        ? `${short} — состав поставки известен WB, а наше зеркало отстаёт: `
          + 'догрузите задания («Забрать новые» / «Загрузить историю» на вкладке «Заказы»)'
        : undefined;
}

/**
 * Почему группа плана не поедет в поставку. Бэкенд может прислать и код, и
 * готовую фразу — неизвестное значение показываем как есть, а не «—».
 */
export const SUPPLY_BLOCKED_LABEL: Record<string, string> = {
    already_in_supply: 'задание уже в другой поставке',
    terminal_status: 'терминальный статус — в поставку не берётся',
    no_warehouse: 'у задания не указан склад WB',
    supply_closed: 'поставка уже передана — доложить нельзя',
    not_found: 'задание не найдено в зеркале',
};

export function supplyBlockedLabel(reason: string): string {
    return SUPPLY_BLOCKED_LABEL[reason] ?? reason;
}

export function deliveryLabel(v: number | null | undefined): string {
    if (v == null) return '—';
    return DELIVERY_TYPE_LABEL[v] ?? `#${v}`;
}

// ─── Режим контура ──────────────────────────────────────────────────────────

/** Подсказка на выключенных кнопках записи (title) для РЕАЛЬНО загруженного safe. */
export const WRITE_DISABLED_HINT =
    'Режим safe: запись в WB отключена — действие недоступно. '
    + 'Переключите режим контура (WB_FBS_MODE), чтобы разрешить запись.';

/** То же, когда режим контура не загрузился: причина другая, совет — тоже. */
export const MODE_UNKNOWN_HINT =
    'Режим контура не загружен — кнопки записи выключены на всякий случай. '
    + 'Нажмите «Повторить» в плашке вверху страницы.';

/**
 * Текст подсказки на выключенной кнопке. Врать про safe, когда режим просто не
 * доехал, нельзя: пользователь на боевом контуре шёл крутить WB_FBS_MODE по
 * прямому указанию подсказки, хотя запись была разрешена, а упал один запрос.
 */
export function writeDisabledHint(info: FbsModeInfo | null): string {
    return info === null ? MODE_UNKNOWN_HINT : WRITE_DISABLED_HINT;
}

/** Плашка «режим не загрузился» с ретраем — единственный способ вернуть кнопки. */
export function FbsModeErrorBanner(
    { message, retrying, onRetry }: { message: string; retrying: boolean; onRetry: () => void },
) {
    return (
        <div
            className="glass-card"
            style={{
                padding: 16,
                marginBottom: 16,
                borderLeft: '4px solid var(--color-danger)',
                display: 'flex',
                gap: 12,
                alignItems: 'flex-start',
            }}
        >
            <div style={{ fontSize: 22, lineHeight: 1.2 }}>⚠️</div>
            <div style={{ flex: 1 }}>
                <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-danger)', marginBottom: 4 }}>
                    Режим контура не загружен — кнопки записи выключены
                </div>
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                    Чтение работает, но пока неизвестно, разрешена ли запись в WB, действия
                    заблокированы. Это не значит, что включён режим safe.
                    {' '}
                    <span style={{ color: 'var(--color-text-dim)' }}>{message}</span>
                </div>
            </div>
            <button className="btn btn-secondary btn-sm" onClick={onRetry} disabled={retrying}>
                {retrying ? 'Загрузка…' : 'Повторить'}
            </button>
        </div>
    );
}

/**
 * Баннер режима вверху страницы. В `prod` не рисуется вовсе — боевой контур
 * это норма, а лишняя плашка приучает не читать предупреждения.
 */
/**
 * Возраст данных и тумблер автообновления.
 *
 * Свой таймер внутри — намеренно: подпись «N мин назад» обязана стареть между
 * обновлениями, а держать её в состоянии страницы значило бы перерисовывать
 * всю таблицу раз в полминуты ради одной строки текста.
 */
export function AutoRefreshBadge(
    { lastAt, enabled, onToggle }: { lastAt: number | null; enabled: boolean; onToggle: (v: boolean) => void },
) {
    const [, forceTick] = useState(0);

    useEffect(() => {
        const timer = setInterval(() => forceTick(t => t + 1), 30_000);
        return () => clearInterval(timer);
    }, []);

    const age = lastAt == null ? null : formatAge(Date.now() - lastAt);
    return (
        <label
            style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer' }}
            title="Обновлять данные раздела автоматически раз в 3 минуты. На скрытой вкладке пауза, при возврате — сразу догоняем."
        >
            <input type="checkbox" checked={enabled} onChange={e => onToggle(e.target.checked)} />
            <span style={{ color: 'var(--color-text-muted)' }}>
                Автообновление{age ? ` · ${age}` : ''}
            </span>
        </label>
    );
}

export function FbsModeBanner({ info }: { info: FbsModeInfo | null }) {
    if (!info || info.mode === 'prod') return null;

    const isSandbox = info.mode === 'sandbox';
    const color = isSandbox ? 'var(--color-accent)' : 'var(--color-warning)';
    const missingKey = isSandbox ? !info.has_sandbox_key : false;

    return (
        <div
            className="glass-card"
            style={{
                padding: 16,
                marginBottom: 16,
                borderLeft: `4px solid ${color}`,
                display: 'flex',
                gap: 12,
                alignItems: 'flex-start',
            }}
        >
            <div style={{ fontSize: 22, lineHeight: 1.2 }}>{isSandbox ? '🧪' : '🛡️'}</div>
            <div style={{ flex: 1 }}>
                <div style={{ fontSize: 15, fontWeight: 600, color, marginBottom: 4 }}>
                    {isSandbox
                        ? 'Песочница WB: работа на тестовых данных'
                        : 'Режим приёмки: остатки на реальный WB НЕ передаются, поставки не создаются'}
                </div>
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                    {isSandbox
                        ? 'Склады, остатки и задания здесь — из тестового контура WB. '
                          + 'Боевой кабинет не затрагивается.'
                        : 'Чтение из кабинета WB работает: справочники, задания и поставки видны как есть. '
                          + 'Любая запись (передача остатков, создание складов и поставок) заблокирована '
                          + 'до переключения режима — кнопки записи выключены.'}
                    {' '}
                    <span style={{ color: 'var(--color-text-dim)' }}>
                        Контур: {info.mode} · {info.api_base}
                    </span>
                </div>
                {missingKey && (
                    <div style={{ fontSize: 13, color: 'var(--color-danger)', marginTop: 6 }}>
                        Ключа песочницы нет. Нужен токен «Маркетплейс» со scope Test — добавьте его
                        в Настройках → Интеграции (тип ключа wb_marketplace_sandbox). Боевой ключ
                        в песочнице не работает.
                    </div>
                )}
            </div>
        </div>
    );
}

// ─── Бейджи ─────────────────────────────────────────────────────────────────

export function SupplierStatusBadge({ status }: { status: FbsSupplierStatus | string }) {
    return (
        <span className={`badge ${SUPPLIER_STATUS_BADGE[status] ?? 'badge-secondary'}`}>
            {SUPPLIER_STATUS_LABEL[status] ?? status}
        </span>
    );
}

// ─── Файлы из base64 (стикеры, QR) ──────────────────────────────────────────

const STICKER_MIME: Record<FbsStickerType, { mime: string; ext: string }> = {
    png: { mime: 'image/png', ext: 'png' },
    svg: { mime: 'image/svg+xml', ext: 'svg' },
    zplv: { mime: 'text/plain', ext: 'zpl' },
    zplh: { mime: 'text/plain', ext: 'zpl' },
};

export function stickerMime(type: FbsStickerType): { mime: string; ext: string } {
    return STICKER_MIME[type] ?? STICKER_MIME.png;
}

/** base64 → Blob без промежуточного fetch(data:) — WB отдаёт файлы строкой. */
export function base64ToBlob(b64: string, mime: string): Blob {
    const clean = b64.includes(',') ? b64.slice(b64.indexOf(',') + 1) : b64;
    const bin = atob(clean.replace(/\s/g, ''));
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Blob([bytes], { type: mime });
}

export function downloadBlob(blob: Blob, filename: string): void {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    // revoke откладываем: Safari отменяет скачивание, если url умирает сразу
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** Один base64-файл → скачивание. */
export function downloadBase64(b64: string, mime: string, filename: string): void {
    downloadBlob(base64ToBlob(b64, mime), filename);
}

/**
 * Пачка ZPL — склеиваем в один файл: принтер печатает этикетки подряд.
 * Скачивать 100 файлов по одному браузер всё равно не даст.
 */
export function downloadZplBundle(files: string[], filename: string): void {
    const text = files.map(f => atob(f.replace(/\s/g, ''))).join('\n');
    downloadBlob(new Blob([text], { type: 'text/plain' }), filename);
}

// ─── Стикеры пачкой ─────────────────────────────────────────────────────────

/** WB принимает максимум 100 заданий за один запрос стикеров. */
export const WB_STICKER_CHUNK = 100;

export function chunkIds(ids: number[], size = WB_STICKER_CHUNK): number[][] {
    const out: number[][] = [];
    for (let i = 0; i < ids.length; i += size) out.push(ids.slice(i, i + size));
    return out;
}

/**
 * Стикеры на произвольное число заданий: запросы идут ПАЧКАМИ по 100 и
 * ПОСЛЕДОВАТЕЛЬНО — параллельный залп выжигает лимит WB, а любой 4XX стоит
 * 10 запросов бакета. Поставка целиком обычно больше сотни заданий, поэтому
 * чанкование живёт здесь, а не в вызывающем.
 */
export async function fetchStickersChunked(
    orderIds: number[],
    type: FbsStickerType,
    width: number,
    height: number,
): Promise<FbsSticker[]> {
    const out: FbsSticker[] = [];
    for (const chunk of chunkIds(orderIds)) {
        const res = await api.getFbsStickers({ order_ids: chunk, type, width, height });
        if (Array.isArray(res)) out.push(...res);
    }
    return out;
}

/**
 * Отдать полученные стикеры пользователю: картинки — одним листом на печать
 * (с откатом на файлы, если окно печати заблокировано), ZPL — одним файлом.
 * Возвращает число реально отданных файлов (0 — WB не вернул ни одного).
 */
export function deliverStickers(stickers: FbsSticker[], type: FbsStickerType): number {
    // Фильтруем ПАРЫ, а не строки: WB для части заданий отдаёт элемент без
    // `file`, и после `map(...).filter(...)` массив файлов становится короче
    // исходного — индекс `i` начинает указывать на ЧУЖОЙ order_id, и сборщик
    // клеит этикетку не на ту коробку (ошибка тихая, ловится на приёмке WB).
    const withFile = stickers.filter((s): s is FbsSticker & { file: string } => !!s.file);
    if (withFile.length === 0) return 0;
    const files = withFile.map(s => s.file);
    const { mime, ext } = stickerMime(type);
    if (type === 'zplv' || type === 'zplh') {
        downloadZplBundle(files, `fbs-стикеры-${files.length}.zpl`);
    } else if (files.length === 1) {
        downloadBase64(files[0], mime, `fbs-стикер-${withFile[0].order_id}.${ext}`);
    } else if (!printStickerImages(files, mime)) {
        // окно печати заблокировано браузером — отдаём файлами
        files.forEach((f, i) => downloadBase64(f, mime, `fbs-стикер-${withFile[i]?.order_id ?? i}.${ext}`));
    }
    return files.length;
}

/**
 * Пачка картинок (png/svg) — единый HTML-лист: по этикетке на страницу.
 * Возвращает false, если окно печати заблокировано браузером — тогда
 * вызывающий откатывается на скачивание файлов по одному.
 */
// ─── Копирование в буфер ────────────────────────────────────────────────────

/**
 * Копирование текста (QR поставки) с откатом на execCommand: `navigator.clipboard`
 * недоступен вне https/localhost, и без отката кнопка «скопировать» молча ничего
 * не делает. Возвращает true, если текст ушёл в буфер.
 */
export async function copyText(text: string): Promise<boolean> {
    if (!text) return false;
    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
            return true;
        }
    } catch {
        // падаем в откат ниже
    }
    try {
        const area = document.createElement('textarea');
        area.value = text;
        area.style.position = 'fixed';
        area.style.opacity = '0';
        document.body.appendChild(area);
        area.select();
        const ok = document.execCommand('copy');
        document.body.removeChild(area);
        return ok;
    } catch {
        return false;
    }
}

// ─── Выделение всех отфильтрованных заданий ─────────────────────────────────

/** Потолок `limit` у GET /fbs/orders — больше сервер не отдаёт. */
export const SELECT_ALL_PAGE_SIZE = 500;

/** Страховка: «выбрать все» на многотысячной выборке не должно вешать браузер. */
export const SELECT_ALL_MAX = 5000;

interface OrderIdPage {
    items: { wb_order_id: number }[];
}

/**
 * Собрать id ВСЕХ заданий под текущими фильтрами, а не только видимой страницы.
 *
 * Страница отдаёт максимум 500 строк, а заданий в выборке бывает больше —
 * поэтому идём страницами ПОСЛЕДОВАТЕЛЬНО (параллельный залп бьёт по бэку без
 * нужды) и дедупим id: между запросами могло приехать новое задание и сдвинуть
 * окно, тогда одна и та же строка попадётся дважды.
 */
export async function collectAllOrderIds(
    fetchPage: (offset: number, limit: number) => Promise<OrderIdPage>,
    total: number,
    opts: { pageSize?: number; max?: number } = {},
): Promise<{ ids: number[]; truncated: boolean }> {
    const pageSize = Math.max(1, opts.pageSize ?? SELECT_ALL_PAGE_SIZE);
    const max = opts.max ?? SELECT_ALL_MAX;
    const target = Math.min(Math.max(total, 0), max);
    const ids: number[] = [];
    const seen = new Set<number>();
    let offset = 0;
    while (ids.length < target) {
        const limit = Math.min(pageSize, target - ids.length);
        const page = await fetchPage(offset, limit);
        const chunk = page?.items ?? [];
        for (const o of chunk) {
            if (seen.has(o.wb_order_id)) continue;
            seen.add(o.wb_order_id);
            ids.push(o.wb_order_id);
        }
        // выдача кончилась раньше заявленного total — не крутить пустые запросы
        if (chunk.length < limit) break;
        offset += chunk.length;
    }
    return { ids, truncated: total > target };
}

// ─── Обратная загрузка истории заданий ──────────────────────────────────────

/** Глубина бэкфилла по умолчанию — потолок ручки (WB дальше не отдаёт). */
export const BACKFILL_DAYS_DEFAULT = 90;

/** Варианты глубины в селекте «Загрузить историю». */
export const BACKFILL_DAYS_OPTIONS: readonly number[] = [7, 30, 90];

/**
 * Период словами: «за 3 месяца» читается человеком, «за 90 дней» — счётчиком.
 * Кратные 30 дни показываем месяцами, 7 — неделей, остальное — днями.
 */
export function backfillPeriodLabel(days: number): string {
    const d = Math.max(1, Math.round(num(days)));
    if (d === 7) return 'за неделю';
    if (d % 30 === 0) {
        const months = d / 30;
        return `за ${formatNumber(months, 0)} ${pluralRu(months, ['месяц', 'месяца', 'месяцев'])}`;
    }
    return `за ${formatNumber(d, 0)} ${pluralRu(d, ['день', 'дня', 'дней'])}`;
}

/**
 * Итог бэкфилла человеческим текстом: «Загружено 1240 заданий за 3 месяца».
 * Текст бэкенда, если он есть, приоритетнее — он знает про усечения окон.
 */
export function backfillResultMessage(res: FbsOrderBackfillResult, days: number): string {
    if (res?.message) return res.message;
    const fetched = num(res?.fetched);
    const base = `Загружено ${formatNumber(fetched, 0)} `
        + `${pluralRu(fetched, ['задание', 'задания', 'заданий'])} ${backfillPeriodLabel(days)}`;
    const written = num(res?.written_off_marked);
    return written > 0
        ? `${base} · списание отмечено у ${formatNumber(written, 0)}`
        : base;
}

// ─── Лист подбора: печать ───────────────────────────────────────────────────

const HTML_ESCAPE: Record<string, string> = {
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
};

/** Данные поставки идут из WB — в печатный HTML их подставляем только экранированными. */
export function escapeHtml(v: string | number | null | undefined): string {
    if (v == null) return '';
    return String(v).replace(/[&<>"']/g, c => HTML_ESCAPE[c]);
}

/**
 * Печатная вёрстка листа подбора: шапка поставки + таблица позиций + итог.
 * Никаких интерфейсных элементов — документ уходит сборщику на бумагу.
 */
export function buildPickListPrintHtml(pick: FbsPickList): string {
    const title = `Лист подбора ${pick.wb_supply_id}`;
    const meta: [string, string][] = [
        ['Поставка', pick.wb_supply_id],
        ['Название', pick.supply_name || '—'],
        ['Склад продавца', pick.wb_warehouse_name
            || (pick.wb_warehouse_id != null ? `#${pick.wb_warehouse_id}` : '—')],
        ['Габарит', cargoCabinetLabel(pick.cargo_type)],
        ['Заказы', formatNumber(pick.orders_count, 0)],
        ['Создана', pick.created_at_wb ? formatDateTime(pick.created_at_wb) : '—'],
        ['Статус', pick.done ? 'Передана в доставку' : 'Активная'],
    ];
    const metaHtml = meta
        .map(([k, v]) => `<div><span class="k">${escapeHtml(k)}:</span> ${escapeHtml(v)}</div>`)
        .join('');

    const rowsHtml = pick.rows.map((r, i) => `<tr>
<td class="n">${i + 1}</td>
<td>${escapeHtml(r.article || '—')}${r.brand ? `<div class="sub">${escapeHtml(r.brand)}</div>` : ''}</td>
<td>${escapeHtml(r.subject || '—')}</td>
<td class="mono">${escapeHtml(r.barcode || '—')}</td>
<td class="n big">${escapeHtml(formatNumber(r.qty, 0))}</td>
<td class="n">${r.stock_available == null ? '—' : escapeHtml(formatNumber(r.stock_available, 0))}</td>
<td class="box"></td>
</tr>`).join('');

    return '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        + `<title>${escapeHtml(title)}</title>`
        + '<style>'
        + '@page{margin:10mm}'
        + 'body{margin:0;font-family:Arial,Helvetica,sans-serif;color:#000;font-size:12px}'
        + 'h1{font-size:18px;margin:0 0 8px}'
        + '.meta{display:flex;flex-wrap:wrap;gap:4px 24px;margin-bottom:12px;font-size:12px}'
        + '.meta .k{color:#555}'
        + 'table{width:100%;border-collapse:collapse}'
        + 'th,td{border:1px solid #999;padding:4px 6px;text-align:left;vertical-align:top}'
        + 'th{background:#eee;font-size:11px;text-transform:uppercase}'
        + 'td.n,th.n{text-align:right;white-space:nowrap}'
        + 'td.big{font-size:15px;font-weight:700}'
        + 'td.mono{font-family:"Courier New",monospace}'
        + 'td.box{width:28px}'
        + '.sub{color:#555;font-size:10px}'
        + 'tfoot td{font-weight:700;background:#f4f4f4}'
        + 'thead{display:table-header-group}tr{page-break-inside:avoid}'
        + '</style></head><body>'
        + `<h1>${escapeHtml(title)}</h1>`
        + `<div class="meta">${metaHtml}</div>`
        + '<table><thead><tr>'
        + '<th class="n">#</th><th>Артикул</th><th>Предмет</th><th>Баркод</th>'
        + '<th class="n">Кол-во</th><th class="n">Остаток</th><th>✓</th>'
        + '</tr></thead><tbody>'
        + (rowsHtml || '<tr><td colspan="7">В поставке нет заданий</td></tr>')
        + '</tbody><tfoot><tr>'
        + `<td colspan="4">Позиций: ${escapeHtml(formatNumber(pick.positions_count, 0))}</td>`
        + `<td class="n">${escapeHtml(formatNumber(pick.total_qty, 0))}</td>`
        + '<td colspan="2"></td>'
        + '</tr></tfoot></table>'
        + '</body></html>';
}

/**
 * Открыть готовый HTML отдельным окном и отправить на печать.
 * false — окно заблокировано браузером (вызывающий показывает подсказку).
 */
export function printHtmlDocument(html: string): boolean {
    const win = window.open('', '_blank');
    if (!win) return false;
    win.document.write(
        html.replace(
            '</body>',
            '<script>window.onload=function(){window.focus();window.print();}<\/script></body>',
        ),
    );
    win.document.close();
    return true;
}

export function printStickerImages(files: string[], mime: string): boolean {
    const win = window.open('', '_blank');
    if (!win) return false;
    const imgs = files
        .map(f => `<div class="s"><img src="data:${mime};base64,${f.replace(/\s/g, '')}" /></div>`)
        .join('');
    win.document.write(
        '<!doctype html><html><head><meta charset="utf-8"><title>Стикеры WB FBS</title>'
        + '<style>@page{margin:4mm}body{margin:0;font-family:sans-serif}'
        + '.s{page-break-after:always;display:flex;align-items:center;justify-content:center}'
        + '.s:last-child{page-break-after:auto}img{max-width:100%}</style></head><body>'
        + imgs
        + '<script>window.onload=function(){window.focus();window.print();}<\/script>'
        + '</body></html>',
    );
    win.document.close();
    return true;
}

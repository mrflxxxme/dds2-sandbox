import type { FunnelDayRow, FunnelGroupRow, FunnelSkuRow } from '@/types/api';

/* ─── Каталог колонок воронки ──────────────────────────────────────────────
 * Один источник правды для всех группировок: что показываем, как считаем,
 * как красим и как сворачиваем в строку ИТОГО. Таблица, менеджер колонок и
 * Excel-экспорт читают отсюда — добавить метрику = добавить одну запись.
 * ─────────────────────────────────────────────────────────────────────── */

/** Строка любой группировки: день / артикул / бренд / категория / ярлык / склейка / размер.
 *  brand/subject вынесены из пересечения: в дневной строке они `string`, в товарной —
 *  `string | null`, и без этого пересечение типов схлопывает null. */
export type Row =
    & Omit<Partial<FunnelDayRow>, 'brand' | 'subject'>
    & Omit<Partial<FunnelSkuRow>, 'brand' | 'subject'>
    & Omit<Partial<FunnelGroupRow>, 'brand' | 'subject' | 'children'>
    & { brand?: string | null; subject?: string | null; children?: Row[] };

/** Единица измерения — бейдж справа от названия в менеджере колонок. */
export type Unit = '#' | '₽' | 'CR';

/** Куда «хорошо» для относительной окраски: больше среднего, меньше среднего, никак. */
export type Dir = 'up' | 'down' | 'none';

/* ─── Условное форматирование цифр ───────────────────────────────────────
 * Каждая колонка сравнивается сама с собой: цвет числа зависит от того, где
 * оно стоит среди соседей того же уровня. Низ колонки — тёмно-бордовый, верх —
 * тёмно-зелёный, середина нейтральная. Для колонок, где меньше значит лучше
 * (расход, ДРР, себестоимость), шкала переворачивается.
 * ─────────────────────────────────────────────────────────────────────── */

/** Режим показа величины в ячейке: только цвет цифр или плюс полоска под числом. */
export type Shading = 'text' | 'bar';

export const SHADING_OPTIONS: { key: Shading; label: string; hint: string }[] = [
    { key: 'text', label: 'Цифры', hint: 'только цвет цифр' },
    { key: 'bar', label: 'Полоски', hint: 'полоска величины под числом' },
];

const TEXT_SCALE: { t: number; c: [number, number, number] }[] = [
    { t: 0.00, c: [220, 38, 38] },   // ярко-красный — низ колонки
    { t: 0.25, c: [234, 88, 12] },
    { t: 0.50, c: [239, 143, 0] },   // оранжевый — середина
    { t: 0.75, c: [101, 163, 13] },
    { t: 1.00, c: [21, 128, 61] },   // зелёный — верх колонки
];

/** Цвет цифр по позиции значения в своей колонке [0..1]. */
export function textScaleColor(pos: number): string {
    const p = Math.max(0, Math.min(1, pos));
    let i = 0;
    while (i < TEXT_SCALE.length - 2 && p > TEXT_SCALE[i + 1].t) i++;
    const a = TEXT_SCALE[i], b = TEXT_SCALE[i + 1];
    const k = b.t === a.t ? 0 : (p - a.t) / (b.t - a.t);
    const mix = (x: number, y: number) => Math.round(x + (y - x) * k);
    return `rgb(${mix(a.c[0], b.c[0])}, ${mix(a.c[1], b.c[1])}, ${mix(a.c[2], b.c[2])})`;
}

/** Позиция значения по РАНГУ среди соседей, а не по расстоянию между min и max.
 *  Один выброс (день с рекламной вспышкой) иначе прижимает всю колонку ко дну
 *  шкалы и она выглядит одноцветной. Ранг даёт равномерную градацию. */
export function rankPos(sorted: number[], v: number): number {
    if (sorted.length < 2) return 0.5;
    let lo = 0, hi = sorted.length;
    while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (sorted[mid] < v) lo = mid + 1; else hi = mid;
    }
    // при повторах берём середину группы равных, чтобы одинаковые значения красились одинаково
    let hi2 = lo;
    while (hi2 < sorted.length && sorted[hi2] === v) hi2++;
    return ((lo + hi2 - 1) / 2) / (sorted.length - 1);
}

export interface ColumnDef {
    key: string;
    label: string;
    unit: Unit;
    title?: string;
    /** Сырое число: сортировка, ИТОГО, экспорт. null = «нет данных» («—»). */
    value: (r: Row) => number | null;
    /** Как считать в строке ИТОГО: sum — сложить, derive — вывести из суммы базовых полей. */
    total: 'sum' | ((t: Totals) => number | null);
    /** Показ значения; по умолчанию — число ru-RU / проценты / рубли по unit. */
    format?: (v: number, r: Row) => string;
    /** Светофор: цвет текста по значению. */
    color?: (v: number, r: Row) => string | undefined;
    bold?: boolean;
    /** Колонка есть только в расширенном режиме (остатки грузятся отдельным запросом). */
    extendedOnly?: boolean;
    /** Куда «хорошо» для относительной окраски (только CR-колонки). */
    dir?: Dir;
    /** Ширина колонки, px. Таблица рисуется с table-layout: fixed, иначе колонки
     *  меряются по содержимому и раскрытие строки с крупными числами двигает всю таблицу. */
    w?: number;
    /** Снимок на момент, а не поток: по дням складывать НЕЛЬЗЯ — в ИТОГО берём последний день.
     *  По товарам/брендам сумма корректна (остатки разных SKU складываются). */
    snapshot?: boolean;
}

export interface ColumnGroup {
    key: string;
    label: string;
    color: string;
    /** Ключи колонок в порядке показа. */
    cols: string[];
}

/* ─── Форматтеры ─────────────────────────────────────────────────────────── */

const nf = (n: number, digits = 0) => n.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits });
export const fmtInt = (n: number) => nf(Math.round(n));
export const fmtRub = (n: number) => `${nf(Math.round(n))} ₽`;
export const fmtRub2 = (n: number) => `${nf(n, 2)} ₽`;
export const fmtPct = (n: number) => `${n.toFixed(1)}%`;

const num = (v: unknown): number => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
};
/** Первое непустое из нескольких полей (day-строки дублируют метрики старыми именами). */
const pick = (r: Row, ...keys: (keyof Row)[]): number | null => {
    for (const k of keys) {
        const v = r[k];
        if (v != null) return num(v);
    }
    return null;
};

/* ─── Светофоры ──────────────────────────────────────────────────────────── */

const C = { good: '#16a34a', ok: '#65a30d', warn: '#ea580c', bad: '#dc2626', dim: '#9ca3af', ink: '#111827', accent: '#6366f1' };

const marginColor = (v: number) => (v > 20 ? C.good : v > 10 ? C.ok : v > 0 ? C.warn : C.bad);
const drrColor = (v: number) => (v <= 0 ? C.dim : v > 30 ? C.bad : v > 15 ? C.warn : C.good);
const sppColor = (v: number) => (v > 40 ? C.bad : v > 20 ? C.warn : C.good);
const ctrColor = (v: number) => (v > 5 ? C.good : v > 2 ? C.ink : C.warn);
const crColor = (hi: number, mid: number) => (v: number) => (v > hi ? C.good : v > mid ? C.ink : C.warn);
const profitColor = (v: number) => (v > 0 ? C.good : v < 0 ? C.bad : C.dim);
/** Прогноз исчерпания стока: <7 дн — красный, до 14 — оранжевый, до 29 — жёлтый. */
export const daysColor = (v: number) => (v >= 999 ? C.dim : v < 7 ? C.bad : v <= 14 ? C.warn : v <= 29 ? '#ca8a04' : C.good);

/* ─── Аккумулятор для строки ИТОГО ───────────────────────────────────────── */

export interface Totals {
    open_card: number; add_to_cart: number; orders_count: number; orders_sum: number; revenue: number;
    adv_sum: number; adv_views: number; adv_clicks: number; tax: number; profit: number;
    commission: number; cost_total: number;
    wb_stock_qty: number; wb_stock_cost: number; own_stock_cost: number;
    /** Взвешенные средние: сумма (ставка × вес) и сумма весов. */
    spp_w: number; spp_wt: number; buyout_w: number; buyout_wt: number;
}

export function emptyTotals(): Totals {
    return {
        open_card: 0, add_to_cart: 0, orders_count: 0, orders_sum: 0, revenue: 0,
        adv_sum: 0, adv_views: 0, adv_clicks: 0, tax: 0, profit: 0,
        commission: 0, cost_total: 0, wb_stock_qty: 0, wb_stock_cost: 0, own_stock_cost: 0,
        spp_w: 0, spp_wt: 0, buyout_w: 0, buyout_wt: 0,
    };
}

/** Складывает строки верхнего уровня (вложенные дети уже входят в родителя). */
export function accumulate(rows: Row[]): Totals {
    const t = emptyTotals();
    for (const r of rows) {
        const ordersSum = pick(r, 'orders_sum_rub', 'orders_sum') ?? 0;
        const orders = pick(r, 'orders_count', 'orders') ?? 0;
        t.open_card += pick(r, 'open_card', 'opens') ?? 0;
        t.add_to_cart += num(r.add_to_cart);
        t.orders_count += orders;
        t.orders_sum += ordersSum;
        t.revenue += pick(r, 'revenue', 'buyout_sum') ?? 0;
        t.adv_sum += pick(r, 'adv_sum', 'ad_sum') ?? 0;
        t.adv_views += pick(r, 'adv_views', 'ad_views') ?? 0;
        t.adv_clicks += pick(r, 'adv_clicks', 'ad_clicks') ?? 0;
        t.tax += num(r.tax);
        t.profit += num(r.profit);
        t.commission += num(r.commission);
        t.cost_total += num(r.cost_total);
        t.wb_stock_qty += num(r.wb_stock_qty);
        t.wb_stock_cost += num(r.wb_stock_cost);
        t.own_stock_cost += num(r.own_stock_cost);
        // СПП взвешиваем суммой заказов, выкуп — числом заказов
        if (r.spp_rate) { t.spp_w += num(r.spp_rate) * ordersSum; t.spp_wt += ordersSum; }
        const buyout = pick(r, 'buyout_percent', 'buyout_pct');
        if (buyout) { t.buyout_w += buyout * orders; t.buyout_wt += orders; }
    }
    return t;
}

const div = (a: number, b: number, mult = 1): number | null => (b > 0 ? (a / b) * mult : null);

/* ─── Колонки ────────────────────────────────────────────────────────────── */

export const COLUMNS: ColumnDef[] = [
    // Ключевые метрики
    { key: 'orders_count', label: 'Заказы', unit: '#', w: 76, dir: 'up', bold: true, value: r => pick(r, 'orders_count', 'orders'), total: 'sum', format: fmtInt },
    { key: 'wb_stock_qty', label: 'Остатки', unit: '#', w: 82, extendedOnly: true, snapshot: true, title: 'Остаток на складах WB, шт (включая товары в пути)', value: r => pick(r, 'wb_stock_qty'), total: 'sum', format: fmtInt },
    { key: 'revenue', label: 'Выручка', unit: '₽', w: 118, dir: 'up', bold: true, value: r => pick(r, 'revenue', 'buyout_sum'), total: 'sum', format: fmtRub, color: v => (v > 0 ? C.ink : C.bad) },
    { key: 'adv_sum', label: 'Рекл. расход', unit: '₽', w: 104, dir: 'down', value: r => pick(r, 'adv_sum', 'ad_sum'), total: 'sum', format: fmtRub, color: v => (v > 0 ? '#ea580c' : C.dim) },
    { key: 'profit', label: 'Прибыль', unit: '₽', w: 110, dir: 'up', bold: true, value: r => pick(r, 'profit'), total: 'sum', format: fmtRub, color: profitColor },
    { key: 'margin', label: 'Маржа', unit: 'CR', w: 62, dir: 'up', title: 'Прибыль / выручка', value: r => pick(r, 'margin'), total: t => div(t.profit, t.revenue, 100), format: fmtPct, color: marginColor },
    { key: 'drr', label: 'ДРР', unit: 'CR', w: 58, dir: 'down', title: 'Рекламный расход / сумма заказов', value: r => pick(r, 'drr'), total: t => div(t.adv_sum, t.orders_sum, 100), format: fmtPct, color: drrColor },
    { key: 'spp_rate', label: 'СПП', unit: 'CR', w: 58, dir: 'down', title: 'Скидка постоянного покупателя — скидка за счёт WB', value: r => pick(r, 'spp_rate'), total: t => div(t.spp_w, t.spp_wt), format: fmtPct, color: sppColor },
    { key: 'price_after_spp', label: 'После СПП', unit: '₽', w: 82, title: 'Средняя цена за вычетом СПП: ср. цена × (1 − СПП). Столько платит покупатель', value: r => { const p = pick(r, 'avg_price'); const s = pick(r, 'spp_rate'); return p == null ? null : p * (1 - (s ?? 0) / 100); }, total: t => { const p = div(t.orders_sum, t.orders_count); const s = div(t.spp_w, t.spp_wt) ?? 0; return p == null ? null : p * (1 - s / 100); }, format: fmtRub },
    { key: 'cost_per_unit', label: 'С/С ед.', unit: '₽', w: 74, dir: 'down', title: 'Себестоимость одной единицы: себестоимость продаж / заказы', value: r => div(num(r.cost_total), pick(r, 'orders_count', 'orders') ?? 0), total: t => div(t.cost_total, t.orders_count), format: fmtRub },

    // Воронка
    { key: 'adv_views', label: 'Показы', unit: '#', w: 98, dir: 'up', title: 'Рекламные показы (показов карточки в выдаче WB нам не отдаёт)', value: r => pick(r, 'adv_views', 'ad_views'), total: 'sum', format: fmtInt },
    { key: 'ctr', label: 'CTR', unit: 'CR', w: 58, dir: 'up', title: 'Клики / показы рекламы', value: r => pick(r, 'ctr'), total: t => div(t.adv_clicks, t.adv_views, 100), format: fmtPct, color: ctrColor },
    { key: 'open_card', label: 'Переходы', unit: '#', w: 98, dir: 'up', value: r => pick(r, 'open_card', 'opens'), total: 'sum', format: fmtInt },
    { key: 'add_to_cart_pct', label: 'CR1', unit: 'CR', w: 58, dir: 'up', title: 'CR1 — конверсия в корзину: корзины / переходы', value: r => pick(r, 'add_to_cart_pct'), total: t => div(t.add_to_cart, t.open_card, 100), format: fmtPct, color: crColor(8, 4) },
    { key: 'add_to_cart', label: 'Корзина', unit: '#', w: 76, dir: 'up', value: r => pick(r, 'add_to_cart'), total: 'sum', format: fmtInt },
    { key: 'cart_to_order_pct', label: 'CR2', unit: 'CR', w: 58, dir: 'up', title: 'CR2 — конверсия в заказ: заказы / корзины', value: r => pick(r, 'cart_to_order_pct'), total: t => div(t.orders_count, t.add_to_cart, 100), format: fmtPct, color: crColor(15, 8) },
    { key: 'orders_sum_rub', label: 'Сумма заказов', unit: '₽', w: 118, dir: 'up', value: r => pick(r, 'orders_sum_rub', 'orders_sum'), total: 'sum', format: fmtRub },
    { key: 'avg_price', label: 'Ср. цена', unit: '₽', w: 82, value: r => pick(r, 'avg_price'), total: t => div(t.orders_sum, t.orders_count), format: fmtRub },
    { key: 'buyout_percent', label: 'Выкуп', unit: 'CR', w: 68, dir: 'up', title: 'Доля заказов, которые фактически выкупили', value: r => pick(r, 'buyout_percent', 'buyout_pct'), total: t => div(t.buyout_w, t.buyout_wt), format: fmtPct },

    // Себестоимость продаж
    { key: 'cost_total', label: 'Себестоимость', unit: '₽', w: 118, dir: 'down', value: r => pick(r, 'cost_total'), total: 'sum', format: fmtRub },
    { key: 'tax', label: 'Налог', unit: '₽', w: 110, dir: 'down', value: r => pick(r, 'tax'), total: 'sum', format: fmtRub, color: () => '#6b7280' },

    // Остатки на FBO
    { key: 'wb_stock_cost', label: 'С/С остатков WB', unit: '₽', w: 122, extendedOnly: true, snapshot: true, title: 'Себестоимость остатков на складах WB', value: r => pick(r, 'wb_stock_cost'), total: 'sum', format: fmtRub },
    { key: 'own_stock_cost', label: 'С/С своих складов', unit: '₽', w: 132, extendedOnly: true, snapshot: true, title: 'Себестоимость остатков на наших складах: с резервом, без брака', value: r => pick(r, 'own_stock_cost'), total: 'sum', format: fmtRub },
    { key: 'total_stock_cost', label: 'С/С остатков всего', unit: '₽', w: 138, extendedOnly: true, snapshot: true, value: r => num(r.wb_stock_cost) + num(r.own_stock_cost), total: t => t.wb_stock_cost + t.own_stock_cost, format: fmtRub, bold: true },
    { key: 'stock_days_left', label: 'Хватит, дн', unit: '#', w: 82, extendedOnly: true, snapshot: true, title: 'Через сколько дней закончится сток при темпе продаж за последние 7 дней', value: r => pick(r, 'stock_days_left'), total: () => null, format: v => (v >= 999 ? '∞' : fmtInt(v)), color: daysColor, bold: true },

    // Реклама
    { key: 'cpm', label: 'CPM', unit: '₽', w: 95, dir: 'down', title: 'Стоимость 1000 показов', value: r => pick(r, 'cpm'), total: t => div(t.adv_sum, t.adv_views, 1000), format: fmtRub2 },
    { key: 'cpc', label: 'CPC', unit: '₽', w: 78, dir: 'down', title: 'Стоимость клика', value: r => pick(r, 'cpc'), total: t => div(t.adv_sum, t.adv_clicks), format: fmtRub2 },
    { key: 'cpl', label: 'CPL', unit: '₽', w: 78, dir: 'down', title: 'Стоимость корзины: рекламный расход / корзины', value: r => div(pick(r, 'adv_sum', 'ad_sum') ?? 0, num(r.add_to_cart)), total: t => div(t.adv_sum, t.add_to_cart), format: fmtRub2 },
    { key: 'cpo', label: 'CPO', unit: '₽', w: 82, dir: 'down', title: 'Стоимость заказа: рекламный расход / заказы', value: r => div(pick(r, 'adv_sum', 'ad_sum') ?? 0, pick(r, 'orders_count', 'orders') ?? 0), total: t => div(t.adv_sum, t.orders_count), format: fmtRub2 },
    { key: 'adv_clicks', label: 'Клики', unit: '#', w: 90, dir: 'up', value: r => pick(r, 'adv_clicks', 'ad_clicks'), total: 'sum', format: fmtInt },

    // Комиссия
    { key: 'commission_rate', label: 'Комиссия, %', unit: 'CR', w: 88, dir: 'down', title: 'Расходы WB: комиссия + логистика + штрафы + хранение', value: r => pick(r, 'commission_rate'), total: t => div(t.commission, t.revenue, 100), format: fmtPct, color: v => (v > 0 ? C.accent : C.dim) },
    { key: 'commission', label: 'Комиссия, руб', unit: '₽', w: 110, dir: 'down', value: r => pick(r, 'commission'), total: 'sum', format: fmtRub, color: v => (v > 0 ? C.accent : C.dim) },
];

export const COLUMN_BY_KEY: Record<string, ColumnDef> = Object.fromEntries(COLUMNS.map(c => [c.key, c]));

/** Раскладка по умолчанию — повторяет группы референса, где у нас есть данные. */
export const DEFAULT_GROUPS: ColumnGroup[] = [
    { key: 'key', label: 'Ключевые метрики', color: '#7c3aed', cols: ['orders_count', 'wb_stock_qty', 'revenue', 'adv_sum', 'profit', 'margin', 'drr', 'spp_rate', 'price_after_spp', 'cost_per_unit'] },
    { key: 'funnel', label: 'Воронка', color: '#f59e0b', cols: ['adv_views', 'ctr', 'open_card', 'add_to_cart_pct', 'add_to_cart', 'cart_to_order_pct', 'orders_sum_rub', 'avg_price', 'buyout_percent'] },
    { key: 'cost', label: 'Себестоимость продаж', color: '#6366f1', cols: ['cost_total', 'tax'] },
    { key: 'fbo', label: 'Остатки на FBO', color: '#8b5cf6', cols: ['wb_stock_cost', 'own_stock_cost', 'total_stock_cost', 'stock_days_left'] },
    { key: 'ads', label: 'Реклама', color: '#f97316', cols: ['cpm', 'cpc', 'cpl', 'cpo', 'adv_clicks'] },
    { key: 'commission', label: 'Комиссия', color: '#ec4899', cols: ['commission_rate', 'commission'] },
];

/** Палитра для новых групп, которые создаёт пользователь. */
export const GROUP_COLORS = ['#16a34a', '#f59e0b', '#6366f1', '#8b5cf6', '#f97316', '#ec4899', '#0ea5e9', '#14b8a6'];

/** Полный снимок настройки колонок: порядок групп, состав и что включено. */
export interface ColumnLayout {
    groups: ColumnGroup[];
    /** Колонки вне групп — показываются последними, без цветной полосы. */
    ungrouped: string[];
    /** Ключи включённых колонок. */
    visible: string[];
}

export function defaultLayout(): ColumnLayout {
    const grouped = DEFAULT_GROUPS.flatMap(g => g.cols);
    return {
        groups: DEFAULT_GROUPS.map(g => ({ ...g, cols: [...g.cols] })),
        ungrouped: COLUMNS.map(c => c.key).filter(k => !grouped.includes(k)),
        visible: grouped.filter(k => !COLUMN_BY_KEY[k]?.extendedOnly),
    };
}

/** Раскладка из localStorage: чинит рассинхрон, если каталог колонок изменился. */
export function reconcileLayout(saved: ColumnLayout | null): ColumnLayout {
    const base = defaultLayout();
    if (!saved?.groups?.length) return base;
    const known = new Set(COLUMNS.map(c => c.key));
    const LEGACY_KEY_COLOR = '#16a34a';   // «Ключевые метрики» были зелёными до 30.07.2026
    const groups = saved.groups
        .filter(g => g && typeof g.key === 'string')
        .map(g => ({
            key: g.key,
            label: g.label,
            color: g.key === 'key' && g.color === LEGACY_KEY_COLOR ? '#7c3aed' : g.color,
            cols: (g.cols || []).filter(k => known.has(k)),
        }));
    const placed = new Set(groups.flatMap(g => g.cols));
    const ungrouped = (saved.ungrouped || []).filter(k => known.has(k) && !placed.has(k));
    placed.forEach(k => k);
    // Колонки, появившиеся в каталоге после сохранения, кладём в «без группы» выключенными
    const fresh = COLUMNS.map(c => c.key).filter(k => !placed.has(k) && !ungrouped.includes(k));
    return {
        groups,
        ungrouped: [...ungrouped, ...fresh],
        visible: (saved.visible || []).filter(k => known.has(k)),
    };
}

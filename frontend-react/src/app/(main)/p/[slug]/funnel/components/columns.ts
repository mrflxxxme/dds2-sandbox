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

/* ─── Подсветка значений ─────────────────────────────────────────────────
 * Цвет цифры задаёт сама метрика (светофоры ниже) — как было в прежнем разделе.
 * Ранговая раскраска ВСЕХ цифр отвергнута 31.07.2026: таблица рябила.
 * Величину при желании показывает полоска под числом — она и считает ранг.
 * ─────────────────────────────────────────────────────────────────────── */

/** Режим показа величины в ячейке: только цифры или плюс полоска под числом. */
export type Shading = 'text' | 'bar';

export const SHADING_OPTIONS: { key: Shading; label: string; hint: string }[] = [
    { key: 'text', label: 'Цифры', hint: 'только цифры со своим светофором' },
    { key: 'bar', label: 'Полоски', hint: 'полоска величины под числом' },
];

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
    /** Мягкая заливка ячейки по значению — как в прежнем разделе (прибыль/убыток,
     *  крупный расход, всплеск ДРР, «большие» дни по переходам/корзинам/заказам). */
    bg?: (v: number, r: Row) => string | undefined;
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

const marginColor = (v: number) => (v > 20 ? C.good : v > 0 ? C.ok : C.bad);

/* Фоновые подсветки прежнего раздела. Пороги абсолютные и подобраны под масштаб
 * дня: на строках артикулов такие значения не набираются, и заливки нет — ровно
 * как было раньше. */
const BG = { warm: '#fffbeb', blue: '#eff6ff', green: '#f0fdf4', violet: '#faf5ff', red: '#fef2f2' };
const bgOver = (limit: number, color: string) => (v: number) => (v > limit ? color : undefined);
const profitBg = (v: number) => (v > 0 ? BG.green : v < 0 ? BG.red : undefined);
const drrColor = (v: number) => (v <= 0 ? C.dim : v > 30 ? C.bad : v > 15 ? C.warn : C.good);
const advColor = (v: number) => (v > 400_000 ? C.bad : v > 100_000 ? '#f59e0b' : v > 0 ? '#f97316' : C.dim);
const sppColor = (v: number) => (v > 40 ? C.bad : v > 20 ? C.warn : C.good);
const ctrColor = (v: number) => (v > 5 ? C.good : v > 2 ? C.ink : C.warn);
const profitColor = (v: number) => (v > 0 ? C.good : v < 0 ? C.bad : C.dim);
/** Прогноз исчерпания стока: <7 дн — красный, до 14 — оранжевый, до 29 — жёлтый. */
export const daysColor = (v: number) => (v >= 999 ? C.dim : v < 7 ? C.bad : v <= 14 ? C.warn : v <= 29 ? '#ca8a04' : C.good);

/* ─── Аккумулятор для строки ИТОГО ───────────────────────────────────────── */

export interface Totals {
    open_card: number; add_to_cart: number; orders_count: number; orders_sum: number; revenue: number;
    adv_sum: number; adv_views: number; adv_clicks: number; tax: number; nds: number; acquiring: number; profit: number;
    commission: number; cost_total: number;
    wb_stock_qty: number; wb_stock_cost: number; own_stock_cost: number;
    /** Взвешенные средние: сумма (ставка × вес) и сумма весов. */
    spp_w: number; spp_wt: number; buyout_w: number; buyout_wt: number;
}

export function emptyTotals(): Totals {
    return {
        open_card: 0, add_to_cart: 0, orders_count: 0, orders_sum: 0, revenue: 0,
        adv_sum: 0, adv_views: 0, adv_clicks: 0, tax: 0, nds: 0, acquiring: 0, profit: 0,
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
        t.nds += num(r.nds);
        t.acquiring += num(r.acquiring);
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
    { key: 'orders_count', label: 'Заказы', unit: '#', w: 76, dir: 'up', bold: true, value: r => pick(r, 'orders_count', 'orders'), total: 'sum', format: fmtInt, bg: bgOver(2_500, BG.green) },
    { key: 'wb_stock_qty', label: 'Остатки', unit: '#', w: 82, extendedOnly: true, snapshot: true, title: 'Остаток на складах WB, шт (включая товары в пути)', value: r => pick(r, 'wb_stock_qty'), total: 'sum', format: fmtInt },
    { key: 'revenue', label: 'Выручка', unit: '₽', w: 118, dir: 'up', bold: true, value: r => pick(r, 'revenue', 'buyout_sum'), total: 'sum', format: fmtRub, color: v => (v > 0 ? C.ink : C.bad) },
    { key: 'adv_sum', label: 'Расходы', unit: '₽', w: 110, dir: 'down', title: 'Расход на внутреннюю рекламу', value: r => pick(r, 'adv_sum', 'ad_sum'), total: 'sum', format: fmtRub, color: advColor, bg: bgOver(400_000, BG.red) },
    { key: 'profit', label: 'Прибыль', unit: '₽', w: 110, dir: 'up', bold: true, value: r => pick(r, 'profit'), total: 'sum', format: fmtRub, color: profitColor, bg: profitBg },
    { key: 'margin', label: 'Маржа', unit: 'CR', w: 62, dir: 'up', title: 'Прибыль / выручка', value: r => pick(r, 'margin'), total: t => div(t.profit, t.revenue, 100), format: fmtPct, color: marginColor },
    { key: 'drr', label: 'ДРР', unit: 'CR', w: 58, dir: 'down', title: 'Рекламный расход / сумма заказов', value: r => pick(r, 'drr'), total: t => div(t.adv_sum, t.orders_sum, 100), format: fmtPct, color: drrColor, bg: bgOver(30, BG.red) },
    { key: 'spp_rate', label: 'СПП', unit: 'CR', w: 58, dir: 'down', title: 'Скидка постоянного покупателя — скидка за счёт WB', value: r => pick(r, 'spp_rate'), total: t => div(t.spp_w, t.spp_wt), format: fmtPct, color: sppColor },
    { key: 'price_after_spp', label: 'После СПП', unit: '₽', w: 82, title: 'Средняя цена за вычетом СПП: ср. цена × (1 − СПП). Столько платит покупатель', value: r => { const p = pick(r, 'avg_price'); const s = pick(r, 'spp_rate'); return p == null ? null : p * (1 - (s ?? 0) / 100); }, total: t => { const p = div(t.orders_sum, t.orders_count); const s = div(t.spp_w, t.spp_wt) ?? 0; return p == null ? null : p * (1 - s / 100); }, format: fmtRub },
    { key: 'cost_per_unit', label: 'С/С ед.', unit: '₽', w: 74, dir: 'down', title: 'Себестоимость одной единицы: себестоимость продаж / заказы', value: r => div(num(r.cost_total), pick(r, 'orders_count', 'orders') ?? 0), total: t => div(t.cost_total, t.orders_count), format: fmtRub },

    // Воронка
    { key: 'adv_views', label: 'Просмотры', unit: '#', w: 104, dir: 'up', title: 'Рекламные показы (показов карточки в выдаче WB нам не отдаёт)', value: r => pick(r, 'adv_views', 'ad_views'), total: 'sum', format: fmtInt },
    { key: 'ctr', label: 'CTR', unit: 'CR', w: 58, dir: 'up', title: 'Клики / показы рекламы', value: r => pick(r, 'ctr'), total: t => div(t.adv_clicks, t.adv_views, 100), format: fmtPct, color: ctrColor },
    { key: 'open_card', label: 'Переходы', unit: '#', w: 98, dir: 'up', value: r => pick(r, 'open_card', 'opens'), total: 'sum', format: fmtInt, bg: bgOver(300_000, BG.warm) },
    { key: 'add_to_cart_pct', label: 'В корзину', unit: 'CR', w: 84, dir: 'up', title: 'CR1 — конверсия в корзину: корзины / переходы', value: r => pick(r, 'add_to_cart_pct'), total: t => div(t.add_to_cart, t.open_card, 100), format: fmtPct },
    { key: 'add_to_cart', label: 'Корзины', unit: '#', w: 82, dir: 'up', value: r => pick(r, 'add_to_cart'), total: 'sum', format: fmtInt, bg: bgOver(15_000, BG.blue) },
    { key: 'cart_to_order_pct', label: 'В заказ', unit: 'CR', w: 76, dir: 'up', title: 'CR2 — конверсия в заказ: заказы / корзины', value: r => pick(r, 'cart_to_order_pct'), total: t => div(t.orders_count, t.add_to_cart, 100), format: fmtPct },
    { key: 'orders_sum_rub', label: 'Сумма', unit: '₽', w: 118, dir: 'up', value: r => pick(r, 'orders_sum_rub', 'orders_sum'), total: 'sum', format: fmtRub, bg: bgOver(5_000_000, BG.violet) },
    { key: 'avg_price', label: 'Ср. цена', unit: '₽', w: 82, value: r => pick(r, 'avg_price'), total: t => div(t.orders_sum, t.orders_count), format: fmtRub },
    { key: 'buyout_percent', label: 'Выкуп', unit: 'CR', w: 68, dir: 'up', title: 'Доля заказов, которые фактически выкупили', value: r => pick(r, 'buyout_percent', 'buyout_pct'), total: t => div(t.buyout_w, t.buyout_wt), format: fmtPct },

    // Себестоимость продаж
    { key: 'cost_total', label: 'Себест.', unit: '₽', w: 112, dir: 'down', value: r => pick(r, 'cost_total'), total: 'sum', format: fmtRub },
    { key: 'tax', label: 'Налог', unit: '₽', w: 110, dir: 'down', value: r => pick(r, 'tax'), total: 'sum', format: fmtRub, color: () => '#6b7280' },
    { key: 'acquiring', label: 'Эквайринг', unit: '₽', w: 110, dir: 'down', title: 'Эквайринг WB (комиссия за организацию платежей). Уже входит в «Расход WB» — это его составляющая, а не добавка', value: r => pick(r, 'acquiring'), total: 'sum', format: fmtRub, color: v => (v > 0 ? '#6366f1' : '#9ca3af') },
    { key: 'acquiring_rate', label: 'Эквайринг, %', unit: 'CR', w: 104, dir: 'down', title: 'Эквайринг в % от выручки', value: r => { const a = pick(r, 'acquiring'); const rev = pick(r, 'revenue', 'buyout_sum'); return a == null || !rev ? null : (a / rev) * 100; }, total: t => div(t.acquiring, t.revenue, 100), format: fmtPct, color: v => (v > 0 ? '#6366f1' : '#9ca3af') },
    { key: 'nds', label: 'НДС', unit: '₽', w: 104, dir: 'down', title: 'НДС, выделенный из налога: цена после СПП × ставка / (1 + ставка)', value: r => pick(r, 'nds'), total: 'sum', format: fmtRub, color: () => '#6b7280' },

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

    // Расходы WB (в старом разделе — «Расх. WB %» и «Комиссия ₽»)
    { key: 'commission_rate', label: 'Расход WB, %', unit: 'CR', w: 100, dir: 'down', title: 'Расходы WB: комиссия + логистика + штрафы + хранение, в % от выручки', value: r => pick(r, 'commission_rate'), total: t => div(t.commission, t.revenue, 100), format: fmtPct, color: v => (v > 0 ? C.accent : C.dim) },
    { key: 'commission', label: 'Расход WB, ₽', unit: '₽', w: 112, dir: 'down', title: 'Расходы WB в рублях: комиссия + логистика + штрафы + хранение', value: r => pick(r, 'commission'), total: 'sum', format: fmtRub, color: v => (v > 0 ? C.accent : C.dim) },
];

export const COLUMN_BY_KEY: Record<string, ColumnDef> = Object.fromEntries(COLUMNS.map(c => [c.key, c]));

/** Преднастройка «Ключевые метрики» — набор Дениса от 31.07.2026.
 *  Порядок колонок остаётся раскладочный, меняется только состав видимых. */
export const KEY_METRICS: string[] = [
    'orders_count',      // Заказы
    'wb_stock_qty',      // Остатки (складская колонка: в товарных группировках)
    'orders_sum_rub',    // Сумма
    'revenue',           // Выручка
    'adv_sum',           // Расходы
    'profit',            // Прибыль
    'margin',            // Маржа
    'commission_rate',   // Расход WB, %
    'drr',               // ДРР
    'spp_rate',          // СПП
    'price_after_spp',   // Цена после СПП
    'cost_per_unit',     // С/С ед.
];

/** Раскладка по умолчанию — порядок и группы прежнего раздела (решение Дениса 31.07.2026).
 *  Метрики, которых в старом разделе не было (С/С ед., После СПП, CPL, CPO), стоят рядом
 *  со своими родственниками, а не отдельной группой. */
export const DEFAULT_GROUPS: ColumnGroup[] = [
    // Первым блоком — ключевые метрики (набор и порядок Дениса от 31.07.2026):
    // менеджер видит главное, не листая 30 колонок. Каждая метрика живёт в одной
    // группе, поэтому в блоках ниже этих колонок уже нет.
    { key: 'key', label: 'Ключевые метрики', color: '#7c3aed', cols: KEY_METRICS },
    // Цвета соседних групп разведены по тону: два оранжевых блока подряд сливались в один
    { key: 'funnel', label: 'Воронка', color: '#f59e0b', cols: ['open_card', 'add_to_cart'] },
    { key: 'ads', label: 'Внутренняя реклама', color: '#0ea5e9', cols: ['adv_views', 'adv_clicks', 'ctr', 'cpc', 'cpm', 'cpl', 'cpo'] },
    { key: 'fin', label: 'Финансы', color: '#10b981', cols: ['cost_total', 'buyout_percent', 'tax', 'nds', 'acquiring', 'acquiring_rate', 'commission', 'avg_price'] },
    { key: 'conv', label: 'Конверсия', color: '#ec4899', cols: ['add_to_cart_pct', 'cart_to_order_pct'] },
    { key: 'fbo', label: 'Остатки на FBO', color: '#8b5cf6', cols: ['wb_stock_cost', 'own_stock_cost', 'total_stock_cost', 'stock_days_left'] },
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

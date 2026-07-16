// Эффективность запроса (кластера) — ЧИСЛОМ (%), как ДРР.
// Чистый модуль без React — тестируется юнитами (см. __tests__/lib/clusterEff.test.ts).
//
// Идея: единый ДРР на все строки. Есть заказы → ФАКТИЧЕСКИЙ ДРР (как столбец ДРР).
// Нет заказов → ПРОГНОЗНЫЙ ДРР: оцениваем заказы из воронки по средним конверсиям
// кампании (корзины × cart→order, иначе клики × click→order) и считаем ДРР по прогнозу.
// Так виден «на сколько хорош» одним числом, а цвет — тот же drrColor (ниже = лучше).
import type { SearchCluster } from '@/types/api';

const n = (x: unknown): number => Number(x) || 0;

/** Средние конверсии кампании + AOV — база для прогноза беззаказных запросов. */
export type EffCtx = { targetDrr: number; aov: number; avgCr: number; avgCr2: number };

export type EffResult = { value: number | null; projected: boolean };

/** Эффективный ДРР кластера (%). projected=true — прогноз (заказов ещё нет). */
export function effectiveDrr(c: SearchCluster, ctx: EffCtx): EffResult {
    if (c.locked) return { value: null, projected: false };  // <100 показов — не судим
    const orders = n(c.orders), spend = n(c.spend);
    if (orders >= 1) {
        // Фактический ДРР (как столбец ДРР). Нет AOV → бэкенд отдал null.
        return { value: c.drr == null ? null : Number(c.drr), projected: false };
    }
    // Прогноз заказов из самого глубокого доступного сигнала воронки
    const atbs = n(c.atbs), clicks = n(c.clicks);
    let estOrders = 0;
    if (atbs > 0 && ctx.avgCr2 > 0) estOrders = atbs * (ctx.avgCr2 / 100);        // корзины × cart→order
    else if (clicks > 0 && ctx.avgCr > 0) estOrders = clicks * (ctx.avgCr / 100); // клики × click→order
    if (estOrders <= 0 || ctx.aov <= 0 || spend <= 0) return { value: null, projected: true };
    return { value: (spend / (estOrders * ctx.aov)) * 100, projected: true };
}

// ─── Рекомендация ставки ──────────────────────────────────────────────────────
// Стратегия менеджера: цель ДРР = T% (обычно 8). Пока ср. позиция рекламы не топ-1,
// есть смысл поднимать ставку (растёт CTR по запросу). Шаг — до СЛЕДУЮЩЕГО ЦЕЛОГО
// ДРР (1,3%→2%→3%…→T), чтобы не прыгать ×N разом. Потолок подъёма — топ-1 или ДРР=T.
//
// ЯКОРЬ формулы — фактически оплаченная CPM (`cpm` = расход/показы), а НЕ текущая ставка.
// ДРР породил именно этот операционный CPM; ставка же могла быть только что изменена, а стата
// WB отстаёт. Если брать якорем текущую ставку, то после применения рекомендации новая ставка
// становится базой и снова множится на T/d при неизменном (устаревшем) ДРР → разгон: вверх до
// десятков тысяч, вниз до пола WB. Оплаченная CPM историческая и при смене ставки НЕ меняется,
// поэтому рекомендация сходится за одно применение (дальше — ждём свежую стату).
// Формула (консервативно, при ~постоянной выручке): ставка_шага = оплач_CPM × (цель_шага / ДРР).
// Направление (up/down) и «двигать уже некуда» решаем относительно ТЕКУЩЕЙ ставки.
/** projected=true — рекомендация по ПРОГНОЗНОМУ ДРР (у фразы нет заказов); спекулятивна. */
export type BidRec = { bid: number; dir: 'up' | 'down'; stepDrr: number; ceilBid: number; projected: boolean };

/** Минимальная ставка CPM поискового аукциона WB, ₽. Точного значения API не отдаёт
 *  (`/adv/v1/cpm` → 404), это документированный пол аукциона. Ниже WB ставку не примет
 *  («no less than X»), поэтому рекомендации к нему клампятся. Переопределяется opts.minBid. */
export const WB_MIN_CPM = 150;

/** Минимум заказов, при котором доверяем ДРР для ПОДЪЁМА ставки. ДРР по 1 заказу
 *  статистически шумный, а подъём = рост расхода → на тонких данных не поднимаем.
 *  Снижение (высокий ДРР) порогом не гейтится: резать расход безопаснее. */
export const REC_MIN_ORDERS_UP = 3;

/** Минимум корзин для ПОДЪЁМА по ПРОГНОЗНОМУ ДРР (беззаказные фразы). Прогноз строится
 *  на СРЕДНИХ конверсиях кампании — у конкретной фразы реальная конверсия может быть 0.
 *  Поднимать ставку на 0 заказов рискованно, поэтому требуем реальный сигнал воронки
 *  (корзины). Клик-сигнал (atbs=0) для подъёма недостаточен. Снижение — без порога. */
export const REC_MIN_CARTS_UP = 3;

/** Общее ядро шага ставки к целевому ДРР. `anchor` — оплаченная CPM, породившая ДРР (см.
 *  шапку раздела): по ней считаем целевую ставку. `current` — текущая ставка: по ней решаем
 *  направление и «двигать уже некуда» (если целевая не по ту сторону от текущей — рекомендации
 *  нет, разгон невозможен). `canUp` гейтит только ПОДЪЁМ (снижение всегда разрешено — резать
 *  расход безопаснее). Формула: ставка_шага = anchor × (цель_шага / ДРР). */
function buildRec(anchor: number, current: number, d: number, T: number, minBid: number, avgPos: number, canUp: boolean, projected: boolean): BidRec | null {
    if (!isFinite(anchor) || anchor <= 0 || !isFinite(current) || current <= 0 || !isFinite(d) || d <= 0) return null;
    const curR = Math.round(current);
    const clamp = (b: number) => (minBid > 0 ? Math.max(b, minBid) : b);  // WB не примет ниже минимума
    const ceilBid = Math.round(clamp(anchor * (T / d)));  // потолок: ставка при ДРР = T
    if (d < T) {
        // Подъём — только если не топ-1 (ср. позиция > 1,2; дневное среднее почти не бывает ровно 1)
        if (avgPos > 0 && avgPos <= 1.2) return null;
        if (!canUp) return null;   // недостаточно данных для подъёма (заказы/корзины)
        const step = Math.min(Math.floor(d) + 1, T);   // следующее целое ДРР, но не выше T
        if (step <= d) return null;
        const bid = Math.round(clamp(anchor * (step / d)));
        if (bid <= curR) return null;   // шаг уже не выше текущей ставки — поднимать некуда (ждём свежую стату)
        return { bid, dir: 'up', stepDrr: step, ceilBid, projected };
    }
    if (d > T) {
        const step = Math.max(Math.ceil(d) - 1, T);     // предыдущее целое ДРР, но не ниже T
        if (step >= d) return null;
        const bid = Math.round(clamp(anchor * (step / d)));
        if (bid >= curR) return null;   // шаг уже не ниже текущей ставки — снижать некуда (кламп к полу/ждём стату)
        return { bid, dir: 'down', stepDrr: step, ceilBid, projected };
    }
    return null;  // ДРР ≈ T — держать
}

/** База ставки: своя ставка фразы; нет своей → базовая ставка кампании. */
function bidBase(c: SearchCluster, defaultBid: number | null): number {
    return c.bid != null ? Number(c.bid) : (defaultBid != null ? Number(defaultBid) : NaN);
}

/** Якорь формулы — оплаченная CPM (расход/показы), породившая ДРР: историческая, при смене
 *  ставки не двигается, поэтому рекомендация не разгоняется. Нет CPM (0 показов/расхода) →
 *  фолбэк на текущую ставку (тогда якорь = текущая ставка, как было до фикса). */
function recAnchor(c: SearchCluster, defaultBid: number | null): number {
    const cpm = n(c.cpm);
    return cpm > 0 ? cpm : bidBase(c, defaultBid);
}

/** Рекомендованная ставка на один шаг ДРР по РЕАЛЬНОМУ ДРР (заказы ≥ 1). null — рекомендации
 *  нет (нет заказов, уже топ-1 при низком ДРР, мало заказов для подъёма, кламп свёл к базе,
 *  ДРР ровно на цели). Для беззаказных фраз — см. recommendProjectedBid. */
export function recommendBid(
    c: SearchCluster,
    opts: { targetDrr: number; defaultBid: number | null; minOrdersUp?: number; minBid?: number },
): BidRec | null {
    const T = opts.targetDrr > 0 ? opts.targetDrr : 8;
    const minOrdersUp = opts.minOrdersUp ?? REC_MIN_ORDERS_UP;
    const minBid = opts.minBid ?? WB_MIN_CPM;
    const orders = n(c.orders);
    if (orders < 1 || c.drr == null) return null;   // нужен реальный ДРР (заказы)
    const d = Number(c.drr);
    if (!isFinite(d) || d <= 0) return null;
    return buildRec(recAnchor(c, opts.defaultBid), bidBase(c, opts.defaultBid), d, T, minBid, n(c.avg_pos), orders >= minOrdersUp, false);
}

/** Рекомендация по ПРОГНОЗНОМУ ДРР для БЕЗЗАКАЗНОЙ фразы (projected DRR из effectiveDrr).
 *  Асимметрия: снижение при высоком прогнозном ДРР — свободно (резать вероятный слив);
 *  подъём — только при достаточном сигнале воронки (корзины ≥ minCartsUp), т.к. прогноз
 *  на средних конверсиях кампании может завышать перспективность фразы с 0 заказов.
 *  Возвращает projected=true. Для фраз с заказами → null (это путь recommendBid). */
export function recommendProjectedBid(
    c: SearchCluster,
    projectedDrr: number,
    opts: { targetDrr: number; defaultBid: number | null; minCartsUp?: number; minBid?: number },
): BidRec | null {
    if (n(c.orders) >= 1) return null;   // беззаказные only
    const T = opts.targetDrr > 0 ? opts.targetDrr : 8;
    const minCartsUp = opts.minCartsUp ?? REC_MIN_CARTS_UP;
    const minBid = opts.minBid ?? WB_MIN_CPM;
    const pd = Number(projectedDrr);
    if (!isFinite(pd) || pd <= 0) return null;
    return buildRec(recAnchor(c, opts.defaultBid), bidBase(c, opts.defaultBid), pd, T, minBid, n(c.avg_pos), n(c.atbs) >= minCartsUp, true);
}

/** Средние конверсии кампании из полного набора кластеров (для EffCtx). */
export function campaignEffAverages(clusters: SearchCluster[], targetDrr: number, aov: number): EffCtx {
    let clicks = 0, atbs = 0, orders = 0;
    for (const c of clusters) { clicks += n(c.clicks); atbs += n(c.atbs); orders += n(c.orders); }
    return {
        targetDrr,
        aov,
        avgCr: clicks ? (orders / clicks) * 100 : 0,   // клик→заказ, %
        avgCr2: atbs ? (orders / atbs) * 100 : 0,      // корзина→заказ, %
    };
}

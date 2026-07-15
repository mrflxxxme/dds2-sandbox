/**
 * «Срочно к отправке» — чистая логика блока на черновике сборки.
 *
 * Источник срочности — Аналитика остатков (`getStockAnalytics`, mode
 * `wb_assembly_transit`): запас в днях уже учитывает WB + созданные сборки +
 * путь, т.е. черновик — единственное, что ещё может ускорить пополнение.
 * Пороги корзин зеркалят светофор Аналитики: 0 / <7 / 7–14.
 *
 * Потери = ₽/день × разрыв, где разрыв = плечо поставки − запас (дни, ≥0):
 * сколько дней SKU простоит в нуле, если отправить сборку прямо сейчас.
 */
import type { StockAnalyticsArticle } from '@/types/api';

export type UrgencyBucket = 'zero' | 'critical' | 'danger';

export interface UrgentShipRow {
    nm_id: number;
    vendor: string;
    subject: string;
    /** Запас в днях (кламп ≥0). */
    daysLeft: number;
    /** Средняя реализация, ₽/день. */
    dailyRevenue: number;
    /** Дней простоя в нуле до прихода поставки. */
    gapDays: number;
    /** Потери реализации за разрыв, ₽. */
    loss: number;
    /** Локализация артикула, % (null — нет данных). */
    locPct: number | null;
    /** Штук в черновике (0 для сегмента «вне черновика»). */
    draftQty: number;
    /** Свободный остаток на ФФ — есть чем пополнить черновик. */
    ffFree: number;
    bucket: UrgencyBucket;
}

export interface UrgentShipSummary {
    /** Срочные SKU, уже лежащие в черновике, — двигаем сборку. */
    inDraft: UrgentShipRow[];
    /** Срочные SKU вне черновика со свободным ФФ-стоком — кандидаты на добавление. */
    missing: UrgentShipRow[];
    totalLoss: number;
    missingLoss: number;
    zeroCount: number;
    criticalCount: number;
    dangerCount: number;
}

const BUCKET_ORDER: Record<UrgencyBucket, number> = { zero: 0, critical: 1, danger: 2 };

/** Корзина срочности по запасу в днях; null — не срочный (>14 дн). */
export function classifyUrgency(daysLeft: number): UrgencyBucket | null {
    if (daysLeft <= 0) return 'zero';
    if (daysLeft < 7) return 'critical';
    if (daysLeft <= 14) return 'danger';
    return null;
}

export function buildUrgentShip(params: {
    articles: StockAnalyticsArticle[];
    /** nm_id → штук в строках черновика (Σ tgt). */
    draftQtyByNm: ReadonlyMap<number, number>;
    /** nm_id → % локализации (getLocalizationSkus). */
    locPctByNm?: ReadonlyMap<number, number>;
    /** Плечо поставки в днях (сборка ФФ + доставка до WB). */
    leadDays: number;
    /** Окно, за которое собрана revenue_bdr (trend_days запроса). */
    trendDays?: number;
}): UrgentShipSummary {
    const { articles, draftQtyByNm, locPctByNm, leadDays, trendDays = 7 } = params;

    const inDraft: UrgentShipRow[] = [];
    const missing: UrgentShipRow[] = [];

    for (const a of articles) {
        const bucket = classifyUrgency(a.days_left);
        if (!bucket) continue;

        const daysLeft = Math.max(0, a.days_left);
        const dailyRevenue = (a.revenue_bdr ?? 0) / trendDays;
        const gapDays = Math.max(0, leadDays - daysLeft);
        const draftQty = draftQtyByNm.get(a.nm_id) ?? 0;
        const ffFree = a.stocks_rf ?? 0;
        const row: UrgentShipRow = {
            nm_id: a.nm_id,
            vendor: a.vendor_code || `nm ${a.nm_id}`,
            subject: a.subject || '',
            daysLeft,
            dailyRevenue: Math.round(dailyRevenue * 100) / 100,
            gapDays: Math.round(gapDays * 10) / 10,
            loss: Math.round(dailyRevenue * gapDays),
            locPct: locPctByNm?.get(a.nm_id) ?? null,
            draftQty,
            ffFree,
            bucket,
        };

        if (draftQty > 0) inDraft.push(row);
        // Вне черновика показываем только то, что реально можно доложить (есть ФФ-сток).
        else if (ffFree > 0) missing.push(row);
    }

    const byUrgency = (x: UrgentShipRow, y: UrgentShipRow) =>
        BUCKET_ORDER[x.bucket] - BUCKET_ORDER[y.bucket]
        || y.loss - x.loss
        || y.dailyRevenue - x.dailyRevenue;
    inDraft.sort(byUrgency);
    missing.sort(byUrgency);

    return {
        inDraft,
        missing,
        totalLoss: inDraft.reduce((s, r) => s + r.loss, 0),
        missingLoss: missing.reduce((s, r) => s + r.loss, 0),
        zeroCount: inDraft.filter(r => r.bucket === 'zero').length,
        criticalCount: inDraft.filter(r => r.bucket === 'critical').length,
        dangerCount: inDraft.filter(r => r.bucket === 'danger').length,
    };
}

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
    /** Штук в черновике ВСЕГО (строки + предбронь; 0 для сегмента «вне черновика»). */
    draftQty: number;
    /** Из них в ПРЕДБРОНИ (ждут целую паллету/приёмку — в заявки пока не едут). */
    prebookQty: number;
    /** Остаток на складах WB, шт. */
    wbStock: number;
    /** В созданных сборках (ещё не отгружены), шт. */
    inAssembly: number;
    /** В пути до WB (отгружено, не принято), шт. */
    inTransit: number;
    /** Свободный остаток на ФФ — есть чем пополнить черновик. */
    ffFree: number;
    /** Кратность короба (штук в коробе); null — ppbOf не передан или кратность не задана. */
    ppb: number | null;
    bucket: UrgencyBucket;
}

export interface UrgentShipSummary {
    /** Срочные SKU, уже лежащие в черновике, — двигаем сборку. */
    inDraft: UrgentShipRow[];
    /** Срочные SKU вне черновика, из которых набирается ≥1 целый короб, — кандидаты на добавление. */
    missing: UrgentShipRow[];
    /** Срочные SKU вне черновика, где свободного ФФ-стока меньше короба, — россыпь
     *  (запрещена всегда, расчёт такое не берёт). Пуст, если ppbOf не передан. */
    looseOnly: UrgentShipRow[];
    /** Срочные SKU вне черновика без заданной кратности короба — «нет данных ≠ россыпь».
     *  Пуст, если ppbOf не передан. */
    noPpb: UrgentShipRow[];
    totalLoss: number;
    /** Потери по ВСЕМ срочным вне черновика (missing + looseOnly + noPpb). */
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
    /** nm_id → штук в черновике ВСЕГО (строки + предбронь, Σ tgt). */
    draftQtyByNm: ReadonlyMap<number, number>;
    /** nm_id → штук в ПРЕДБРОНИ (доля draftQtyByNm) — для честной колонки
     *  «В черновике»: предбронь спланирована, но в заявки ещё не едет. */
    prebookQtyByNm?: ReadonlyMap<number, number>;
    /** nm_id → % локализации (getLocalizationSkus). */
    locPctByNm?: ReadonlyMap<number, number>;
    /** Плечо поставки в днях (сборка ФФ + доставка до WB). */
    leadDays: number;
    /** Окно, за которое собрана revenue_bdr (trend_days запроса). */
    trendDays?: number;
    /** nm_id → кратность короба (штук в коробе). Если передан, «вне черновика»
     *  делится на missing (≥1 целый короб) / looseOnly (россыпь) / noPpb (нет
     *  кратности). Без него — прежнее поведение: всё в missing. */
    ppbOf?: (nm: number) => number | null | undefined;
}): UrgentShipSummary {
    const { articles, draftQtyByNm, prebookQtyByNm, locPctByNm, leadDays, trendDays = 7, ppbOf } = params;

    const inDraft: UrgentShipRow[] = [];
    const missing: UrgentShipRow[] = [];
    const looseOnly: UrgentShipRow[] = [];
    const noPpb: UrgentShipRow[] = [];

    for (const a of articles) {
        const bucket = classifyUrgency(a.days_left);
        if (!bucket) continue;

        const daysLeft = Math.max(0, a.days_left);
        const dailyRevenue = (a.revenue_bdr ?? 0) / trendDays;
        const gapDays = Math.max(0, leadDays - daysLeft);
        const draftQty = draftQtyByNm.get(a.nm_id) ?? 0;
        const ffFree = a.stocks_rf ?? 0;
        // stocks_rf приходит уже за вычетом резерва других черновиков (вычитает
        // вызывающий) — здесь ничего не вычитаем повторно.
        const rawPpb = ppbOf?.(a.nm_id);
        const ppb = rawPpb != null && rawPpb > 0 ? rawPpb : null;
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
            // Кламп draftQty: карты считаются из одного черновика, но защищаемся
            // от рассинхрона вызывающего (предбронь не может превышать итог).
            prebookQty: Math.min(draftQty, prebookQtyByNm?.get(a.nm_id) ?? 0),
            wbStock: a.stocks_wb ?? 0,
            inAssembly: a.in_assembly ?? 0,
            inTransit: a.in_transit ?? 0,
            ffFree,
            ppb,
            bucket,
        };

        if (draftQty > 0) inDraft.push(row);
        // Вне черновика показываем только то, по чему есть ФФ-сток.
        else if (ffFree > 0) {
            if (!ppbOf) missing.push(row); // обратная совместимость: без кратности — всё «добавляемое»
            else if (ppb === null) noPpb.push(row); // кратность не задана: «нет данных ≠ россыпь»
            else if (Math.floor(ffFree / ppb) >= 1) missing.push(row); // есть ≥1 целый короб — реально добавляемое
            else looseOnly.push(row); // россыпь меньше короба — запрещена, расчёт такое не берёт
        }
    }

    const byUrgency = (x: UrgentShipRow, y: UrgentShipRow) =>
        BUCKET_ORDER[x.bucket] - BUCKET_ORDER[y.bucket]
        || y.loss - x.loss
        || y.dailyRevenue - x.dailyRevenue;
    inDraft.sort(byUrgency);
    missing.sort(byUrgency);
    looseOnly.sort(byUrgency);
    noPpb.sort(byUrgency);

    return {
        inDraft,
        missing,
        looseOnly,
        noPpb,
        totalLoss: inDraft.reduce((s, r) => s + r.loss, 0),
        // «Всё срочное вне черновика»: добавляемые + россыпь + без кратности.
        missingLoss: [...missing, ...looseOnly, ...noPpb].reduce((s, r) => s + r.loss, 0),
        zeroCount: inDraft.filter(r => r.bucket === 'zero').length,
        criticalCount: inDraft.filter(r => r.bucket === 'critical').length,
        dangerCount: inDraft.filter(r => r.bucket === 'danger').length,
    };
}

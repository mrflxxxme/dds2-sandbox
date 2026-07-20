/**
 * buildUrgentShip — деление «вне черновика» по кратности короба (ppbOf):
 * missing (≥1 целый короб) / looseOnly (россыпь меньше короба — запрещена) /
 * noPpb (кратность не задана — «нет данных ≠ россыпь»).
 */
import { describe, expect, it } from 'vitest';
import { buildUrgentShip } from '@/lib/assembly/urgentShip';
import type { StockAnalyticsArticle } from '@/types/api';

/** Срочный артикул (days_left=3) с дефолтами; revenue_bdr=700 при trendDays=7 → 100 ₽/день. */
function art(over: Partial<StockAnalyticsArticle> & { nm_id: number }): StockAnalyticsArticle {
    return {
        vendor_code: `sku-${over.nm_id}`,
        subject: 'Панель',
        brand: 'brand',
        orders_30d: 0,
        trend_pct: 0,
        avg_daily: 0,
        stocks_wb: 0,
        days_left: 3,
        traffic_light: 'red',
        forecast: [],
        stocks_rf: 0,
        revenue_bdr: 700,
        ...over,
    };
}

const noDraft = new Map<number, number>();
const LEAD = 10; // gap = 10 − 3 = 7 дн → loss = 100 ₽/день × 7 = 700 ₽

describe('buildUrgentShip · ppbOf', () => {
    it('(а) ffFree < ppb → looseOnly, не missing; ppb в строке для подписи «14/15»', () => {
        const s = buildUrgentShip({
            articles: [art({ nm_id: 1, stocks_rf: 14 })], // прод-пример: свободно 14 при коробе 15
            draftQtyByNm: noDraft,
            leadDays: LEAD,
            ppbOf: () => 15,
        });
        expect(s.missing).toHaveLength(0);
        expect(s.noPpb).toHaveLength(0);
        expect(s.looseOnly).toHaveLength(1);
        expect(s.looseOnly[0].nm_id).toBe(1);
        expect(s.looseOnly[0].ffFree).toBe(14);
        expect(s.looseOnly[0].ppb).toBe(15);
    });

    it('(б) ffFree ≥ ppb (есть ≥1 целый короб) → missing', () => {
        const s = buildUrgentShip({
            articles: [
                art({ nm_id: 1, stocks_rf: 15 }), // ровно короб
                art({ nm_id: 2, stocks_rf: 31 }), // 2 короба + россыпь сверху — всё равно добавляемый
            ],
            draftQtyByNm: noDraft,
            leadDays: LEAD,
            ppbOf: () => 15,
        });
        expect(s.missing.map(r => r.nm_id).sort()).toEqual([1, 2]);
        expect(s.looseOnly).toHaveLength(0);
        expect(s.noPpb).toHaveLength(0);
        expect(s.missing[0].ppb).toBe(15);
    });

    it('(в) без ppbOf — прежнее поведение: всё в missing, ppb=null', () => {
        const s = buildUrgentShip({
            articles: [art({ nm_id: 1, stocks_rf: 14 }), art({ nm_id: 2, stocks_rf: 200 })],
            draftQtyByNm: noDraft,
            leadDays: LEAD,
        });
        expect(s.missing).toHaveLength(2);
        expect(s.looseOnly).toHaveLength(0);
        expect(s.noPpb).toHaveLength(0);
        expect(s.missing.every(r => r.ppb === null)).toBe(true);
    });

    it('(г) ppbOf вернул null/0 → noPpb («нет данных ≠ россыпь»)', () => {
        const ppbMap = new Map<number, number | null>([[1, null], [2, 0]]);
        const s = buildUrgentShip({
            articles: [art({ nm_id: 1, stocks_rf: 14 }), art({ nm_id: 2, stocks_rf: 14 })],
            draftQtyByNm: noDraft,
            leadDays: LEAD,
            ppbOf: nm => ppbMap.get(nm),
        });
        expect(s.missing).toHaveLength(0);
        expect(s.looseOnly).toHaveLength(0);
        expect(s.noPpb.map(r => r.nm_id).sort()).toEqual([1, 2]);
        expect(s.noPpb.every(r => r.ppb === null)).toBe(true);
    });

    it('(д) резерв уже вычтен вызывающим: stocks_rf берётся как есть, без повторного вычета', () => {
        // Вызывающий передал stocks_rf = 20 − 6 (резерв других черновиков) = 14.
        const s = buildUrgentShip({
            articles: [art({ nm_id: 1, stocks_rf: 14 })],
            draftQtyByNm: noDraft,
            leadDays: LEAD,
            ppbOf: () => 15,
        });
        expect(s.looseOnly[0].ffFree).toBe(14); // не 8 и не 20
    });

    it('missingLoss = потери по ВСЕМ срочным вне черновика (missing + looseOnly + noPpb)', () => {
        const ppbMap = new Map<number, number | null>([[1, 15], [2, 15], [3, null]]);
        const s = buildUrgentShip({
            articles: [
                art({ nm_id: 1, stocks_rf: 30 }), // missing, loss 700
                art({ nm_id: 2, stocks_rf: 14 }), // looseOnly, loss 700
                art({ nm_id: 3, stocks_rf: 5 }),  // noPpb, loss 700
            ],
            draftQtyByNm: noDraft,
            leadDays: LEAD,
            ppbOf: nm => ppbMap.get(nm),
        });
        expect(s.missing).toHaveLength(1);
        expect(s.looseOnly).toHaveLength(1);
        expect(s.noPpb).toHaveLength(1);
        expect(s.missingLoss).toBe(2100);
    });

    it('SKU в черновике идёт в inDraft независимо от кратности; ffFree=0 не попадает никуда', () => {
        const s = buildUrgentShip({
            articles: [
                art({ nm_id: 1, stocks_rf: 14 }), // в черновике → inDraft, хоть и россыпь
                art({ nm_id: 2, stocks_rf: 0 }),  // вне черновика без ФФ-стока → нигде
            ],
            draftQtyByNm: new Map([[1, 45]]),
            leadDays: LEAD,
            ppbOf: () => 15,
        });
        expect(s.inDraft.map(r => r.nm_id)).toEqual([1]);
        expect(s.missing).toHaveLength(0);
        expect(s.looseOnly).toHaveLength(0);
        expect(s.noPpb).toHaveLength(0);
    });
});
